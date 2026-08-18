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


_TITLE = "# atli — Jira & Confluence CLI"
_USAGE_PATTERN = "atli [--profile NAME] <service> <tool> [flags]"
_JIRA_EXAMPLE = "atli jira get-issue --issue-key PROJ-1"
_CONFLUENCE_EXAMPLE = 'atli confluence search --query "deploy"'
_DISCOVERY = """\
## Discovery
atli tools [--service jira]           # one line per tool
atli jira get-issue --help            # params, types, defaults"""
_NOTES = """\
## Notes
- Tool output prints verbatim (LLM-ready markdown from mcp-atlassian).
- Repeatable list flags repeat: `--read-users alice --read-users bob`.
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
    jira, confluence = detect_services(environ)
    if not (jira or confluence):
        return ""
    return _assemble(jira, confluence, profile_name, config_path)


def render_export(
    environ: Mapping[str, str],
    profile_name: str | None,
    config_path: Path | None,
) -> str:
    """The default primer for ``--export``: never silenced.

    With no service configured the Configured line reads ``(none)`` and both
    example lines appear — the customization bootstrap must always print.
    """
    jira, confluence = detect_services(environ)
    return _assemble(jira, confluence, profile_name, config_path)


def _assemble(
    jira: bool, confluence: bool, profile_name: str | None, config_path: Path | None
) -> str:
    """Assemble the primer: dynamic header, then the static usage core."""
    configured = [
        name for name, on in (("jira", jira), ("confluence", confluence)) if on
    ]
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
    # render_default is silent), both example lines appear so the exported
    # template is canonical.
    show_jira = jira or not (jira or confluence)
    show_confluence = confluence or not (jira or confluence)
    if show_jira:
        lines.append(_JIRA_EXAMPLE)
    if show_confluence:
        lines.append(_CONFLUENCE_EXAMPLE)
    lines += ["", _DISCOVERY, "", _NOTES]
    return "\n".join(lines) + "\n"


def _display_path(config_path: Path) -> str:
    """The config path as shown in the Profile line, home abbreviated to ``~``."""
    text = str(config_path)
    home = str(Path.home())
    if text == home:
        return "~"
    if text.startswith(home + os.sep):
        return "~" + text[len(home):]
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
