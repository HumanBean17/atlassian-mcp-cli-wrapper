"""SessionStart primer: pure content and decisions, no CLI or MCP imports.

The ``atli prime`` fast path must stay cheap — it dispatches before the
runner is ever imported — so this module touches nothing but the standard
library (plus :mod:`mcp_atlassian_cli.config` for its ``ConfigError``).
"""

from __future__ import annotations

from collections.abc import Mapping

_SERVICE_ENV_PREFIXES: tuple[str, ...] = ("JIRA", "CONFLUENCE")
"""Service names, in display order, matched against ``<NAME>_URL`` etc."""


def detect_services(environ: Mapping[str, str]) -> tuple[bool, bool]:
    """Return ``(jira_configured, confluence_configured)`` for ``environ``.

    A service counts as configured when its URL and at least one credential
    combination from the README auth matrix are set (present and non-empty):
    ``<S>_USERNAME``+``<S>_API_TOKEN`` (cloud or DC basic), ``<S>_PERSONAL_TOKEN``
    (PAT), or ``<S>_CLIENT_CERT`` (mTLS; ``+KEY`` is optional). OAuth-only
    setups are deliberately not detected.
    """
    return tuple(  # type: ignore[return-value]
        _configured(environ, service) for service in _SERVICE_ENV_PREFIXES
    )


def _configured(environ: Mapping[str, str], service: str) -> bool:
    """One service per the auth matrix: URL plus any credential combination."""
    value = environ.get
    if not value(f"{service}_URL"):
        return False
    credentials = (
        bool(value(f"{service}_USERNAME")) and bool(value(f"{service}_API_TOKEN")),
        bool(value(f"{service}_PERSONAL_TOKEN")),
        bool(value(f"{service}_CLIENT_CERT")),
    )
    return any(credentials)
