"""Interactive onboarding wizard (``atli init <service>``).

Pure decision and I/O logic in the :mod:`mcp_atlassian_cli.prime`
discipline: this module imports nothing that reaches the server stack —
the single exception is the lazy ``ToolRunner`` import inside
:func:`verify_profile`, which the wizard pays only at the live-verification
step. All prompting goes through the injectable :class:`Prompt` protocol
(``select`` / ``text`` / ``secret`` / ``confirm``) so tests script the
conversation instead of driving a TTY; production uses the InquirerPy-backed
adapter on a terminal and the plain-text adapter when stdin is piped (agents
driving atli via Bash keep working).
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from tomllib import TOMLDecodeError
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from getpass import getpass
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from mcp_atlassian_cli import providers
from mcp_atlassian_cli.config import (
    ConfigError,
    apply_profile,
    upsert_profile,
    validate_credentials,
)


@dataclass(frozen=True)
class VarSpec:
    """One environment variable the wizard collects for an auth method."""

    env_name: str
    secret: bool
    prompt_label: str
    """Human label shown at the prompt, env name included."""


@dataclass(frozen=True)
class AuthMethod:
    """One selectable authentication shape for a service."""

    label: str
    variables: tuple[VarSpec, ...]


@dataclass(frozen=True)
class ServiceSpec:
    """Everything the wizard needs to know about one service."""

    name: str
    url_var: str
    url_default: str | None
    url_hint: str | None
    auth_methods: tuple[AuthMethod, ...]
    ssl_var: str
    verify_tool: str
    verify_args: dict[str, object] = field(default_factory=dict)


_CLOUD_LABEL = "Cloud — username (email) + API token"
_DC_LABEL = "Data Center / Server — personal token"

SERVICES: dict[str, ServiceSpec] = {
    "jira": ServiceSpec(
        name="jira",
        url_var="JIRA_URL",
        url_default=None,
        url_hint=None,
        auth_methods=(
            AuthMethod(
                label=_CLOUD_LABEL,
                variables=(
                    VarSpec("JIRA_USERNAME", False, "Email (JIRA_USERNAME)"),
                    VarSpec("JIRA_API_TOKEN", True, "API token (JIRA_API_TOKEN)"),
                ),
            ),
            AuthMethod(
                label=_DC_LABEL,
                variables=(VarSpec("JIRA_PERSONAL_TOKEN", True, "Personal token (JIRA_PERSONAL_TOKEN)"),),
            ),
        ),
        ssl_var="JIRA_SSL_VERIFY",
        verify_tool="jira_search",
        verify_args={"jql": "ORDER BY created DESC", "limit": 1},
    ),
    "confluence": ServiceSpec(
        name="confluence",
        url_var="CONFLUENCE_URL",
        url_default=None,
        url_hint="Cloud URLs end with /wiki (https://your-company.atlassian.net/wiki)",
        auth_methods=(
            AuthMethod(
                label=_CLOUD_LABEL,
                variables=(
                    VarSpec("CONFLUENCE_USERNAME", False, "Email (CONFLUENCE_USERNAME)"),
                    VarSpec("CONFLUENCE_API_TOKEN", True, "API token (CONFLUENCE_API_TOKEN)"),
                ),
            ),
            AuthMethod(
                label=_DC_LABEL,
                variables=(
                    VarSpec(
                        "CONFLUENCE_PERSONAL_TOKEN",
                        True,
                        "Personal token (CONFLUENCE_PERSONAL_TOKEN)",
                    ),
                ),
            ),
        ),
        ssl_var="CONFLUENCE_SSL_VERIFY",
        verify_tool="confluence_search",
        verify_args={"query": 'type = "page"', "limit": 1},
    ),
    "bitbucket": ServiceSpec(
        name="bitbucket",
        url_var="BITBUCKET_URL",
        url_default="https://bitbucket.org",
        url_hint=None,
        auth_methods=(
            AuthMethod(
                label=_CLOUD_LABEL,
                variables=(
                    VarSpec("BITBUCKET_USERNAME", False, "Username (BITBUCKET_USERNAME)"),
                    VarSpec("BITBUCKET_API_TOKEN", True, "API token (BITBUCKET_API_TOKEN)"),
                ),
            ),
            AuthMethod(
                label=_DC_LABEL,
                variables=(
                    VarSpec(
                        "BITBUCKET_PERSONAL_TOKEN",
                        True,
                        "Personal token (BITBUCKET_PERSONAL_TOKEN)",
                    ),
                ),
            ),
        ),
        ssl_var="BITBUCKET_SSL_VERIFY",
        verify_tool="bitbucket_list_repositories",
        verify_args={"max_results": 1},
    ),
}
"""Services in menu order, each with its URL variable, auth shapes, TLS
variable, and the cheap read-only call that proves a setup works."""


def validate_url(text: str) -> str | None:
    """Return why ``text`` is not a usable service URL, or ``None`` if it is.

    Only ``http``/``https`` with a non-empty host is accepted — that is the
    shape every supported Atlassian/Bitbucket deployment has, and anything
    else would fail far less legibly inside the HTTP stack.
    """
    if not text:
        return "URL is empty — enter the full address, e.g. https://your-company.atlassian.net"
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https"):
        if "://" in text:
            return f"'{text}' needs an http:// or https:// scheme, not '{parts.scheme}://'."
        return f"'{text}' has no http:// or https:// scheme — include it, e.g. https://{text.lstrip('/')}"
    if not parts.hostname:
        return f"'{text}' has no host — use the full address, e.g. https://jira.example.com"
    return None


_PROFILE_NAME_OK = re.compile(r"[A-Za-z0-9_-]+")


def validate_profile_name(name: str) -> str | None:
    """Return why ``name`` cannot head a ``[profiles.<name>]`` table, or None.

    The name becomes a TOML bare header key, so it must be exactly the
    character set tomllib accepts there — letters, digits, ``-``, ``_``.
    Anything else (dots nest tables, spaces and quotes break the header,
    non-ASCII varies by parser) would produce a file that does not parse.
    """
    if not name:
        return "Profile name is empty."
    if name != name.strip():
        return "Profile name has leading or trailing whitespace."
    if _PROFILE_NAME_OK.fullmatch(name) is None:
        return (
            "Profile name may only use letters, digits, '-', and '_' — "
            f"'{name}' cannot head a [profiles.{name}] table."
        )
    return None


def _credential_validator(env_name: str) -> Callable[[str], str | None]:
    """The per-variable prompt validator: required + latin-1-safe.

    Every collected value is header-bound (Basic-auth usernames included),
    so every value gets the latin-1 entry check — the cryptic ``http.client``
    failure must never survive the prompt. :func:`validate_credentials` is
    the same rule runtime enforces; its message names the offending
    character.
    """

    def check(text: str) -> str | None:
        if not text:
            return "Value is required."
        try:
            validate_credentials({env_name: text})
        except ConfigError as error:
            return str(error)
        return None

    return check


@runtime_checkable
class Prompt(Protocol):
    """The wizard's conversation surface; injectable for tests.

    ``select`` presents (value, label) choices and returns the chosen
    value; ``text`` returns the effective answer (the default when the
    user accepts it); ``secret`` never echoes and cannot prefill — the
    caller decides what an empty answer means; ``confirm`` returns a bool.
    ``validate`` callables return ``None`` when the text is acceptable and
    the reason string when it is not; both adapters re-prompt on a reason.
    """

    def select(
        self,
        message: str,
        choices: Sequence[tuple[str, str]],
        *,
        default: str | None = None,
        instruction: str | None = None,
    ) -> str: ...

    def text(
        self,
        message: str,
        *,
        default: str | None = None,
        validate: Callable[[str], str | None] | None = None,
    ) -> str: ...

    def secret(
        self,
        message: str,
        *,
        validate: Callable[[str], str | None] | None = None,
    ) -> str: ...

    def confirm(self, message: str, *, default: bool = True) -> bool: ...


def _prompt_toolkit_validator(
    check: Callable[[str], str | None],
) -> "object":
    """Adapt a ``text -> reason | None`` callable to prompt_toolkit's
    ``Validator`` (what InquirerPy's ``validate`` parameter wants)."""
    from prompt_toolkit.document import Document
    from prompt_toolkit.validation import ValidationError, Validator

    class _Check(Validator):
        def validate(self, document: Document) -> None:
            reason = check(document.text)
            if reason is not None:
                raise ValidationError(
                    message=reason, cursor_position=document.cursor_position
                )

    return _Check()


class InquirerPrompt:
    """The terminal :class:`Prompt`: InquirerPy arrow-key selects with a
    pointer, live inline validation, hidden secrets.

    InquirerPy (and prompt_toolkit beneath it) is imported lazily inside
    each method so the piped/agent path and every scripted test pay nothing.
    """

    def _select(
        self,
        message: str,
        choices: Sequence[tuple[str, str]],
        *,
        default: str | None,
        instruction: str | None,
    ):
        from InquirerPy import inquirer
        from InquirerPy.base.control import Choice

        kwargs: dict[str, object] = {}
        if default is not None:
            kwargs["default"] = default
        if instruction is not None:
            kwargs["instruction"] = instruction
        return inquirer.select(
            message=message,
            choices=[Choice(value=value, name=label) for value, label in choices],
            **kwargs,
        )

    def select(
        self,
        message: str,
        choices: Sequence[tuple[str, str]],
        *,
        default: str | None = None,
        instruction: str | None = None,
    ) -> str:
        return str(self._select(message, choices, default=default, instruction=instruction).execute())

    def text(
        self,
        message: str,
        *,
        default: str | None = None,
        validate: Callable[[str], str | None] | None = None,
    ) -> str:
        from InquirerPy import inquirer

        kwargs: dict[str, object] = {}
        if default is not None:
            kwargs["default"] = default
        if validate is not None:
            kwargs["validate"] = _prompt_toolkit_validator(validate)
        return str(inquirer.text(message=message, **kwargs).execute())

    def secret(
        self,
        message: str,
        *,
        validate: Callable[[str], str | None] | None = None,
    ) -> str:
        from InquirerPy import inquirer

        kwargs: dict[str, object] = {}
        if validate is not None:
            kwargs["validate"] = _prompt_toolkit_validator(validate)
        return str(inquirer.secret(message=message, **kwargs).execute())

    def confirm(self, message: str, *, default: bool = True) -> bool:
        # Not inquirer.confirm: its Enter keybinding does not fire on
        # current prompt_toolkit (Enter submits the empty buffer instead of
        # the default), so confirm is a two-choice select — the same
        # machinery the rest of the wizard uses and the pty smoke tests
        # verify.
        answer = self._select(
            message, (("yes", "Yes"), ("no", "No")),
            default="yes" if default else "no", instruction=None,
        ).execute()
        return answer == "yes"


class PlainPrompt:
    """The non-TTY :class:`Prompt`: numbered menus and plain ``input``.

    The piped/agent path — answers are numbers or raw text on stdin, one
    per line, in the spirit of the pre-interactive ``ConsolePrompt``
    (differences: every choice is numbered — the old harness menu's ``s``
    skip alias is now just another numbered choice — and validation
    re-prompts with the reason printed via ``echo``).
    """

    def __init__(self, echo: Callable[[str], None] = print) -> None:
        self._echo = echo

    def select(
        self,
        message: str,
        choices: Sequence[tuple[str, str]],
        *,
        default: str | None = None,
        instruction: str | None = None,
    ) -> str:
        if default is None:
            default = choices[0][0]
        lines = [message] if instruction is None else [message, instruction]
        for number, (value, label) in enumerate(choices, start=1):
            marker = " (default)" if value == default else ""
            lines.append(f"{number}) {label}{marker}")
        prompt_text = "\n".join(lines)
        while True:
            answer = input(f"{prompt_text}: ").strip()
            if answer == "":
                return default
            # isdecimal, not isdigit: "²".isdigit() is True but int() rejects it
            if answer.isdecimal() and 1 <= int(answer) <= len(choices):
                return choices[int(answer) - 1][0]
            self._echo(f"Choose 1-{len(choices)} (or press Enter for the default).")

    def text(
        self,
        message: str,
        *,
        default: str | None = None,
        validate: Callable[[str], str | None] | None = None,
    ) -> str:
        suffix = f" [{default}]" if default is not None else ""
        while True:
            answer = input(f"{message}{suffix}: ")
            effective = default if (answer == "" and default is not None) else answer
            if validate is None:
                return effective
            reason = validate(effective)
            if reason is None:
                return effective
            self._echo(reason)

    def secret(
        self,
        message: str,
        *,
        validate: Callable[[str], str | None] | None = None,
    ) -> str:
        while True:
            answer = getpass(f"{message}: ")
            if validate is None:
                return answer
            reason = validate(answer)
            if reason is None:
                return answer
            self._echo(reason)

    def confirm(self, message: str, *, default: bool = True) -> bool:
        suffix = "[Y/n]" if default else "[y/N]"
        while True:
            answer = input(f"{message} {suffix}: ").strip().lower()
            if answer == "":
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self._echo("Please answer y or n.")


def console_prompt() -> Prompt:
    """The production prompt: InquirerPy on a real terminal, plain text
    otherwise.

    A TTY is necessary but not sufficient. ``TERM=dumb`` (CI contexts,
    stripped environments) makes prompt_toolkit fall back to a rendering
    path with no cursor addressing and, critically, no password masking:
    a typed token would be shown in clear text. And InquirerPy renders to
    stdout, so a redirected stdout (``atli init | tee``) hits the same
    plain-text output class even with a TTY stdin — both streams must be
    terminals. Windows is exempt from the TERM check: its consoles never
    set TERM, and prompt_toolkit selects its Windows output backends on
    the platform, not on TERM. Terminals that fail any check get the
    plain adapter, whose secrets go through :func:`getpass` (echo-off at
    the termios level) instead of terminal rendering."""
    term_ok = sys.platform == "win32" or os.environ.get("TERM", "") not in ("", "dumb")
    smart_terminal = sys.stdin.isatty() and sys.stdout.isatty() and term_ok
    return InquirerPrompt() if smart_terminal else PlainPrompt()


def write_config(path: Path, content: str) -> None:
    """Atomically write ``content`` to ``path`` with owner-only permissions.

    The tmp file is CREATED with mode 0600 (``os.open`` — umask can only
    clear bits, never set them), so the credentials never exist on disk
    with group/other read bits at any instant; the rename over the target
    is atomic, so a crash mid-write can only leave the pre-write file,
    never a partial or exposed one. A failure before the rename unlinks
    the tmp file — owner-only already, but a stray credential-bearing
    ``.tmp`` beside the config helps no one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    descriptor = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    path.chmod(0o600)


def config_target_path(
    scope: str,
    *,
    environ: Mapping[str, str],
    home: Path,
    cwd: Path,
) -> Path:
    """The config file a scope writes: project → ``./.atli.toml`` beside the
    repo, global → the machine file runtime reads.

    Global honors ``$ATLI_CONFIG`` when it points at an existing file (init
    must write what runtime loads); a set-but-missing path is the same
    user-fixable error ``config.find_config_file`` raises — in production
    ``main`` surfaces it before init ever runs, the check here covers direct
    library use. No explicit override: ``~/.config/atli/config.toml``.
    """
    if scope == "project":
        return cwd / ".atli.toml"
    if scope == "global":
        explicit = environ.get("ATLI_CONFIG")
        if explicit:
            path = Path(explicit)
            if path.is_file():
                return path
            raise ConfigError(
                f"ATLI_CONFIG is set to '{explicit}', but that file does not "
                "exist. Point ATLI_CONFIG at an existing TOML config file, "
                "or unset it."
            )
        return home / ".config" / "atli" / "config.toml"
    raise ConfigError(f"Unknown scope '{scope}' — use 'project' or 'global'.")


def _secret_keys(service: str) -> set[str]:
    """Env names collected hidden for ``service`` — the only values the
    summary masks (a username typed in plain sight is confirmation
    material, not a secret)."""
    spec = SERVICES[service]
    return {
        variable.env_name
        for method in spec.auth_methods
        for variable in method.variables
        if variable.secret
    }


def detect_auth_method(spec: ServiceSpec, current: Mapping[str, str]) -> int:
    """Index of the auth method whose variables the existing profile
    already carries (all of them), else 0 (Cloud) — re-running init
    preselects the shape the profile already uses."""
    for index, method in enumerate(spec.auth_methods):
        if all(variable.env_name in current for variable in method.variables):
            return index
    return 0


def collect_profile(
    service: str,
    prompt: Prompt,
    *,
    current: Mapping[str, str] = {},
    keep_url: str | None = None,
) -> dict[str, str]:
    """Collect URL, auth method + its variables, and the TLS choice.

    ``current`` is the profile as it exists on disk (empty for a new
    profile): non-secret prompts prefill from it (Enter keeps the value),
    secrets offer "Enter to keep current", the auth method and TLS answer
    preselect from it — re-running init edits the profile instead of
    re-typing it. ``keep_url`` skips the URL prompt entirely (a
    credentials-only retry keeps the URL just entered this session). Every
    answer is validated where it is entered — URL shape, non-empty
    credentials, latin-1-safe header values — so a bad value re-prompts
    immediately with the reason instead of surfacing as an HTTP error on
    first use.
    """
    spec = SERVICES[service]
    values: dict[str, str] = {}

    if keep_url is not None:
        values[spec.url_var] = keep_url
    else:
        url_default = current.get(spec.url_var) or spec.url_default
        url_prompt = f"{service.title()} URL"
        if spec.url_hint:
            url_prompt += f" ({spec.url_hint})"
        # strip inside the validator too, so a pasted trailing space is
        # accepted in both adapters, not just after the fact
        values[spec.url_var] = prompt.text(
            url_prompt,
            default=url_default,
            validate=lambda text: validate_url(text.strip()),
        ).strip()

    default_method = detect_auth_method(spec, current)
    choice = prompt.select(
        "Auth method",
        [(str(index), method.label) for index, method in enumerate(spec.auth_methods)],
        default=str(default_method),
    )
    method = spec.auth_methods[int(choice)]

    for variable in method.variables:
        existing = current.get(variable.env_name)
        if variable.secret:
            if existing:
                answer = prompt.secret(f"{variable.prompt_label} (Enter to keep current)")
                values[variable.env_name] = answer if answer else existing
            else:
                values[variable.env_name] = prompt.secret(
                    variable.prompt_label, validate=_credential_validator(variable.env_name)
                )
        else:
            values[variable.env_name] = prompt.text(
                variable.prompt_label,
                default=existing,
                validate=_credential_validator(variable.env_name),
            )

    verify_default = current.get(spec.ssl_var) != "false"
    if not prompt.confirm(
        "Verify TLS certificates? (answer No behind a corporate proxy with "
        "a self-signed CA)",
        default=verify_default,
    ):
        values[spec.ssl_var] = "false"
    return values


def choose_scope(prompt: Prompt) -> str:
    """Project vs global; global is the default — credentials inside a repo
    risk accidental commits."""
    return prompt.select(
        "Config scope",
        (
            ("global", "Global — ~/.config/atli/config.toml + home harness settings"),
            ("project", "Project — ./.atli.toml + repo harness settings"),
        ),
        default="global",
    )


def choose_profile_name(service: str, prompt: Prompt) -> str:
    """Profile name, defaulting to the service name."""
    return prompt.text(
        "Profile name", default=service, validate=validate_profile_name
    )


_SKIP_HARNESS = "__skip__"


def choose_harness(prompt: Prompt, home: Path) -> str | None:
    """Pick a harness for the SessionStart hook, or skip.

    Supported harnesses in registry order; a detected config dir under
    ``home`` marks (detected) and makes that harness the default — falling
    back to claude. The skip choice defers to ``atli prime --install``.
    """
    from mcp_atlassian_cli.install import HARNESSES

    supported = [h for h in HARNESSES.values() if h.supported]
    detected = [h for h in supported if (home / h.detect_dir_name).exists()]
    default = detected[0].name if detected else supported[0].name
    choices = [
        (h.name, f"{h.name} (detected)" if h in detected else h.name) for h in supported
    ]
    choices.append((_SKIP_HARNESS, "Skip for now — install later with `atli prime --install`"))
    answer = prompt.select(
        "Harness — install the SessionStart primer hook?", choices, default=default
    )
    return None if answer == _SKIP_HARNESS else answer


def choose_service(prompt: Prompt) -> str:
    """The bare ``atli init`` service menu; the pointer starts at jira."""
    return prompt.select(
        "Which service?",
        [(name, name.title()) for name in SERVICES],
    )


def render_summary(
    service: str,
    values: Mapping[str, str],
    *,
    config_path: Path,
    profile_name: str,
    harness: str | None,
    scope: str,
) -> str:
    """The pre-write confirmation: everything about to be written. Secrets
    (values collected hidden) mask as a fixed-length ``****`` — no length
    leak; everything else, usernames included, shows as entered so it can
    actually be confirmed. Verification already succeeded by the time this
    renders, and the summary says so."""
    secrets = _secret_keys(service)
    lines = [f"Service: {service}"]
    for key, value in values.items():
        lines.append(f"{key}: {'****' if key in secrets else value}")
    lines.append(f"Config: {config_path}")
    lines.append(f"Profile: {profile_name}")
    lines.append(f"Scope: {scope}")
    lines.append(f"Harness: {harness if harness is not None else 'skipped'}")
    lines.append("Verified: yes — one live read-only call succeeded")
    return "\n".join(lines)


def verify_profile(
    service: str,
    values: Mapping[str, str],
    environ: MutableMapping[str, str],
    runner_factory: Callable[[], object] | None = None,
) -> None:
    """Prove the collected setup with one cheap read-only call.

    Applies the profile to ``environ`` first — ``apply_profile``'s
    per-prefix replacement gives the same stale-credential isolation a real
    run gets — then pays the one-time mcp-atlassian import (skipped
    entirely when ``runner_factory`` is injected) and fires the service's
    verification call. Any result, including zero hits, proves URL +
    credentials + TLS settings; auth/URL failures raise the runner's
    exceptions for the caller's recovery menu.
    """
    spec = SERVICES[service]
    apply_profile(values, environ)
    if runner_factory is not None:
        runner = runner_factory()
    else:
        from mcp_atlassian_cli.runner import ToolRunner

        runner = ToolRunner()
    runner.call_tool(spec.verify_tool, dict(spec.verify_args))


_EXAMPLE_COMMAND: dict[str, str] = {
    "jira": 'search --jql "assignee = currentUser()"',
    "confluence": 'search --query "deploy"',
    "bitbucket": "list-repositories",
}


def _load_existing_profile(
    config_path: Path,
    profile_name: str,
) -> tuple[str, dict[str, str], str | None]:
    """Read the target config for prefill: its text, the named profile's
    current values (flat strings), and the existing ``default_profile``.

    An unparsable file — or a ``profiles`` key that is not a table, the
    same shape rule :func:`config.load_config` enforces — is a hard stop:
    the wizard refuses to prefill from, or merge into, a file it cannot
    understand (a clean exit-2 message beats a corrupted write). Value
    coercion mirrors runtime too: booleans become ``"true"``/``"false"``
    (NOT Python's ``"True"``, which would break the TLS-answer default),
    numbers their decimal strings; values of any other type are skipped
    rather than fatal — runtime's ``load_config`` will name them if the
    user ever tries to run with that profile.
    """
    try:
        text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"Could not read config file '{config_path}': {error}") from error
    if not text:
        return "", {}, None
    try:
        data = tomllib.loads(text)
    except TOMLDecodeError as error:
        raise ConfigError(
            f"Could not parse existing config '{config_path}': {error}. "
            "Fix the file (`atli profiles` shows what currently loads) and "
            "re-run init."
        ) from error
    raw_profiles = data.get("profiles", {})
    if not isinstance(raw_profiles, dict):
        raise ConfigError(
            f"Invalid config file '{config_path}': 'profiles' must be a "
            "table of [profiles.<name>] tables."
        )
    section = raw_profiles.get(profile_name)
    current = {
        key: ("true" if value else "false") if isinstance(value, bool) else str(value)
        for key, value in section.items()
        if isinstance(value, (str, int, float, bool))
    } if isinstance(section, dict) else {}
    default = data.get("default_profile")
    return text, current, default if isinstance(default, str) else None


def run_init(
    service: str,
    *,
    prompt: Prompt,
    home: Path,
    cwd: Path,
    environ: MutableMapping[str, str],
    runner_factory: Callable[[], object] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """The whole ``atli init <service>`` wizard; returns the process exit code.

    Flow: scope and profile name first (they decide which file is edited),
    then the profile's values — prefilled from the existing section when
    there is one — then the live verification, and only then the summary:
    confirm means "write it", with nothing left between yes and the write.
    Nothing touches disk until the live verification succeeds — a declined
    confirm or an aborted recovery leaves the machine exactly as it was.
    Exit codes: 0 success (a clean decline included); 1 verification
    failure whose recovery was abandoned; 2 user-fixable configuration
    problems (provider mismatch, unreadable config, broken harness
    settings).
    """
    from mcp_atlassian_cli.install import install as install_hook
    from mcp_atlassian_cli.runner import ToolCallFailure, ToolRunnerError

    if service == "bitbucket" and providers.detect_provider() == "atlassian":
        hint = providers.bitbucket_hint("atlassian")
        out(hint or "This environment has the [atlassian] provider, which cannot mount Bitbucket tools.")
        return 2

    spec = SERVICES[service]
    # Every key the wizard can ever own for this service: the URL, all auth
    # methods' variables, and the TLS toggle. Keys NOT in this run's values
    # are dropped from the target section at merge time, so re-running init
    # with different answers never leaves superseded state behind (a stale
    # SSL_VERIFY="false" would silently downgrade TLS; old Cloud
    # credentials would ride next to a new personal token).
    owned_keys = {spec.url_var, spec.ssl_var} | {
        variable.env_name for method in spec.auth_methods for variable in method.variables
    }

    scope = choose_scope(prompt)
    if scope == "project":
        out(
            "Tip: add .atli.toml to your repository's .gitignore — "
            "it holds plaintext credentials."
        )
    profile_name = choose_profile_name(service, prompt)
    try:
        config_path = config_target_path(scope, environ=environ, home=home, cwd=cwd)
        existing, current, previous_default = _load_existing_profile(config_path, profile_name)
    except ConfigError as error:
        out(str(error))
        return 2
    if scope == "project" and environ.get("ATLI_CONFIG"):
        out(
            f"Note: $ATLI_CONFIG ({environ['ATLI_CONFIG']}) is set — at "
            "runtime atli prefers it over ./.atli.toml (and errors if it "
            "points at a missing file), so this profile will not be the "
            "one atli loads unless you unset it."
        )
    if scope == "global" and (cwd / ".atli.toml").is_file():
        out(
            "Note: a ./.atli.toml exists in this directory — at runtime it "
            "outranks the global config, so commands run from here keep "
            "loading the project profile."
        )

    values = collect_profile(service, prompt, current=current)
    while True:
        try:
            out(f"Verifying — one read-only call to {spec.verify_tool} …")
            verify_profile(service, values, environ, runner_factory=runner_factory)
        except (ToolCallFailure, ToolRunnerError) as error:
            out(str(error))
            action = prompt.select(
                "Verification failed",
                (
                    ("retry", "Re-enter credentials (same URL)"),
                    ("url", "Change URL"),
                    ("abort", "Abort"),
                ),
                default="retry",
            )
            if action == "abort":
                return 1
            keep_url = values[spec.url_var] if action == "retry" else None
            values = collect_profile(
                service, prompt, current=current, keep_url=keep_url
            )
            continue
        break

    harness = choose_harness(prompt, home)
    out(
        render_summary(
            service,
            values,
            config_path=config_path,
            profile_name=profile_name,
            harness=harness,
            scope=scope,
        )
    )
    if not prompt.confirm("Write this configuration?", default=True):
        out("Nothing written.")
        return 0

    try:
        merged = upsert_profile(
            existing, profile_name, values, owned_keys - set(values)
        )
    except ConfigError as error:
        out(str(error))
        return 2
    write_config(config_path, merged)
    out(f"config: {config_path} (profile '{profile_name}', chmod 600)")
    if previous_default is not None and previous_default != profile_name:
        out(f"default profile: '{previous_default}' (unchanged)")
    if harness is not None:
        try:
            out(
                install_hook(
                    harness,
                    "user" if scope == "global" else "project",
                    home=home,
                    cwd=cwd,
                )
            )
        except ConfigError as error:
            out(str(error))
            return 2
    else:
        out("hook: skipped (install later with `atli prime --install`)")
    out(f"{service}: {values.get(spec.url_var, '')}")
    out(f"Try: atli {service} {_EXAMPLE_COMMAND[service]}")
    out("Primer: atli prime")
    return 0
