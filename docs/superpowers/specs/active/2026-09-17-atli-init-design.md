# Interactive onboarding: `atli init <service>`

**Status:** implemented

## Problem

Getting from `pip install` to a first working command today is README archaeology:

- Credentials must be hand-written into a TOML profile (`.atli.toml` or
  `~/.config/atli/config.toml`), with `chmod 600` applied by hand. Typos, wrong
  tokens, and non-latin-1 paste artifacts surface only on the first real tool
  call — or as a cryptic `'latin-1' codec` error (#10).
- Corp-network quirks such as `CONFLUENCE_SSL_VERIFY = "false"` (self-signed
  CA) are tribal knowledge; nothing at setup time offers them.
- The SessionStart primer hook must be discovered and installed separately
  (`atli prime --install`), and the harness registry only actually supports
  Claude Code — codex is registered as unsupported (hooks were experimental),
  and qwen / gigacode (a qwen fork: `GIGACODE.md` for `QWEN.md`, `.gigacode`
  for `.qwen`, otherwise full parity) do not exist at all. Codex hooks are now
  stable: enabled by default, defined in `hooks.json` with the same
  `{"hooks": {"SessionStart": [...]}}` JSON shape as Claude Code (per current
  Codex docs), with a one-time `/hooks` trust review.

## Goal

1. `atli init jira` / `atli init confluence` / `atli init bitbucket` walk a
   user from zero to a working, verified setup in one interactive flow: URL,
   credentials, TLS-verify choice, storage scope, profile name, harness pick.
2. Credentials are verified with one cheap live call **before** anything is
   written; a failed verification writes nothing and offers recovery.
3. One scope answer drives both destinations: project → `./.atli.toml` +
   repo-local harness settings; global → `~/.config/atli/config.toml` +
   home-level harness settings.
4. The harness registry learns qwen and gigacode and promotes codex to
   supported (`.codex/hooks.json`), so both `atli init` and
   `atli prime --install` can install the SessionStart hook for all four.
5. Config writes are surgical and comment-preserving: only the collected
   profile keys change; every other byte of the user's file survives.

## Non-goals

- A non-interactive / flag-driven mode (scriptable init). Tests inject prompt
  callables; humans get the wizard.
- mTLS (`CLIENT_CERT`) in the wizard — README-documented for the rare case.
- Writing instruction/memory files (`CLAUDE.md`, `AGENTS.md`, `QWEN.md`,
  `GIGACODE.md`). The harness step installs the SessionStart hook only; the
  primer it injects is always current because `atli prime` renders it live.
- OAuth (`ATLASSIAN_OAUTH_*`) flows.
- Auto-editing `.gitignore` (project scope prints advice only).
- Changing gemini's unsupported status.
- Editing or deleting existing profiles non-interactively; `atli profiles`
  stays display-only.

## Command surface & dispatch

- `atli init jira|confluence|bitbucket` — one wizard, parameterized by
  service. Bare `atli init` renders a numbered service menu and runs the same
  wizard; the three explicit subcommands remain the canonical surface.
- `main._run` gains an `init` fast path beside `prime`: it dispatches before
  the runner is built and before ambient credential validation — a broken
  ambient environment must not block onboarding (the latin-1 check instead
  runs on *collected* values at prompt time). `build.create_app` registers a
  display-only `init` stub (the `_prime_stub` pattern) so root `--help` lists
  it; the real app is built by a new `build.create_init_app`, mirroring
  `create_prime_app`.
- `atli init bitbucket` under the `[atlassian]` provider fails fast: exit 2
  with the existing `providers.bitbucket_hint` guidance, before any prompt.
- Exit codes keep the contract: 0 success — including a clean decline at the
  confirmation step; 1 verification failure, abort after a failed
  verification, or Ctrl-C; 2 usage/config errors (unknown service,
  unset-`ATLI_CONFIG`-style problems, provider mismatch above).

## Wizard flow

Per service, in order:

1. **URL** — free-text, validated as `http(s)://<host>` (re-prompt with the
   reason otherwise). Bitbucket pre-fills `https://bitbucket.org` (Enter
   accepts); confluence's prompt hints at the `…/wiki` suffix.
2. **Auth method** — numbered menu, Cloud vs Data Center/Server:

   | Service | Cloud collects | DC/Server collects |
   |---|---|---|
   | jira, confluence | `USERNAME` (email) + `API_TOKEN` | `PERSONAL_TOKEN` |
   | bitbucket | `USERNAME` + `API_TOKEN` | `PERSONAL_TOKEN` |

   Tokens are entered hidden (`getpass`-backed callable). Each collected
   value is latin-1-validated immediately — same rule as
   `config.validate_credentials` (suffixes in `config.CREDENTIAL_SUFFIXES`) —
   with a re-prompt carrying that function's user-facing explanation.
3. **TLS verification** — "Verify TLS certificates? [Y/n]" for all three
   services (the library reads `JIRA_SSL_VERIFY` / `CONFLUENCE_SSL_VERIFY` /
   `BITBUCKET_SSL_VERIFY`); default Y leaves the variable unset; n writes
   `<SVC>_SSL_VERIFY = "false"`. Prompt help mentions the corporate-CA case.
4. **Scope** — project vs global, default **global** (credentials inside a
   repo risk accidental commits). Choosing project prints "add `.atli.toml`
   to `.gitignore`" advice — advice only.
5. **Profile name** — default is the service name. An existing profile of
   that name in the target file is always **merged**: collected keys are
   updated, unrelated keys (e.g. `TOOLSETS`) preserved. `default_profile` is
   set to this name only if the key is absent; an existing different default
   is never silently changed, and the summary says which default is active.
6. **Harness** — numbered menu: claude / codex / qwen / gigacode / skip.
   Entries whose config dir (`.claude`, `.codex`, `.qwen`, `.gigacode`)
   exists under `~` are marked "detected"; the default is the first detected
   harness, else claude.
7. **Summary & confirm** — service, URL, masked credentials (fixed-length
   `****`), target config path, profile name, harness + scope, verification
   intent. Confirm proceeds to verification; anything else aborts with
   nothing written.

## Scope model & config destination

- project → `./.atli.toml`; hook installed with scope `project` (repo-local
  harness settings). global → `~/.config/atli/config.toml`; hook scope
  `user` (home-level settings).
- `$ATLI_CONFIG`, when set, is what runtime reads: global scope writes
  **that** path (surfacing the existing set-but-missing error as exit 2
  beforehand); project scope still writes `./.atli.toml` but the summary
  warns that runtime lookup prefers `$ATLI_CONFIG`.

## Verification before writing

After confirmation, the wizard applies the collected profile to
`os.environ` via the existing `config.apply_profile` (stale-prefix isolation
and OAuth cleanup included; the process is short-lived, so an aborted run's
dirty env is irrelevant), then imports `runner.ToolRunner` lazily and fires
one cheap read-only call:

| Service | Tool call |
|---|---|
| jira | `search` with `jql = "ORDER BY created DESC"`, `limit = 1` |
| confluence | `search` with `query = 'type = "page"'` (CQL), `limit = 1` |
| bitbucket | `list_repositories` with `max_results = 1` (other params optional) |

Success (even zero results) proves URL + credentials + TLS settings and
proceeds to the write phase. `ToolCallFailure` / `ToolRunnerError` prints the
server's message and offers: re-enter credentials (back to step 2) / change
URL (back to step 1) / abort — exit 1, nothing written.

## Harness registry (`install.py`)

| name | detect dir | settings (user & project) | supported | note |
|---|---|---|---|---|
| claude | `.claude` | `.claude/settings.json` | yes | unchanged |
| codex | `.codex` | `.codex/hooks.json` | yes (promoted) | report line carries "trust it via `/hooks` on first run" |
| qwen | `.qwen` | `.qwen/settings.json` | yes (new) | |
| gigacode | `.gigacode` | `.gigacode/settings.json` | yes (new) | qwen fork, full parity, renamed paths |
| gemini | `.gemini` | `.gemini/settings.json` | no | unchanged note |

Codex's `hooks.json` uses the same JSON hook shape as Claude Code, so
`install.merge_hook` is reused verbatim for all four; `HOOK_COMMAND`
(`atli prime --hook-json`) stays the single installed command and
idempotency key. Codex injects plain stdout as developer context (the JSON
envelope also works); the primer is far under the ~2500-token
`additionalContextLimit` default. `atli prime --install` auto-detection
benefits automatically: existing `.qwen` / `.gigacode` / `.codex` dirs now
resolve to installs instead of silence or the unsupported note.

## Write phase

- **Config** — a surgical text merge in a new `init.py` (pure decision +
  I/O logic; no CLI or MCP imports, the `prime.py` discipline):
  - The `[profiles.<name>]` section is located by exact header match and
    extends to the next table header (or EOF). Within it, each collected key
    updates its `KEY = "…"` line in place (first occurrence); missing keys
    append at section end. No section → the whole table appends at EOF,
    preceded by a blank line. Every other byte — comments, key order, other
    tables — is preserved.
  - Values serialize as TOML basic strings using the JSON-compatible escape
    set (`\"`, `\\`, `\n`, … , `\uXXXX`); profile values are flat strings by
    the `config._coerce_value` contract.
  - `default_profile = "<name>"` inserts before the first table header only
    when absent (step 5 rule).
  - The file (created or existing) is `chmod 600` after writing, both scopes.
- **Hook** — skipped when the harness menu answer was skip; otherwise
  `install.install(name, scope, home, cwd)` per the existing
  merge-never-clobber contract, and its report line prints as-is (codex's
  line includes the trust caveat).
- **Final report** — what was written where, the profile's URL row in the
  `atli profiles` style, and next steps: a first command to try, `atli prime`
  to preview the primer.

## Error handling

| Situation | Behavior |
|---|---|
| `init bitbucket` under `[atlassian]` | exit 2 before prompts, `providers.bitbucket_hint` guidance |
| URL fails shape validation | re-prompt with reason |
| Collected credential fails latin-1 check | re-prompt with the `validate_credentials`-style explanation |
| Live verification fails | server message + menu: re-enter / change URL / abort (exit 1, nothing written) |
| Abort (decline at confirm, or menu-abort) | "nothing written", exit 1 after failed verification; exit 0 when declined at the confirmation step before any failure |
| Ctrl-C at any prompt | clean "aborted — nothing written" message, exit 1 (traceback suppressed) |
| Target config unparsable as text / IO error | exit 2 with the `ConfigError` message, nothing written |
| `$ATLI_CONFIG` set but missing (global scope) | exit 2, the existing `find_config_file` error text |

## Testing

- **`tests/test_init.py`** (new) — wizard branching with scripted prompt
  callables (`ask` / `ask_secret` injections): URL re-prompt, auth-method
  branches, SSL toggle on/off, scope branch, profile-name default and merge,
  harness pick → delegated install (tmp home/cwd), skip path, summary
  rendering, masked credentials. Verification with a stubbed runner factory
  (the `main` pattern): success writes; failure menu re-enter/URL/abort;
  abort writes nothing. TOML surgery: create-new, merge-existing,
  comment/foreign-table preservation, escaped values, `default_profile`
  insertion vs preservation, `$ATLI_CONFIG` targeting, `chmod 600`.
- **`tests/test_install.py`** — qwen/gigacode entries install and are
  idempotent against tmp home/cwd; codex `hooks.json` merge (including a
  pre-existing hook entry) plus the trust-note report line; gemini unchanged.
- **`tests/test_main.py`** — the `init` fast path dispatches without
  building the runner (stub-asserted) and is exempt from ambient credential
  validation; `init bitbucket` provider fail-fast maps to exit 2.
- **`tests/test_build.py`** — root help lists `init`; `create_init_app`
  exposes the three service commands.

## Documentation

- **README** — an "Onboarding" section leading with `atli init <service>`
  (the hand-written profile path stays as the power-user alternative);
  the harness table gains qwen / gigacode / codex (hooks.json + `/hooks`
  trust note); the SSL-verify paragraph references the wizard question.
- **AGENTS.md** — one bullet: `atli init <service>` interactively creates a
  verified profile and installs the SessionStart hook (claude / codex /
  qwen / gigacode).
