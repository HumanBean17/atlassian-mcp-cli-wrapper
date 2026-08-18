"""Tests for the schema-generated cyclopts command tree."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cyclopts import App
from cyclopts.exceptions import (
    CycloptsError,
    MissingArgumentError,
    UnknownOptionError,
)

from mcp_atlassian_cli.build import create_app, create_prime_app
from mcp_atlassian_cli.config import ConfigError
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


def test_optional_none_default_with_pydantic_loaded(capsys: pytest.CaptureFixture[str]) -> None:
    """A None-default optional param parses even when pydantic is loaded.

    Production always imports pydantic (fastmcp) before parsing. cyclopts then
    validates parameter *defaults* with ``pydantic.TypeAdapter``, so a plain
    ``str`` annotation with a ``None`` default raises ValidationError and every
    tool invocation would exit 2.
    """
    import pydantic  # noqa: F401  (cyclopts checks "pydantic" in sys.modules)

    expand = ToolParam(name="expand", type=str, required=False, default=None)
    comment_limit = ToolParam(name="comment_limit", type=int, required=False, default=10)
    spy = DispatchSpy()
    app = create_app([jira_get_issue_spec((ISSUE_KEY, expand, comment_limit))], spy)

    invoke(app, ["jira", "get-issue", "--issue-key", "P"])

    assert spy.calls == [
        ("jira_get_issue", {"issue_key": "P", "comment_limit": 10})
    ]
    capsys.readouterr()


def test_dispatch_intact_with_annotated_optional_none(capsys: pytest.CaptureFixture[str]) -> None:
    """The ``T | None`` base must survive inside ``Annotated`` wrapping.

    Descriptions ride in as ``Annotated[base, Parameter(help=...)]``; the base
    (with its ``| None`` for None-default optionals) must stay the first
    ``Annotated`` slot, or pydantic's default validation rejects ``None``
    again — same failure mode as the test above.
    """
    import pydantic  # noqa: F401  (cyclopts checks "pydantic" in sys.modules)

    expand = ToolParam(
        name="expand",
        type=str,
        required=False,
        default=None,
        description="Fields to expand in the response.",
    )
    comment_limit = ToolParam(name="comment_limit", type=int, required=False, default=10)
    spy = DispatchSpy()
    app = create_app([jira_get_issue_spec((ISSUE_KEY, expand, comment_limit))], spy)

    invoke(app, ["jira", "get-issue", "--issue-key", "P"])

    assert spy.calls == [
        ("jira_get_issue", {"issue_key": "P", "comment_limit": 10})
    ]
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


def test_tools_listing_one_line_per_tool(capsys: pytest.CaptureFixture[str]) -> None:
    """66 of 98 real descriptions end their first sentence with ``.\\n\\n``, so a
    ``". "``-only split prints the whole multi-paragraph description and blank
    lines wreck the aligned table. The listing must be exactly one physical line
    per tool: first line of the description, then the first sentence of that."""
    app = create_app(
        [
            jira_get_issue_spec(
                description="Get an issue.\n\nLong second paragraph with more detail."
            ),
            ToolSpec(
                tool_name="jira_search",
                service="jira",
                command_name="search",
                description="Returns issues matching a query\n\nDetails.",
                params=(ToolParam(name="jql", type=str, required=True, default=None),),
            ),
        ],
        DispatchSpy(),
    )

    invoke(app, ["tools"])

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert lines[0].count("Get an issue.") == 1
    assert "second paragraph" not in lines[0]
    assert lines[1].count("Returns issues matching a query") == 1
    assert "Details." not in lines[1]


def test_first_sentence_takes_first_line() -> None:
    from mcp_atlassian_cli.build import _first_sentence

    assert _first_sentence("Two. Sentences. On one line.\n\nRest") == (
        "Two. "
    )
    assert _first_sentence("No period at all\n\nDetails.") == "No period at all"
    assert _first_sentence("") == "(no description)"
    assert _first_sentence("Single line only.") == "Single line only."


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


def test_help_shows_param_descriptions(capsys: pytest.CaptureFixture[str]) -> None:
    """Each parameter's schema description renders next to its flag, verbatim,
    while the [required]/[default: …] markers keep printing. Params without a
    description render exactly as before."""
    issue_key = ToolParam(
        name="issue_key",
        type=str,
        required=True,
        default=None,
        description="The issue key, e.g. PROJ-123.",
    )
    compact = ToolParam(
        name="compact",
        type=bool,
        required=False,
        default=False,
        description="Return a compact view.",
    )
    comment_limit = ToolParam(name="comment_limit", type=int, required=False, default=10)
    app = create_app([jira_get_issue_spec((issue_key, compact, comment_limit))], DispatchSpy())

    with pytest.raises(SystemExit) as excinfo:
        app(["jira", "get-issue", "--help"], exit_on_error=True)

    assert excinfo.value.code in (None, 0)
    out = capsys.readouterr().out
    assert "The issue key, e.g. PROJ-123." in out
    assert "Return a compact view." in out
    assert "--issue-key" in out
    assert "[required]" in out
    assert "[default: 10]" in out


def test_root_help_documents_globals(capsys: pytest.CaptureFixture[str]) -> None:
    app = create_app([], DispatchSpy())

    with pytest.raises(SystemExit):
        app(["--help"], exit_on_error=True)

    out = capsys.readouterr().out
    assert "--profile" in out
    assert "atli tools" in out
    assert "atli <service> <tool> --help" in out


def test_version_flag_is_a_tool_param_not_an_app_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """``--version`` must reach the tool: cyclopts' default ``version_flags``
    would swallow it, print the app version, and exit 0 without dispatching —
    shadowing the real ``version`` param of ``confluence_get_page_history``."""
    page_id = ToolParam(name="page_id", type=str, required=True, default=None)
    version = ToolParam(name="version", type=int, required=False, default=None)
    spec = ToolSpec(
        tool_name="confluence_get_page_history",
        service="confluence",
        command_name="get-page-history",
        description="Get the history of a page.",
        params=(page_id, version),
    )
    spy = DispatchSpy()
    app = create_app([spec], spy)

    invoke(app, ["confluence", "get-page-history", "--page-id", "123", "--version", "2"])

    assert spy.calls == [
        ("confluence_get_page_history", {"page_id": "123", "version": 2})
    ]
    out = capsys.readouterr().out
    assert "DISPATCHED" in out


def test_root_has_no_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """The spec's command surface never promised ``atli --version``: with
    ``version_flags=[]`` it must be a usage error (CycloptsError), not the
    cyclopts version banner with exit 0."""
    from cyclopts.exceptions import CycloptsError

    app = create_app([jira_get_issue_spec()], DispatchSpy())

    with pytest.raises(CycloptsError):
        app(["--version"], exit_on_error=False, print_error=False)
    capsys.readouterr()


def test_service_app_has_no_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """Service sub-apps are constructed like the root: no ``--version`` either."""
    spy = DispatchSpy()
    app = create_app([jira_get_issue_spec()], spy)

    with pytest.raises(CycloptsError):
        app(["jira", "--version"], exit_on_error=False, print_error=False)
    assert spy.calls == []
    capsys.readouterr()


def test_repeated_list_flag_binds_list(capsys: pytest.CaptureFixture[str]) -> None:
    """Repeatable list flags: ``--flag a --flag b`` -> ``["a", "b"]`` (a comma
    inside one flag stays one element). Real param: ``read_users`` on
    ``confluence set-page-restrictions``."""
    page_id = ToolParam(name="page_id", type=str, required=True, default=None)
    read_users = ToolParam(name="read_users", type=list, required=False, default=None)
    spec = ToolSpec(
        tool_name="confluence_set_page_restrictions",
        service="confluence",
        command_name="set-page-restrictions",
        description="Set page restrictions.",
        params=(page_id, read_users),
    )
    spy = DispatchSpy()
    app = create_app([spec], spy)

    invoke(app, ["confluence", "set-page-restrictions", "--page-id", "9"])
    assert spy.calls[-1] == ("confluence_set_page_restrictions", {"page_id": "9"})

    invoke(
        app,
        [
            "confluence",
            "set-page-restrictions",
            "--page-id",
            "9",
            "--read-users",
            "alice",
            "--read-users",
            "bob",
        ],
    )
    assert spy.calls[-1] == (
        "confluence_set_page_restrictions",
        {"page_id": "9", "read_users": ["alice", "bob"]},
    )
    capsys.readouterr()


PRIME_JIRA_ENV = {
    "JIRA_URL": "https://corp.atlassian.net",
    "JIRA_USERNAME": "you@corp.com",
    "JIRA_API_TOKEN": "secret",
}
PRIME_CONFLUENCE_ENV = {
    "CONFLUENCE_URL": "https://wiki.internal",
    "CONFLUENCE_PERSONAL_TOKEN": "pat-secret",
}
PRIME_BOTH_ENV = {**PRIME_JIRA_ENV, **PRIME_CONFLUENCE_ENV}


@pytest.fixture
def prime_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Hermetic cwd/HOME for prime tests (override lookup touches both).

    Returns ``(cwd, home)`` so tests can place candidate PRIME.md files.
    """
    monkeypatch.delenv("ATLI_PRIME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")
    return tmp_path / "cwd", tmp_path / "home"


def test_prime_prints_primer(
    prime_dirs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    app = create_prime_app(PRIME_BOTH_ENV, None, None)
    invoke(app, ["prime"])
    out = capsys.readouterr().out
    assert "# atli — Jira & Confluence CLI" in out
    assert "Configured: jira, confluence" in out
    assert "atli jira get-issue --issue-key PROJ-1" in out
    assert 'atli confluence search --query "deploy"' in out


def test_prime_hook_json_wraps_primer(
    prime_dirs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    app = create_prime_app(PRIME_BOTH_ENV, "work", Path("/x/config.toml"))
    invoke(app, ["prime", "--hook-json"])
    out = capsys.readouterr().out
    assert out.count("\n") == 1  # one JSON line plus print's newline
    payload = json.loads(out)
    assert "# atli — Jira & Confluence CLI" in (
        payload["hookSpecificOutput"]["additionalContext"]
    )


def test_prime_silent_when_unconfigured(
    prime_dirs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    app = create_prime_app({}, None, None)
    invoke(app, ["prime"])
    assert capsys.readouterr().out == ""


def test_prime_hook_json_empty_when_unconfigured(
    prime_dirs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    app = create_prime_app({}, None, None)
    invoke(app, ["prime", "--hook-json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["hookSpecificOutput"]["additionalContext"] == ""


def test_prime_override_beats_silence(
    prime_dirs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    cwd, _ = prime_dirs
    override = cwd / ".atli" / "PRIME.md"
    override.parent.mkdir()
    override.write_text("TEAM PRIME OVERRIDE\n")
    app = create_prime_app({}, None, None)

    invoke(app, ["prime"])
    assert capsys.readouterr().out.splitlines() == ["TEAM PRIME OVERRIDE"]

    invoke(app, ["prime", "--hook-json"])
    payload = json.loads(capsys.readouterr().out)
    assert (
        payload["hookSpecificOutput"]["additionalContext"] == "TEAM PRIME OVERRIDE"
    )

    invoke(app, ["prime", "--export"])
    out = capsys.readouterr().out
    assert "Configured: (none)" in out
    assert "TEAM PRIME OVERRIDE" not in out


def test_prime_atli_prime_missing_raises(
    prime_dirs: tuple[Path, Path],
) -> None:
    app = create_prime_app({"ATLI_PRIME": "/no/such/prime.md"}, None, None)
    with pytest.raises(ConfigError):
        invoke(app, ["prime"])


def test_prime_help_documents_flags(capsys: pytest.CaptureFixture[str]) -> None:
    app = create_prime_app({}, None, None)
    with pytest.raises(SystemExit) as excinfo:
        app(["prime", "--help"], exit_on_error=True)
    assert excinfo.value.code in (None, 0)
    out = capsys.readouterr().out
    assert "--hook-json" in out
    assert "--export" in out
