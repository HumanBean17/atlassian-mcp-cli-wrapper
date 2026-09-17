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
from dataclasses import dataclass, field
from urllib.parse import urlsplit


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
