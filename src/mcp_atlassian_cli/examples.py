"""Curated example invocations rendered into ``<tool> --help``.

The tool schemas cannot teach two things an agent needs: which optional flags
a tool actually requires (``update-page`` marks nothing required — the server
demands ``--page-id``), and the canonical value formats (JQL syntax, relative
dates, ``accountid:…`` identifiers). A small, hand-checked corpus of real
invocations teaches both, exactly where an agent looks before calling.

Curation rules:

- Examples teach identifiers and formats; prose belongs to the schema
  descriptions that already render alongside.
- Long-content examples use ``@file`` (``--content @page.md``), teaching the
  expansion mechanism by demonstration.
- The corpus stays small and high-traffic; tools without an entry render
  exactly as before (no drift risk for the long tail).
- ``tests/test_examples.py`` cross-checks every entry against the installed
  mcp-atlassian server source — after a version bump, fix the corpus, not the
  tests.
"""

from __future__ import annotations

EXAMPLES: dict[str, tuple[str, ...]] = {
    "jira_search": (
        "atli jira search --jql 'assignee = currentUser() AND updated > -7d' --limit 20",
    ),
    "jira_get_issue": (
        "atli jira get-issue --issue-key PROJ-123 --fields summary,status,assignee",
    ),
    "jira_create_issue": (
        "atli jira create-issue --project-key OPS --summary 'Deploy failed' "
        "--issue-type Bug --description @incident.md",
    ),
    "jira_add_comment": (
        "atli jira add-comment --issue-key PROJ-123 --body @comment.md",
    ),
    "jira_transition_issue": (
        "atli jira transition-issue --issue-key PROJ-123 --transition-id 31",
    ),
    "jira_get_user_profile": (
        "atli jira get-user-profile --user-identifier 'accountid:5b10ac8d82e05b22cc7d4ef5'",
    ),
    "confluence_search": (
        "atli confluence search --query 'deploy runbook' --limit 10",
    ),
    "confluence_get_page": (
        "atli confluence get-page --page-id 123456789",
        "atli confluence get-page --title 'Runbook' --space-key OPS",
    ),
    "confluence_create_page": (
        "atli confluence create-page --space-key OPS --title 'Runbook' --content @page.md",
    ),
    "confluence_update_page": (
        "atli confluence update-page --page-id 123456789 --content @page.md",
    ),
    "confluence_add_comment": (
        "atli confluence add-comment --page-id 123456789 --body @comment.md",
    ),
}


def render_examples(tool_name: str) -> str | None:
    """The ``Example invocations:`` docstring block for ``tool_name``, or ``None``.

    The header is deliberately NOT ``Examples:`` — cyclopts parses docstrings
    with ``docstring_parser``, which routes a numpydoc ``Examples:`` section
    into an attribute cyclopts never renders. A generic header survives as
    the long description; markdown bullets, one per line, are the one
    construct that survives rich's re-flowing.
    """
    lines = EXAMPLES.get(tool_name)
    if not lines:
        return None
    return "Example invocations:\n" + "\n".join(f"- {line}" for line in lines)
