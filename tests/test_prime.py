"""Tests for prime.py: detection, override resolution, rendering, envelope."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mcp_atlassian_cli.config import ConfigError
from mcp_atlassian_cli.prime import detect_services, read_override

JIRA_CLOUD = {
    "JIRA_URL": "https://corp.atlassian.net",
    "JIRA_USERNAME": "you@corp.com",
    "JIRA_API_TOKEN": "secret",
}
CONFLUENCE_PAT = {
    "CONFLUENCE_URL": "https://wiki.internal",
    "CONFLUENCE_PERSONAL_TOKEN": "pat-secret",
}


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        (JIRA_CLOUD, (True, False)),
        (
            {
                "JIRA_URL": "https://jira.internal",
                "JIRA_PERSONAL_TOKEN": "pat",
            },
            (True, False),
        ),
        (
            {"JIRA_URL": "https://jira.internal", "JIRA_CLIENT_CERT": "/c.pem"},
            (True, False),
        ),
        (CONFLUENCE_PAT, (False, True)),
        ({**JIRA_CLOUD, **CONFLUENCE_PAT}, (True, True)),
        ({"JIRA_URL": "https://corp.atlassian.net"}, (False, False)),
        (
            {
                "JIRA_URL": "https://corp.atlassian.net",
                "JIRA_USERNAME": "you@corp.com",
            },
            (False, False),
        ),
        (
            {
                "JIRA_URL": "",
                "JIRA_USERNAME": "you@corp.com",
                "JIRA_API_TOKEN": "secret",
            },
            (False, False),
        ),
        ({}, (False, False)),
    ],
)
def test_detect_services(environ: dict[str, str], expected: tuple[bool, bool]) -> None:
    assert detect_services(environ) == expected


@pytest.fixture
def override_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """An empty cwd and fake HOME, with ATLI_PRIME unset.

    Returns ``(cwd, home)`` so tests can place candidate PRIME.md files.
    """
    monkeypatch.delenv("ATLI_PRIME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")
    return tmp_path / "cwd", tmp_path / "home"


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_atli_prime_env_var_wins(
    override_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd, home = override_dirs
    explicit = write(cwd.parent / "explicit.md", "EXPLICIT\n\n")
    write(cwd / ".atli" / "PRIME.md", "CWD OVERRIDE\n")
    write(home / ".config" / "atli" / "PRIME.md", "HOME OVERRIDE\n")
    monkeypatch.setenv("ATLI_PRIME", str(explicit))
    assert read_override(os.environ) == "EXPLICIT"


def test_atli_prime_env_var_missing_is_config_error(
    override_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLI_PRIME", "/no/such/prime.md")
    with pytest.raises(ConfigError) as excinfo:
        read_override(os.environ)
    assert "ATLI_PRIME" in str(excinfo.value)
    assert "/no/such/prime.md" in str(excinfo.value)


def test_cwd_override_beats_home(override_dirs: tuple[Path, Path]) -> None:
    cwd, home = override_dirs
    write(cwd / ".atli" / "PRIME.md", "CWD OVERRIDE\n")
    write(home / ".config" / "atli" / "PRIME.md", "HOME OVERRIDE\n")
    assert read_override(os.environ) == "CWD OVERRIDE"


def test_home_override_when_no_cwd_file(override_dirs: tuple[Path, Path]) -> None:
    _, home = override_dirs
    write(home / ".config" / "atli" / "PRIME.md", "HOME OVERRIDE\n")
    assert read_override(os.environ) == "HOME OVERRIDE"


def test_no_override_anywhere(override_dirs: tuple[Path, Path]) -> None:
    assert read_override(os.environ) is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_unreadable_override_is_config_error(
    override_dirs: tuple[Path, Path],
) -> None:
    cwd, _ = override_dirs
    path = write(cwd / ".atli" / "PRIME.md", "LOCKED\n")
    path.chmod(0o000)
    with pytest.raises(ConfigError) as excinfo:
        read_override(os.environ)
    assert "Could not read PRIME override" in str(excinfo.value)
