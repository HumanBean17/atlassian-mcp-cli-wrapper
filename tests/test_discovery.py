"""Tests for tool metadata parsing and name mapping."""

from types import SimpleNamespace

from mcp_atlassian_cli.discovery import (
    ToolParam,
    ToolSpec,
    parse_tool,
    split_service,
    to_kebab,
)


def test_split_service_known():
    assert split_service("jira_get_issue") == ("jira", "get_issue")
    assert split_service("confluence_search") == ("confluence", "search")


def test_split_service_unknown():
    assert split_service("health_check") == (None, "health_check")
    assert split_service("jira") == (None, "jira")


def test_to_kebab():
    assert to_kebab("get_issue") == "get-issue"
    assert to_kebab("search_v2") == "search-v2"
    assert to_kebab("simple") == "simple"


def test_parse_tool_full():
    tool = SimpleNamespace(
        name="jira_get_issue",
        description="Get issue.\nLong detail.",
        inputSchema={
            "type": "object",
            "properties": {
                "issue_key": {
                    "type": "string",
                    "description": "The issue key, e.g. PROJ-123.",
                },
                "compact": {"type": "boolean", "default": False},
                "comment_limit": {"type": "integer", "default": 10},
                "labels": {"type": "array"},
            },
            "required": ["issue_key"],
        },
    )

    spec = parse_tool(tool)

    assert isinstance(spec, ToolSpec)
    assert spec.tool_name == "jira_get_issue"
    assert spec.service == "jira"
    assert spec.command_name == "get-issue"
    assert spec.description == "Get issue.\nLong detail."
    assert [p.name for p in spec.params] == [
        "issue_key",
        "compact",
        "comment_limit",
        "labels",
    ]
    assert [p.type for p in spec.params] == [str, bool, int, list]
    assert [p.required for p in spec.params] == [True, False, False, False]
    assert [p.default for p in spec.params] == [None, False, 10, None]
    assert [p.description for p in spec.params] == [
        "The issue key, e.g. PROJ-123.",
        None,
        None,
        None,
    ]


def test_parse_tool_no_description():
    tool = SimpleNamespace(
        name="health_check",
        description=None,
        inputSchema={"type": "object", "properties": {}},
    )

    spec = parse_tool(tool)

    assert spec.service is None
    assert spec.command_name == "health-check"
    assert spec.description == ""
    assert all(isinstance(p, ToolParam) for p in spec.params)


def test_parse_tool_unknown_type_falls_back_to_str():
    tool = SimpleNamespace(
        name="jira_search",
        description="Search.",
        inputSchema={
            "type": "object",
            "properties": {
                "created_after": {"type": "Datetime"},
                "project": {},
            },
            "required": [],
        },
    )

    spec = parse_tool(tool)

    assert [p.type for p in spec.params] == [str, str]


def test_parse_tool_empty_schema():
    tool = SimpleNamespace(
        name="confluence_ping",
        description="Ping.",
        inputSchema={"type": "object"},
    )

    spec = parse_tool(tool)

    assert spec.params == ()
