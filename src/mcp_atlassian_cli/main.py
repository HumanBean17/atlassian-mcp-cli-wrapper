"""Entry point for the atli CLI."""

from __future__ import annotations

import sys

import cyclopts

_HELP = "atli - a CLI for Jira and Confluence, powered by mcp-atlassian."


def main(argv: list[str] | None = None) -> int:
    """Dispatch ``argv`` to the atli root application and return the exit code."""
    if argv is None:
        argv = sys.argv[1:]
    app = cyclopts.App(name="atli", help=_HELP)
    try:
        app(argv, exit_on_error=False, print_error=False)
    except cyclopts.CycloptsError as error:
        print(error, file=sys.stderr)
        return 2
    # cyclopts still raises SystemExit(0) for --help/--version even with
    # exit_on_error=False; main() always returns an int.
    except SystemExit as error:
        return int(error.code or 0)
    return 0


def cli() -> None:
    """Console-script wrapper."""
    sys.exit(main())
