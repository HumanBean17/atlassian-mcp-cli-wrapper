"""Tests for the ToolRunner transport seam over the in-memory fastmcp client."""

import io
import json
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_atlassian_cli.discovery import ToolSpec
from mcp_atlassian_cli.runner import (
    ToolCallFailure,
    ToolRunner,
    ToolRunnerError,
    result_to_text,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_result_to_text_text_blocks():
    result = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="a"),
            SimpleNamespace(type="text", text="b"),
        ]
    )
    assert result_to_text(result) == "a\nb"


def test_result_to_text_no_text_blocks():
    result = SimpleNamespace(
        content=[SimpleNamespace(type="image", data="xyz", annotations=None)]
    )
    rendered = result_to_text(result)
    assert json.loads(rendered) == [{"type": "image", "data": "xyz"}]
    assert rendered == json.dumps([{"type": "image", "data": "xyz"}], indent=2)


def test_result_to_text_empty_text_block_is_still_text():
    result = SimpleNamespace(content=[SimpleNamespace(type="text", text="")])
    assert result_to_text(result) == ""


def test_list_tool_specs(stub_app):
    specs = ToolRunner(app=stub_app).list_tool_specs()

    assert len(specs) == 4
    assert all(isinstance(spec, ToolSpec) for spec in specs)
    names = {spec.tool_name for spec in specs}
    assert {"jira_get_issue", "confluence_search"} <= names

    get_issue = next(spec for spec in specs if spec.tool_name == "jira_get_issue")
    by_name = {param.name: param for param in get_issue.params}
    assert by_name["issue_key"].required is True
    assert by_name["compact"].required is False
    assert by_name["compact"].default is False
    assert by_name["compact"].type is bool


def test_call_tool_text(stub_app):
    runner = ToolRunner(app=stub_app)
    assert runner.call_tool("jira_get_issue", {"issue_key": "PROJ-1"}) == (
        "issue PROJ-1 compact=False"
    )
    assert runner.call_tool(
        "jira_get_issue", {"issue_key": "PROJ-1", "compact": True}
    ) == "issue PROJ-1 compact=True"


def test_call_tool_lists(stub_app):
    rendered = ToolRunner(app=stub_app).call_tool(
        "jira_search", {"jql": "assignee=me", "labels": ["a", "b"]}
    )
    assert "labels=['a', 'b']" in rendered


def test_call_tool_error(stub_app):
    runner = ToolRunner(app=stub_app)
    with pytest.raises(ToolCallFailure) as excinfo:
        runner.call_tool("jira_boom", {"message": "x"})
    assert "kaboom from server" in str(excinfo.value)


def test_call_tool_error_is_quiet_on_stderr(stub_app, capsys):
    """A failing tool must surface only as ToolCallFailure — fastmcp's server-side
    rich traceback for the same failure must never reach the CLI's stderr."""
    with pytest.raises(ToolCallFailure):
        ToolRunner(app=stub_app).call_tool("jira_boom", {"message": "x"})
    assert capsys.readouterr().err == ""


def test_root_logger_handlers_never_emit(stub_app):
    """mcp_atlassian.setup_logging installs a WARNING StreamHandler on the ROOT
    logger at import time; the CLI must neutralize it, not just the fastmcp
    logger. A record reaching a root handler here would pollute the CLI's
    stderr contract (deprecation warnings, library ERROR tracebacks)."""
    import logging

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        runner = ToolRunner(app=stub_app)
        runner.list_tool_specs()
        with pytest.raises(ToolCallFailure):
            runner.call_tool("jira_boom", {"message": "x"})
    finally:
        root.removeHandler(handler)

    assert stream.getvalue() == ""


def test_silenced_loggers_after_client(stub_app):
    """After the first client, the root logger is CRITICAL+NullHandler-only and
    fastmcp neither propagates nor has real handlers."""
    import logging

    ToolRunner(app=stub_app).list_tool_specs()

    root = logging.getLogger()
    assert root.level == logging.CRITICAL
    assert [type(handler) for handler in root.handlers] == [logging.NullHandler]
    fastmcp_logger = logging.getLogger("fastmcp")
    assert fastmcp_logger.propagate is False
    assert fastmcp_logger.getEffectiveLevel() == logging.CRITICAL


def test_silencing_happens_after_app_resolution(stub_app):
    """Ordering regression: ``mcp_atlassian.setup_logging`` runs at import time
    and REINSTALLS a WARNING StreamHandler on the root logger, stripping
    whatever was there. ``_client`` must silence AFTER ``self._app`` resolves
    (the first resolution is what imports the library) — silencing earlier is
    simply undone by the import."""
    # Import the real library with stderr pointed at a private sink: the import
    # itself runs setup_logging, which would otherwise write deprecation
    # warnings into the test runner's captured stderr.
    real_stderr = sys.stderr
    library_sink = io.StringIO()
    sys.stderr = library_sink
    try:
        import mcp_atlassian
    finally:
        sys.stderr = real_stderr

    class ReimportingRunner(ToolRunner):
        """Simulates the first ``self._app`` resolution importing the library."""

        @property
        def _app(self) -> Any:
            app = super()._app
            sys.stderr = library_sink
            try:
                mcp_atlassian.setup_logging()
            finally:
                sys.stderr = real_stderr
            return app

    with pytest.raises(ToolCallFailure):
        ReimportingRunner(app=stub_app).call_tool("jira_boom", {"message": "x"})

    assert library_sink.getvalue() == ""


def test_lazy_default_app():
    """Constructing ToolRunner() without an app must not import mcp_atlassian.

    Runs in a fresh interpreter (the only honest check: this test process itself
    imports fastmcp via the stub_app fixture), then asserts two things: the
    import succeeded, and `mcp_atlassian` never entered sys.modules.
    """
    code = (
        "import sys\n"
        "from mcp_atlassian_cli.runner import ToolRunner\n"
        "runner = ToolRunner()\n"
        "assert 'mcp_atlassian' not in sys.modules, 'lazy _app leaked an import'\n"
        "print('lazy-ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "lazy-ok"
    # Checked in the child above; repeating it here would only test whether
    # some *other* test in this process imported mcp_atlassian (some do).


def _fake_installed_dists(monkeypatch, versions: dict[str, str]) -> None:
    """Point ``importlib.metadata`` at a fake environment: name -> version.

    The guidance logic must be tested against pinned environments, not
    whatever this dev venv happens to have installed.
    """
    import importlib.metadata

    def fake_distribution(name):
        if name not in versions:
            raise importlib.metadata.PackageNotFoundError(name)
        return SimpleNamespace(version=versions[name])

    def fake_version(name):
        if name not in versions:
            raise importlib.metadata.PackageNotFoundError(name)
        return versions[name]

    monkeypatch.setattr(importlib.metadata, "distribution", fake_distribution)
    monkeypatch.setattr(importlib.metadata, "version", fake_version)


def _break_provider_import(monkeypatch, reason: str = "simulated broken install") -> None:
    """Make ``from mcp_atlassian.servers.main import main_mcp`` raise ImportError."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "mcp_atlassian.servers.main":
            raise ImportError(reason)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_import_failure_without_provider_names_both_extras(monkeypatch):
    """A failed import with no provider dist surfaces as ToolRunnerError
    (never ImportError), with install guidance for both extras."""
    _fake_installed_dists(monkeypatch, {})
    _break_provider_import(monkeypatch)
    with pytest.raises(ToolRunnerError) as excinfo:
        ToolRunner()._app
    message = str(excinfo.value)
    assert "mcp-atlassian" in message
    # The guidance must name both provider install paths and the choose-one
    # rule — this is the expected path for every bare (provider-less) install.
    assert "mcp-atlassian-cli[atlassian]" in message
    assert "mcp-atlassian-cli[bitbucket]" in message
    assert "cannot coexist" in message
    assert "simulated broken install" in message


def test_import_failure_diagnoses_fastmcp_chimera(monkeypatch):
    """Issue #7: fork + fastmcp 2.x + leftover fastmcp-slim. The guidance must
    prescribe the two-step uninstall, not claim the provider is missing."""
    _fake_installed_dists(
        monkeypatch,
        {
            "mcp-atlassian-with-bitbucket": "1.0.5",
            "fastmcp": "2.14.7",
            "fastmcp-slim": "3.4.7",
        },
    )
    _break_provider_import(
        monkeypatch, "cannot import name 'PrivateKeyJWTClientAuthenticator'"
    )
    with pytest.raises(ToolRunnerError) as excinfo:
        ToolRunner()._app
    message = str(excinfo.value)
    assert "pip uninstall -y fastmcp fastmcp-slim" in message
    assert "mcp-atlassian-cli[bitbucket]" in message
    assert "No mcp-atlassian server" not in message
    assert "PrivateKeyJWTClientAuthenticator" in message


def test_chimera_when_fastmcp_missing_entirely(monkeypatch):
    """fastmcp gone + fastmcp-slim leftover: reinstalling the fork would
    re-create the hybrid, so the same uninstall guidance must fire."""
    _fake_installed_dists(
        monkeypatch,
        {"mcp-atlassian-with-bitbucket": "1.0.5", "fastmcp-slim": "3.4.7"},
    )
    _break_provider_import(monkeypatch)
    with pytest.raises(ToolRunnerError) as excinfo:
        ToolRunner()._app
    assert "pip uninstall -y fastmcp fastmcp-slim" in str(excinfo.value)


def test_coherent_upstream_fastmcp3_is_not_chimera(monkeypatch):
    """Upstream provider with its normal fastmcp 3.x meta + slim pairing: the
    failure is generic — no misleading uninstall-both-fastmcp advice."""
    _fake_installed_dists(
        monkeypatch,
        {
            "mcp-atlassian": "0.23.1",
            "fastmcp": "3.4.7",
            "fastmcp-slim": "3.4.7",
        },
    )
    _break_provider_import(monkeypatch)
    with pytest.raises(ToolRunnerError) as excinfo:
        ToolRunner()._app
    message = str(excinfo.value)
    assert "mcp-atlassian-cli[atlassian]" in message
    assert "pip uninstall -y fastmcp fastmcp-slim" not in message


def test_import_failure_provider_without_slim(monkeypatch):
    """Fork installed, no fastmcp-slim anywhere: the repair must actually
    rewrite the provider's files — a plain reinstall is a pip no-op while
    the dist reads as installed, so the advice uninstalls it first."""
    _fake_installed_dists(monkeypatch, {"mcp-atlassian-with-bitbucket": "1.0.5"})
    _break_provider_import(monkeypatch)
    with pytest.raises(ToolRunnerError) as excinfo:
        ToolRunner()._app
    message = str(excinfo.value)
    assert "pip uninstall -y mcp-atlassian-with-bitbucket" in message
    assert "mcp-atlassian-cli[bitbucket]" in message
    assert "No mcp-atlassian server" not in message


def test_import_failure_with_both_providers(monkeypatch):
    """Both provider dists registered: the shared-mcp_atlassian collision.
    Guidance must name the full uninstall (both providers + fastmcp pair)
    and one reinstall; the fork wins the extra hint."""
    _fake_installed_dists(
        monkeypatch,
        {
            "mcp-atlassian": "0.23.1",
            "mcp-atlassian-with-bitbucket": "1.0.5",
            "fastmcp": "2.14.7",
        },
    )
    _break_provider_import(monkeypatch)
    with pytest.raises(ToolRunnerError) as excinfo:
        ToolRunner()._app
    message = str(excinfo.value)
    assert "pip uninstall -y mcp-atlassian mcp-atlassian-with-bitbucket fastmcp fastmcp-slim" in message
    assert "mcp-atlassian-cli[bitbucket]" in message
    assert "No mcp-atlassian server" not in message


def test_import_failure_missing_server_module(monkeypatch):
    """Issue #7 follow-up: upstream's uninstall deletes the shared
    mcp_atlassian files under the fork, and the extra reinstall is a no-op.
    The message must point at uninstalling the provider dist itself."""
    _fake_installed_dists(
        monkeypatch,
        {"mcp-atlassian-with-bitbucket": "1.0.5", "fastmcp": "2.14.7"},
    )
    _break_provider_import(monkeypatch, "No module named 'mcp_atlassian.servers.main'")
    with pytest.raises(ToolRunnerError) as excinfo:
        ToolRunner()._app
    message = str(excinfo.value)
    assert "pip uninstall -y mcp-atlassian-with-bitbucket" in message
    assert "mcp-atlassian-cli[bitbucket]" in message
    assert "No module named 'mcp_atlassian.servers.main'" in message
