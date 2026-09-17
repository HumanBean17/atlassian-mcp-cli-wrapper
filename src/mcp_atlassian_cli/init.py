"""Interactive onboarding wizard (``atli init <service>``).

Pure decision and I/O logic in the :mod:`mcp_atlassian_cli.prime`
discipline: this module imports nothing that reaches the server stack —
the single exception is the lazy ``ToolRunner`` import inside
:func:`verify_profile`, which the wizard pays only at the live-verification
step. All prompting goes through the injectable :class:`Prompt` protocol so
tests script the conversation instead of driving a TTY.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from mcp_atlassian_cli.config import ConfigError


@dataclass(frozen=True)
class VarSpec:
    """One environment variable the wizard collects for an auth method."""

    env_name: str
    secret: bool
    prompt_label: str
    """Human label shown at the prompt, env name included."""


@dataclass(frozen=True)
class AuthMethod:
    """One selectable authentication shape for a service."""

    label: str
    variables: tuple[VarSpec, ...]


@dataclass(frozen=True)
class ServiceSpec:
    """Everything the wizard needs to know about one service."""

    name: str
    url_var: str
    url_default: str | None
    url_hint: str | None
    auth_methods: tuple[AuthMethod, ...]
    ssl_var: str
    verify_tool: str
    verify_args: dict[str, object] = field(default_factory=dict)


_CLOUD_LABEL = "Cloud — username (email) + API token"
_DC_LABEL = "Data Center / Server — personal token"

SERVICES: dict[str, ServiceSpec] = {
    "jira": ServiceSpec(
        name="jira",
        url_var="JIRA_URL",
        url_default=None,
        url_hint=None,
        auth_methods=(
            AuthMethod(
                label=_CLOUD_LABEL,
                variables=(
                    VarSpec("JIRA_USERNAME", False, "Email (JIRA_USERNAME)"),
                    VarSpec("JIRA_API_TOKEN", True, "API token (JIRA_API_TOKEN)"),
                ),
            ),
            AuthMethod(
                label=_DC_LABEL,
                variables=(VarSpec("JIRA_PERSONAL_TOKEN", True, "Personal token (JIRA_PERSONAL_TOKEN)"),),
            ),
        ),
        ssl_var="JIRA_SSL_VERIFY",
        verify_tool="search",
        verify_args={"jql": "ORDER BY created DESC", "limit": 1},
    ),
    "confluence": ServiceSpec(
        name="confluence",
        url_var="CONFLUENCE_URL",
        url_default=None,
        url_hint="Cloud URLs end with /wiki (https://your-company.atlassian.net/wiki)",
        auth_methods=(
            AuthMethod(
                label=_CLOUD_LABEL,
                variables=(
                    VarSpec("CONFLUENCE_USERNAME", False, "Email (CONFLUENCE_USERNAME)"),
                    VarSpec("CONFLUENCE_API_TOKEN", True, "API token (CONFLUENCE_API_TOKEN)"),
                ),
            ),
            AuthMethod(
                label=_DC_LABEL,
                variables=(
                    VarSpec(
                        "CONFLUENCE_PERSONAL_TOKEN",
                        True,
                        "Personal token (CONFLUENCE_PERSONAL_TOKEN)",
                    ),
                ),
            ),
        ),
        ssl_var="CONFLUENCE_SSL_VERIFY",
        verify_tool="search",
        verify_args={"query": 'type = "page"', "limit": 1},
    ),
    "bitbucket": ServiceSpec(
        name="bitbucket",
        url_var="BITBUCKET_URL",
        url_default="https://bitbucket.org",
        url_hint=None,
        auth_methods=(
            AuthMethod(
                label=_CLOUD_LABEL,
                variables=(
                    VarSpec("BITBUCKET_USERNAME", False, "Username (BITBUCKET_USERNAME)"),
                    VarSpec("BITBUCKET_API_TOKEN", True, "API token (BITBUCKET_API_TOKEN)"),
                ),
            ),
            AuthMethod(
                label=_DC_LABEL,
                variables=(
                    VarSpec(
                        "BITBUCKET_PERSONAL_TOKEN",
                        True,
                        "Personal token (BITBUCKET_PERSONAL_TOKEN)",
                    ),
                ),
            ),
        ),
        ssl_var="BITBUCKET_SSL_VERIFY",
        verify_tool="list_repositories",
        verify_args={"max_results": 1},
    ),
}
"""Services in menu order, each with its URL variable, auth shapes, TLS
variable, and the cheap read-only call that proves a setup works."""


def validate_url(text: str) -> str | None:
    """Return why ``text`` is not a usable service URL, or ``None`` if it is.

    Only ``http``/``https`` with a non-empty host is accepted — that is the
    shape every supported Atlassian/Bitbucket deployment has, and anything
    else would fail far less legibly inside the HTTP stack.
    """
    if not text:
        return "URL is empty — enter the full address, e.g. https://your-company.atlassian.net"
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https"):
        return f"'{text}' has no http:// or https:// scheme — include it, e.g. https://{text.lstrip('/')}"
    if not parts.hostname:
        return f"'{text}' has no host — use the full address, e.g. https://jira.example.com"
    return None


_PROFILE_NAME_FORBIDDEN = set('[]".#\n\r\t')


def validate_profile_name(name: str) -> str | None:
    """Return why ``name`` cannot head a ``[profiles.<name>]`` table, or None.

    A dotted or bracketed name would silently nest TOML tables (or fail to
    parse), so the character set is restricted to what a bare header key
    tolerates; surrounding whitespace invites lookalike duplicates.
    """
    if not name:
        return "Profile name is empty."
    if name != name.strip():
        return "Profile name has leading or trailing whitespace."
    offending = sorted(set(name) & _PROFILE_NAME_FORBIDDEN)
    if offending:
        return (
            "Profile name contains character(s) TOML table headers cannot "
            f"carry: {' '.join(repr(c) for c in offending)}. Use letters, "
            "digits, '-', and '_'."
        )
    return None


def toml_basic_string(value: str) -> str:
    """Serialize ``value`` as a TOML basic string (double quotes).

    Uses the JSON-compatible escape set, which is exactly the escape set a
    TOML basic string accepts for the characters that need escaping
    (``\"``, ``\\\\``, control characters); everything else — including any
    unicode — passes through raw.
    """
    return json.dumps(value, ensure_ascii=False)


def mask(value: str) -> str:
    """A fixed-length mask; never echoes secret length back to the console."""
    return "****"


def _is_table_header(line: str) -> bool:
    """True when ``line`` opens a TOML table (``[x]``) or array of tables."""
    return line.lstrip().startswith("[")


def _assignment_line(line: str, key: str) -> bool:
    """True when ``line`` assigns ``key`` (``KEY = "value"`` shape, tolerating
    leading spaces and spaces around ``=``; an inline comment may follow)."""
    stripped = line.strip()
    if not stripped.startswith(key):
        return False
    return stripped[len(key):].lstrip().startswith("=")


def merge_profile_text(text: str, profile_name: str, values: Mapping[str, str]) -> str:
    """Merge ``values`` into the ``[profiles.<profile_name>]`` table of ``text``.

    Surgical by design — the same merge-never-clobber discipline the hook
    installer applies to harness settings: only the target section's key
    lines change (first occurrence of each key replaced wholesale, missing
    keys appended at section end); comments, key order, and every other
    table survive byte-identical. Profiles are flat ``KEY = "value"``
    tables by the config-file contract, so line surgery is exact. No
    section yet: the table appends at EOF behind a blank separator.
    """
    lines = text.splitlines(keepends=True)
    header = f"[profiles.{profile_name}]"
    rendered = {key: f"{key} = {toml_basic_string(value)}\n" for key, value in values.items()}

    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if _matches_header(line, header)
        ),
        None,
    )
    if header_index is None:
        merged = list(lines)
        if merged and not merged[-1].endswith("\n"):
            merged[-1] += "\n"
        if merged:
            merged.append("\n")
        merged.append(header + "\n")
        merged.extend(rendered.values())
        return "".join(merged)

    body_end = header_index + 1
    while body_end < len(lines) and not _is_table_header(lines[body_end]):
        body_end += 1

    updated = lines[: header_index + 1]
    replaced: set[str] = set()
    pending: list[str] = []
    for line in lines[header_index + 1 : body_end]:
        claimed = next(
            (key for key in rendered if key not in replaced and _assignment_line(line, key)),
            None,
        )
        if claimed is not None:
            updated.append(rendered[claimed])
            replaced.add(claimed)
        else:
            updated.append(line)
    for key, line_text in rendered.items():
        if key not in replaced:
            pending.append(line_text)
    updated.extend(pending)
    updated.extend(lines[body_end:])
    return "".join(updated)


def _matches_header(line: str, header: str) -> bool:
    """True when ``line`` is the ``header`` table header (trailing spaces or
    a trailing comment tolerated)."""
    stripped = line.rstrip()
    if stripped == header:
        return True
    return stripped.startswith(header) and stripped[len(header):][:1] in (" ", "\t")


def ensure_default_profile(text: str, profile_name: str) -> str:
    """Set ``default_profile`` to ``profile_name`` unless one is already set.

    The assignment inserts immediately before the first table header so it
    stays top-level (a ``default_profile`` after a table header would belong
    to that table); an existing assignment — any value — is never changed,
    because silently re-pointing someone's default profile is exactly the
    kind of surprise a merge must not cause.
    """
    lines = text.splitlines(keepends=True)
    first_table = next(
        (index for index, line in enumerate(lines) if _is_table_header(line)),
        len(lines),
    )
    already_set = any(
        _assignment_line(line, "default_profile") for line in lines[:first_table]
    )
    if already_set:
        return text
    assignment = f"default_profile = {toml_basic_string(profile_name)}\n"
    if first_table == len(lines):
        merged = list(lines)
        if merged and not merged[-1].endswith("\n"):
            merged[-1] += "\n"
        merged.append(assignment)
        return "".join(merged)
    return "".join(lines[:first_table] + [assignment] + lines[first_table:])


def write_config(path: Path, content: str) -> None:
    """Atomically write ``content`` to ``path`` with owner-only permissions.

    The sibling tmp file is chmod-ed BEFORE the rename, so the credentials
    never exist on disk with group/other read bits — a crash mid-write can
    only leave the pre-write file, never a partial or exposed one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.chmod(0o600)
    os.replace(tmp_path, path)


def config_target_path(
    scope: str,
    *,
    environ: Mapping[str, str],
    home: Path,
    cwd: Path,
) -> Path:
    """The config file a scope writes: project → ``./.atli.toml`` beside the
    repo, global → the machine file runtime reads.

    Global honors ``$ATLI_CONFIG`` when it points at an existing file (init
    must write what runtime loads); a set-but-missing path is the same
    user-fixable error ``config.find_config_file`` raises — in production
    ``main`` surfaces it before init ever runs, the check here covers direct
    library use. No explicit override: ``~/.config/atli/config.toml``.
    """
    if scope == "project":
        return cwd / ".atli.toml"
    if scope == "global":
        explicit = environ.get("ATLI_CONFIG")
        if explicit:
            path = Path(explicit)
            if path.is_file():
                return path
            raise ConfigError(
                f"ATLI_CONFIG is set to '{explicit}', but that file does not "
                "exist. Point ATLI_CONFIG at an existing TOML config file, "
                "or unset it."
            )
        return home / ".config" / "atli" / "config.toml"
    raise ConfigError(f"Unknown scope '{scope}' — use 'project' or 'global'.")
