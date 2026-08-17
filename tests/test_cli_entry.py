"""Tests for the atli CLI entry point."""

import subprocess
from pathlib import Path

import mcp_atlassian_cli

REPO_ROOT = Path(__file__).resolve().parent.parent
ATLI = REPO_ROOT / ".venv" / "bin" / "atli"


def test_version_attribute():
    assert mcp_atlassian_cli.__version__ == "0.1.0"


def test_help_subprocess():
    result = subprocess.run(
        [str(ATLI), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "atli" in result.stdout
