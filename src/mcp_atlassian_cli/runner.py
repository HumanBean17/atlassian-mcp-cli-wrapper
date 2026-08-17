"""ToolRunner: the transport seam between the CLI and a FastMCP app.

Every fastmcp/mcp_atlassian import happens inside a method or property, never
at module import time: ``mcp_atlassian`` reads its configuration from the
environment when it is imported, so the caller must have applied the profile
environment before ``ToolRunner._app`` first runs.

Each public call owns one ``asyncio.run``; loops and sessions are never shared
between ``list_tool_specs`` and ``call_tool``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mcp_atlassian_cli.discovery import ToolSpec, parse_tool

_PIN_OR_UPDATE = (
    "Could not start the mcp-atlassian tool server. "
    "Pin mcp-atlassian>=0.23,<0.24 (which brings fastmcp 3.4.x), "
    "or update this CLI."
)


class ToolRunnerError(Exception):
    """Transport or compatibility failure.

    The fix is outside this call: pin compatible mcp-atlassian/fastmcp versions
    or update the CLI.
    """


class ToolCallFailure(Exception):
    """A tool or server error; the message is the server's error text verbatim."""


def result_to_text(result: Any) -> str:
    """Render a ``CallToolResult``-shaped object to text.

    Text blocks (``.type == "text"``) are joined with newlines. Results with no
    text blocks (images, embedded resources, ...) are rendered as indented JSON,
    one dict per block with the block's non-``None`` fields.
    """
    content = list(getattr(result, "content", ()) or ())
    texts = [block.text for block in content if getattr(block, "type", None) == "text"]
    if texts:
        return "\n".join(texts)
    return json.dumps([_block_fields(block) for block in content], indent=2, default=str)


def _block_fields(block: Any) -> dict[str, Any]:
    """One content block as a dict of its non-``None`` fields."""
    model_dump = getattr(block, "model_dump", None)
    if model_dump is not None:
        return dict(model_dump(exclude_none=True))
    return {key: value for key, value in vars(block).items() if value is not None}


def _silence_server_logging() -> None:
    """Keep the server libraries' log records off the CLI's stderr.

    fastmcp logs a rich traceback to stderr for every tool failure — the same
    failure the CLI already reports through :class:`ToolCallFailure`. And
    ``mcp_atlassian``'s ``setup_logging`` (run at import time) installs a
    WARNING-level ``StreamHandler`` on the ROOT logger, so its deprecation
    warnings and error tracebacks propagate straight to the CLI's stderr. The
    CLI itself logs nothing, so letting either through would corrupt its stderr
    contract (one clean error line per failure).

    The ``NullHandler`` on each silenced logger matters as much as the clearing:
    with no handler found, ``logging`` falls back to its ``lastResort`` stderr
    handler and the record (traceback included) leaks out anyway.
    """
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(logging.NullHandler())
    root.setLevel(logging.CRITICAL)

    logger = logging.getLogger("fastmcp")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    logger.setLevel(logging.CRITICAL)


class ToolRunner:
    """Synchronous facade over an in-memory fastmcp client session."""

    def __init__(self, app: Any | None = None) -> None:
        self._app_instance: Any | None = app

    @property
    def _app(self) -> Any:
        """The FastMCP app to talk to, imported lazily on first use."""
        if self._app_instance is None:
            try:
                from mcp_atlassian.servers.main import main_mcp
            except (ImportError, AttributeError, TypeError) as error:
                raise ToolRunnerError(f"{_PIN_OR_UPDATE} ({error})") from error
            self._app_instance = main_mcp
        return self._app_instance

    def _client(self) -> Any:
        """An unopened in-memory client for ``self._app``.

        The silencing must come AFTER ``self._app`` resolves: the first
        resolution imports ``mcp_atlassian``, whose ``setup_logging`` installs a
        fresh WARNING StreamHandler on the root logger — silencing before the
        import would just be undone by it.
        """
        from fastmcp import Client

        app = self._app
        _silence_server_logging()
        return Client(app)

    def list_tool_specs(self) -> list[ToolSpec]:
        """List the app's tools as parsed specs (one ``asyncio.run``)."""

        async def run() -> list[ToolSpec]:
            async with self._client() as client:
                tools = await client.list_tools()
            return [parse_tool(tool) for tool in tools]

        try:
            return asyncio.run(run())
        except ToolRunnerError:
            raise
        except Exception as error:
            raise ToolRunnerError(f"{_PIN_OR_UPDATE} ({error})") from error

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call one tool and render its result (one ``asyncio.run``)."""

        async def run() -> str:
            async with self._client() as client:
                result = await client.call_tool(name, arguments)
            return result_to_text(result)

        try:
            from fastmcp.exceptions import ToolError  # noqa: F401  (except clause)

            return asyncio.run(run())
        except ToolRunnerError:
            raise
        except ToolError as error:
            raise ToolCallFailure(str(error)) from error
        except Exception as error:
            raise ToolRunnerError(f"{_PIN_OR_UPDATE} ({error})") from error
