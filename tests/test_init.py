"""Tests for the interactive onboarding wizard (``atli init <service>``)."""

from __future__ import annotations

import stat
import tomllib
from pathlib import Path

import pytest

from mcp_atlassian_cli.config import ConfigError
from mcp_atlassian_cli.init import (
    SERVICES,
    config_target_path,
    ensure_default_profile,
    mask,
    merge_profile_text,
    toml_basic_string,
    validate_profile_name,
    validate_url,
    write_config,
)


def roundtrip(value: str) -> str:
    """Assert ``value`` survives a TOML round-trip through the escaper."""
    return tomllib.loads("K = " + toml_basic_string(value))["K"]


def test_validate_url_accepts() -> None:
    assert validate_url("https://x.atlassian.net") is None
    assert validate_url("http://jira.internal:8080") is None
    assert validate_url("https://host/wiki") is None


def test_validate_url_rejects() -> None:
    empty = validate_url("")
    schemeless = validate_url("jira.example.com")
    wrong_scheme = validate_url("ftp://x")
    no_host = validate_url("https://")
    for error in (empty, schemeless, wrong_scheme, no_host):
        assert isinstance(error, str) and error
    assert "scheme" in schemeless or "http" in schemeless
    assert "ftp" in wrong_scheme
    assert "host" in no_host


def test_validate_profile_name() -> None:
    for good in ("work", "dc-2", "Work_Prod"):
        assert validate_profile_name(good) is None
    for bad in ("a.b", "[x]", 'a"b', "a#b", " work", "work ", "", "a\nb"):
        assert validate_profile_name(bad) is not None


def test_toml_basic_string_roundtrip() -> None:
    for value in ("plain", "back\\slash", 'quo"te', "new\nline", "tab\tchar"):
        assert roundtrip(value) == value


def test_toml_basic_string_handles_unicode_and_controls() -> None:
    assert roundtrip("uni→code") == "uni→code"
    assert roundtrip("ctl\x01") == "ctl\x01"


def test_mask_is_fixed_length() -> None:
    assert mask("x") == mask("long-token-value") == "****"


def test_services_table_shape() -> None:
    assert list(SERVICES) == ["jira", "confluence", "bitbucket"]
    jira = SERVICES["jira"]
    assert jira.url_var == "JIRA_URL"
    assert jira.url_default is None
    cloud, dc = jira.auth_methods
    assert [v.env_name for v in cloud.variables] == ["JIRA_USERNAME", "JIRA_API_TOKEN"]
    assert [v.secret for v in cloud.variables] == [False, True]
    assert [v.env_name for v in dc.variables] == ["JIRA_PERSONAL_TOKEN"]
    assert jira.ssl_var == "JIRA_SSL_VERIFY"
    assert (jira.verify_tool, jira.verify_args) == (
        "search",
        {"jql": "ORDER BY created DESC", "limit": 1},
    )

    confluence = SERVICES["confluence"]
    assert confluence.url_hint and "wiki" in confluence.url_hint
    assert confluence.verify_args == {"query": 'type = "page"', "limit": 1}

    bitbucket = SERVICES["bitbucket"]
    assert bitbucket.url_default == "https://bitbucket.org"
    assert bitbucket.verify_tool == "list_repositories"
    assert bitbucket.verify_args == {"max_results": 1}
    cloud_bb = bitbucket.auth_methods[0]
    assert [v.env_name for v in cloud_bb.variables] == [
        "BITBUCKET_USERNAME",
        "BITBUCKET_API_TOKEN",
    ]


def test_merge_appends_new_profile_preserving_everything() -> None:
    text = (
        "# my config\n"
        'default_profile = "other"\n'
        "\n"
        "[profiles.other]\n"
        'JIRA_URL = "https://old.example.com"  # inline comment\n'
        'TOOLSETS = "all"\n'
    )

    result = merge_profile_text(
        text, "work", {"JIRA_URL": "https://new.example.com", "JIRA_API_TOKEN": "tok"}
    )

    for original_line in (
        "# my config",
        'default_profile = "other"',
        "[profiles.other]",
        'JIRA_URL = "https://old.example.com"  # inline comment',
        'TOOLSETS = "all"',
    ):
        assert original_line in result
    data = tomllib.loads(result)
    assert data["profiles"]["work"] == {
        "JIRA_URL": "https://new.example.com",
        "JIRA_API_TOKEN": "tok",
    }
    assert data["profiles"]["other"]["JIRA_URL"] == "https://old.example.com"


def test_merge_updates_existing_key_in_place() -> None:
    text = (
        "[profiles.work]\n"
        'JIRA_URL = "https://old.example.com"\n'
        'TOOLSETS = "all"\n'
        "\n"
        "[profiles.dc]\n"
        'JIRA_URL = "https://dc.example.com"\n'
    )

    result = merge_profile_text(text, "work", {"JIRA_URL": "https://new.example.com"})

    data = tomllib.loads(result)
    assert data["profiles"]["work"] == {
        "JIRA_URL": "https://new.example.com",
        "TOOLSETS": "all",
    }
    assert data["profiles"]["dc"]["JIRA_URL"] == "https://dc.example.com"
    assert result.index("[profiles.dc]") > result.index("TOOLSETS")  # order kept


def test_merge_adds_missing_keys_to_section() -> None:
    text = "[profiles.work]\nJIRA_URL = \"https://x.example.com\"\n\n[profiles.dc]\nK = \"v\"\n"

    result = merge_profile_text(
        text,
        "work",
        {"JIRA_URL": "https://x.example.com", "JIRA_USERNAME": "a@b.c", "JIRA_API_TOKEN": "t"},
    )

    data = tomllib.loads(result)
    assert data["profiles"]["work"] == {
        "JIRA_URL": "https://x.example.com",
        "JIRA_USERNAME": "a@b.c",
        "JIRA_API_TOKEN": "t",
    }
    work_start = result.index("[profiles.work]")
    dc_start = result.index("[profiles.dc]")
    appended = result[work_start:dc_start]
    assert appended.index("JIRA_USERNAME") < appended.index("JIRA_API_TOKEN")


def test_merge_replaces_only_first_duplicate_key() -> None:
    text = '[profiles.work]\nJIRA_URL = "https://one.example.com"\nJIRA_URL = "https://two.example.com"\n'
    second_line = 'JIRA_URL = "https://two.example.com"'

    result = merge_profile_text(text, "work", {"JIRA_URL": "https://new.example.com"})

    assert 'JIRA_URL = "https://new.example.com"' in result
    assert second_line in result  # the duplicate survives byte-identical
    assert result.index("https://new.example.com") < result.index("https://two.example.com")


def test_merge_into_empty_text() -> None:
    result = merge_profile_text("", "work", {"JIRA_URL": "https://x.example.com"})

    assert result == '[profiles.work]\nJIRA_URL = "https://x.example.com"\n'


def test_merge_escapes_values() -> None:
    tricky = 'quo"te\\back\nline'

    result = merge_profile_text("", "work", {"JIRA_PERSONAL_TOKEN": tricky})

    assert tomllib.loads(result)["profiles"]["work"]["JIRA_PERSONAL_TOKEN"] == tricky


def test_ensure_default_profile_inserts_before_first_table() -> None:
    text = "# top comment\n\n[profiles.x]\nK = \"v\"\n"

    result = ensure_default_profile(text, "x")

    data = tomllib.loads(result)
    assert data["default_profile"] == "x"
    assert result.index("# top comment") < result.index('default_profile = "x"')
    assert result.index('default_profile = "x"') < result.index("[profiles.x]")


def test_ensure_default_profile_preserves_existing() -> None:
    text = 'default_profile = "dc"\n\n[profiles.dc]\nK = "v"\n'

    assert ensure_default_profile(text, "work") == text


def test_ensure_default_profile_empty_text() -> None:
    result = ensure_default_profile("", "work")

    assert tomllib.loads(result)["default_profile"] == "work"


def test_write_config_atomic_and_private(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "config.toml"

    write_config(target, '[profiles.work]\nK = "v"\n')

    assert target.read_text(encoding="utf-8") == '[profiles.work]\nK = "v"\n'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert sorted(p.name for p in target.parent.iterdir()) == ["config.toml"]


def test_config_target_path_matrix(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    assert config_target_path("project", environ={}, home=home, cwd=cwd) == cwd / ".atli.toml"
    assert (
        config_target_path("global", environ={}, home=home, cwd=cwd)
        == home / ".config" / "atli" / "config.toml"
    )

    explicit = tmp_path / "explicit.toml"
    explicit.write_text("", encoding="utf-8")
    assert (
        config_target_path("global", environ={"ATLI_CONFIG": str(explicit)}, home=home, cwd=cwd)
        == explicit
    )

    with pytest.raises(ConfigError, match="ATLI_CONFIG"):
        config_target_path(
            "global", environ={"ATLI_CONFIG": str(tmp_path / "missing.toml")}, home=home, cwd=cwd
        )
    with pytest.raises(ConfigError, match="scope"):
        config_target_path("bogus", environ={}, home=home, cwd=cwd)
