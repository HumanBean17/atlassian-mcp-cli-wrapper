"""SessionStart primer: pure content and decisions, no CLI or MCP imports.

The ``atli prime`` fast path must stay cheap — it dispatches before the
runner is ever imported — so this module touches nothing but the standard
library (plus :mod:`mcp_atlassian_cli.config` for its ``ConfigError``).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from mcp_atlassian_cli.config import ConfigError

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


def read_override(environ: Mapping[str, str]) -> str | None:
    """Return the PRIME.md override content (trailing whitespace stripped).

    Lookup order, first existing file wins: ``$ATLI_PRIME`` (a set-but-missing
    path is an error the user must fix, mirroring ``ATLI_CONFIG``), then
    ``./.atli/PRIME.md``, then ``~/.config/atli/PRIME.md``. ``None`` means no
    override exists; an existing-but-empty file yields ``""`` — an explicit
    override that prints nothing.
    """
    explicit = environ.get("ATLI_PRIME")
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
