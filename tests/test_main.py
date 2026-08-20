"""Integration tests for main(): profiles -> env -> build -> dispatch."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from mcp_atlassian_cli.discovery import ToolParam, ToolSpec
from mcp_atlassian_cli.main import main
from mcp_atlassian_cli.runner import ToolRunner

REPO_ROOT = Path(__file__).resolve().parent.parent

CORP_TOML = """\
[profiles.corp]
JIRA_URL = "https://corp.atlassian.net"
JIRA_API_TOKEN = "corp-secret"

[profiles.partner]
CONFLUENCE_URL = "https://partner.wiki"
CONFLUENCE_PERSONAL_TOKEN = "partner-secret"
"""

CORP_URL = "https://corp.atlassian.net"
CORP_TOKEN = "corp-secret"

_SERVICE_ENV_PREFIXES = ("JIRA_", "CONFLUENCE_", "MCP_ATLASSIAN_")
_CROSS_SERVICE_KEYS = (
    "ATLASSIAN_OAUTH_ENABLE",
    "ATLASSIAN_OAUTH_CLIENT_ID",
    "ATLASSIAN_OAUTH_CLIENT_SECRET",
    "ATLASSIAN_OAUTH_REDIRECT_URI",
    "ATLASSIAN_OAUTH_SCOPE",
    "ATLASSIAN_OAUTH_CLOUD_ID",
    "ATLASSIAN_OAUTH_ACCESS_TOKEN",
    "ATLASSIAN_EXTERNAL_AUTH_ENABLE",
)


def stub_factory(app: Any) -> Callable[[], ToolRunner]:
    """A runner factory pinned to ``app``: never touches real Atlassian."""
    return lambda: ToolRunner(app=app)


@pytest.fixture(autouse=True)
def hermetic_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep config discovery and profile env mutation inside the test.

    ``apply_profile`` mutates ``os.environ`` in-process and nothing undoes that,
    so service-prefixed variables (and the cross-service ``ATLASSIAN_*``
    credential keys it clears) are snapshotted and restored after each test.
    HOME is pointed at an empty tmp_path so a developer's real
    ``~/.config/atli/config.toml`` can never leak into a test.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ATLI_CONFIG", raising=False)
    monkeypatch.delenv("ATLI_PROFILE", raising=False)
    before = {
        key: value
        for key, value in os.environ.items()
        if key.startswith(_SERVICE_ENV_PREFIXES) or key in _CROSS_SERVICE_KEYS
    }
    yield
    for key in list(os.environ):
        if (
            key.startswith(_SERVICE_ENV_PREFIXES) or key in _CROSS_SERVICE_KEYS
        ) and key not in before:
            del os.environ[key]
    os.environ.update(before)


class SpyRunner:
    """Records call_tool invocations and returns canned output."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                tool_name="jira_get_issue",
                service="jira",
                command_name="get-issue",
                description="Get an issue.",
                params=(
                    ToolParam(name="issue_key", type=str, required=True, default=None),
                    ToolParam(name="compact", type=bool, required=False, default=False),
                ),
            )
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, dict(arguments)))
        return "DISPATCHED"


def test_main_dispatches_via_stub(stub_app, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ["jira", "get-issue", "--issue-key", "PROJ-1"],
        runner_factory=stub_factory(stub_app),
    )
    out, err = capsys.readouterr()
    assert code == 0
    assert "issue PROJ-1 compact=False" in out
    assert err == ""


def test_main_dispatches_through_runner(capsys: pytest.CaptureFixture[str]) -> None:
    """main() must route tool calls through the injected runner's call_tool."""
    spy = SpyRunner()
    code = main(
        ["jira", "get-issue", "--issue-key", "PROJ-1"],
        runner_factory=lambda: spy,
    )
    assert code == 0
    assert spy.calls == [("jira_get_issue", {"issue_key": "PROJ-1", "compact": False})]
    assert "DISPATCHED" in capsys.readouterr().out


def test_main_tool_failure_exit_1(stub_app, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ["jira", "boom", "--message", "x"],
        runner_factory=stub_factory(stub_app),
    )
    out, err = capsys.readouterr()
    assert code == 1
    assert "kaboom from server" in err
    assert out == ""


def test_main_usage_error_exit_2(stub_app, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["jira", "totally-unknown"], runner_factory=stub_factory(stub_app))
    out, err = capsys.readouterr()
    assert code == 2
    assert err != ""
    assert out == ""


def test_main_missing_at_file_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    """A ``@path`` value pointing at a missing file is a usage error (exit 2)
    whose message teaches the ``@@`` escape — never a traceback."""
    spy = SpyRunner()
    code = main(
        ["jira", "get-issue", "--issue-key", "@/no/such/file"],
        runner_factory=lambda: spy,
    )
    out, err = capsys.readouterr()
    assert code == 2
    assert "file not found" in err
    assert "@@" in err
    assert out == ""


def test_main_missing_required_exit_2(stub_app, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["jira", "get-issue"], runner_factory=stub_factory(stub_app))
    out, err = capsys.readouterr()
    assert code == 2
    assert err != ""


def test_main_profile_flag_applies_env(
    stub_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".atli.toml").write_text(CORP_TOML)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JIRA_URL", raising=False)

    code = main(["--profile", "corp", "tools"], runner_factory=stub_factory(stub_app))

    assert code == 0
    assert os.environ["JIRA_URL"] == CORP_URL
    capsys.readouterr()


def test_main_profile_replacement_isolation(
    stub_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".atli.toml").write_text(CORP_TOML)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JIRA_URL", "https://ambient.atlassian.net")
    monkeypatch.setenv("JIRA_API_TOKEN", "ambient-token")

    code = main(["--profile", "partner", "tools"], runner_factory=stub_factory(stub_app))

    assert code == 0
    assert os.environ["JIRA_URL"] == "https://ambient.atlassian.net"
    assert os.environ["CONFLUENCE_URL"] == "https://partner.wiki"
    capsys.readouterr()


def test_main_unknown_profile_exit_2(
    stub_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".atli.toml").write_text(CORP_TOML)
    monkeypatch.chdir(tmp_path)

    code = main(["--profile", "ghost", "tools"], runner_factory=stub_factory(stub_app))
    out, err = capsys.readouterr()

    assert code == 2
    assert "corp" in err
    assert "partner" in err
    assert out == ""


def test_main_empty_profile_value_exit_2(
    stub_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--profile \"\"` must error, not silently use the default profile."""
    (tmp_path / ".atli.toml").write_text(CORP_TOML)
    monkeypatch.chdir(tmp_path)

    code = main(["--profile", "", "jira", "get-issue"], runner_factory=stub_factory(stub_app))
    out, err = capsys.readouterr()

    assert code == 2
    assert "requires a profile name" in err
    assert out == ""


def test_main_profile_after_subcommand_gets_hint(
    stub_app, capsys: pytest.CaptureFixture[str]
) -> None:
    """A misplaced --profile stays a usage error, but the message teaches the
    correct placement instead of dead-ending at cyclopts' bare unknown-option
    text. Asserts on OUR hint, not cyclopts' wording."""
    code = main(
        ["jira", "get-issue", "--profile", "work", "--issue-key", "X"],
        runner_factory=stub_factory(stub_app),
    )
    out, err = capsys.readouterr()

    assert code == 2
    assert "--profile" in err
    assert "before the subcommand" in err
    assert out == ""


def test_main_unrelated_usage_error_gets_no_hint(
    stub_app, capsys: pytest.CaptureFixture[str]
) -> None:
    """The hint is reserved for --profile mistakes, not appended to every
    usage error."""
    code = main(
        ["jira", "get-issue", "--issue-key", "P", "--bogus", "1"],
        runner_factory=stub_factory(stub_app),
    )
    out, err = capsys.readouterr()

    assert code == 2
    assert "before the subcommand" not in err
    assert out == ""


def test_main_value_containing_profile_gets_no_hint(
    stub_app, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unused token that merely CONTAINS ``--profile`` is not a placement
    mistake — appending the hint would steer the agent toward flag placement
    when its real problem is a stray value."""
    code = main(
        ["jira", "get-issue", "--issue-key", "X", "--compact", "maybe --profile=x"],
        runner_factory=stub_factory(stub_app),
    )
    out, err = capsys.readouterr()

    assert code == 2
    assert "before the subcommand" not in err
    assert out == ""


def test_main_stray_token_containing_profile_gets_no_hint(
    stub_app, capsys: pytest.CaptureFixture[str]
) -> None:
    """Same guard for a stray positional carrying the substring."""
    code = main(
        ["jira", "get-issue", "--issue-key", "X", "stray --profile=y"],
        runner_factory=stub_factory(stub_app),
    )
    out, err = capsys.readouterr()

    assert code == 2
    assert "before the subcommand" not in err
    assert out == ""


def test_main_tools_hint_when_empty(capsys: pytest.CaptureFixture[str]) -> None:
    from fastmcp import FastMCP

    code = main(["tools"], runner_factory=stub_factory(FastMCP("empty")))
    out, err = capsys.readouterr()
    assert code == 0
    assert "No services configured" in out
    assert err == ""


def test_main_profiles_command(
    stub_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".atli.toml").write_text(CORP_TOML)
    monkeypatch.chdir(tmp_path)

    code = main(["profiles"], runner_factory=stub_factory(stub_app))
    out, err = capsys.readouterr()

    assert code == 0
    assert "corp" in out
    assert CORP_URL in out
    assert CORP_TOKEN not in out
    assert err == ""


def test_main_profiles_no_config_file(
    stub_app,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No config file anywhere -> `atli profiles` says so (not 'No profiles
    configured'), because that is distinguishable and the more useful answer."""
    monkeypatch.chdir(tmp_path)

    code = main(["profiles"], runner_factory=stub_factory(stub_app))
    out, err = capsys.readouterr()

    assert code == 0
    assert out.splitlines() == ["No config file found."]
    assert err == ""


def test_broken_pipe_is_not_a_traceback(
    stub_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`atli <tool> | head` must exit 0 quietly, not traceback on EPIPE.

    Runs the real interpreter in a subprocess whose stdout is a pipe closed
    after one line — the same condition `| head -1` creates at shutdown flush.
    """
    monkeypatch.delenv("ATLI_CONFIG", raising=False)
    code = (
        "from mcp_atlassian_cli.main import main\n"
        f"runner = type('R', (), {{'list_tool_specs': staticmethod(lambda: []),"
        " 'call_tool': staticmethod(lambda n, a: 'DISPATCHED')})()\n"
        "rc = main(['tools'], runner_factory=lambda: runner)\n"
        "sys_exit = rc\n"
    )
    script = tmp_path / "pipe_case.py"
    script.write_text(code)
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=REPO_ROOT,
    )
    proc.stdout.readline()  # reader takes one line, then abandons the pipe
    proc.stdout.close()
    _stdout, stderr = proc.communicate(timeout=60)
    assert proc.returncode == 0, stderr
    assert "BrokenPipeError" not in stderr
    assert "Traceback" not in stderr


def test_no_server_import_at_module_import() -> None:
    """Importing main must never pull mcp_atlassian before the env is applied."""
    code = (
        "import mcp_atlassian_cli.main, sys; "
        "sys.exit(0 if 'mcp_atlassian' not in sys.modules else 1)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr


WORK_TOML = """\
[profiles.work]
JIRA_URL = "https://work.atlassian.net"
JIRA_USERNAME = "you@work.com"
JIRA_API_TOKEN = "work-secret"
"""

PRIME_JIRA_ENV = {
    "JIRA_URL": "https://work.atlassian.net",
    "JIRA_USERNAME": "you@work.com",
    "JIRA_API_TOKEN": "work-secret",
}
PRIME_CONFLUENCE_ENV = {
    "CONFLUENCE_URL": "https://wiki.internal",
    "CONFLUENCE_PERSONAL_TOKEN": "pat-secret",
}


def forbidden_runner() -> ToolRunner:
    """A factory whose construction means the fast path is broken."""
    raise AssertionError("runner must not be constructed for prime")


@pytest.fixture
def prime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutral override lookup; Jira cloud + Confluence PAT configured."""
    monkeypatch.delenv("ATLI_PRIME", raising=False)
    for key, value in {**PRIME_JIRA_ENV, **PRIME_CONFLUENCE_ENV}.items():
        monkeypatch.setenv(key, value)


def test_main_prime_never_constructs_runner(
    prime_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["prime"], runner_factory=forbidden_runner)
    out, err = capsys.readouterr()
    assert code == 0
    assert "# atli — Jira & Confluence CLI" in out
    assert err == ""


def test_main_prime_hook_json(
    prime_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["prime", "--hook-json"], runner_factory=forbidden_runner)
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["additionalContext"] != ""


def test_main_prime_help(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["prime", "--help"], runner_factory=forbidden_runner)
    out, err = capsys.readouterr()
    assert code == 0
    assert "--hook-json" in out
    assert err == ""


def test_main_prime_bad_flag_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["prime", "--bogus"], runner_factory=forbidden_runner)
    out, err = capsys.readouterr()
    assert code == 2
    assert err != ""
    assert out == ""


def test_main_prime_profile_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".atli.toml").write_text(WORK_TOML)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ATLI_PRIME", raising=False)

    code = main(["--profile", "work", "prime"], runner_factory=forbidden_runner)
    out, err = capsys.readouterr()

    assert code == 0
    assert "Configured: jira" in out
    assert "Profile: work" in out
    assert err == ""


def test_main_prime_silent_when_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ATLI_PRIME", raising=False)
    monkeypatch.chdir(tmp_path)
    for key in [
        key for key in os.environ if key.startswith(("JIRA_", "CONFLUENCE_"))
    ]:
        monkeypatch.delenv(key, raising=False)

    code = main(["prime"], runner_factory=forbidden_runner)
    out, err = capsys.readouterr()

    assert code == 0
    assert out == ""
    assert err == ""


def test_main_prime_atli_prime_missing_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ATLI_PRIME", "/no/such/prime.md")

    code = main(["prime"], runner_factory=forbidden_runner)
    out, err = capsys.readouterr()

    assert code == 2
    assert "ATLI_PRIME" in err
    assert out == ""


def test_prime_never_imports_mcp_atlassian(tmp_path: Path) -> None:
    """The fast path must run prime end-to-end without the server import.

    Same shape as test_no_server_import_at_module_import, but driving a full
    main(['prime', '--hook-json']) dispatch in a hermetic subprocess.
    """
    code = (
        "import sys\n"
        "from mcp_atlassian_cli.main import main\n"
        "rc = main(['prime', '--hook-json'])\n"
        "assert 'mcp_atlassian' not in sys.modules, 'prime imported the server!'\n"
        "sys.exit(rc)\n"
    )
    script = tmp_path / "prime_case.py"
    script.write_text(code)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("ATLI_CONFIG", "ATLI_PROFILE", "ATLI_PRIME")
        and not key.startswith(("JIRA_", "CONFLUENCE_", "MCP_ATLASSIAN_"))
    }
    env.update(PRIME_JIRA_ENV, HOME=str(tmp_path))
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "prime imported the server" not in proc.stderr
    assert '"hookEventName":"SessionStart"' in proc.stdout
