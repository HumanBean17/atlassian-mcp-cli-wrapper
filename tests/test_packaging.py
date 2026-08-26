"""Tests for the provider-selection packaging contract.

The base install carries NO provider — a bare install is inert and the first
tool command fails with guidance naming the extras. The provider comes from
exactly one of two mutually exclusive extras: ``[atlassian]`` (upstream,
Jira+Confluence) or ``[bitbucket]`` (the fork, which adds Bitbucket). Both
dists ship the same ``mcp_atlassian`` import package with conflicting fastmcp
pins, so no dependency may appear in the base ``dependencies`` at all: pip
cannot suppress a base dependency via an extra marker (it evaluates base deps
with ``extra=""`` even when resolving ``pkg[extra]``), so any base provider
pin would make ``[bitbucket]`` uninstallable. CI additionally verifies the
resolved dists on pip and uv for all three install shapes.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ATLASSIAN_EXTRA = ["mcp-atlassian>=0.23,<0.24"]
BITBUCKET_EXTRA = ["mcp-atlassian-with-bitbucket>=1.0.5,<1.1"]


def _pyproject() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_base_dependencies_carry_no_provider() -> None:
    dependencies = _pyproject()["project"]["dependencies"]
    assert "cyclopts>=4.22,<5" in dependencies
    offenders = [
        dep
        for dep in dependencies
        if dep.startswith(("mcp-atlassian", "mcp_atlassian"))
    ]
    assert not offenders, (
        f"provider pin(s) {offenders} in base dependencies would conflict "
        "with the [bitbucket] extra — a provider may only ship via an extra "
        "(pip cannot suppress a base dependency via an extra marker)"
    )


def test_extras_are_exactly_the_two_providers() -> None:
    extras = _pyproject()["project"]["optional-dependencies"]
    assert extras == {"atlassian": ATLASSIAN_EXTRA, "bitbucket": BITBUCKET_EXTRA}
