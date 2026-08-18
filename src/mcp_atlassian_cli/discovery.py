"""Tool metadata parsing and name mapping.

Pure functions over MCP ``Tool``-shaped objects: no server or CLI imports.
"""

from dataclasses import dataclass
from typing import Any

SERVICE_PREFIXES: frozenset[str] = frozenset({"jira", "confluence"})

_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
}


@dataclass(frozen=True)
class ToolParam:
    """One schema property of a tool."""

    name: str
    type: type
    required: bool
    default: Any
    description: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    """A parsed tool, ready to become a CLI subcommand."""

    tool_name: str
    service: str | None
    command_name: str
    description: str
    params: tuple[ToolParam, ...]


def split_service(tool_name: str) -> tuple[str | None, str]:
    """Split ``tool_name`` at the first ``_`` into (service, rest).

    The left side counts as a service only when it is in ``SERVICE_PREFIXES``;
    otherwise the name comes back whole with ``None``.
    """
    head, sep, rest = tool_name.partition("_")
    if sep and head in SERVICE_PREFIXES:
        return head, rest
    return None, tool_name


def to_kebab(name: str) -> str:
    """Replace ``_`` with ``-``."""
    return name.replace("_", "-")


def parse_tool(tool: Any) -> ToolSpec:
    """Build a ``ToolSpec`` from an MCP ``Tool``-shaped object."""
    schema = tool.inputSchema
    properties = schema.get("properties") or {}
    required = schema.get("required") or ()

    params = tuple(
        ToolParam(
            name=name,
            type=_TYPE_MAP.get(prop.get("type"), str),
            required=name in required,
            default=None if name in required else prop.get("default"),
            # JSON Schema mandates a string; a malformed future schema must
            # degrade to "no description" here, not surface as a rendering
            # error inside cyclopts' help path.
            description=raw_description
            if isinstance(raw_description := prop.get("description"), str)
            else None,
        )
        for name, prop in properties.items()
    )

    service, rest = split_service(tool.name)
    return ToolSpec(
        tool_name=tool.name,
        service=service,
        command_name=to_kebab(rest),
        description=tool.description or "",
        params=params,
    )
