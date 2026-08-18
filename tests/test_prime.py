"""Tests for prime.py: detection, override resolution, rendering, envelope."""

from __future__ import annotations

import pytest

from mcp_atlassian_cli.prime import detect_services

JIRA_CLOUD = {
    "JIRA_URL": "https://corp.atlassian.net",
    "JIRA_USERNAME": "you@corp.com",
    "JIRA_API_TOKEN": "secret",
}
CONFLUENCE_PAT = {
    "CONFLUENCE_URL": "https://wiki.internal",
    "CONFLUENCE_PERSONAL_TOKEN": "pat-secret",
}


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        (JIRA_CLOUD, (True, False)),
        (
            {
                "JIRA_URL": "https://jira.internal",
                "JIRA_PERSONAL_TOKEN": "pat",
            },
            (True, False),
        ),
        (
            {"JIRA_URL": "https://jira.internal", "JIRA_CLIENT_CERT": "/c.pem"},
            (True, False),
        ),
        (CONFLUENCE_PAT, (False, True)),
        ({**JIRA_CLOUD, **CONFLUENCE_PAT}, (True, True)),
        ({"JIRA_URL": "https://corp.atlassian.net"}, (False, False)),
        (
            {
                "JIRA_URL": "https://corp.atlassian.net",
                "JIRA_USERNAME": "you@corp.com",
            },
            (False, False),
        ),
        (
            {
                "JIRA_URL": "",
                "JIRA_USERNAME": "you@corp.com",
                "JIRA_API_TOKEN": "secret",
            },
            (False, False),
        ),
        ({}, (False, False)),
    ],
)
def test_detect_services(environ: dict[str, str], expected: tuple[bool, bool]) -> None:
    assert detect_services(environ) == expected
