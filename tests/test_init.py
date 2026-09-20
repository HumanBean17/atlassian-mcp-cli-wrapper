"""Tests for the interactive onboarding wizard (``atli init <service>``).

The wizard's logic is tested through the :class:`mcp_atlassian_cli.init.Prompt`
protocol with a scripted double — no terminal involved. The InquirerPy
adapter itself is covered by pty-driven smoke tests at the bottom (POSIX
only), which drive real keypresses through the same ``run_init`` flow.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from mcp_atlassian_cli.config import ConfigError
from mcp_atlassian_cli.install import HOOK_COMMAND
from mcp_atlassian_cli import init
from mcp_atlassian_cli.init import (
    SERVICES,
    config_target_path,
    detect_auth_method,
    validate_profile_name,
    validate_url,
    write_config,
)


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
    for bad in ("a.b", "[x]", 'a"b', "a#b", " work", "work ", "", "a\nb", "my work", "работа"):
        assert validate_profile_name(bad) is not None


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
        "jira_search",
        {"jql": "ORDER BY created DESC", "limit": 1},
    )
    # The real server registers tools flat-prefixed (conftest stub mounts
    # them the same way); a bare name here fails on every live verify.
    for name, svc in SERVICES.items():
        assert svc.verify_tool.startswith(f"{name}_"), svc.verify_tool

    confluence = SERVICES["confluence"]
    assert confluence.url_hint and "wiki" in confluence.url_hint
    assert confluence.verify_tool == "confluence_search"
    assert confluence.verify_args == {"query": 'type = "page"', "limit": 1}

    bitbucket = SERVICES["bitbucket"]
    assert bitbucket.url_default == "https://bitbucket.org"
    assert bitbucket.verify_tool == "bitbucket_list_repositories"
    assert bitbucket.verify_args == {"max_results": 1}
    cloud_bb = bitbucket.auth_methods[0]
    assert [v.env_name for v in cloud_bb.variables] == [
        "BITBUCKET_USERNAME",
        "BITBUCKET_API_TOKEN",
    ]


def test_detect_auth_method() -> None:
    spec = SERVICES["jira"]
    assert detect_auth_method(spec, {}) == 0  # nothing known: Cloud
    assert (
        detect_auth_method(
            spec, {"JIRA_USERNAME": "a@b.c", "JIRA_API_TOKEN": "t"}
        )
        == 0
    )
    assert detect_auth_method(spec, {"JIRA_PERSONAL_TOKEN": "pat"}) == 1
    # messy half-state (cloud username only): Cloud is the preselection
    assert detect_auth_method(spec, {"JIRA_USERNAME": "a@b.c"}) == 0


def test_credential_validator_required_and_latin1() -> None:
    from mcp_atlassian_cli.init import _credential_validator

    check = _credential_validator("JIRA_API_TOKEN")
    assert check("") == "Value is required."
    assert check("plain-token") is None
    reason = check("токен")
    assert reason is not None and "latin-1" in reason


def test_write_config_atomic_and_private(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "config.toml"

    write_config(target, '[profiles.work]\nK = "v"\n')

    assert target.read_text(encoding="utf-8") == '[profiles.work]\nK = "v"\n'
    if os.name == "posix":  # Windows has no POSIX mode bits (ACLs govern)
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
    """Prompt double over the new Prompt protocol: pops scripted answers in
    order, records every call (message + default) for prefill assertions.

    Entries are ``(kind, answer)`` — ("select", value), ("text", str),
    ("secret", str), ("confirm", bool). Validators run against the scripted
    answer and must pass: a user cannot submit an invalid answer, so an
    invalid script entry is a test bug.
    """

    def __init__(self, script: list[tuple[str, Any]]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, str, Any]] = []

    def _pop(self, kind: str, message: str, default: Any, answer: Any, validate: Any = None) -> Any:
        if not self.script:
            raise AssertionError(f"script exhausted; unexpected {kind} prompt: {message!r}")
        expected, scripted = self.script.pop(0)
        assert expected == kind, f"expected {expected} prompt, got {kind}: {message!r}"
        if validate is not None:
            reason = validate(scripted)
            assert reason is None, f"scripted answer {scripted!r} rejected: {reason}"
        self.calls.append((kind, message, default))
        return scripted

    def select(
        self, message: str, choices: Any, *, default: str | None = None, instruction: str | None = None
    ) -> str:
        answer = self._pop("select", message, default, None)
        values = [value for value, _ in choices]
        assert answer in values, f"scripted select answer {answer!r} not in {values}"
        return answer

    def text(
        self, message: str, *, default: str | None = None, validate: Any = None
    ) -> str:
        return self._pop("text", message, default, None, validate=validate)

    def secret(self, message: str, *, validate: Any = None) -> str:
        return self._pop("secret", message, None, None, validate=validate)

    def confirm(self, message: str, *, default: bool = True) -> bool:
        answer = self._pop("confirm", message, default, None)
        assert isinstance(answer, bool), f"scripted confirm answer must be a bool, got {answer!r}"
        return answer


def text_call(prompt: ScriptedPrompt, needle: str) -> tuple[str, str, Any]:
    """The first recorded text call whose message contains ``needle``."""
    return next(call for call in prompt.calls if call[0] == "text" and needle in call[1])


class Recorder:
    """Collects wizard out() lines for assertions."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)


def test_collect_profile_cloud_happy() -> None:
    prompt = ScriptedPrompt(
        [
            ("text", "https://work.atlassian.net"),
            ("select", "0"),
            ("text", "you@work.com"),
            ("secret", "tok-123"),
            ("confirm", True),
        ]
    )

    values = init.collect_profile("jira", prompt)

    assert values == {
        "JIRA_URL": "https://work.atlassian.net",
        "JIRA_USERNAME": "you@work.com",
        "JIRA_API_TOKEN": "tok-123",
    }


def test_collect_profile_dc_and_ssl_off() -> None:
    prompt = ScriptedPrompt(
        [
            ("text", "https://confluence.internal/wiki"),
            ("select", "1"),
            ("secret", "pat-token"),
            ("confirm", False),
        ]
    )

    values = init.collect_profile("confluence", prompt)

    assert values == {
        "CONFLUENCE_URL": "https://confluence.internal/wiki",
        "CONFLUENCE_PERSONAL_TOKEN": "pat-token",
        "CONFLUENCE_SSL_VERIFY": "false",
    }


def test_collect_profile_url_default_bitbucket() -> None:
    prompt = ScriptedPrompt(
        [
            ("text", "https://bitbucket.org"),  # Enter accepts the default
            ("select", "0"),
            ("text", "you"),
            ("secret", "bb-token"),
            ("confirm", True),
        ]
    )

    values = init.collect_profile("bitbucket", prompt)

    assert values["BITBUCKET_URL"] == "https://bitbucket.org"
    _, _, offered_default = text_call(prompt, "Bitbucket URL")
    assert offered_default == "https://bitbucket.org"


def test_collect_profile_prefills_from_current() -> None:
    """Re-running init edits: URL/auth/username prefill, an empty secret
    keeps the current one, and a stored verify=false defaults TLS to No."""
    current = {
        "JIRA_URL": "https://old.atlassian.net",
        "JIRA_USERNAME": "old@work.com",
        "JIRA_API_TOKEN": "old-token",
        "JIRA_SSL_VERIFY": "false",
    }
    prompt = ScriptedPrompt(
        [
            ("text", "https://old.atlassian.net"),  # Enter keeps the old URL
            ("select", "0"),  # auth preselected: cloud
            ("text", "old@work.com"),  # Enter keeps the old username
            ("secret", ""),  # Enter keeps the current token
            ("confirm", True),  # TLS default is No (stored false); flip to yes
        ]
    )

    values = init.collect_profile("jira", prompt, current=current)

    assert values["JIRA_API_TOKEN"] == "old-token"  # kept, not replaced
    assert values["JIRA_URL"] == "https://old.atlassian.net"
    assert "JIRA_SSL_VERIFY" not in values  # flipped back to verify
    _, _, url_default = text_call(prompt, "Jira URL")
    assert url_default == "https://old.atlassian.net"
    _, _, user_default = text_call(prompt, "Email (JIRA_USERNAME)")
    assert user_default == "old@work.com"
    secret_calls = [c for c in prompt.calls if c[0] == "secret"]
    assert "keep current" in secret_calls[0][1]
    tls_call = next(c for c in prompt.calls if c[0] == "confirm")
    assert tls_call[2] is False  # default No because the profile stores false


def test_collect_profile_prefills_dc_auth_method() -> None:
    prompt = ScriptedPrompt(
        [
            ("text", "https://dc.internal"),
            ("select", "1"),
            ("secret", ""),
            ("confirm", True),
        ]
    )

    values = init.collect_profile(
        "jira", prompt, current={"JIRA_URL": "https://dc.internal", "JIRA_PERSONAL_TOKEN": "pat"}
    )

    assert values["JIRA_PERSONAL_TOKEN"] == "pat"
    auth_call = next(c for c in prompt.calls if c[0] == "select" and "Auth" in c[1])
    assert auth_call[2] == "1"  # DC preselected from the existing profile


def test_collect_profile_secret_required_without_current() -> None:
    prompt = ScriptedPrompt(
        [
            ("text", "https://work.atlassian.net"),
            ("select", "0"),
            ("text", "you@work.com"),
            ("secret", ""),
            ("confirm", True),
        ]
    )

    with pytest.raises(AssertionError, match="rejected"):
        init.collect_profile("jira", prompt)
    # no "keep current" hint when there is nothing to keep
    assert "keep current" not in prompt.calls[-1][1]


def test_collect_profile_trims_url() -> None:
    prompt = ScriptedPrompt(
        [
            ("text", "  https://work.atlassian.net  "),  # pasted spaces: fine
            ("select", "0"),
            ("text", "you@work.com"),
            ("secret", "tok"),
            ("confirm", True),
        ]
    )

    values = init.collect_profile("jira", prompt)

    assert values["JIRA_URL"] == "https://work.atlassian.net"


def test_collect_profile_rejects_bad_answers_at_the_prompt() -> None:
    with pytest.raises(AssertionError, match="rejected"):
        # invalid URL cannot be submitted — the validator fires
        init.collect_profile(
            "jira",
            ScriptedPrompt(
                [
                    ("text", "not-a-url"),
                    ("select", "0"),
                    ("text", "you@work.com"),
                    ("secret", "tok"),
                    ("confirm", True),
                ]
            ),
        )
    with pytest.raises(AssertionError, match="rejected"):
        # non-latin-1 credentials cannot be submitted
        init.collect_profile(
            "jira",
            ScriptedPrompt(
                [
                    ("text", "https://work.atlassian.net"),
                    ("select", "0"),
                    ("text", "фыва@work.com"),
                    ("secret", "tok"),
                    ("confirm", True),
                ]
            ),
        )


def test_choose_scope() -> None:
    assert init.choose_scope(ScriptedPrompt([("select", "global")])) == "global"
    assert init.choose_scope(ScriptedPrompt([("select", "project")])) == "project"


def test_choose_profile_name() -> None:
    assert init.choose_profile_name("jira", ScriptedPrompt([("text", "jira")])) == "jira"
    assert (
        init.choose_profile_name("jira", ScriptedPrompt([("text", "work")])) == "work"
    )


def test_choose_profile_name_validator_rejects_bad_names() -> None:
    with pytest.raises(AssertionError, match="rejected"):
        init.choose_profile_name("jira", ScriptedPrompt([("text", "a.b")]))


def test_choose_harness(tmp_path: Path) -> None:
    empty_home = tmp_path / "empty"
    empty_home.mkdir()
    assert init.choose_harness(ScriptedPrompt([("select", "claude")]), empty_home) == "claude"
    assert init.choose_harness(ScriptedPrompt([("select", "__skip__")]), empty_home) is None

    qwen_home = tmp_path / "qwen"
    (qwen_home / ".qwen").mkdir(parents=True)
    prompt = ScriptedPrompt([("select", "qwen")])
    assert init.choose_harness(prompt, qwen_home) == "qwen"
    assert prompt.calls[0][2] == "qwen"  # detected harness is the default


def test_choose_harness_multiple_detected_pins_registry_order(tmp_path: Path) -> None:
    home = tmp_path / "home"
    for dir_name in (".qwen", ".claude", ".gigacode"):
        (home / dir_name).mkdir(parents=True)

    prompt = ScriptedPrompt([("select", "claude")])
    assert init.choose_harness(prompt, home) == "claude"
    assert prompt.calls[0][2] == "claude"  # registry order, not alphabetical


def test_choose_service() -> None:
    assert init.choose_service(ScriptedPrompt([("select", "confluence")])) == "confluence"
    assert init.choose_service(ScriptedPrompt([("select", "bitbucket")])) == "bitbucket"


def test_render_summary_shows_usernames_masks_secrets() -> None:
    summary = init.render_summary(
        "jira",
        {
            "JIRA_URL": "https://work.atlassian.net",
            "JIRA_USERNAME": "you@work.com",
            "JIRA_API_TOKEN": "tok",
        },
        config_path=Path("/tmp/x/.atli.toml"),  # native separators
        profile_name="jira",
        harness=None,
        scope="project",
    )

    assert "https://work.atlassian.net" in summary
    # A username typed in plain sight is confirmation material, not a secret
    assert "JIRA_USERNAME: you@work.com" in summary
    assert "JIRA_API_TOKEN: ****" in summary
    assert "tok" not in summary
    assert "Harness: skipped" in summary
    assert str(Path("/tmp/x/.atli.toml")) in summary
    assert "jira" in summary
    assert "project" in summary
    assert "Verified: yes" in summary


def test_render_summary_masks_dc_token() -> None:
    summary = init.render_summary(
        "confluence",
        {"CONFLUENCE_URL": "https://x/wiki", "CONFLUENCE_PERSONAL_TOKEN": "pat"},
        config_path=Path("/tmp/x/c.toml"),
        profile_name="confluence",
        harness="qwen",
        scope="global",
    )
    assert "CONFLUENCE_PERSONAL_TOKEN: ****" in summary
    assert "pat" not in summary
    assert "Harness: qwen" in summary


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

    assert stub.calls == [("jira_search", {"jql": "ORDER BY created DESC", "limit": 1})]
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

    assert stub.calls == [("bitbucket_list_repositories", {"max_results": 1})]


def test_verify_profile_against_prefix_mounted_server(stub_app: Any) -> None:
    """The wizard must call the flat-prefixed tool names the real server
    registers. The conftest stub mounts tools exactly as mcp-atlassian
    does (``jira_search``), and ToolRunner is the real runner — a bare
    ``search`` name fails here exactly as it would in production. A
    name-agnostic StubRunner cannot catch this class of bug."""
    from mcp_atlassian_cli.runner import ToolRunner

    init.verify_profile(
        "jira",
        {
            "JIRA_URL": "https://work.atlassian.net",
            "JIRA_USERNAME": "you@work.com",
            "JIRA_API_TOKEN": "tok",
        },
        {},
        runner_factory=lambda: ToolRunner(app=stub_app),
    )


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


# The full-wizard scripts, in the new flow order: scope -> profile name ->
# URL -> auth -> credentials -> TLS -> [live verify] -> harness -> confirm.


def run_wizard(
    script: list[tuple[str, Any]], **kwargs: object
) -> tuple[int, Recorder, ScriptedPrompt]:
    out = Recorder()
    prompt = ScriptedPrompt(script)
    code = init.run_init(
        str(kwargs.pop("service", "jira")),
        prompt=prompt,
        home=kwargs.pop("home"),
        cwd=kwargs.pop("cwd"),
        environ=kwargs.pop("environ", {}),
        runner_factory=kwargs.pop("runner_factory", lambda: StubRunner()),
        out=out,
    )
    assert not kwargs, f"unused kwargs: {kwargs}"
    assert not prompt.script, f"script not fully consumed: {prompt.script}"
    return code, out, prompt


JIRA_CLOUD_SCRIPT: list[tuple[str, Any]] = [
    ("select", "global"),  # scope
    ("text", "jira"),  # profile name (default)
    ("text", "https://work.atlassian.net"),
    ("select", "0"),  # auth: Cloud
    ("text", "you@work.com"),
    ("secret", "tok-123"),
    ("confirm", True),  # TLS
    ("select", "claude"),  # harness
    ("confirm", True),  # write
]


def test_run_init_happy_path_writes_and_reports(tmp_path: Path) -> None:
    home = tmp_path / "home"

    code, out, _ = run_wizard(list(JIRA_CLOUD_SCRIPT), home=home, cwd=tmp_path)

    assert code == 0
    config = home / ".config" / "atli" / "config.toml"
    if os.name == "posix":
        assert stat.S_IMODE(config.stat().st_mode) == 0o600
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert data["default_profile"] == "jira"
    assert data["profiles"]["jira"]["JIRA_API_TOKEN"] == "tok-123"
    settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert HOOK_COMMAND in settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    joined = "\n".join(out.lines)
    assert str(config) in joined
    assert "atli jira search" in joined
    # verification happens BEFORE the summary: confirm means "write it"
    assert joined.index("Verifying") < joined.index("Service: jira")
    assert "Verified: yes" in joined


def test_run_init_summary_hides_secret_but_not_username(tmp_path: Path) -> None:
    home = tmp_path / "home"

    _, out, _ = run_wizard(list(JIRA_CLOUD_SCRIPT), home=home, cwd=tmp_path)

    summary_block = "\n".join(out.lines)  # the summary is one multi-line out() call
    assert "you@work.com" in summary_block
    assert "tok-123" not in summary_block


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

    code, out, _ = run_wizard(list(JIRA_CLOUD_SCRIPT), home=home, cwd=tmp_path)

    assert code == 0
    result = config.read_text(encoding="utf-8")
    if os.name == "posix":
        assert stat.S_IMODE(config.stat().st_mode) == 0o600  # tightened on overwrite
    assert "# hand-written" in result
    assert 'default_profile = "dc"' in result
    assert 'JIRA_URL = "https://dc.internal"' in result
    data = tomllib.loads(result)
    assert data["profiles"]["dc"]["JIRA_URL"] == "https://dc.internal"
    assert data["profiles"]["jira"]["JIRA_URL"] == "https://work.atlassian.net"
    assert any("default profile: 'dc' (unchanged)" in line for line in out.lines)


def test_run_init_project_scope_and_skip_hook(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    script = [(k, v) for k, v in JIRA_CLOUD_SCRIPT]
    script[0] = ("select", "project")  # scope: project
    script[7] = ("select", "__skip__")  # harness: skip

    code, out, _ = run_wizard(script, home=home, cwd=project)

    assert code == 0
    assert (project / ".atli.toml").is_file()
    assert not (home / ".claude").exists()
    assert not (project / ".claude").exists()
    joined = "\n".join(out.lines)
    assert ".gitignore" in joined
    assert "hook: skipped" in joined


def test_run_init_decline_at_confirm(tmp_path: Path) -> None:
    home = tmp_path / "home"
    script = [(k, v) for k, v in JIRA_CLOUD_SCRIPT]
    script[-1] = ("confirm", False)

    code, out, _ = run_wizard(script, home=home, cwd=tmp_path)

    assert code == 0
    assert out.lines[-1] == "Nothing written."
    assert not (home / ".config").exists()


def test_run_init_verification_failure_abort(tmp_path: Path) -> None:
    from mcp_atlassian_cli.runner import ToolCallFailure

    home = tmp_path / "home"
    script = list(JIRA_CLOUD_SCRIPT[:7]) + [("select", "abort")]  # drop harness/confirm; abort

    code, out, _ = run_wizard(
        script,
        home=home,
        cwd=tmp_path,
        runner_factory=lambda: StubRunner(error=ToolCallFailure("401 Unauthorized")),
    )

    assert code == 1
    assert not (home / ".config").exists()
    assert not (home / ".claude").exists()  # no hook either
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

    script = list(JIRA_CLOUD_SCRIPT[:7]) + [
        ("select", "retry"),  # recovery: re-enter credentials, same URL
        # second collect: URL skipped (kept), auth/cloud, username, token
        ("select", "0"),
        ("text", "you@work.com"),
        ("secret", "tok-fixed"),
        ("confirm", True),
        ("select", "claude"),
        ("confirm", True),
    ]
    factory = FlakyFactory()

    code, out, prompt = run_wizard(script, home=home, cwd=tmp_path, runner_factory=factory)

    assert code == 0
    data = tomllib.loads(
        (home / ".config" / "atli" / "config.toml").read_text(encoding="utf-8")
    )
    assert data["profiles"]["jira"]["JIRA_API_TOKEN"] == "tok-fixed"
    # the retry kept this session's URL without re-asking: the URL prompt
    # ran exactly once (a re-ask would also have desynced the script and
    # failed run_wizard's consumption assert)
    url_prompts = [c for c in prompt.calls if c[0] == "text" and "Jira URL" in c[1]]
    assert len(url_prompts) == 1


def test_run_init_change_url_menu_restarts_at_url(tmp_path: Path) -> None:
    from mcp_atlassian_cli.runner import ToolCallFailure

    home = tmp_path / "home"
    script = list(JIRA_CLOUD_SCRIPT[:7]) + [
        ("select", "url"),  # recovery: change URL
        ("text", "https://fixed.atlassian.net"),  # URL re-asked
        ("select", "0"),
        ("text", "you@work.com"),
        ("secret", "tok"),
        ("confirm", True),
        ("select", "claude"),
        ("confirm", True),
    ]

    class FailOnceFactory:
        def __init__(self) -> None:
            self._failed = False

        def __call__(self) -> StubRunner:
            if self._failed:
                return StubRunner()
            self._failed = True
            return StubRunner(error=ToolCallFailure("401"))

    code, _, _ = run_wizard(
        script,
        home=home,
        cwd=tmp_path,
        runner_factory=FailOnceFactory(),
    )

    assert code == 0
    profile = tomllib.loads(
        (home / ".config" / "atli" / "config.toml").read_text(encoding="utf-8")
    )["profiles"]["jira"]
    assert profile["JIRA_URL"] == "https://fixed.atlassian.net"


def test_run_init_rerun_prefills_from_existing_profile(tmp_path: Path) -> None:
    """Second run on an existing profile: Enter keeps everything, the empty
    secret keeps the stored token, foreign keys survive, nothing changes."""
    home = tmp_path / "home"
    config = home / ".config" / "atli" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "[profiles.jira]\n"
        'JIRA_URL = "https://work.atlassian.net"\n'
        'JIRA_USERNAME = "you@work.com"\n'
        'JIRA_API_TOKEN = "stored-token"\n'
        'TOOLSETS = "all"\n',
        encoding="utf-8",
    )
    script = [
        ("select", "global"),
        ("text", "jira"),
        ("text", "https://work.atlassian.net"),  # Enter keeps (default = stored)
        ("select", "0"),  # auth preselected: cloud
        ("text", "you@work.com"),  # Enter keeps
        ("secret", ""),  # Enter keeps the stored token
        ("confirm", True),
        ("select", "claude"),
        ("confirm", True),
    ]

    code, _, prompt = run_wizard(script, home=home, cwd=tmp_path)

    assert code == 0
    profile = tomllib.loads(config.read_text(encoding="utf-8"))["profiles"]["jira"]
    assert profile["JIRA_API_TOKEN"] == "stored-token"
    assert profile["TOOLSETS"] == "all"  # foreign key untouched
    # prefill defaults were actually offered
    _, _, url_default = text_call(prompt, "Jira URL")
    assert url_default == "https://work.atlassian.net"
    _, _, user_default = text_call(prompt, "Email (JIRA_USERNAME)")
    assert user_default == "you@work.com"


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

    code, _, _ = run_wizard(
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

    code, out, _ = run_wizard(list(JIRA_CLOUD_SCRIPT), home=home, cwd=tmp_path)

    assert code == 2
    config = home / ".config" / "atli" / "config.toml"
    assert config.is_file()  # config write happened before the hook failure
    assert any("not valid JSON" in line for line in out.lines)


def test_run_init_tls_flip_removes_stale_ssl_verify(tmp_path: Path) -> None:
    """Stored verify=false defaults TLS to No; flipping to Yes must remove
    the stale flag — keeping it would silently downgrade TLS."""
    home = tmp_path / "home"
    config = home / ".config" / "atli" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '[profiles.jira]\nJIRA_URL = "https://work.atlassian.net"\nJIRA_SSL_VERIFY = "false"\n',
        encoding="utf-8",
    )

    code, out, _ = run_wizard(list(JIRA_CLOUD_SCRIPT), home=home, cwd=tmp_path)

    assert code == 0
    profile = tomllib.loads(config.read_text(encoding="utf-8"))["profiles"]["jira"]
    assert "JIRA_SSL_VERIFY" not in profile


def test_run_init_unparseable_existing_config_exit_2(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = home / ".config" / "atli" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("this is [ not toml", encoding="utf-8")

    code, out, _ = run_wizard(
        [("select", "global"), ("text", "jira")], home=home, cwd=tmp_path
    )

    assert code == 2
    assert any("Could not parse" in line for line in out.lines)
    assert config.read_text(encoding="utf-8") == "this is [ not toml"  # untouched


def test_run_init_non_table_profiles_exit_2(tmp_path: Path) -> None:
    """A parseable file whose `profiles` key is not a table must be a clean
    exit 2 (the same shape rule load_config enforces), never a traceback."""
    home = tmp_path / "home"
    config = home / ".config" / "atli" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('profiles = 3\n', encoding="utf-8")

    code, out, _ = run_wizard(
        [("select", "global"), ("text", "jira")], home=home, cwd=tmp_path
    )

    assert code == 2
    assert any("'profiles' must be a table" in line for line in out.lines)


def test_run_init_bare_bool_ssl_verify_prefills_false(tmp_path: Path) -> None:
    """A TOML-boolean JIRA_SSL_VERIFY = false must prefill the TLS answer
    as No (runtime's _coerce_value yields "false", not Python's "False") —
    accepting the default must then KEEP verify off, not silently drop it."""
    home = tmp_path / "home"
    config = home / ".config" / "atli" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "[profiles.jira]\nJIRA_URL = \"https://work.atlassian.net\"\nJIRA_SSL_VERIFY = false\n",
        encoding="utf-8",
    )
    script = [
        ("select", "global"),
        ("text", "jira"),
        ("text", "https://work.atlassian.net"),  # Enter keeps the stored URL
        ("select", "0"),
        ("text", "you@work.com"),
        ("secret", "tok-123"),
        ("confirm", False),  # accept the preselected No
        ("select", "__skip__"),
        ("confirm", True),
    ]

    code, _, prompt = run_wizard(script, home=home, cwd=tmp_path)

    assert code == 0
    profile = tomllib.loads(config.read_text(encoding="utf-8"))["profiles"]["jira"]
    assert profile["JIRA_SSL_VERIFY"] == "false"  # kept, not dropped
    tls_call = next(c for c in prompt.calls if c[0] == "confirm" and "TLS" in c[1])
    assert tls_call[2] is False  # the offered default was No


def test_run_init_project_scope_warns_about_atli_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("", encoding="utf-8")
    script = [(k, v) for k, v in JIRA_CLOUD_SCRIPT]
    script[0] = ("select", "project")

    code, out, _ = run_wizard(
        script, home=home, cwd=tmp_path, environ={"ATLI_CONFIG": str(explicit)}
    )

    assert code == 0
    assert (tmp_path / ".atli.toml").is_file()
    assert any("ATLI_CONFIG" in line and "prefers it over" in line for line in out.lines)


def test_run_init_atli_config_missing_exit_2(tmp_path: Path) -> None:
    home = tmp_path / "home"
    out = Recorder()

    code = init.run_init(
        "jira",
        prompt=ScriptedPrompt(list(JIRA_CLOUD_SCRIPT)),
        home=home,
        cwd=tmp_path,
        environ={"ATLI_CONFIG": str(tmp_path / "missing.toml")},
        runner_factory=lambda: StubRunner(),
        out=out,
    )

    assert code == 2
    assert any("ATLI_CONFIG" in line for line in out.lines)
    assert not (home / ".config").exists()


def test_run_init_upsert_refusal_preserves_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config-layer refusal (unwritable merge) must become a clean exit 2
    with the file on disk untouched."""
    home = tmp_path / "home"

    def refusing_upsert(*args: object, **kwargs: object) -> str:
        raise ConfigError("Refusing to write: boom")

    monkeypatch.setattr(init, "upsert_profile", refusing_upsert)
    code, out, _ = run_wizard(list(JIRA_CLOUD_SCRIPT), home=home, cwd=tmp_path)

    assert code == 2
    assert any("Refusing to write" in line for line in out.lines)
    assert not (home / ".config" / "atli" / "config.toml").exists()


def test_run_init_happy_path_never_echoes_secret(tmp_path: Path) -> None:
    home = tmp_path / "home"

    code, out, _ = run_wizard(list(JIRA_CLOUD_SCRIPT), home=home, cwd=tmp_path)

    assert code == 0
    assert all("tok-123" not in line for line in out.lines)


def test_console_prompt_dispatches_on_tty_and_term(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_atlassian_cli.init import InquirerPrompt, PlainPrompt, console_prompt

    # The TERM rows below test the POSIX dispatch; on Windows the platform
    # exemption bypasses TERM entirely (covered by its own test below).
    monkeypatch.setattr(sys, "platform", "darwin")

    class _FakeStream:
        def __init__(self, tty: bool) -> None:
            self._tty = tty

        def isatty(self) -> bool:
            return self._tty

    monkeypatch.setattr(sys, "stdin", _FakeStream(tty=True))
    monkeypatch.setattr(sys, "stdout", _FakeStream(tty=True))
    monkeypatch.setenv("TERM", "xterm-256color")
    assert isinstance(console_prompt(), InquirerPrompt)
    # TERM=dumb: prompt_toolkit's fallback rendering has no cursor
    # addressing and no password masking — the plain adapter is the only
    # safe prompt there, even on a real TTY.
    monkeypatch.setenv("TERM", "dumb")
    assert isinstance(console_prompt(), PlainPrompt)
    monkeypatch.delenv("TERM")
    assert isinstance(console_prompt(), PlainPrompt)
    monkeypatch.setenv("TERM", "xterm-256color")
    # InquirerPy renders to stdout: a redirected stdout (atli init | tee)
    # hits the same unmasked plain-text output class — plain adapter.
    monkeypatch.setattr(sys, "stdout", _FakeStream(tty=False))
    assert isinstance(console_prompt(), PlainPrompt)
    monkeypatch.setattr(sys, "stdout", _FakeStream(tty=True))
    # stdin piped (an agent driving atli): plain adapter regardless.
    monkeypatch.setattr(sys, "stdin", _FakeStream(tty=False))
    assert isinstance(console_prompt(), PlainPrompt)


def test_console_prompt_windows_ignores_term(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows consoles never set TERM, and prompt_toolkit picks its
    Windows output backends on the platform — the TERM gate must not
    exclude them from the interactive prompt."""
    from mcp_atlassian_cli.init import InquirerPrompt, console_prompt

    class _FakeStream:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdin", _FakeStream())
    monkeypatch.setattr(sys, "stdout", _FakeStream())
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("TERM", raising=False)
    assert isinstance(console_prompt(), InquirerPrompt)


class _ScriptedInput:
    """Feeds input() lines; records the prompts seen."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = list(lines)
        self.prompts: list[str] = []

    def __call__(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        if not self.lines:
            raise EOFError("scripted input exhausted")
        return self.lines.pop(0)


def test_plain_prompt_select_text_secret_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_atlassian_cli.init import PlainPrompt

    echoed: list[str] = []
    prompt = PlainPrompt(echo=echoed.append)

    monkeypatch.setattr("builtins.input", _ScriptedInput(
        [
            "",  # select: Enter -> default
            "bad",  # select: invalid -> re-ask
            "2",  # select: second choice
            "typed value",  # text
            "n",  # confirm
        ]
    ))
    monkeypatch.setattr("mcp_atlassian_cli.init.getpass", lambda prompt="": "hidden-token")

    assert (
        prompt.select("Pick", [("a", "Alpha"), ("b", "Beta")], default="a") == "a"
    )
    assert (
        prompt.select("Pick", [("a", "Alpha"), ("b", "Beta")], default="a") == "b"
    )
    assert prompt.text("Name", default="fallback") == "typed value"
    assert prompt.confirm("Proceed?", default=True) is False
    assert prompt.secret("Token") == "hidden-token"
    assert any("Choose 1-2" in line for line in echoed)


def test_plain_prompt_text_validates_and_applies_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_atlassian_cli.init import PlainPrompt

    echoed: list[str] = []
    prompt = PlainPrompt(echo=echoed.append)
    scripted = _ScriptedInput(["", "bad", "good"])

    monkeypatch.setattr("builtins.input", scripted)
    validator = lambda text: None if text == "good" else f"not good: {text!r}"  # noqa: E731

    # Enter accepts the default — validated too (a bad stored value is caught)
    assert prompt.text("X", default="good", validate=validator) == "good"
    # no default: the invalid "bad" is rejected with its reason, "good" passes
    assert prompt.text("X", default=None, validate=validator) == "good"
    assert any("not good" in line for line in echoed)


# --- pty smoke tests: the real InquirerPy adapter under a real terminal ----
# (POSIX only — Windows has no pty; the logic above is adapter-independent)

_pty_only = pytest.mark.skipif(
    sys.platform == "win32", reason="pty-driven TUI tests are POSIX-only"
)


_SMOKE_CHILD = """
import sys
from pathlib import Path
from mcp_atlassian_cli.init import InquirerPrompt, run_init

home = Path(sys.argv[1]); cwd = Path(sys.argv[2])

class StubRunner:
    def call_tool(self, name, args):
        return "ok"

try:
    code = run_init(
        "jira",
        prompt=InquirerPrompt(),
        home=home,
        cwd=cwd,
        environ={},
        runner_factory=StubRunner,
        out=lambda line: print("OUT:", line, file=sys.stderr),
    )
except KeyboardInterrupt:
    print("OUT: KeyboardInterrupt", file=sys.stderr)
    sys.exit(1)
sys.exit(code)
"""


def _drive_pty(args: list[str], keys: list[str], timeout: float = 25.0) -> tuple[int, str]:
    """Run the smoke child under a pty, send ``keys``, return (exit, output).

    Everything here exists because a pty is not a terminal emulator:
    - a reader thread drains continuously — the ANSI-heavy redraws far
      exceed the kernel tty buffer, and an undrained pty makes the child
      block inside its render flush, which looks exactly like ignored
      keypresses;
    - it answers prompt_toolkit's cursor-position query (\x1b[6n → a CPR
      reply); with a smart TERM the app blocks until the terminal answers;
      the query is matched on a rolling tail so a split across reads still
      gets its reply;
    - keys are OUTPUT-GATED, never sent on a blind timer: the first key
      waits for the first prompt to render (a byte written while the child
      is still in canonical mode is consumed by the line discipline —
      ctrl-c becomes a literal ^C and Enter becomes \n — and the script
      desyncs), and every later key waits for the previous answer to
      register and the output to go quiet (the next prompt is up);
    - a real-size window and a real TERM: renderers behave differently at
      0x0, and TERM=dumb drops prompt_toolkit into a fallback with no
      masking and no cursor addressing (console_prompt refuses it too);
    - the child is killed and reaped in ``finally`` so a failing test can
      never leak a wedged process onto the runner.
    """
    import fcntl
    import os
    import pty
    import struct
    import termios
    import threading
    import time

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    env = dict(os.environ, TERM="xterm-256color")
    proc = subprocess.Popen(
        args, stdin=slave, stdout=slave, stderr=slave, close_fds=True, env=env
    )
    os.close(slave)

    transcript = bytearray()
    lock = threading.Lock()

    def snapshot() -> bytes:
        with lock:
            return bytes(transcript)

    def drain() -> None:
        tail = b""
        while True:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            with lock:
                transcript.extend(chunk)
            tail = (tail + chunk)[-8:]
            if b"\x1b[6n" in tail:
                try:
                    os.write(master, b"\x1b[24;1R")
                except OSError:
                    break

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()

    special = {"enter": "\r", "down": "\x1b[B", "up": "\x1b[A", "ctrlc": "\x03"}

    def wait_until(predicate, deadline: float) -> bool:
        while time.time() < deadline:
            if predicate(snapshot()):
                return True
            time.sleep(0.05)
        return False

    def wait_quiet(deadline: float, stability: float = 0.3) -> None:
        last_size, last_change = len(snapshot()), time.time()
        while time.time() < deadline:
            time.sleep(0.1)
            size = len(snapshot())
            if size != last_size:
                last_size, last_change = size, time.time()
            elif time.time() - last_change >= stability:
                return

    deadline = time.time() + timeout
    try:
        first_prompt = wait_until(lambda buf: b"Config scope" in buf, deadline)
        assert first_prompt, "wizard never rendered its first prompt"
        for key in keys:
            size_before = len(snapshot())
            os.write(master, special.get(key, key).encode())
            if not wait_until(lambda buf: len(buf) > size_before, deadline):
                break  # child exited (or hung); the poll loop below decides
            wait_quiet(deadline)
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.2)
    finally:
        try:
            os.close(master)
        except OSError:
            pass
        reader.join(timeout=3)
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
    return proc.returncode, snapshot().decode(errors="replace")


@_pty_only
def test_pty_smoke_happy_path(tmp_path: Path) -> None:
    child = tmp_path / "smoke_child.py"
    child.write_text(_SMOKE_CHILD, encoding="utf-8")
    home = tmp_path / "home"

    # All defaults, typed values where there is no default:
    # scope enter(global) -> name enter(jira) -> URL typed -> auth enter(cloud)
    # -> username typed -> token typed -> TLS enter(Yes) -> verify ok ->
    # harness enter(claude) -> write enter(Yes)
    code, output = _drive_pty(
        [sys.executable, str(child), str(home), str(tmp_path)],
        [
            "enter",  # scope: global
            "enter",  # profile name: jira
            "https://pty.atlassian.net", "enter",  # URL
            "enter",  # auth: Cloud
            "pty@work.com", "enter",  # username
            "pty-token", "enter",  # api token (hidden)
            "enter",  # TLS: Yes
            "enter",  # harness: claude (nothing detected)
            "enter",  # write: Yes
        ],
    )

    assert code == 0, output
    config = home / ".config" / "atli" / "config.toml"
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert data["default_profile"] == "jira"
    assert data["profiles"]["jira"]["JIRA_URL"] == "https://pty.atlassian.net"
    assert data["profiles"]["jira"]["JIRA_USERNAME"] == "pty@work.com"
    assert data["profiles"]["jira"]["JIRA_API_TOKEN"] == "pty-token"
    assert (home / ".claude" / "settings.json").is_file()  # hook installed
    # the secret was never echoed — not while typing (raw mode) and not in
    # the completed-answer line (masked transformer)
    assert "pty-token" not in output


@_pty_only
def test_pty_smoke_ctrl_c_aborts_writing_nothing(tmp_path: Path) -> None:
    child = tmp_path / "smoke_child.py"
    child.write_text(_SMOKE_CHILD, encoding="utf-8")
    home = tmp_path / "home"

    code, output = _drive_pty(
        [sys.executable, str(child), str(home), str(tmp_path)],
        ["ctrlc"],
    )

    assert code == 1, output
    assert "KeyboardInterrupt" in output
    assert not (home / ".config").exists()  # nothing written


@_pty_only
def test_pty_smoke_arrow_keys_change_selection(tmp_path: Path) -> None:
    """down+enter picks the SECOND choice: Data Center auth, so the wizard
    collects exactly one secret (the personal token)."""
    child = tmp_path / "smoke_child.py"
    child.write_text(_SMOKE_CHILD, encoding="utf-8")
    home = tmp_path / "home"

    code, output = _drive_pty(
        [sys.executable, str(child), str(home), str(tmp_path)],
        [
            "enter",  # scope: global
            "enter",  # profile name: jira
            "https://pty.internal", "enter",  # URL
            "down", "enter",  # auth: Data Center (second choice)
            "pat-token", "enter",  # personal token
            "enter",  # TLS: Yes
            "enter",  # harness
            "enter",  # write
        ],
    )

    assert code == 0, output
    data = tomllib.loads(
        (home / ".config" / "atli" / "config.toml").read_text(encoding="utf-8")
    )
    profile = data["profiles"]["jira"]
    assert profile["JIRA_PERSONAL_TOKEN"] == "pat-token"
    assert "JIRA_USERNAME" not in profile
    assert "JIRA_API_TOKEN" not in profile
