"""Tests for the schema-generated cyclopts command tree."""

from __future__ import annotations

from typing import Any

import pytest
from cyclopts import App
from cyclopts.exceptions import MissingArgumentError, UnknownOptionError

from mcp_atlassian_cli.build import create_app
from mcp_atlassian_cli.discovery import ToolParam, ToolSpec

ISSUE_KEY = ToolParam(name="issue_key", type=str, required=True, default=None)
COMPACT = ToolParam(name="compact", type=bool, required=False, default=False)


class DispatchSpy:
    """Records ``(tool_name, arguments)`` pairs and returns DISPATCHED."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, tool_name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((tool_name, dict(arguments)))
        return "DISPATCHED"


def invoke(app: App, argv: list[str]) -> None:
    """Run a command that is expected to succeed.

    cyclopts 4.22 ends every successful top-level ``app(...)`` call with
    ``SystemExit(0)`` (its default result action), so a zero exit is fine.
    """
    try:
        app(argv, exit_on_error=False, print_error=False)
    except SystemExit as error:
        assert error.code in (None, 0), f"unexpected exit code {error.code!r}"


def jira_get_issue_spec(
    params: tuple[ToolParam, ...] = (ISSUE_KEY, COMPACT),
    description: str = "Get an issue. More detail.",
) -> ToolSpec:
    return ToolSpec(
        tool_name="jira_get_issue",
        service="jira",
        command_name="get-issue",
        description=description,
        params=params,
    )


def test_dispatch_required_and_default(capsys: pytest.CaptureFixture[str]) -> None:
    spy = DispatchSpy()
    app = create_app([jira_get_issue_spec()], spy)

    invoke(app, ["jira", "get-issue", "--issue-key", "PROJ-1"])

    assert spy.calls == [("jira_get_issue", {"issue_key": "PROJ-1", "compact": False})]
    assert "DISPATCHED" in capsys.readouterr().out


def test_dispatch_flags(capsys: pytest.CaptureFixture[str]) -> None:
    comment_limit = ToolParam(name="comment_limit", type=int, required=False, default=10)
    spy = DispatchSpy()
    app = create_app([jira_get_issue_spec((ISSUE_KEY, COMPACT, comment_limit))], spy)

    invoke(app, ["jira", "get-issue", "--issue-key", "P", "--compact"])
    assert spy.calls[-1] == (
        "jira_get_issue",
        {"issue_key": "P", "compact": True, "comment_limit": 10},
    )

    invoke(app, ["jira", "get-issue", "--issue-key", "P", "--comment-limit", "5"])
    assert spy.calls[-1] == (
        "jira_get_issue",
        {"issue_key": "P", "compact": False, "comment_limit": 5},
    )
    capsys.readouterr()


def test_optional_none_excluded(capsys: pytest.CaptureFixture[str]) -> None:
    labels = ToolParam(name="labels", type=list, required=False, default=None)
    spy = DispatchSpy()
    app = create_app([jira_get_issue_spec((ISSUE_KEY, labels))], spy)

    invoke(app, ["jira", "get-issue", "--issue-key", "P"])

    assert spy.calls == [("jira_get_issue", {"issue_key": "P"})]
    capsys.readouterr()


def test_missing_required_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    app = create_app([jira_get_issue_spec()], DispatchSpy())

    with pytest.raises(MissingArgumentError):
        app(["jira", "get-issue"], exit_on_error=False, print_error=False)
    capsys.readouterr()


def test_unknown_option(capsys: pytest.CaptureFixture[str]) -> None:
    app = create_app([jira_get_issue_spec()], DispatchSpy())

    with pytest.raises(UnknownOptionError):
        app(
            ["jira", "get-issue", "--issue-key", "P", "--bogus", "1"],
            exit_on_error=False,
            print_error=False,
        )
    capsys.readouterr()


def test_unprefixed_tool_at_root(capsys: pytest.CaptureFixture[str]) -> None:
    spec = ToolSpec(
        tool_name="health_check",
        service=None,
        command_name="health-check",
        description="Report server health.",
        params=(),
    )
    spy = DispatchSpy()
    app = create_app([spec], spy)

    invoke(app, ["health-check"])

    assert spy.calls == [("health_check", {})]
    assert "DISPATCHED" in capsys.readouterr().out


def test_tools_command_listing(capsys: pytest.CaptureFixture[str]) -> None:
    confluence_search = ToolSpec(
        tool_name="confluence_search",
        service="confluence",
        command_name="search",
        description="",
        params=(ToolParam(name="query", type=str, required=True, default=None),),
    )
    app = create_app([jira_get_issue_spec(), confluence_search], DispatchSpy())

    invoke(app, ["tools"])
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert "jira get-issue" in lines[0]
    assert "Get an issue." in lines[0]
    assert "More detail" not in lines[0]
    assert "confluence search" in lines[1]
    assert "(no description)" in lines[1]

    invoke(app, ["tools", "--service", "jira"])
    out = capsys.readouterr().out
    assert "jira get-issue" in out
    assert "confluence" not in out

    invoke(app, ["tools", "--service", "nope"])
    assert capsys.readouterr().out.splitlines() == ["No tools for service 'nope'."]

    empty_app = create_app([], DispatchSpy())
    invoke(empty_app, ["tools"])
    assert capsys.readouterr().out.splitlines() == [
        "No services configured — set JIRA_URL / CONFLUENCE_URL "
        "or a profile (see atli --help)."
    ]


def test_profiles_command(capsys: pytest.CaptureFixture[str]) -> None:
    app = create_app([], DispatchSpy(), profiles_text="* corp (default)")
    invoke(app, ["profiles"])
    assert capsys.readouterr().out.splitlines() == ["* corp (default)"]

    app = create_app([], DispatchSpy(), profiles_text=None)
    invoke(app, ["profiles"])
    assert capsys.readouterr().out.splitlines() == ["No config file found."]


def test_help_lists_tool(capsys: pytest.CaptureFixture[str]) -> None:
    app = create_app([jira_get_issue_spec()], DispatchSpy())

    with pytest.raises(SystemExit) as excinfo:
        app(["jira", "get-issue", "--help"], exit_on_error=True)

    assert excinfo.value.code in (None, 0)
    out = capsys.readouterr().out
    assert "Get an issue. More detail." in out
    assert "--issue-key" in out


def test_root_help_documents_globals(capsys: pytest.CaptureFixture[str]) -> None:
    app = create_app([], DispatchSpy())

    with pytest.raises(SystemExit):
        app(["--help"], exit_on_error=True)

    out = capsys.readouterr().out
    assert "--profile" in out
    assert "atli tools" in out
    assert "atli <service> <tool> --help" in out
