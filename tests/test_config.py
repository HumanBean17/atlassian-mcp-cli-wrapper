"""Tests for profile config discovery, env application, and argv pre-parse."""

import tomllib

import pytest

from conftest import isolate_home

from mcp_atlassian_cli.config import (
    ConfigError,
    apply_profile,
    describe_profiles,
    extract_profile_flag,
    find_config_file,
    load_config,
    resolve_profile_name,
    upsert_profile,
    validate_credentials,
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
    isolate_home(monkeypatch, home_config)
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


def test_apply_profile_replaces_bitbucket_prefix():
    """A BITBUCKET_-defining profile gets the same stale-credential isolation
    as jira/confluence: ambient BITBUCKET_* is dropped wholesale and the
    cross-service OAuth keys are cleared with it."""
    environ = {
        "BITBUCKET_URL": "https://ambient.bitbucket.org",
        "BITBUCKET_USERNAME": "ambient-user",
        "BITBUCKET_APP_PASSWORD": "ambient-password",
        "ATLASSIAN_OAUTH_ACCESS_TOKEN": "ambient-oauth-token",
        "PATH": "/usr/bin",
    }

    apply_profile(
        {
            "BITBUCKET_URL": "https://bitbucket.internal",
            "BITBUCKET_PERSONAL_TOKEN": "server-pat",
        },
        environ,
    )

    assert environ["BITBUCKET_URL"] == "https://bitbucket.internal"
    assert environ["BITBUCKET_PERSONAL_TOKEN"] == "server-pat"
    assert "BITBUCKET_USERNAME" not in environ
    assert "BITBUCKET_APP_PASSWORD" not in environ
    assert "ATLASSIAN_OAUTH_ACCESS_TOKEN" not in environ
    assert environ["PATH"] == "/usr/bin"


def test_apply_profile_leaves_bitbucket_untouched_when_unmentioned():
    """A jira-only profile must not disturb ambient BITBUCKET_* values."""
    environ = {
        "JIRA_URL": "https://ambient.atlassian.net",
        "BITBUCKET_URL": "https://bitbucket.internal",
        "BITBUCKET_PERSONAL_TOKEN": "server-pat",
    }

    apply_profile({"JIRA_URL": "https://corp.atlassian.net"}, environ)

    assert environ["BITBUCKET_URL"] == "https://bitbucket.internal"
    assert environ["BITBUCKET_PERSONAL_TOKEN"] == "server-pat"


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
    # The empty space form must fail exactly like `--profile=`, not silently
    # fall through to the default profile's instance.
    with pytest.raises(ConfigError):
        extract_profile_flag(["--profile", "", "jira", "get-issue"])
    assert extract_profile_flag(["--profile", "a", "--profile", "b", "jira"]) == (
        "b",
        ["jira"],
    )


def test_resolve_empty_flag_name_is_error(tmp_path):
    """Belt and braces: a non-None empty flag must not fall back to
    $ATLI_PROFILE / default_profile via `flag or ...` truthiness."""
    config = load_config_from_text(tmp_path, CORP_TOML)
    with pytest.raises(ConfigError):
        resolve_profile_name("", config, {"ATLI_PROFILE": "corp"})


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


def test_describe_profiles_shows_bitbucket_url(tmp_path):
    toml = """
[profiles.bb]
BITBUCKET_URL = "https://bitbucket.internal"
BITBUCKET_PERSONAL_TOKEN = "server-pat"
"""
    config = load_config_from_text(tmp_path, toml)

    output = describe_profiles(config, "bb")

    assert "bitbucket: https://bitbucket.internal" in output
    assert "server-pat" not in output


def test_validate_credentials_accepts_printable_ascii():
    validate_credentials(
        {
            "JIRA_USERNAME": "you@example.com",
            "JIRA_API_TOKEN": "AbC123+/=",
            "CONFLUENCE_PERSONAL_TOKEN": "NzQyOTQ2OTQ",
            "ATLASSIAN_OAUTH_CLIENT_SECRET": "s3cret",
            "BITBUCKET_API_TOKEN": "fork-token",
        }
    )


def test_validate_credentials_accepts_latin1_range_text():
    # Accented characters encode fine in HTTP headers (requests uses
    # latin-1 for auth values) — an accented username must keep working;
    # rejecting it would block valid Server/DC setups.
    validate_credentials(
        {
            "JIRA_USERNAME": "björn",
            "CONFLUENCE_PERSONAL_TOKEN": "café",
        }
    )


@pytest.mark.parametrize(
    "variable",
    [
        "JIRA_USERNAME",
        "JIRA_PERSONAL_TOKEN",
        "JIRA_API_TOKEN",
        "CONFLUENCE_USERNAME",
        "CONFLUENCE_PERSONAL_TOKEN",
        "CONFLUENCE_API_TOKEN",
        "BITBUCKET_USERNAME",
        "BITBUCKET_PERSONAL_TOKEN",
        "BITBUCKET_API_TOKEN",
        "BITBUCKET_APP_PASSWORD",
        "ATLASSIAN_OAUTH_ACCESS_TOKEN",
        "ATLASSIAN_OAUTH_CLIENT_SECRET",
        "ATLASSIAN_OAUTH_CLIENT_ID",
    ],
)
def test_validate_credentials_rejects_beyond_latin1(variable):
    # Every credential-shaped variable the providers actually read, pinning
    # the suffix list end-to-end: a Cyrillic value must never slip through
    # to the HTTP layer on any of them.
    with pytest.raises(ConfigError, match=variable):
        validate_credentials({variable: "пароль"})


def test_validate_credentials_rejects_non_ascii_token():
    # "пароль" typed in a Cyrillic layout — every char breaks latin-1 headers.
    with pytest.raises(ConfigError) as excinfo:
        validate_credentials({"CONFLUENCE_PERSONAL_TOKEN": "пароль"})

    message = str(excinfo.value)
    assert "CONFLUENCE_PERSONAL_TOKEN" in message
    assert "U+043F" in message  # 'п'
    assert "latin-1" in message
    assert "repr" in message  # the inspect-the-value advice
    # The secret itself must not be echoed back in full.
    assert "пароль" not in message


def test_validate_credentials_rejects_control_characters():
    with pytest.raises(ConfigError, match="cannot carry"):
        validate_credentials({"JIRA_API_TOKEN": "abc\x00def"})


def test_validate_credentials_ignores_non_credential_variables():
    # URLs and flags are percent-encoded downstream, never header-bound:
    # non-ASCII there is legitimate and none of these may raise. The mTLS
    # key passphrase is file-bound, not header-bound, so it stays unchecked.
    validate_credentials(
        {
            "JIRA_URL": "https://юза.example.com",
            "MCP_ATLASSIAN_USE_SYSTEM_TRUSTSTORE": "да",
            "ATLASSIAN_OAUTH_ENABLE": "вкл",
            "ATLI_PROFILE": "профиль",
            "SOME_API_TOKEN": "not-a-service-prefix",
            "JIRA_CLIENT_KEY_PASSWORD": "пароль-ключа",
        }
    )


def test_validate_credentials_offending_character_is_first_bad_one():
    with pytest.raises(ConfigError) as excinfo:
        validate_credentials({"JIRA_USERNAME": "abc\u2019де"})  # curly apostrophe

    message = str(excinfo.value)
    assert "U+2019" in message
    assert "at position 3" in message


def load_config_from_text(tmp_path, text: str):
    file = tmp_path / "config.toml"
    file.write_text(text)
    return load_config(file)


# --- upsert_profile: the tomlkit-backed write half of the config format ----


def test_upsert_appends_new_profile_preserving_everything():
    text = (
        "# my config\n"
        'default_profile = "other"\n'
        "\n"
        "[profiles.other]\n"
        'JIRA_URL = "https://old.example.com"  # inline comment\n'
        'TOOLSETS = "all"\n'
    )

    result = upsert_profile(
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
    assert data["default_profile"] == "other"  # existing default never changed


def test_upsert_updates_existing_key_in_place_keeping_comments():
    text = (
        "[profiles.work]\n"
        'JIRA_URL = "https://old.example.com"  # keep me\n'
        'TOOLSETS = "all"\n'
        "\n"
        "# note about prod\n"
        "[profiles.prod]\n"
        'JIRA_URL = "https://prod.example.com"\n'
    )

    result = upsert_profile(text, "work", {"JIRA_URL": "https://new.example.com"})

    assert "# keep me" in result  # the replaced line keeps its comment
    assert "# note about prod" in result
    data = tomllib.loads(result)
    assert data["profiles"]["work"] == {
        "JIRA_URL": "https://new.example.com",
        "TOOLSETS": "all",
    }
    assert data["profiles"]["prod"]["JIRA_URL"] == "https://prod.example.com"


def test_upsert_adds_missing_keys_to_existing_section():
    text = '[profiles.work]\nJIRA_URL = "https://x.example.com"\n\n[profiles.dc]\nK = "v"\n'

    result = upsert_profile(
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
    assert data["profiles"]["dc"] == {"K": "v"}


def test_upsert_drops_superseded_keys():
    """Re-running the wizard with different answers must not leave the old
    answers behind (a stale SSL_VERIFY=false would downgrade TLS; old
    Cloud credentials would ride next to a personal token)."""
    seeded = (
        "[profiles.work]\n"
        'JIRA_URL = "https://x.example.com"\n'
        'JIRA_USERNAME = "you@x.com"\n'
        'JIRA_API_TOKEN = "old"\n'
        'JIRA_SSL_VERIFY = "false"\n'
    )

    result = upsert_profile(
        seeded,
        "work",
        {"JIRA_URL": "https://x.example.com", "JIRA_PERSONAL_TOKEN": "pat"},
        drop={"JIRA_USERNAME", "JIRA_API_TOKEN", "JIRA_SSL_VERIFY"},
    )

    profile = tomllib.loads(result)["profiles"]["work"]
    assert profile == {
        "JIRA_URL": "https://x.example.com",
        "JIRA_PERSONAL_TOKEN": "pat",
    }


def test_upsert_into_empty_text():
    result = upsert_profile("", "work", {"JIRA_URL": "https://x.example.com"})

    data = tomllib.loads(result)
    assert data["profiles"]["work"]["JIRA_URL"] == "https://x.example.com"
    assert data["default_profile"] == "work"
    assert result.endswith("\n")


def test_upsert_escapes_values():
    tricky = 'quo"te\\back\nline'

    result = upsert_profile("", "work", {"JIRA_PERSONAL_TOKEN": tricky})

    assert tomllib.loads(result)["profiles"]["work"]["JIRA_PERSONAL_TOKEN"] == tricky


def test_upsert_handles_exotic_profile_names_and_multiline_strings():
    """The cases the old line-surgery refused (quoted keys, multi-line
    string values) are ordinary edits for tomlkit."""
    text = (
        '[profiles."weird name"]\n'
        'K = """\n'
        "multi\n"
        'line\n"""\n'
    )

    result = upsert_profile(text, "weird name", {"JIRA_URL": "https://x"})

    data = tomllib.loads(result)
    assert data["profiles"]["weird name"]["JIRA_URL"] == "https://x"
    assert data["profiles"]["weird name"]["K"] == "multi\nline\n"


def test_upsert_default_profile_insertion():
    # absent -> set, before the first table so it stays top-level
    result = upsert_profile("# top comment\n\n[profiles.x]\nK = \"v\"\n", "x", {"K": "v"})
    data = tomllib.loads(result)
    assert data["default_profile"] == "x"
    assert result.index("# top comment") < result.index('default_profile = "x"')
    assert result.index('default_profile = "x"') < result.index("[profiles.x]")
    # present with a different value -> never changed
    text = 'default_profile = "dc"\n\n[profiles.dc]\nK = "v"\n'
    assert upsert_profile(text, "work", {"K": "v"}) == text.replace(
        '[profiles.dc]\nK = "v"', '[profiles.dc]\nK = "v"\n\n[profiles.work]\nK = "v"'
    )
    # set_default=False leaves a missing default alone
    result = upsert_profile("[profiles.x]\nK = \"v\"\n", "x", {"K": "v"}, set_default=False)
    assert "default_profile" not in tomllib.loads(result)


def test_upsert_refuses_unparseable_input():
    with pytest.raises(ConfigError, match="Could not parse"):
        upsert_profile("this is [ not toml", "work", {"K": "v"})


def test_upsert_refuses_non_table_profiles():
    with pytest.raises(ConfigError, match="profiles"):
        upsert_profile('profiles = "oops"\n', "work", {"K": "v"})
    with pytest.raises(ConfigError, match="profile 'work' must be a"):
        upsert_profile("[profiles]\nwork = \"oops\"\n", "work", {"K": "v"})


def test_upsert_guard_refuses_non_roundtripping_output(monkeypatch):
    """If tomlkit ever emitted text that tomllib cannot parse (or that
    dropped the requested values), upsert must refuse, not return it."""
    import mcp_atlassian_cli.config as config_mod

    real_dumps = config_mod.tomlkit.dumps  # before any patching

    monkeypatch.setattr(config_mod.tomlkit, "dumps", lambda doc: "garbage = [")
    with pytest.raises(ConfigError, match="Refusing to write"):
        upsert_profile("", "work", {"K": "v"})

    def losing_dumps(doc):
        return real_dumps(doc).replace('K = "v"', 'K = "changed"')

    monkeypatch.setattr(config_mod.tomlkit, "dumps", losing_dumps)
    with pytest.raises(ConfigError, match="does not carry exactly"):
        upsert_profile("", "work", {"K": "v"})
