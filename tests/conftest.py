"""Shared fixtures for atli tests."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def stub_app() -> Any:
    """A FastMCP app shaped like mcp-atlassian's real one.

    Two sub-apps mounted with ``jira``/``confluence`` namespaces, so tool names
    come out flat-prefixed exactly as the real server produces them:
    ``jira_get_issue``, ``jira_search``, ``jira_boom``, ``confluence_search``.
    """
    from fastmcp import FastMCP

    jira = FastMCP("jira")

    @jira.tool
    def get_issue(issue_key: str, compact: bool = False) -> str:
        """Get a Jira issue by key."""
        return f"issue {issue_key} compact={compact}"

    @jira.tool
    def search(jql: str, labels: list[str] | None = None) -> str:
        """Search issues with JQL."""
        return f"search {jql} labels={labels}"

    @jira.tool
    def boom(message: str) -> str:
        """Always fails; fastmcp converts the raise into a tool error."""
        raise ValueError("kaboom from server")

    confluence = FastMCP("confluence")

    @confluence.tool
    def search(query: str) -> str:
        """Search Confluence pages."""
        return f"found: {query}"

    app = FastMCP("stub")
    app.mount(jira, namespace="jira")
    app.mount(confluence, namespace="confluence")
    return app
