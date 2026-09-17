"""Tests for the interactive onboarding wizard (``atli init <service>``)."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from mcp_atlassian_cli.config import ConfigError
from mcp_atlassian_cli.install import HOOK_COMMAND
from mcp_atlassian_cli import init
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


class ScriptedPrompt:
    """Prompt double: pops scripted answers in order, records prompts.

    Entries are ``(kind, answer)`` with kind ``"ask"``/``"secret"``; an
    empty answer string stands for "user pressed Enter" — default
    interpretation lives in the wizard functions, not the prompt.
    """

    def __init__(self, script: list[tuple[str, str]]) -> None:
        self.script = list(script)
        self.prompts: list[str] = []

    def _pop(self, kind: str) -> str:
        if not self.script:
            raise AssertionError(f"script exhausted; unexpected {kind} prompt")
        expected, answer = self.script.pop(0)
        assert expected == kind, f"expected {expected} prompt, got {kind}"
        return answer

    def ask(self, prompt: str, *, default: str | None = None) -> str:
        self.prompts.append(prompt)
        return self._pop("ask")

    def ask_secret(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._pop("secret")


class Recorder:
    """Collects wizard out() lines for assertions."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)


def test_collect_profile_cloud_happy() -> None:
    prompt = ScriptedPrompt(
        [
            ("ask", "https://work.atlassian.net"),
            ("ask", "1"),
            ("ask", "you@work.com"),
            ("secret", "tok-123"),
            ("ask", ""),
        ]
    )

    values = init.collect_profile("jira", prompt, out=Recorder())

    assert values == {
        "JIRA_URL": "https://work.atlassian.net",
        "JIRA_USERNAME": "you@work.com",
        "JIRA_API_TOKEN": "tok-123",
    }


def test_collect_profile_dc_and_ssl_off() -> None:
    prompt = ScriptedPrompt(
        [
            ("ask", "https://confluence.internal/wiki"),
            ("ask", "2"),
            ("secret", "pat-token"),
            ("ask", "n"),
        ]
    )

    values = init.collect_profile("confluence", prompt, out=Recorder())

    assert values == {
        "CONFLUENCE_URL": "https://confluence.internal/wiki",
        "CONFLUENCE_PERSONAL_TOKEN": "pat-token",
        "CONFLUENCE_SSL_VERIFY": "false",
    }


def test_collect_profile_url_default_bitbucket() -> None:
    prompt = ScriptedPrompt(
        [
            ("ask", ""),  # Enter accepts https://bitbucket.org
            ("ask", "1"),
            ("ask", "you"),
            ("secret", "bb-token"),
            ("ask", "y"),
        ]
    )

    values = init.collect_profile("bitbucket", prompt, out=Recorder())

    assert values["BITBUCKET_URL"] == "https://bitbucket.org"
    assert values["BITBUCKET_USERNAME"] == "you"
    assert values["BITBUCKET_API_TOKEN"] == "bb-token"


def test_collect_profile_reprompts() -> None:
    out = Recorder()
    prompt = ScriptedPrompt(
        [
            ("ask", "not-a-url"),
            ("ask", "https://jira.example.com"),
            ("ask", "1"),
            ("ask", "you@work.com"),
            ("secret", ""),  # empty secret re-prompts
            ("secret", "токен"),  # non-latin-1 re-prompts
            ("secret", "tok"),
            ("ask", ""),
        ]
    )

    values = init.collect_profile("jira", prompt, out=out)

    assert values["JIRA_API_TOKEN"] == "tok"
    joined = "\n".join(out.lines)
    assert "http" in joined  # URL validation error surfaced
    assert "latin-1" in joined  # credential validation error surfaced


def test_choose_scope() -> None:
    assert init.choose_scope(ScriptedPrompt([("ask", "")])) == "global"
    assert init.choose_scope(ScriptedPrompt([("ask", "2")])) == "project"


def test_choose_profile_name() -> None:
    assert init.choose_profile_name("jira", ScriptedPrompt([("ask", "")])) == "jira"
    assert (
        init.choose_profile_name("jira", ScriptedPrompt([("ask", "a.b"), ("ask", "work")]))
        == "work"
    )


def test_choose_harness(tmp_path: Path) -> None:
    empty_home = tmp_path / "empty"
    empty_home.mkdir()
    assert init.choose_harness(ScriptedPrompt([("ask", "")]), empty_home) == "claude"
    assert init.choose_harness(ScriptedPrompt([("ask", "s")]), empty_home) is None
    assert init.choose_harness(ScriptedPrompt([("ask", "9"), ("ask", "1")]), empty_home) == "claude"

    qwen_home = tmp_path / "qwen"
    (qwen_home / ".qwen").mkdir(parents=True)
    assert init.choose_harness(ScriptedPrompt([("ask", "")]), qwen_home) == "qwen"


def test_choose_service() -> None:
    assert init.choose_service(ScriptedPrompt([("ask", "2")])) == "confluence"
    assert init.choose_service(ScriptedPrompt([("ask", "7"), ("ask", "3")])) == "bitbucket"


def test_render_summary_masks_credentials() -> None:
    summary = init.render_summary(
        "jira",
        {
            "JIRA_URL": "https://work.atlassian.net",
            "JIRA_USERNAME": "you@work.com",
            "JIRA_API_TOKEN": "tok",
        },
        config_path=Path("/tmp/x/.atli.toml"),
        profile_name="jira",
        harness=None,
        scope="project",
    )

    assert "https://work.atlassian.net" in summary
    assert "JIRA_USERNAME: ****" in summary
    assert "JIRA_API_TOKEN: ****" in summary
    assert "tok" not in summary
    assert "hook: skipped" in summary
    assert "/tmp/x/.atli.toml" in summary
    assert "jira" in summary
    assert "project" in summary


class StubRunner:
    """Records call_tool invocations; optionally raises on every call."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._error = error

    def call_tool(self, name: str, args: dict[str, object]) -> str:
        self.calls.append((name, dict(args)))
        if self._error is not None:
            raise self._error
        return "ok"


def test_verify_profile_applies_and_calls() -> None:
    environ = {
        "JIRA_API_TOKEN": "stale-token",
        "CONFLUENCE_URL": "https://ambient.example.com/wiki",
    }
    values = {
        "JIRA_URL": "https://work.atlassian.net",
        "JIRA_USERNAME": "you@work.com",
        "JIRA_API_TOKEN": "fresh-token",
    }
    stub = StubRunner()

    init.verify_profile("jira", values, environ, runner_factory=lambda: stub)

    assert stub.calls == [("search", {"jql": "ORDER BY created DESC", "limit": 1})]
    assert environ["JIRA_API_TOKEN"] == "fresh-token"  # stale credential replaced
    assert environ["CONFLUENCE_URL"] == "https://ambient.example.com/wiki"  # untouched prefix
    assert environ["JIRA_URL"] == "https://work.atlassian.net"


def test_verify_profile_failure_propagates() -> None:
    from mcp_atlassian_cli.runner import ToolCallFailure

    stub = StubRunner(error=ToolCallFailure("401 Unauthorized"))

    with pytest.raises(ToolCallFailure, match="401"):
        init.verify_profile(
            "jira",
            {"JIRA_URL": "https://x", "JIRA_PERSONAL_TOKEN": "t"},
            {},
            runner_factory=lambda: stub,
        )


def test_verify_profile_bitbucket_args() -> None:
    stub = StubRunner()

    init.verify_profile(
        "bitbucket",
        {"BITBUCKET_URL": "https://bitbucket.org", "BITBUCKET_USERNAME": "u", "BITBUCKET_API_TOKEN": "t"},
        {},
        runner_factory=lambda: stub,
    )

    assert stub.calls == [("list_repositories", {"max_results": 1})]


def test_init_module_has_no_server_import_at_import_time() -> None:
    """Importing the wizard module must never pull mcp_atlassian."""
    code = (
        "import mcp_atlassian_cli.init, sys; "
        "sys.exit(0 if 'mcp_atlassian' not in sys.modules else 1)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert result.returncode == 0, result.stderr


JIRA_CLOUD_SCRIPT = [
    ("ask", "https://work.atlassian.net"),
    ("ask", "1"),
    ("ask", "you@work.com"),
    ("secret", "tok-123"),
    ("ask", ""),  # TLS: verify (default)
    ("ask", ""),  # scope: global (default)
    ("ask", ""),  # profile name: jira (default)
    ("ask", "1"),  # harness: claude
    ("ask", ""),  # proceed (default y)
]


def run_jira(script: list[tuple[str, str]], **kwargs: object) -> tuple[int, Recorder]:
    out = Recorder()
    code = init.run_init(
        "jira",
        prompt=ScriptedPrompt(script),
        home=kwargs.pop("home"),
        cwd=kwargs.pop("cwd"),
        environ=kwargs.pop("environ", {}),
        runner_factory=kwargs.pop("runner_factory", lambda: StubRunner()),
        out=out,
    )
    assert not kwargs, f"unused kwargs: {kwargs}"
    return code, out


def test_run_init_happy_path_writes_and_reports(tmp_path: Path) -> None:
    home = tmp_path / "home"

    code, out = run_jira(
        list(JIRA_CLOUD_SCRIPT),
        home=home,
        cwd=tmp_path,
        runner_factory=lambda: StubRunner(),
    )

    assert code == 0
    config = home / ".config" / "atli" / "config.toml"
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert data["default_profile"] == "jira"
    assert data["profiles"]["jira"]["JIRA_API_TOKEN"] == "tok-123"
    settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert HOOK_COMMAND in settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    joined = "\n".join(out.lines)
    assert str(config) in joined
    assert "atli jira search" in joined


def test_run_init_merge_preserves_existing_profile_and_default(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = home / ".config" / "atli" / "config.toml"
    config.parent.mkdir(parents=True)
    seeded = (
        "# hand-written\n"
        'default_profile = "dc"\n'
        "\n"
        "[profiles.dc]\n"
        'JIRA_URL = "https://dc.internal"\n'
    )
    config.write_text(seeded, encoding="utf-8")

    code, _ = run_jira(list(JIRA_CLOUD_SCRIPT), home=home, cwd=tmp_path)

    assert code == 0
    result = config.read_text(encoding="utf-8")
    assert "# hand-written" in result
    assert 'default_profile = "dc"' in result
    assert 'JIRA_URL = "https://dc.internal"' in result
    data = tomllib.loads(result)
    assert data["profiles"]["dc"]["JIRA_URL"] == "https://dc.internal"
    assert data["profiles"]["jira"]["JIRA_URL"] == "https://work.atlassian.net"


def test_run_init_project_scope_and_skip_hook(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    script = list(JIRA_CLOUD_SCRIPT)
    script[5] = ("ask", "2")  # scope: project
    script[7] = ("ask", "s")  # harness: skip

    code, out = run_jira(script, home=home, cwd=project)

    assert code == 0
    assert (project / ".atli.toml").is_file()
    assert not (home / ".claude").exists()
    assert not (project / ".claude").exists()
    joined = "\n".join(out.lines)
    assert ".gitignore" in joined
    assert "hook: skipped" in joined


def test_run_init_decline_at_confirm(tmp_path: Path) -> None:
    home = tmp_path / "home"
    script = list(JIRA_CLOUD_SCRIPT)
    script[8] = ("ask", "n")

    code, out = run_jira(script, home=home, cwd=tmp_path)

    assert code == 0
    assert out.lines[-1] == "Nothing written."
    assert not (home / ".config").exists()


def test_run_init_verification_failure_abort(tmp_path: Path) -> None:
    from mcp_atlassian_cli.runner import ToolCallFailure

    home = tmp_path / "home"
    script = list(JIRA_CLOUD_SCRIPT) + [("ask", "3")]  # menu: abort

    code, out = run_jira(
        script,
        home=home,
        cwd=tmp_path,
        runner_factory=lambda: StubRunner(error=ToolCallFailure("401 Unauthorized")),
    )

    assert code == 1
    assert not (home / ".config").exists()
    assert any("401" in line for line in out.lines)


def test_run_init_verification_retry_then_success(tmp_path: Path) -> None:
    from mcp_atlassian_cli.runner import ToolCallFailure

    home = tmp_path / "home"

    class FlakyFactory:
        def __init__(self) -> None:
            self.runners: list[StubRunner] = [
                StubRunner(error=ToolCallFailure("401 Unauthorized")),
                StubRunner(),
            ]

        def __call__(self) -> StubRunner:
            return self.runners.pop(0)

    script = list(JIRA_CLOUD_SCRIPT) + [
        ("ask", "1"),  # menu: re-enter
        # second wizard pass with the corrected token
        ("ask", "https://work.atlassian.net"),
        ("ask", "1"),
        ("ask", "you@work.com"),
        ("secret", "tok-fixed"),
        ("ask", ""),
        ("ask", ""),
        ("ask", ""),
        ("ask", "1"),
        ("ask", ""),
    ]
    factory = FlakyFactory()

    code, _ = run_jira(script, home=home, cwd=tmp_path, runner_factory=factory)

    assert code == 0
    data = tomllib.loads(
        (home / ".config" / "atli" / "config.toml").read_text(encoding="utf-8")
    )
    assert data["profiles"]["jira"]["JIRA_API_TOKEN"] == "tok-fixed"


def test_run_init_bitbucket_provider_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(init.providers, "detect_provider", lambda: "atlassian")
    monkeypatch.setattr(init.providers, "bitbucket_hint", lambda p: "install the fork provider")
    home = tmp_path / "home"
    out = Recorder()

    code = init.run_init(
        "bitbucket",
        prompt=ScriptedPrompt([]),  # zero prompts: gate fires first
        home=home,
        cwd=tmp_path,
        environ={},
        runner_factory=lambda: StubRunner(),
        out=out,
    )

    assert code == 2
    assert out.lines == ["install the fork provider"]
    assert not (home / ".config").exists()


def test_run_init_atli_config_respected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("", encoding="utf-8")

    code, _ = run_jira(
        list(JIRA_CLOUD_SCRIPT), home=home, cwd=tmp_path, environ={"ATLI_CONFIG": str(explicit)}
    )

    assert code == 0
    data = tomllib.loads(explicit.read_text(encoding="utf-8"))
    assert data["profiles"]["jira"]["JIRA_URL"] == "https://work.atlassian.net"
    assert not (home / ".config").exists()


def test_run_init_install_configerror_returns_2(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"broken":', encoding="utf-8")

    code, out = run_jira(list(JIRA_CLOUD_SCRIPT), home=home, cwd=tmp_path)

    assert code == 2
    config = home / ".config" / "atli" / "config.toml"
    assert config.is_file()  # config write happened before the hook failure
    assert any("not valid JSON" in line for line in out.lines)
