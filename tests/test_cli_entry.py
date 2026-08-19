"""Tests for the atli CLI entry point."""

import re
import subprocess
from pathlib import Path

import mcp_atlassian_cli

REPO_ROOT = Path(__file__).resolve().parent.parent
ATLI = REPO_ROOT / ".venv" / "bin" / "atli"


def test_version_attribute():
    # Single source of truth is __version__; the release workflow checks it
    # against the git tag, so here we only assert it's valid semver.
    assert re.fullmatch(r"\d+\.\d+\.\d+", mcp_atlassian_cli.__version__)


def test_help_subprocess():
    result = subprocess.run(
        [str(ATLI), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "atli" in result.stdout
