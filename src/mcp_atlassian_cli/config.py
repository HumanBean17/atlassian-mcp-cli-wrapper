"""Profile configuration: discovery, env application, and argv pre-parse.

Profiles are named sets of environment variables (TOML) that select a Jira /
Confluence instance. The profile's env vars must land in ``os.environ`` BEFORE
``mcp_atlassian`` is first imported, because that library reads its config from
the environment at import time. That is why ``--profile`` is stripped from argv
by :func:`extract_profile_flag` before the real CLI parser ever runs, instead of
being an ordinary command-line flag.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path

SERVICE_ENV_PREFIXES: tuple[str, ...] = ("JIRA_", "CONFLUENCE_", "MCP_ATLASSIAN_")
"""Environment-variable prefixes owned by mcp-atlassian, one per service.

Applying a profile replaces the environment per prefix: if the profile defines
any variable with a prefix, every ambient variable with that prefix is dropped
first, so stale credentials from another instance cannot leak through.
"""

CROSS_SERVICE_CREDENTIAL_KEYS: tuple[str, ...] = (
    "ATLASSIAN_OAUTH_ENABLE",
    "ATLASSIAN_OAUTH_CLIENT_ID",
    "ATLASSIAN_OAUTH_CLIENT_SECRET",
    "ATLASSIAN_OAUTH_REDIRECT_URI",
    "ATLASSIAN_OAUTH_SCOPE",
    "ATLASSIAN_OAUTH_CLOUD_ID",
    "ATLASSIAN_OAUTH_ACCESS_TOKEN",
    "ATLASSIAN_EXTERNAL_AUTH_ENABLE",
)
"""Credential keys the library reads across BOTH services (no JIRA_/
CONFLUENCE_ prefix), preferring them over username/api-token.

Mirrors mcp_atlassian's env reads as of 0.23.0: ``utils/oauth.py`` reads the
seven ``ATLASSIAN_OAUTH_*`` names (``OAuthConfig.from_env`` and
``BYOAccessTokenOAuthConfig.from_env``), and ``utils/environment.py`` plus
``jira/config.py`` / ``confluence/config.py`` read
``ATLASSIAN_EXTERNAL_AUTH_ENABLE``. An ambient value here would be sent to a
profile-chosen host, so :func:`apply_profile` clears them too.
"""

_PROFILE_USAGE = "Use --profile=NAME or --profile NAME (before the subcommand)."


class ConfigError(Exception):
    """A user-fixable configuration problem (bad file, unknown profile, bad flag)."""


@dataclass
class AtliConfig:
    """A parsed atli config file."""

    path: Path | None
    default_profile: str | None
    profiles: dict[str, dict[str, str]]


def find_config_file() -> Path | None:
    """Return the config file to load, or ``None`` when there is none.

    Checks, in order: ``$ATLI_CONFIG`` (a set-but-missing path is an error the
    user must fix), ``./.atli.toml`` in the process CWD, then
    ``~/.config/atli/config.toml``. The first existing file wins.
    """
    explicit = os.environ.get("ATLI_CONFIG")
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        raise ConfigError(
            f"ATLI_CONFIG is set to '{explicit}', but that file does not exist. "
            "Point ATLI_CONFIG at an existing TOML config file, or unset it."
        )
    candidates = (
        Path.cwd() / ".atli.toml",
        Path.home() / ".config" / "atli" / "config.toml",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Path | None) -> AtliConfig:
    """Parse the config file at ``path``; ``None`` yields an empty config.

    Top-level ``default_profile`` is a string; each ``[profiles.<name>]`` table
    becomes ``profiles[name]`` with every value coerced to ``str`` (booleans as
    ``"true"``/``"false"``, integers as decimal strings).
    """
    if path is None:
        return AtliConfig(path=None, default_profile=None, profiles={})

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as error:
        raise ConfigError(f"Could not read config file '{path}': {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"Could not parse config file '{path}': {error}") from error

    default_profile = data.get("default_profile")
    if default_profile is not None:
        default_profile = str(default_profile)

    raw_profiles = data.get("profiles", {})
    if not isinstance(raw_profiles, dict):
        raise ConfigError(
            f"Invalid config file '{path}': 'profiles' must be a table of "
            "[profiles.<name>] tables."
        )

    profiles: dict[str, dict[str, str]] = {}
    for name, values in raw_profiles.items():
        if not isinstance(values, dict):
            raise ConfigError(
                f"Invalid config file '{path}': profile '{name}' must be a "
                "[profiles.{name}] table."
            )
        profiles[name] = {
            key: _coerce_value(path, name, key, value) for key, value in values.items()
        }

    return AtliConfig(path=path, default_profile=default_profile, profiles=profiles)


def _coerce_value(path: Path, profile: str, key: str, value: object) -> str:
    """Coerce one TOML value to the string form used for environment variables."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    raise ConfigError(
        f"Invalid config file '{path}': profile '{profile}' key '{key}' has "
        f"type {type(value).__name__}; only strings, integers, floats, and "
        "booleans are allowed."
    )


def resolve_profile_name(
    flag: str | None,
    config: AtliConfig,
    env: Mapping[str, str],
) -> str | None:
    """Return the profile to use: ``flag`` > ``$ATLI_PROFILE`` > config default.

    An explicitly empty ``flag`` is an error, not "unset": ``atli --profile ""
    jira get-issue`` must not silently target the default profile's instance.
    """
    if flag == "":
        raise ConfigError(f"--profile requires a profile name. {_PROFILE_USAGE}")
    name = flag or env.get("ATLI_PROFILE") or config.default_profile
    if name is None:
        return None
    if name not in config.profiles:
        if config.profiles:
            available = ", ".join(config.profiles)
            raise ConfigError(
                f"Profile '{name}' is not defined in the config. "
                f"Available profiles: {available}."
            )
        raise ConfigError(
            f"Profile '{name}' is not defined, and no profiles are configured. "
            "Add a [profiles.{name}] table to your config file."
        )
    return name


def apply_profile(
    profile: Mapping[str, str],
    environ: MutableMapping[str, str],
) -> None:
    """Apply ``profile`` to ``environ``, replacing variables per service prefix.

    For each prefix in :data:`SERVICE_ENV_PREFIXES` the profile touches, every
    existing variable with that prefix is deleted before the profile's values
    are written. Prefixes the profile does not touch keep their ambient values.

    When the profile touches ANY service prefix, the cross-service credential
    keys in :data:`CROSS_SERVICE_CREDENTIAL_KEYS` are also deleted — unless the
    profile defines them itself — so an ambient OAuth token can never be sent
    to the profile-chosen host. A profile that sets no service-prefixed key at
    all writes nothing (a TOOLSETS-only profile is a no-op).
    """
    for prefix in SERVICE_ENV_PREFIXES:
        if not any(key.startswith(prefix) for key in profile):
            continue
        for key in [existing for existing in environ if existing.startswith(prefix)]:
            del environ[key]
        for key in CROSS_SERVICE_CREDENTIAL_KEYS:
            if key not in profile:
                environ.pop(key, None)
        environ.update(profile)


def extract_profile_flag(argv: list[str]) -> tuple[str | None, list[str]]:
    """Strip ``--profile`` from ``argv`` before the CLI parser runs.

    Scans from index 0 while tokens start with ``-`` and stops at the first
    non-flag token (the subcommand), so flag values that merely look like
    ``--profile`` are never mistaken for it. Accepts ``--profile=X`` and
    ``--profile X``; repeated flags keep the last value.

    Returns ``(profile_or_None, remaining_argv)``.
    """
    value: str | None = None
    remaining: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if not token.startswith("-"):
            remaining.extend(argv[index:])
            break
        if token == "--profile":
            if index + 1 >= len(argv) or argv[index + 1].startswith("-"):
                raise ConfigError(
                    f"--profile requires a profile name. {_PROFILE_USAGE}"
                )
            value = argv[index + 1]
            if not value:
                raise ConfigError(f"--profile requires a profile name. {_PROFILE_USAGE}")
            index += 2
        elif token.startswith("--profile="):
            name = token.partition("=")[2]
            if not name:
                raise ConfigError(f"--profile requires a profile name. {_PROFILE_USAGE}")
            value = name
            index += 1
        else:
            remaining.append(token)
            index += 1
    return value, remaining


def describe_profiles(config: AtliConfig, active: str | None) -> str:
    """Render the profile list for humans, showing only URLs — never tokens."""
    if not config.profiles:
        if config.path is not None:
            return f"No profiles configured. (config: {config.path})\n"
        return "No profiles configured.\n"

    lines: list[str] = []
    for name, profile in config.profiles.items():
        marker = "*" if name == active else " "
        header = f"{marker} {name}"
        if name == config.default_profile:
            header += " (default)"
        lines.append(header)
        for env_key, label in (("JIRA_URL", "jira"), ("CONFLUENCE_URL", "confluence")):
            if env_key in profile:
                lines.append(f"    {label}: {profile[env_key]}")
    return "\n".join(lines) + "\n"
