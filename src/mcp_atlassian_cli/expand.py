"""``@file`` / stdin expansion for long string values.

Long content (comments, descriptions, page bodies) is hostile to shell
quoting, and agents have file-write tools: letting any string value read a
file turns their natural channel into the CLI's input channel.

A missing file is a LOUD usage error, not a silent pass-through: a typo'd
path silently becoming the literal text ``"@foo.md"`` is far more confusing
downstream than an error that names the flag and shows the ``@@`` escape.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from mcp_atlassian_cli.config import ConfigError


def expand_string(value: str, flag: str) -> str:
    """Expand one string value; see the module docstring for the rationale.

    Rules, in order: ``-`` reads stdin when it is piped (at a TTY it stays a
    literal so interactive use never blocks); ``@@x`` is the literal ``@x``
    escape; a bare ``@`` passes through (no path to read); ``@path`` reads an
    existing file verbatim or errors loudly; anything else passes through.
    """
    if value == "-":
        if sys.stdin.isatty():
            return value
        return sys.stdin.read()
    if value.startswith("@@"):
        return value[1:]
    if value == "@":
        return value
    if value.startswith("@"):
        path = Path(value[1:])
        if not path.is_file():
            raise ConfigError(
                f"--{flag} {value}: file not found. "
                f"Use @@{value[1:]} to pass a literal '@' value"
            )
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise ConfigError(
                f"--{flag}: could not read '{value[1:]}': {error}"
            ) from error
    return value


def expand_value(value: Any, flag: str) -> Any:
    """Apply :func:`expand_string` to a value: strings directly, sequences
    element-wise (strings expanded, everything else untouched), other types
    pass through unchanged."""
    if isinstance(value, str):
        return expand_string(value, flag)
    if isinstance(value, (list, tuple)):
        return type(value)(
            expand_string(item, flag) if isinstance(item, str) else item
            for item in value
        )
    return value
