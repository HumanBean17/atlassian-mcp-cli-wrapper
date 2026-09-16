"""Tests for prime.py: detection, override resolution, rendering, envelope."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import isolate_home

from mcp_atlassian_cli.config import ConfigError
from mcp_atlassian_cli.prime import (
    detect_services,
    read_override,
    render_default,
    render_export,
    wrap_hook_json,
)

JIRA_CLOUD = {
    "JIRA_URL": "https://corp.atlassian.net",
    "JIRA_USERNAME": "you@corp.com",
    "JIRA_API_TOKEN": "secret",
}
CONFLUENCE_PAT = {
    "CONFLUENCE_URL": "https://wiki.internal",
    "CONFLUENCE_PERSONAL_TOKEN": "pat-secret",
}
BITBUCKET_CLOUD_APP_PASSWORD = {
    "BITBUCKET_URL": "https://bitbucket.org/team",
    "BITBUCKET_USERNAME": "you@corp.com",
    "BITBUCKET_APP_PASSWORD": "app-password",
}
BITBUCKET_SERVER_PAT = {
    "BITBUCKET_URL": "https://bitbucket.internal",
    "BITBUCKET_PERSONAL_TOKEN": "server-pat",
}


def expect(
    jira: bool = False, confluence: bool = False, bitbucket: bool = False
) -> dict[str, bool]:
    """The detect_services result for the given configured services."""
    return {"jira": jira, "confluence": confluence, "bitbucket": bitbucket}


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        (JIRA_CLOUD, expect(jira=True)),
        (
            {
                "JIRA_URL": "https://jira.internal",
                "JIRA_PERSONAL_TOKEN": "pat",
            },
            expect(jira=True),
        ),
        (
            {"JIRA_URL": "https://jira.internal", "JIRA_CLIENT_CERT": "/c.pem"},
            expect(jira=True),
        ),
        (CONFLUENCE_PAT, expect(confluence=True)),
        ({**JIRA_CLOUD, **CONFLUENCE_PAT}, expect(jira=True, confluence=True)),
        ({"JIRA_URL": "https://corp.atlassian.net"}, expect()),
        (
            {
                "JIRA_URL": "https://corp.atlassian.net",
                "JIRA_USERNAME": "you@corp.com",
            },
            expect(),
        ),
        (
            {
                "JIRA_URL": "",
                "JIRA_USERNAME": "you@corp.com",
                "JIRA_API_TOKEN": "secret",
            },
            expect(),
        ),
        # A credential set to the empty string counts as unset (truthiness),
        # for clauses as well as the URL var.
        (
            {
                "JIRA_URL": "https://corp.atlassian.net",
                "JIRA_USERNAME": "you@corp.com",
                "JIRA_API_TOKEN": "",
            },
            expect(),
        ),
        ({}, expect()),
        # Bitbucket Cloud: app password (the fork's recommended form).
        (BITBUCKET_CLOUD_APP_PASSWORD, expect(bitbucket=True)),
        # Bitbucket Cloud: API_TOKEN is the fork's alias for the app password.
        (
            {
                "BITBUCKET_URL": "https://bitbucket.org/team",
                "BITBUCKET_USERNAME": "you@corp.com",
                "BITBUCKET_API_TOKEN": "api-token",
            },
            expect(bitbucket=True),
        ),
        # Bitbucket Server/DC: personal token.
        (BITBUCKET_SERVER_PAT, expect(bitbucket=True)),
        # URL alone, or credentials without a password, never count.
        ({"BITBUCKET_URL": "https://bitbucket.org/team"}, expect()),
        (
            {
                "BITBUCKET_URL": "https://bitbucket.org/team",
                "BITBUCKET_USERNAME": "you@corp.com",
            },
            expect(),
        ),
        # No mTLS combination exists for bitbucket (the fork has none).
        (
            {"BITBUCKET_URL": "https://bitbucket.internal", "BITBUCKET_CLIENT_CERT": "/c.pem"},
            expect(),
        ),
        (
            {**JIRA_CLOUD, **CONFLUENCE_PAT, **BITBUCKET_SERVER_PAT},
            expect(jira=True, confluence=True, bitbucket=True),
        ),
    ],
)
def test_detect_services(environ: dict[str, str], expected: dict[str, bool]) -> None:
    result = detect_services(environ)
    assert result == expected
    # Display order is fixed regardless of what is configured.
    assert list(result) == ["jira", "confluence", "bitbucket"]


@pytest.fixture
def override_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """An empty cwd and fake HOME, with ATLI_PRIME unset.

    Returns ``(cwd, home)`` so tests can place candidate PRIME.md files.
    """
    monkeypatch.delenv("ATLI_PRIME", raising=False)
    isolate_home(monkeypatch, tmp_path / "home")
    (tmp_path / "home").mkdir()
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")
    return tmp_path / "cwd", tmp_path / "home"


def write(path: Path, content: str) -> Path:
    """Create parent dirs, write ``content``, return the path."""
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


@pytest.mark.skipif(
    os.name != "posix"
    or (hasattr(os, "geteuid") and os.geteuid() == 0),  # root ignores permissions
    reason="POSIX only: chmod must actually lock reads (Windows chmod can't)",
)
def test_unreadable_override_is_config_error(
    override_dirs: tuple[Path, Path],
) -> None:
    cwd, _ = override_dirs
    path = write(cwd / ".atli" / "PRIME.md", "LOCKED\n")
    path.chmod(0o000)
    with pytest.raises(ConfigError) as excinfo:
        read_override(os.environ)
    assert "Could not read PRIME override" in str(excinfo.value)


CANONICAL_PRIMER = """\
# atli — Jira, Confluence & Bitbucket CLI

Configured: jira, confluence
Profile: work (~/work.toml)

## Usage
atli [--profile NAME] <service> <tool> [flags]
atli jira get-issue --issue-key PROJ-1
atli confluence search --query "deploy"

## Discovery
atli tools [--service jira]           # one line per tool
atli tools --search TEXT              # shortlist by keyword
atli jira get-issue --help            # params, types, defaults, examples
atli prime --install                  # onboard: SessionStart hook

## Notes
- Tool output prints verbatim (LLM-ready markdown from mcp-atlassian).
- Repeatable list flags repeat: `--read-users alice --read-users bob`.
- Long values read files: `--content @page.md` ('-' stdin; '@@' literal).
- Exit codes: 0 success, 1 tool/server failure, 2 usage/config error.
- Startup ~1 s warm; prefer one `search` over many single-item calls.
"""


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    isolate_home(monkeypatch, home)
    return home


def test_render_default_canonical_document(fake_home: Path) -> None:
    """The full primer for both services + a named profile, pinned verbatim."""
    config_path = fake_home / "work.toml"
    rendered = render_default(
        {**JIRA_CLOUD, **CONFLUENCE_PAT}, "work", config_path
    )
    assert rendered == CANONICAL_PRIMER


def test_render_default_jira_only() -> None:
    rendered = render_default(JIRA_CLOUD, None, None)
    assert "Configured: jira\n" in rendered
    assert "atli jira get-issue --issue-key PROJ-1" in rendered
    assert "atli confluence search" not in rendered


def test_render_default_confluence_only() -> None:
    rendered = render_default(CONFLUENCE_PAT, None, None)
    assert "Configured: confluence\n" in rendered
    assert 'atli confluence search --query "deploy"' in rendered
    assert "atli jira get-issue --issue-key" not in rendered
    assert "## Discovery" in rendered  # static core still present


def test_render_default_bitbucket_only() -> None:
    rendered = render_default(BITBUCKET_CLOUD_APP_PASSWORD, None, None)
    assert "Configured: bitbucket\n" in rendered
    assert "atli bitbucket list-repositories" in rendered
    assert "atli jira get-issue --issue-key" not in rendered
    assert 'atli confluence search --query "deploy"' not in rendered


def test_render_default_all_three() -> None:
    rendered = render_default(
        {**JIRA_CLOUD, **CONFLUENCE_PAT, **BITBUCKET_SERVER_PAT}, None, None
    )
    assert "Configured: jira, confluence, bitbucket\n" in rendered
    assert "atli jira get-issue --issue-key PROJ-1" in rendered
    assert 'atli confluence search --query "deploy"' in rendered
    assert "atli bitbucket list-repositories" in rendered


def test_render_default_silent_when_unconfigured() -> None:
    assert render_default({}, "work", Path("/anywhere/config.toml")) == ""


def test_render_default_ambient_profile(fake_home: Path) -> None:
    rendered = render_default(JIRA_CLOUD, None, fake_home / "config.toml")
    assert "Profile: ambient environment\n" in rendered


def test_render_default_no_config_file_omits_profile_line() -> None:
    rendered = render_default(JIRA_CLOUD, None, None)
    assert "Profile:" not in rendered


def test_render_default_config_path_outside_home_is_verbatim(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "atli.toml"
    rendered = render_default(JIRA_CLOUD, "dc", config_path)
    assert f"Profile: dc ({config_path})\n" in rendered


def test_render_export_when_unconfigured() -> None:
    rendered = render_export({}, "work", Path("/anywhere/config.toml"))
    assert rendered != ""
    assert "Configured: (none)\n" in rendered
    assert "atli jira get-issue --issue-key PROJ-1" in rendered
    assert 'atli confluence search --query "deploy"' in rendered
    assert "atli bitbucket list-repositories" in rendered


def test_primer_teaches_search_atfile_and_install() -> None:
    """The primer must carry the three new agent verbs: --search shortlist,
    @file long values, and the --install onboarding command."""
    rendered = render_export({}, None, None)
    assert "--search TEXT" in rendered
    assert "prime --install" in rendered
    assert "--content @page.md" in rendered


def test_primer_lines_stay_compact() -> None:
    """The primer's value is its size: every static-core line stays within
    78 columns so SessionStart injection costs as few tokens as possible."""
    from mcp_atlassian_cli import prime as prime_mod

    for block in (prime_mod._DISCOVERY, prime_mod._NOTES):
        for line in block.splitlines():
            assert len(line) <= 78, line


_TRICKY = '# atli — "quoted"\n\ttab \\ end ünïcode'


def test_wrap_hook_json_round_trips_tricky_content() -> None:
    envelope = wrap_hook_json(_TRICKY)
    assert json.loads(envelope) == {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": _TRICKY,
        }
    }


def test_wrap_hook_json_is_one_compact_line() -> None:
    envelope = wrap_hook_json(_TRICKY)
    assert "\n" not in envelope
    assert envelope.startswith(
        '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":'
    )


def test_wrap_hook_json_empty_content() -> None:
    envelope = wrap_hook_json("")
    assert json.loads(envelope)["hookSpecificOutput"]["additionalContext"] == ""
