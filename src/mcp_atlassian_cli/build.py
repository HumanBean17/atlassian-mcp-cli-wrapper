"""Build a cyclopts command tree from discovered tool specs.

Pure ``ToolSpec`` -> ``cyclopts.App`` wiring: the tool dispatcher is injected,
so this module never imports the runner or anything MCP-related.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from typing import Any

import cyclopts

from mcp_atlassian_cli.discovery import ToolSpec

Dispatch = Callable[[str, dict[str, Any]], str]

_ROOT_HELP = """\
atli - a CLI for Jira and Confluence, powered by mcp-atlassian.

Global flags (parsed before tool discovery, never forwarded):
  --profile NAME   Use a config profile instead of the ambient environment.

Run `atli tools` to list available tools, or `atli <service> <tool> --help`
for a tool's parameters and defaults.
"""


def _first_sentence(description: str) -> str:
    """Return the first line of the description, up to its first ``". "``.

    Real mcp-atlassian descriptions are multi-paragraph docstrings whose first
    sentence usually ends ``".\\n\\n"`` rather than ``". "`` — splitting only on
    ``". "`` would print the whole docstring and its blank lines, wrecking the
    one-line-per-tool table. So: take the first physical line, then the first
    sentence within that line.
    """
    if not description:
        return "(no description)"
    first_line = description.split("\n", 1)[0].rstrip()
    head, sep, _rest = first_line.partition(". ")
    return head + sep if sep else first_line


def _make_handler(spec: ToolSpec, dispatch: Dispatch) -> Callable[..., None]:
    """Build a command function whose signature mirrors ``spec.params``.

    Every parameter is KEYWORD_ONLY (cyclopts binds them from ``--kebab-case``
    flags); required parameters carry no default, optional ones default to the
    schema default so help shows it. cyclopts forwards only explicitly given
    flags to a ``**kwargs`` handler, so absent optionals are filled from the
    schema defaults (then ``None`` values are dropped from the arguments).
    """

    def handler(**kwargs: Any) -> None:
        arguments: dict[str, Any] = {}
        for param in spec.params:
            value = kwargs.get(param.name)
            if value is None and not param.required:
                value = param.default
            if value is not None:
                arguments[param.name] = value
        print(dispatch(spec.tool_name, arguments))

    handler.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        inspect.Parameter(
            name=param.name,
            kind=inspect.Parameter.KEYWORD_ONLY,
            # Optional params with a None default must be annotated ``T | None``:
            # cyclopts validates defaults with pydantic whenever pydantic is in
            # sys.modules (always true here, fastmcp imports it), and
            # ``TypeAdapter(str).validate_python(None)`` raises ValidationError —
            # turning every tool invocation into a usage error (exit 2).
            annotation=param.type
            if param.required or param.default is not None
            else param.type | None,
            default=param.default if not param.required else inspect.Parameter.empty,
        )
        for param in spec.params
    )
    handler.__doc__ = spec.description
    return handler


def _make_tools_command(specs: Sequence[ToolSpec]) -> Callable[..., None]:
    def tools(service: str | None = None) -> None:
        """List available tools, optionally filtered by service."""
        if not specs:
            print(
                "No services configured — set JIRA_URL / CONFLUENCE_URL "
                "or a profile (see atli --help)."
            )
            return None
        listed = [
            spec for spec in specs if service is None or spec.service == service
        ]
        if not listed:
            print(f"No tools for service '{service}'.")
            return None
        rows = [
            (
                spec.service or "-",
                f"{spec.service} {spec.command_name}"
                if spec.service
                else spec.command_name,
                _first_sentence(spec.description),
            )
            for spec in listed
        ]
        service_width = max(len(row[0]) for row in rows)
        path_width = max(len(row[1]) for row in rows)
        for service_name, path, sentence in rows:
            line = f"{service_name:<{service_width}}  {path:<{path_width}}  {sentence}"
            print(line.rstrip())
        return None

    return tools


def _make_profiles_command(profiles_text: str | None) -> Callable[..., None]:
    def profiles() -> None:
        """Show configured profiles."""
        print(profiles_text if profiles_text is not None else "No config file found.")
        return None

    return profiles


def create_app(
    specs: Sequence[ToolSpec],
    dispatch: Dispatch,
    profiles_text: str | None = None,
) -> cyclopts.App:
    """Assemble the ``atli`` root app: built-ins plus one command per spec.

    Assumes no two specs share the same service + command name; if they do,
    the later spec wins silently (cyclopts would otherwise reject the
    duplicate registration).
    """
    app = cyclopts.App(name="atli", help=_ROOT_HELP)
    app.command(_make_tools_command(specs))
    app.command(_make_profiles_command(profiles_text))

    unique: dict[tuple[str | None, str], ToolSpec] = {}
    for spec in specs:
        unique[(spec.service, spec.command_name)] = spec

    service_apps: dict[str, cyclopts.App] = {}
    for spec in unique.values():
        handler = _make_handler(spec, dispatch)
        if spec.service is None:
            app.command(handler, name=spec.command_name)
            continue
        service_app = service_apps.setdefault(
            spec.service, cyclopts.App(name=spec.service)
        )
        service_app.command(handler, name=spec.command_name)
    for service_app in service_apps.values():
        app.command(service_app)
    return app
