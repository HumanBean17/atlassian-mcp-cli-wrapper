"""Tests for the provider-selection packaging contract.

The base install must carry upstream mcp-atlassian ONLY when no extra is
requested; the ``[bitbucket]`` extra swaps in the fork instead. That contract
lives entirely in one dependency marker — a regression to an unconditional
pin would silently break fork users' installs again, so it is guarded here at
the metadata level (CI additionally verifies the resolved dists on pip and uv).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

MARKER_DEP = 'mcp-atlassian>=0.23,<0.24; extra != "bitbucket"'
BITBUCKET_EXTRA = ["mcp-atlassian-with-bitbucket>=1.0.5,<1.1"]


def _pyproject() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_base_dependencies_carry_the_provider_marker() -> None:
    dependencies = _pyproject()["project"]["dependencies"]
    assert "cyclopts>=4.22,<5" in dependencies
    assert MARKER_DEP in dependencies


def test_mcp_atlassian_dep_never_unconditional() -> None:
    dependencies = _pyproject()["project"]["dependencies"]
    offenders = [
        dep
        for dep in dependencies
        if dep.startswith("mcp-atlassian") and dep != MARKER_DEP
    ]
    assert not offenders, (
        f"unconditional provider pin(s) {offenders} would conflict with "
        "the [bitbucket] extra — restore the 'extra != \"bitbucket\"' marker"
    )


def test_bitbucket_extra_pins_the_fork() -> None:
    extras = _pyproject()["project"]["optional-dependencies"]
    assert extras["bitbucket"] == BITBUCKET_EXTRA
