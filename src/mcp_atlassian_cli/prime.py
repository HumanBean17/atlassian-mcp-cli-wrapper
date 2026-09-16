"""SessionStart primer: pure content and decisions, no CLI or MCP imports.

The ``atli prime`` fast path must stay cheap — it dispatches before the
runner is ever imported — so this module touches nothing but the standard
library (plus :mod:`mcp_atlassian_cli.config` for its ``ConfigError``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from mcp_atlassian_cli.config import ConfigError

_AUTH_MATRIX: Mapping[
    str, tuple[str, tuple[tuple[str | tuple[str, ...], ...], ...]]
] = {
    # Each service: its URL variable plus the credential combinations from
    # the README auth matrix. A combination is a tuple of clauses; a clause
    # is one env-var name (counts when set non-empty) or a tuple of
    # alternative names (counts when ANY is set non-empty). A service counts
    # as configured when the URL is set and some combination is fully
    # satisfied. Bitbucket has no mTLS combination (the fork has none); its
    # Cloud secret is the API_TOKEN (the dead-since-2026-06-09 APP_PASSWORD
    # still satisfies detection, for installs with a leftover set).
    "jira": (
        "JIRA_URL",
        (
            ("JIRA_USERNAME", "JIRA_API_TOKEN"),
            ("JIRA_PERSONAL_TOKEN",),
            ("JIRA_CLIENT_CERT",),
        ),
    ),
    "confluence": (
        "CONFLUENCE_URL",
        (
            ("CONFLUENCE_USERNAME", "CONFLUENCE_API_TOKEN"),
            ("CONFLUENCE_PERSONAL_TOKEN",),
            ("CONFLUENCE_CLIENT_CERT",),
        ),
    ),
    "bitbucket": (
        "BITBUCKET_URL",
        (
            ("BITBUCKET_USERNAME", ("BITBUCKET_API_TOKEN", "BITBUCKET_APP_PASSWORD")),
            ("BITBUCKET_PERSONAL_TOKEN",),
        ),
    ),
}
"""Services in display order, mapped to their URL var and auth matrix."""


def detect_services(environ: Mapping[str, str]) -> dict[str, bool]:
    """Per-service configured flags for ``environ``, keyed in display order.

    A service counts as configured when its URL and a credential combination
    from its auth-matrix entry are set (present and non-empty). OAuth-only
    setups are deliberately not detected.
    """
    return {
        service: _configured(environ, service) for service in _AUTH_MATRIX
    }


def _clause_satisfied(environ: Mapping[str, str], clause: str | tuple[str, ...]) -> bool:
    """One clause: a single env name, or alternatives of which any one counts."""
    names = (clause,) if isinstance(clause, str) else clause
    return any(environ.get(name) for name in names)


def _configured(environ: Mapping[str, str], service: str) -> bool:
    """One service per its auth matrix: URL plus one full combination."""
    url_var, combinations = _AUTH_MATRIX[service]
    if not environ.get(url_var):
        return False
    return any(
        all(_clause_satisfied(environ, clause) for clause in combination)
        for combination in combinations
    )


def read_override(environ: Mapping[str, str]) -> str | None:
    """Return the PRIME.md override content (trailing whitespace stripped).

    Lookup order, first existing file wins: ``$ATLI_PRIME`` (a set-but-missing
    path is an error the user must fix, mirroring ``ATLI_CONFIG``), then
    ``./.atli/PRIME.md``, then ``~/.config/atli/PRIME.md``. ``None`` means no
    override exists; an existing-but-empty file yields ``""`` — an explicit
    override that prints nothing.
    """
    explicit = environ.get("ATLI_PRIME")
    # An empty ATLI_PRIME counts as unset — same falsy check ATLI_CONFIG gets
    # in config.find_config_file, so shell plumbing can pass "" harmlessly.
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise ConfigError(
                f"ATLI_PRIME is set to '{explicit}', but that file does not "
                "exist. Point ATLI_PRIME at an existing markdown file, "
                "or unset it."
            )
        return _read_override_file(path)
    candidates = (
        Path.cwd() / ".atli" / "PRIME.md",
        Path.home() / ".config" / "atli" / "PRIME.md",
    )
    for candidate in candidates:
        if candidate.is_file():
            return _read_override_file(candidate)
    return None


def _read_override_file(path: Path) -> str:
    """Read one override file, surfacing failures as ConfigError."""
    try:
        return path.read_text(encoding="utf-8").rstrip()
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"Could not read PRIME override '{path}': {error}") from error


_TITLE = "# atli — Jira, Confluence & Bitbucket CLI"
_USAGE_PATTERN = "atli [--profile NAME] <service> <tool> [flags]"
_JIRA_EXAMPLE = "atli jira get-issue --issue-key PROJ-1"
_CONFLUENCE_EXAMPLE = 'atli confluence search --query "deploy"'
_BITBUCKET_EXAMPLE = "atli bitbucket list-repositories"
_DISCOVERY = """\
## Discovery
atli tools [--service jira]           # one line per tool
atli tools --search TEXT              # shortlist by keyword
atli jira get-issue --help            # params, types, defaults, examples
atli prime --install                  # onboard: SessionStart hook"""
_NOTES = """\
## Notes
- Tool output prints verbatim (LLM-ready markdown from mcp-atlassian).
- Repeatable list flags repeat: `--read-users alice --read-users bob`.
- Long values read files: `--content @page.md` ('-' stdin; '@@' literal).
- Exit codes: 0 success, 1 tool/server failure, 2 usage/config error.
- Startup ~1 s warm; prefer one `search` over many single-item calls."""


def render_default(
    environ: Mapping[str, str],
    profile_name: str | None,
    config_path: Path | None,
) -> str:
    """The default primer; empty string when no service is configured.

    Empty output is the silence rule: zero token cost in SessionStart hooks
    on machines where atli cannot act anyway.
    """
    services = detect_services(environ)
    if not any(services.values()):
        return ""
    return _assemble(services, profile_name, config_path)


def render_export(
    environ: Mapping[str, str],
    profile_name: str | None,
    config_path: Path | None,
) -> str:
    """The default primer for ``--export``: never silenced.

    With no service configured the Configured line reads ``(none)`` and all
    example lines appear — the customization bootstrap must always print.
    """
    return _assemble(detect_services(environ), profile_name, config_path)


def _assemble(
    services: Mapping[str, bool],
    profile_name: str | None,
    config_path: Path | None,
) -> str:
    """Assemble the primer: dynamic header, then the static usage core."""
    configured = [name for name, on in services.items() if on]
    lines = [
        _TITLE,
        "",
        "Configured: " + (", ".join(configured) if configured else "(none)"),
    ]
    if config_path is not None:
        if profile_name is None:
            lines.append("Profile: ambient environment")
        else:
            lines.append(f"Profile: {profile_name} ({_display_path(config_path)})")
    lines += ["", "## Usage", _USAGE_PATTERN]
    # With nothing configured (only reachable via render_export —
    # render_default is silent), every example line appears so the exported
    # template is canonical.
    nothing = not configured
    if services["jira"] or nothing:
        lines.append(_JIRA_EXAMPLE)
    if services["confluence"] or nothing:
        lines.append(_CONFLUENCE_EXAMPLE)
    if services["bitbucket"] or nothing:
        lines.append(_BITBUCKET_EXAMPLE)
    lines += ["", _DISCOVERY, "", _NOTES]
    return "\n".join(lines) + "\n"


def _display_path(config_path: Path) -> str:
    """The config path as shown in the Profile line, home abbreviated to ``~``.

    The abbreviated portion always uses ``/`` separators: the primer is one
    cross-platform document (pinned verbatim in tests), and a Windows-native
    ``~\\work.toml`` would make it platform-dependent for no reader benefit.
    Paths outside the home stay verbatim, native separators included.
    """
    text = str(config_path)
    home = str(Path.home())
    if text == home:
        return "~"
    if text.startswith(home + os.sep):
        rest = text[len(home) + len(os.sep):]
        return "~/" + rest.replace(os.sep, "/")
    return text


def wrap_hook_json(content: str) -> str:
    """Wrap ``content`` in the SessionStart hook envelope (one JSON line).

    Identical in shape to ``bd prime --hook-json``: compact separators, keys
    in this order, non-ASCII raw. Served as-is to Claude Code, Gemini CLI,
    and Codex.
    """
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": content,
            }
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
