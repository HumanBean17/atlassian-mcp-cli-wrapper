"""Tests for the curated examples corpus rendered into ``<tool> --help``."""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

from mcp_atlassian_cli.examples import EXAMPLES, render_examples

_FLAG_RE = re.compile(r"^--[a-z][a-z0-9-]*$")


def _server_tree(service: str) -> ast.Module:
    """The parsed mcp-atlassian server module defining ``<service>_*`` tools.

    Located through the import system (``find_spec`` — no execution, no
    import side effects), so the guard works in any venv layout and Python
    version, CI included.
    """
    spec = importlib.util.find_spec("mcp_atlassian")
    if spec is None or not spec.submodule_search_locations:
        pytest.fail("mcp_atlassian is not installed; cannot verify the corpus")
    path = Path(spec.submodule_search_locations[0]) / "servers" / f"{service}.py"
    if not path.is_file():
        pytest.fail(f"mcp-atlassian server source not found at {path}")
    return ast.parse(path.read_text(encoding="utf-8"))


def _tool_signature(service: str, function_name: str) -> tuple[set[str], set[str]]:
    """``(all_params, required_params)`` for one server tool, from its AST.

    Required = a parameter with no default (positional defaults align to the
    tail; keyword-only defaults sit in kw_defaults). ``ctx`` is the FastMCP
    context, never a CLI flag.
    """
    for node in ast.walk(_server_tree(service)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            args = node.args
            positional = args.args
            defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
            params = {a.arg for a in positional + args.kwonlyargs} - {"ctx"}
            required = {a.arg for a, d in zip(positional, defaults) if d is None and a.arg != "ctx"}
            required |= {
                a.arg for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is None
            }
            return params, required
    pytest.fail(f"no function '{function_name}' in mcp_atlassian.servers.{service}")


def _flags(line: str) -> set[str]:
    """The snake_case param names behind every ``--kebab`` flag in a line."""
    return {
        token[2:].replace("-", "_")
        for token in line.split()
        if token.startswith("--")
    }


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


def test_corpus_flags_exist_on_their_tool() -> None:
    """Every flag an example mentions is a real parameter of that tool —
    checked against the parsed signature, not a text window (a body local
    must never satisfy this check)."""
    for tool_name, lines in EXAMPLES.items():
        service, _, rest = tool_name.partition("_")
        params, _required = _tool_signature(service, rest)
        for line in lines:
            unknown = _flags(line) - params
            assert not unknown, f"{tool_name}: no such params {sorted(unknown)}"


def test_corpus_examples_satisfy_required_params() -> None:
    """Every example is runnable verbatim: each required param of the tool
    appears as a flag in EVERY example line. (update-page's --title once
    shipped missing — exactly this class of bug.)"""
    for tool_name, lines in EXAMPLES.items():
        service, _, rest = tool_name.partition("_")
        _params, required = _tool_signature(service, rest)
        for line in lines:
            missing = required - _flags(line)
            assert not missing, (
                f"{tool_name}: example '{line}' omits required {sorted(missing)}"
            )
