"""Tests for profile config discovery, env application, and argv pre-parse."""

import pytest

from mcp_atlassian_cli.config import (
    ConfigError,
    apply_profile,
    describe_profiles,
    extract_profile_flag,
    find_config_file,
    load_config,
    resolve_profile_name,
)

CORP_TOML = """\
default_profile = "corp"

[profiles.corp]
JIRA_URL = "https://corp.atlassian.net"
JIRA_API_TOKEN = "corp-secret"
SSL_VERIFY = 1

[profiles.wiki]
CONFLUENCE_URL = "https://wiki.internal"
CONFLUENCE_PERSONAL_TOKEN = "wiki-secret"
"""

CORP_PROFILE = {
    "JIRA_URL": "https://corp.atlassian.net",
    "JIRA_API_TOKEN": "corp-secret",
    "SSL_VERIFY": "1",
}


def test_find_config_order(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    local_config = cwd / ".atli.toml"
    local_config.write_text(CORP_TOML)
    explicit = tmp_path / "explicit.toml"
    explicit.write_text(CORP_TOML)
    home_config = tmp_path / "home"
    (home_config / ".config" / "atli").mkdir(parents=True)
    home_file = home_config / ".config" / "atli" / "config.toml"
    home_file.write_text(CORP_TOML)

    monkeypatch.chdir(cwd)
    monkeypatch.setenv("HOME", str(home_config))
    monkeypatch.delenv("ATLI_CONFIG", raising=False)

    assert find_config_file() == local_config

    monkeypatch.setenv("ATLI_CONFIG", str(explicit))
    assert find_config_file() == explicit

    monkeypatch.delenv("ATLI_CONFIG")
    local_config.unlink()
    assert find_config_file() == home_file

    home_file.unlink()
    assert find_config_file() is None


def test_find_config_explicit_missing(tmp_path, monkeypatch):
    missing = tmp_path / "nope.toml"
    monkeypatch.setenv("ATLI_CONFIG", str(missing))
    with pytest.raises(ConfigError) as excinfo:
        find_config_file()
    assert str(missing) in str(excinfo.value)


def test_load_config_parses(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text(CORP_TOML)

    config = load_config(file)

    assert config.path == file
    assert config.default_profile == "corp"
    assert set(config.profiles) == {"corp", "wiki"}
    assert config.profiles["corp"] == CORP_PROFILE
    assert config.profiles["wiki"] == {
        "CONFLUENCE_URL": "https://wiki.internal",
        "CONFLUENCE_PERSONAL_TOKEN": "wiki-secret",
    }
    assert all(
        isinstance(value, str)
        for profile in config.profiles.values()
        for value in profile.values()
    )


def test_load_config_none_path():
    config = load_config(None)
    assert config.path is None
    assert config.default_profile is None
    assert config.profiles == {}


def test_load_config_malformed(tmp_path):
    file = tmp_path / "bad.toml"
    file.write_text("[profiles.corp\noops = ")
    with pytest.raises(ConfigError) as excinfo:
        load_config(file)
    assert str(file) in str(excinfo.value)


def test_resolve_precedence(tmp_path):
    config = load_config_from_text(tmp_path, CORP_TOML)

    assert resolve_profile_name("wiki", config, {"ATLI_PROFILE": "corp"}) == "wiki"
    assert resolve_profile_name(None, config, {"ATLI_PROFILE": "wiki"}) == "wiki"
    assert resolve_profile_name(None, config, {}) == "corp"
    assert resolve_profile_name(None, load_config(None), {}) is None


def test_resolve_unknown_name(tmp_path):
    config = load_config_from_text(tmp_path, CORP_TOML)
    with pytest.raises(ConfigError) as excinfo:
        resolve_profile_name("ghost", config, {})
    message = str(excinfo.value)
    assert "ghost" in message
    assert "corp" in message
    assert "wiki" in message

    with pytest.raises(ConfigError) as empty_info:
        resolve_profile_name("ghost", load_config(None), {})
    assert "corp" not in str(empty_info.value)


def test_apply_profile_replaces_per_service():
    environ = {
        "JIRA_URL": "https://ambient.atlassian.net",
        "JIRA_API_TOKEN": "ambient-token",
        "CONFLUENCE_URL": "https://ambient.wiki",
        "CONFLUENCE_PERSONAL_TOKEN": "ambient-wiki-token",
        "MCP_ATLASSIAN_WRITE_PROTECTION": "true",
        "PATH": "/usr/bin",
    }

    apply_profile({"JIRA_URL": "https://corp.atlassian.net", "JIRA_SSL_VERIFY": "true"}, environ)

    assert environ["JIRA_URL"] == "https://corp.atlassian.net"
    assert environ["JIRA_SSL_VERIFY"] == "true"
    assert "JIRA_API_TOKEN" not in environ
    assert environ["CONFLUENCE_URL"] == "https://ambient.wiki"
    assert environ["CONFLUENCE_PERSONAL_TOKEN"] == "ambient-wiki-token"
    assert environ["MCP_ATLASSIAN_WRITE_PROTECTION"] == "true"
    assert environ["PATH"] == "/usr/bin"


def test_apply_profile_clears_ambient_cross_service_credentials():
    """Ambient ATLASSIAN_OAUTH_* / ATLASSIAN_EXTERNAL_AUTH_ENABLE are read by
    the library for BOTH services and take precedence over username/api-token,
    so a profile-chosen host must never receive an ambient OAuth token."""
    environ = {
        "ATLASSIAN_OAUTH_ACCESS_TOKEN": "ambient-oauth-token",
        "ATLASSIAN_OAUTH_CLIENT_ID": "ambient-client-id",
        "ATLASSIAN_OAUTH_CLIENT_SECRET": "ambient-secret",
        "ATLASSIAN_OAUTH_REDIRECT_URI": "http://localhost:8080/callback",
        "ATLASSIAN_OAUTH_SCOPE": "WRITE",
        "ATLASSIAN_OAUTH_CLOUD_ID": "ambient-cloud-id",
        "ATLASSIAN_OAUTH_ENABLE": "true",
        "ATLASSIAN_EXTERNAL_AUTH_ENABLE": "true",
        "PATH": "/usr/bin",
    }

    apply_profile({"JIRA_URL": "https://corp.atlassian.net"}, environ)

    assert environ == {"JIRA_URL": "https://corp.atlassian.net", "PATH": "/usr/bin"}


def test_apply_profile_keeps_profile_defined_oauth():
    """A profile that itself selects OAuth keeps its own token."""
    environ = {"ATLASSIAN_OAUTH_ACCESS_TOKEN": "ambient-oauth-token"}

    apply_profile(
        {
            "JIRA_URL": "https://corp.atlassian.net",
            "ATLASSIAN_OAUTH_ACCESS_TOKEN": "corp-oauth-token",
        },
        environ,
    )

    assert environ["ATLASSIAN_OAUTH_ACCESS_TOKEN"] == "corp-oauth-token"


def test_apply_profile_without_service_prefix_leaves_ambient_oauth():
    """A profile touching no service prefix is inert (a TOOLSETS-only profile
    is a no-op — see the README note): ambient OAuth must stay untouched."""
    environ = {"ATLASSIAN_OAUTH_ACCESS_TOKEN": "ambient-oauth-token", "TOOLSETS": "all"}

    apply_profile({"TOOLSETS": "jira"}, environ)

    assert environ["ATLASSIAN_OAUTH_ACCESS_TOKEN"] == "ambient-oauth-token"
    assert environ["TOOLSETS"] == "all"


def test_extract_flag_both_forms():
    assert extract_profile_flag(["--profile", "corp", "jira", "get-issue"]) == (
        "corp",
        ["jira", "get-issue"],
    )
    assert extract_profile_flag(["--profile=corp", "confluence", "search"]) == (
        "corp",
        ["confluence", "search"],
    )
    argv = ["jira", "get-issue", "--profile", "corp"]
    assert extract_profile_flag(argv) == (None, argv)


def test_extract_flag_errors():
    with pytest.raises(ConfigError):
        extract_profile_flag(["--profile"])
    with pytest.raises(ConfigError):
        extract_profile_flag(["--profile="])
    with pytest.raises(ConfigError):
        extract_profile_flag(["--profile", "--verbose", "jira"])
    assert extract_profile_flag(["--profile", "a", "--profile", "b", "jira"]) == (
        "b",
        ["jira"],
    )


def test_describe_profiles_hides_secrets(tmp_path):
    config = load_config_from_text(tmp_path, CORP_TOML)

    output = describe_profiles(config, "corp")

    assert "https://corp.atlassian.net" in output
    assert "jira: https://corp.atlassian.net" in output
    assert "corp-secret" not in output
    assert "wiki-secret" not in output
    assert "confluence: https://wiki.internal" in output
    lines = output.splitlines()
    corp_line = next(line for line in lines if "corp" in line)
    assert corp_line.startswith("* ")
    assert corp_line.endswith("(default)")
    wiki_line = next(line for line in lines if "wiki" in line)
    assert wiki_line.startswith("  ")
    assert output.endswith("\n")

    empty_output = describe_profiles(load_config(None), None)
    assert empty_output == "No profiles configured.\n"


def load_config_from_text(tmp_path, text: str):
    file = tmp_path / "config.toml"
    file.write_text(text)
    return load_config(file)
