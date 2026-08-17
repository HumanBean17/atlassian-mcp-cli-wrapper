"""Entry point wiring: profiles -> env -> build -> dispatch.

Only :mod:`mcp_atlassian_cli.config` is imported at module level. The runner and
builder are imported inside :func:`main` *after* the profile environment has
been applied, because ``mcp_atlassian`` reads its configuration from the
environment at import time — importing anything that reaches it earlier would
freeze the wrong credentials for the whole process.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from cyclopts.exceptions import CycloptsError

from mcp_atlassian_cli import config


def main(
    argv: list[str] | None = None,
    runner_factory: Callable[[], "ToolRunner"] | None = None,
) -> int:
    """Run the atli CLI against ``argv`` and return the process exit code.

    Exit codes: 0 success, 1 tool/server failure, 2 usage or config error.
    ``runner_factory`` exists so tests can inject a stub server; production
    uses the default :class:`~mcp_atlassian_cli.runner.ToolRunner`, which
    imports ``mcp_atlassian`` lazily on first use.
    """
    if argv is None:
        argv = sys.argv[1:]

    try:
        profile_flag, rest_argv = config.extract_profile_flag(argv)
        config_path = config.find_config_file()
        config_data = config.load_config(config_path)
        profile_name = config.resolve_profile_name(
            profile_flag, config_data, os.environ
        )
        if profile_name is not None:
            config.apply_profile(config_data.profiles[profile_name], os.environ)
    except config.ConfigError as error:
        print(error, file=sys.stderr)
        return 2

    from mcp_atlassian_cli.build import create_app
    from mcp_atlassian_cli.runner import (
        ToolCallFailure,
        ToolRunner,
        ToolRunnerError,
    )

    try:
        runner = (runner_factory or ToolRunner)()
        specs = runner.list_tool_specs()
        profiles_text = (
            None
            if config_data.path is None
            else config.describe_profiles(config_data, profile_name)
        )
        app = create_app(specs, runner.call_tool, profiles_text)
        app(rest_argv, exit_on_error=False, print_error=False)
    except CycloptsError as error:
        print(error, file=sys.stderr)
        return 2
    except (ToolCallFailure, ToolRunnerError) as error:
        print(error, file=sys.stderr)
        return 1
    except SystemExit as error:
        # cyclopts 4.22 raises SystemExit(0) on happy-path dispatch (its
        # default result action), not just for --help/--version. A zero (or
        # None) exit after successful dispatch IS success; any other int
        # surfaces as that code. main() always returns an int.
        code = error.code
        return code if isinstance(code, int) else 0
    return 0


def cli() -> None:
    """Console-script wrapper."""
    sys.exit(main())
