"""Tests for the interactive onboarding wizard (``atli init <service>``)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from mcp_atlassian_cli.init import (
    SERVICES,
    mask,
    toml_basic_string,
    validate_profile_name,
    validate_url,
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
