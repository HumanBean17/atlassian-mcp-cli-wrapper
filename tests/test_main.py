"""Integration tests for main(): profiles -> env -> build -> dispatch."""

from __future__ import annotations

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


def stub_factory(app: Any) -> Callable[[], ToolRunner]:
    """A runner factory pinned to ``app``: never touches real Atlassian."""
    return lambda: ToolRunner(app=app)


@pytest.fixture(autouse=True)
def hermetic_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep config discovery and profile env mutation inside the test.

    ``apply_profile`` mutates ``os.environ`` in-process and nothing undoes that,
    so service-prefixed variables are snapshotted and restored after each test.
    HOME is pointed at an empty tmp_path so a developer's real
    ``~/.config/atli/config.toml`` can never leak into a test.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ATLI_CONFIG", raising=False)
    monkeypatch.delenv("ATLI_PROFILE", raising=False)
    before = {
        key: value
        for key, value in os.environ.items()
        if key.startswith(_SERVICE_ENV_PREFIXES)
    }
    yield
    for key in list(os.environ):
        if key.startswith(_SERVICE_ENV_PREFIXES) and key not in before:
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
