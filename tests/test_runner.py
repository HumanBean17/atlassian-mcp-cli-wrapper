"""Tests for the ToolRunner transport seam over the in-memory fastmcp client."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp_atlassian_cli.discovery import ToolSpec
from mcp_atlassian_cli.runner import (
    ToolCallFailure,
    ToolRunner,
    ToolRunnerError,
    result_to_text,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_result_to_text_text_blocks():
    result = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="a"),
            SimpleNamespace(type="text", text="b"),
        ]
    )
    assert result_to_text(result) == "a\nb"


def test_result_to_text_no_text_blocks():
    result = SimpleNamespace(
        content=[SimpleNamespace(type="image", data="xyz", annotations=None)]
    )
    rendered = result_to_text(result)
    assert json.loads(rendered) == [{"type": "image", "data": "xyz"}]
    assert rendered == json.dumps([{"type": "image", "data": "xyz"}], indent=2)


def test_result_to_text_empty_text_block_is_still_text():
    result = SimpleNamespace(content=[SimpleNamespace(type="text", text="")])
    assert result_to_text(result) == ""


def test_list_tool_specs(stub_app):
    specs = ToolRunner(app=stub_app).list_tool_specs()

    assert len(specs) == 4
    assert all(isinstance(spec, ToolSpec) for spec in specs)
    names = {spec.tool_name for spec in specs}
    assert {"jira_get_issue", "confluence_search"} <= names

    get_issue = next(spec for spec in specs if spec.tool_name == "jira_get_issue")
    by_name = {param.name: param for param in get_issue.params}
    assert by_name["issue_key"].required is True
    assert by_name["compact"].required is False
    assert by_name["compact"].default is False
    assert by_name["compact"].type is bool


def test_call_tool_text(stub_app):
    runner = ToolRunner(app=stub_app)
    assert runner.call_tool("jira_get_issue", {"issue_key": "PROJ-1"}) == (
        "issue PROJ-1 compact=False"
    )
    assert runner.call_tool(
        "jira_get_issue", {"issue_key": "PROJ-1", "compact": True}
    ) == "issue PROJ-1 compact=True"


def test_call_tool_lists(stub_app):
    rendered = ToolRunner(app=stub_app).call_tool(
        "jira_search", {"jql": "assignee=me", "labels": ["a", "b"]}
    )
    assert "labels=['a', 'b']" in rendered


def test_call_tool_error(stub_app):
    runner = ToolRunner(app=stub_app)
    with pytest.raises(ToolCallFailure) as excinfo:
        runner.call_tool("jira_boom", {"message": "x"})
    assert "kaboom from server" in str(excinfo.value)


def test_call_tool_error_is_quiet_on_stderr(stub_app, capsys):
    """A failing tool must surface only as ToolCallFailure — fastmcp's server-side
    rich traceback for the same failure must never reach the CLI's stderr."""
    with pytest.raises(ToolCallFailure):
        ToolRunner(app=stub_app).call_tool("jira_boom", {"message": "x"})
    assert capsys.readouterr().err == ""


def test_lazy_default_app():
    """Constructing ToolRunner() without an app must not import mcp_atlassian.

    Runs in a fresh interpreter (the only honest check: this test process itself
    imports fastmcp via the stub_app fixture), then asserts two things: the
    import succeeded, and `mcp_atlassian` never entered sys.modules.
    """
    code = (
        "import sys\n"
        "from mcp_atlassian_cli.runner import ToolRunner\n"
        "runner = ToolRunner()\n"
        "assert 'mcp_atlassian' not in sys.modules, 'lazy _app leaked an import'\n"
        "print('lazy-ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "lazy-ok"
    assert "mcp_atlassian" not in sys.modules


def test_default_app_import_failure_is_runner_error(monkeypatch):
    """A broken mcp_atlassian install surfaces as ToolRunnerError, never ImportError."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "mcp_atlassian.servers.main":
            raise ImportError("simulated broken install")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ToolRunnerError) as excinfo:
        ToolRunner()._app
    assert "mcp-atlassian" in str(excinfo.value)
