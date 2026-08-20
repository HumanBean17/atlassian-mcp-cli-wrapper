"""Tests for the curated examples corpus rendered into ``<tool> --help``."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mcp_atlassian_cli.examples import EXAMPLES, render_examples

_FLAG_RE = re.compile(r"^--[a-z][a-z0-9-]*$")


def _server_source(service: str) -> str:
    """The installed mcp-atlassian server module defining ``<service>_*`` tools."""
    path = (
        Path(__file__).resolve().parents[1]
        / ".venv" / "lib" / "python3.11" / "site-packages"
        / "mcp_atlassian" / "servers" / f"{service}.py"
    )
    if not path.is_file():
        pytest.fail(f"mcp-atlassian server source not found at {path}")
    return path.read_text(encoding="utf-8")


def test_render_examples_known_tool() -> None:
    block = render_examples("jira_search")
    assert block is not None
    assert "Example invocations:" in block
    assert "--jql" in block
    assert "currentUser()" in block


def test_render_examples_two_lines_for_get_page() -> None:
    block = render_examples("confluence_get_page")
    assert block is not None
    assert "--page-id" in block
    assert "--title" in block


def test_render_examples_unknown_tool_is_none() -> None:
    assert render_examples("nonexistent_tool") is None


def test_corpus_keys_are_service_prefixed() -> None:
    assert EXAMPLES
    for tool_name in EXAMPLES:
        assert tool_name.startswith(("jira_", "confluence_")), tool_name


def test_corpus_examples_are_well_formed() -> None:
    """Every line is an ``atli`` invocation mentioning only kebab-case flags —
    a schema-name typo (``--issue_key``) must never ship in an example."""
    for tool_name, lines in EXAMPLES.items():
        for line in lines:
            assert line.startswith("atli "), (tool_name, line)
            for token in line.split():
                if token.startswith("--"):
                    assert _FLAG_RE.match(token), (tool_name, token)


def test_corpus_tools_exist_in_mcp_atlassian() -> None:
    """Each corpus key maps to a real server tool: the snake_case function
    name (service prefix stripped) must appear in the installed server
    source. If this fails after a version bump, fix the corpus, not the test."""
    sources = {}
    for tool_name in EXAMPLES:
        service, _, rest = tool_name.partition("_")
        if service not in sources:
            sources[service] = _server_source(service)
        function_name = f"def {rest}("
        assert function_name in sources[service], (
            f"{tool_name}: no function '{rest}' in mcp_atlassian.servers.{service}"
        )


def test_corpus_flags_exist_on_their_tool() -> None:
    """Every flag an example mentions is a parameter of the real tool (the
    function's signature in the server source must contain the snake_case
    name). Guards the corpus against plausible-but-wrong flag names."""
    for tool_name, lines in EXAMPLES.items():
        service, _, rest = tool_name.partition("_")
        source = _server_source(service)
        start = source.index(f"def {rest}(")
        signature = source[start : source.index("):", start) + 1]
        for line in lines:
            for token in line.split():
                if token.startswith("--"):
                    param = token[2:].replace("-", "_")
                    assert re.search(
                        rf"\b{param}\b", signature
                    ), f"{tool_name}: --{token[2:]} is not a parameter of '{rest}'"
