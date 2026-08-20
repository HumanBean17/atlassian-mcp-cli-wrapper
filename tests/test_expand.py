"""Tests for ``@file`` / stdin expansion of string values."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from mcp_atlassian_cli.config import ConfigError
from mcp_atlassian_cli.expand import expand_string, expand_value


class FakeStdin:
    """A stdin stand-in with a controllable ``isatty()`` and text."""

    def __init__(self, text: str, tty: bool) -> None:
        self._text = text
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty

    def read(self) -> str:
        return self._text


def test_at_file_reads_contents_verbatim(tmp_path: Path) -> None:
    target = tmp_path / "comment.md"
    target.write_text("line one\nline two\n", encoding="utf-8")

    # Verbatim: the trailing newline survives — no strip, no add.
    assert expand_string(f"@{target}", "body") == "line one\nline two\n"


def test_at_missing_file_is_a_loud_usage_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.md"

    with pytest.raises(ConfigError) as excinfo:
        expand_string(f"@{missing}", "body")

    message = str(excinfo.value)
    assert f"--body @{missing}: file not found" in message
    assert f"@@{missing}" in message  # the escape is shown, not left to guess


def test_double_at_is_the_literal_escape() -> None:
    assert expand_string("@@literal", "body") == "@literal"
    assert expand_string("@@@x", "body") == "@@x"


def test_bare_at_passes_through() -> None:
    assert expand_string("@", "body") == "@"


def test_dash_reads_stdin_when_piped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", FakeStdin("piped text\n", tty=False))
    assert expand_string("-", "body") == "piped text\n"


def test_dash_passes_through_at_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", FakeStdin("", tty=True))
    assert expand_string("-", "body") == "-"


def test_dash_passes_through_when_stdin_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed stdin (``0<&-``) sets sys.stdin to None — that must be a
    literal "-", never an AttributeError traceback."""
    monkeypatch.setattr("sys.stdin", None)
    assert expand_string("-", "body") == "-"


def test_double_at_alone_is_a_literal_at() -> None:
    assert expand_string("@@", "body") == "@"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_unreadable_file_is_a_config_error(tmp_path: Path) -> None:
    locked = tmp_path / "locked.md"
    locked.write_text("secret", encoding="utf-8")
    locked.chmod(0o000)

    with pytest.raises(ConfigError, match="could not read"):
        expand_string(f"@{locked}", "body")


def test_non_utf8_file_is_a_config_error(tmp_path: Path) -> None:
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\xff\xfe\x00bad")

    with pytest.raises(ConfigError, match="could not read"):
        expand_string(f"@{binary}", "body")


def test_expand_value_preserves_tuple_type(tmp_path: Path) -> None:
    value = ("plain", 7)
    assert expand_value(value, "read-users") == ("plain", 7)
    assert isinstance(expand_value(value, "read-users"), tuple)


def test_plain_strings_pass_through(tmp_path: Path) -> None:
    assert expand_string("hello", "body") == "hello"
    assert expand_string("user@example.com", "body") == "user@example.com"
    assert expand_string("updated > -7d", "jql") == "updated > -7d"


def test_directory_is_not_a_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        expand_string(f"@{tmp_path}", "body")


def test_expand_value_list_is_element_wise(tmp_path: Path) -> None:
    target = tmp_path / "one.txt"
    target.write_text("one", encoding="utf-8")

    value: Any = [f"@{target}", "plain", 3, True]
    assert expand_value(value, "read-users") == ["one", "plain", 3, True]


def test_expand_value_non_strings_untouched() -> None:
    assert expand_value(5, "limit") == 5
    assert expand_value(True, "compact") is True
    assert expand_value(None, "fields") is None
