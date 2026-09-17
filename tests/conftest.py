"""Shared fixtures for atli tests."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest


def isolate_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """Point ``Path.home()`` at ``home`` on every platform.

    ``Path.home()`` resolves through ``USERPROFILE`` on Windows and ``HOME``
    on POSIX; tests that redirect it must set both or their home-relative
    assertions silently read the real profile on Windows runners.
    """
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


@pytest.fixture
def stub_app() -> Any:
    """A FastMCP app shaped like mcp-atlassian's real one.

    Two sub-apps mounted with ``jira``/``confluence`` namespaces, so tool names
    come out flat-prefixed exactly as the real server produces them:
    ``jira_get_issue``, ``jira_search``, ``jira_boom``, ``confluence_search``.

    ``boom`` also logs through loggers that PROPAGATE TO ROOT before raising,
    exactly as the real server does (``mcp_atlassian.setup_logging`` installs a
    WARNING-level StreamHandler on the root logger): a deprecation-style
    WARNING on every call and a library ERROR on failure. The CLI must keep all
    of that off stderr while still reporting the failure itself.
    """
    from fastmcp import FastMCP

    jira = FastMCP("jira")

    @jira.tool
    def get_issue(issue_key: str, compact: bool = False) -> str:
        """Get a Jira issue by key."""
        return f"issue {issue_key} compact={compact}"

    @jira.tool
    def search(jql: str, labels: list[str] | None = None, limit: int = 5) -> str:
        """Search issues with JQL."""
        logging.getLogger("mcp_atlassian.utils.toolsets").warning(
            "TOOLSETS is not set — deprecation-style warning from the library."
        )
        return f"search {jql} labels={labels} limit={limit}"

    @jira.tool
    def boom(message: str) -> str:
        """Always fails; fastmcp converts the raise into a tool error."""
        logging.getLogger("mcp_atlassian.utils.toolsets").warning(
            "TOOLSETS is not set — deprecation-style warning from the library."
        )
        logging.getLogger("mcp.server").error(
            "server-side error traceback for kaboom"
        )
        raise ValueError("kaboom from server")

    confluence = FastMCP("confluence")

    @confluence.tool
    def search(query: str, limit: int = 5) -> str:
        """Search Confluence pages."""
        return f"found: {query} limit={limit}"

    app = FastMCP("stub")
    # Positional namespace: the keyword is `namespace=` on fastmcp 3.x but
    # `prefix=` on 2.x (the [bitbucket] provider's pin) — the positional form
    # is the one both majors share, keeping this stub provider-agnostic.
    app.mount(jira, "jira")
    app.mount(confluence, "confluence")
    return app
