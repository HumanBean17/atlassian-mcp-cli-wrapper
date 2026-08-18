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

from cyclopts.exceptions import CycloptsError, UnknownOptionError

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

    A closed stdout (``atli <tool> | head``) is success, not a traceback: the
    standard EPIPE idiom redirects stdout to ``os.devnull`` so the interpreter's
    shutdown flush has nowhere to complain, and 0 is returned.
    """
    if argv is None:
        argv = sys.argv[1:]

    try:
        return _run(argv, runner_factory)
    except BrokenPipeError:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 0


def _run(
    argv: list[str],
    runner_factory: Callable[[], "ToolRunner"] | None,
) -> int:
    """Profile resolution, app build, and dispatch (see :func:`main`)."""
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

    # `prime` is the SessionStart-hook command: it must never pay the
    # mcp-atlassian import, so it dispatches before the runner is ever built.
    # Accepted edge: a hypothetical prefix-less server tool named `prime`
    # would be shadowed (all real tools are jira_/confluence_-prefixed).
    if rest_argv[:1] == ["prime"]:
        from mcp_atlassian_cli.build import create_prime_app

        try:
            app = create_prime_app(os.environ, profile_name, config_data.path)
            app(rest_argv, exit_on_error=False, print_error=False)
        except CycloptsError as error:
            print(error, file=sys.stderr)
            return 2
        except config.ConfigError as error:
            print(error, file=sys.stderr)
            return 2
        except SystemExit as error:
            code = error.code
            return code if isinstance(code, int) else 0
        return 0

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
        message = str(error)
        # The placement hint fires ONLY when cyclopts itself classifies the
        # token as an unknown option whose keyword is exactly ``--profile``.
        # Matching the message substring would over-fire: other CycloptsError
        # subclasses embed raw user values (a coercion failure on
        # ``--comment-limit "5 --profile=x"``, an unused stray token) that
        # merely contain the substring — steering the agent toward flag
        # placement when its real problem is a bad value.
        if isinstance(error, UnknownOptionError) and error.token.keyword == "--profile":
            message = f"{message} {config.PROFILE_USAGE}"
        print(message, file=sys.stderr)
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
