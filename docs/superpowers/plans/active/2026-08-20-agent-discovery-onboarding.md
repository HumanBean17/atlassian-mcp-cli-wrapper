# Agent Discovery & Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `atli` usable by zero-context agents — `tools --search` shortlisting, curated examples in `<tool> --help`, `@file`/stdin long-value expansion, `prime --install` hook onboarding, and first-contact help polish.

**Architecture:** All changes live in the existing build/dispatch seams: `build.py` gains search, example rendering, expansion wiring, and help polish; two new pure modules (`examples.py`, `expand.py`) and one new IO module (`install.py`) keep build.py free of content; `main.py` gains two error-teaching hooks. No runner, discovery, or config-semantics changes.

**Tech Stack:** Python 3.11+, cyclopts `>=4.22,<5` (resolved 4.22.5), pytest, stdlib only for new modules.

## Global Constraints

- Exit codes unchanged: 0 success, 1 tool/server failure, 2 usage/config error. Every new error path returns 2.
- Tool-call paths stay byte-identical: stdout verbatim tool output, stderr one clean error line. Only help text and unknown-command messages change.
- The discovered tool commands (`atli <service> <tool>`) gain no new flags; `--search` applies to the builtin `tools` only.
- No new runtime dependencies (stdlib `difflib`, `json`, `pathlib` cover everything).
- `mcp-atlassian` must never be imported on the `prime` fast path.
- House style: every module and public function carries a docstring explaining the WHY; tests import via `from mcp_atlassian_cli...` and follow the naming/scenario style of the existing `tests/test_*.py`.
- Commit after every task, `feat:`/`docs:` prefix, present tense.

---

### Task 1: `atli tools --search`

**Files:**
- Modify: `src/mcp_atlassian_cli/build.py` (`_make_tools_command`, ~line 105)
- Modify: `README.md` (Notes for agents section)
- Test: `tests/test_build.py`

**Interfaces:**
- Consumes: `ToolSpec` (fields `service: str | None`, `command_name: str`, `description: str`) from `mcp_atlassian_cli.discovery` — unchanged.
- Produces: `tools` command accepts keyword-only `search: str | None = None` alongside the existing positional `service: str | None = None`. Behavior contract: case-insensitive substring match of the query against the concatenation `f"{service} {command_name} {description}"` (service contributes `""` when `None`; description is the FULL text, not the first sentence). `service` and `search` filters combine with AND.

- [ ] **Step 1: Write failing tests**

In `tests/test_build.py` (reuse `DispatchSpy`/`invoke`/`jira_get_issue_spec` helpers and the multi-spec app pattern used by `test_help_lists_tool`). Scenarios — build an app from two specs with known descriptions, invoke, assert on `capsys` output:

1. `tools --search issue` with specs `jira get-issue` ("Get an issue.") and `confluence search` ("Search Confluence content.") → output contains `jira get-issue` and does NOT contain `confluence search`.
2. Description-depth: a spec whose first sentence is "Get an issue." but whose description continues "Second sentence mentions worklogs." → `tools --search worklog` finds it (proves full-description matching).
3. Case-insensitive: `--search CONFLUENCE` matches the confluence spec.
4. Combined filters: `tools --service jira --search search` with a `jira search` spec and a `confluence search` spec → only `jira search` listed.
5. Zero matches: `tools --search zzzz` → exit 0 (use `invoke`) and output exactly `No tools match 'zzzz'. Broaden the query, or run 'atli tools' for the full list.`
6. Empty `--service` filter message updated: with a `jira`-only app, `tools --service confluence` → output `No tools for service 'confluence'. Use 'atli tools' to list all, or 'atli tools --search TEXT' to shortlist.`
7. No-specs message unchanged: app from zero specs → `No services configured — set JIRA_URL / CONFLUENCE_URL or a profile (see atli --help).` still prints (guards the silence contract).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_build.py -k search -v`
Expected: FAIL — `search` is not a known option (cyclopts UnknownOptionError raised by `invoke`).

- [ ] **Step 3: Implement**

Extend the inner `tools` function of `_make_tools_command` with the keyword-only `search` parameter per the Produces contract. Matching happens on a lowercase query against a lowercase haystack; the filtered list is `specs` passing BOTH the service filter (existing) and the search filter. Zero-match branches print the two new messages exactly as asserted in Step 1 (single-quoted fragments per the assertions). The no-specs early return and the table rendering stay untouched.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_build.py -v`
Expected: PASS (all existing tests plus the new ones).

- [ ] **Step 5: Update README + commit**

README "Notes for agents": extend the `atli <service> <tool> --help` bullet area with one line: `atli tools --search TEXT` shortlists tools by keyword across names and full descriptions. Commit: `git add src/mcp_atlassian_cli/build.py tests/test_build.py README.md && git commit -m "feat: tools --search keyword shortlist"`

---

### Task 2: Curated examples corpus → `<tool> --help`

**Files:**
- Create: `src/mcp_atlassian_cli/examples.py`
- Modify: `src/mcp_atlassian_cli/build.py` (`_make_handler`, ~line 101 where `handler.__doc__` is set)
- Test: `tests/test_examples.py` (new) and `tests/test_build.py`

**Interfaces:**
- Consumes: `ToolSpec.tool_name: str` (e.g. `"jira_get_issue"`).
- Produces (in `examples.py`):
  - `EXAMPLES: dict[str, tuple[str, ...]]` — key is the MCP tool name (snake_case, service-prefixed), value is 1–2 example invocation lines. Initial corpus, exactly these 11 entries:
    - `"jira_search"`: `atli jira search --jql 'assignee = currentUser() AND updated > -7d' --limit 20`
    - `"jira_get_issue"`: `atli jira get-issue --issue-key PROJ-123 --fields summary,status,assignee`
    - `"jira_create_issue"`: `atli jira create-issue --project-key OPS --summary 'Deploy failed' --issue-type Bug --description @incident.md`
    - `"jira_add_comment"`: `atli jira add-comment --issue-key PROJ-123 --body @comment.md`
    - `"jira_transition_issue"`: `atli jira transition-issue --issue-key PROJ-123 --transition-id 31`
    - `"jira_get_user_profile"`: `atli jira get-user-profile --user-identifier 'accountid:5b10ac8d82e05b22cc7d4ef5'`
    - `"confluence_search"`: `atli confluence search --query 'deploy runbook' --limit 10`
    - `"confluence_get_page"`: two lines — `atli confluence get-page --page-id 123456789` and `atli confluence get-page --title 'Runbook' --space-key OPS`
    - `"confluence_create_page"`: `atli confluence create-page --space-key OPS --title 'Runbook' --content @page.md`
    - `"confluence_update_page"`: `atli confluence update-page --page-id 123456789 --content @page.md`
    - `"confluence_add_comment"`: `atli confluence add-comment --page-id 123456789 --body @comment.md`
  - `render_examples(tool_name: str) -> str | None` — returns a markdown-bullet docstring block for known tools (`"Examples:\n"` followed by one `"- " + line` per example), `None` for unknown tools.
- Produces (build.py change): `_make_handler` sets `handler.__doc__` to the description, and when `render_examples(spec.tool_name)` is non-None, appends `"\n\n"` + that block. cyclopts renders the docstring below the Parameters box; markdown bullets keep one line per example (verified against cyclopts 4.22 rich rendering).

- [ ] **Step 1: Write failing tests**

`tests/test_examples.py`: (1) `render_examples("jira_search")` contains `--jql` and `currentUser()`; (2) `render_examples("confluence_get_page")` contains both `--page-id` and `--title`; (3) `render_examples("nonexistent_tool")` returns `None`; (4) structural: every key in `EXAMPLES` starts with `"jira_"` or `"confluence_"`; every example line starts with `"atli "` and mentions only kebab flags (each `--`-token matches `^--[a-z][a-z0-9-]*$` — no underscores); (5) every corpus tool name maps to a real mcp-atlassian server tool: assert the name (minus service prefix, kebab→snake) appears as a function in `.venv/lib/python3.11/site-packages/mcp_atlassian/servers/{service}.py` — read that file in the test and check `f"def {snake_name}(" ` or `f"async def {snake_name}("` occurs, skipping with a clear failure message if the venv path is absent.

In `tests/test_build.py`: (6) `<tool> --help` for a spec with `tool_name="jira_search"` and params `jql, fields, limit` (types str/str/int, jql required) contains the string `assignee = currentUser()` and `Examples:`; (7) the same help contains the param table row `--jql` (examples and params coexist); (8) a spec with an unknown tool_name renders help WITHOUT the substring `Examples:`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_examples.py tests/test_build.py -k "example" -v`
Expected: FAIL — module `mcp_atlassian_cli.examples` does not exist.

- [ ] **Step 3: Implement**

Create `examples.py` with the corpus verbatim from Produces (module docstring explains the curation rules: examples teach identifiers and canonical formats; long-content examples use `@file`; corpus stays small and high-traffic). `render_examples` does the lookup + join. Wire `_make_handler` per Produces. If test (5) flags a name mismatch, fix the corpus entry — the mcp-atlassian source is the source of truth, NOT the corpus.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_examples.py tests/test_build.py -v`
Expected: PASS.

- [ ] **Step 5: Update README + commit**

README "Notes for agents": after the schema-description bullet add: popular tools show a real invocation under `Examples:` in `--help` (identifiers like `--page-id`, JQL/date formats, `@file` for long content). Commit: `git add src/mcp_atlassian_cli/examples.py src/mcp_atlassian_cli/build.py tests/test_examples.py tests/test_build.py README.md && git commit -m "feat: curated examples in tool --help"`

---

### Task 3: `@file` / stdin value expansion

**Files:**
- Create: `src/mcp_atlassian_cli/expand.py`
- Modify: `src/mcp_atlassian_cli/build.py` (`_make_handler` inner `handler`, ~lines 82–90)
- Modify: `src/mcp_atlassian_cli/main.py` (dispatch try-block, after the `except CycloptsError` clause at ~line 103)
- Test: `tests/test_expand.py` (new), `tests/test_build.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `mcp_atlassian_cli.config.ConfigError`; `ToolParam.name` via `discovery.to_kebab` for flag display.
- Produces (in `expand.py`):
  - `expand_string(value: str, flag: str) -> str` — exact rules, in order:
    1. `value == "-"` and `sys.stdin` is NOT a tty → return all of stdin (read to EOF, verbatim, no strip). If stdin IS a tty → return `"-"` unchanged (never block).
    2. `value.startswith("@@")` → return `value[1:]` (one `@` stripped).
    3. `value == "@"` → return unchanged (no path to read).
    4. `value.startswith("@")` with `path = value[1:]`: if `Path(path).is_file()` → return `Path(path).read_text(encoding="utf-8")` verbatim (no strip). If reading raises `OSError`/`UnicodeDecodeError` → raise `ConfigError(f"--{flag}: could not read '{path}': {error}")`. If the file does not exist (or is a directory) → raise `ConfigError(f"--{flag} {value}: file not found. Use @@{value[1:]} to pass a literal '@' value")`.
    5. anything else → return unchanged.
  - `expand_value(value: Any, flag: str) -> Any` — `str` → `expand_string`; `list`/`tuple` → same-length sequence applying `expand_string` to `str` elements and passing others through; every other type → unchanged.
- Produces (build.py): inside the generated handler, for each spec param, AFTER default-fill and BEFORE the `None`-drop: if the value is not `None`, it passes through `expand.expand_value(value, to_kebab(param.name))`. Expansion applies uniformly to final values (user-passed or schema default — the pinned mcp-atlassian surface has no `@`/`-`-only string defaults).
- Produces (main.py): the dispatch try-block gains `except config.ConfigError as error: print(error, file=sys.stderr); return 2` (ConfigError from inside a handler propagates through cyclopts uncaught; this maps it to the exit-2 contract).

- [ ] **Step 1: Write failing tests**

`tests/test_expand.py` (use `tmp_path`, `monkeypatch` for stdin — a stub object with `isatty()` returning False/True and the piped text): (1) `@file` with known content including a trailing newline → contents verbatim; (2) `@missing.md` → raises `ConfigError`, message contains `--body @missing.md: file not found` and `@@missing.md`; (3) `@@literal` → `@literal`; (4) bare `"@"` → unchanged; (5) `"-"` with non-tty stdin stub → stdin text; `"-"` with tty stub → unchanged; (6) plain strings with no leading `@` (`"hello"`, `"user@example.com"`) → unchanged; (7) `expand_value` on `["alice", 3]` with a `@file` element → file contents replace the string, `3` untouched; (8) int/bool passthrough.

`tests/test_build.py` (end-to-end through dispatch): (9) write `tmp_path/comment.md`, invoke `atli jira add-comment --issue-key P --body @{path}` on a spec with a `body` str param → `DispatchSpy` received `{"issue_key": "P", "body": "<file contents>"}`; (10) required str param given `@missing` → the invocation raises `ConfigError` through `app(..., exit_on_error=False)` (assert raises).

`tests/test_main.py` (exit-code mapping): (11) `main(["jira", "get-issue", "--issue-key", "@/no/such/file"], runner_factory=<stub from existing tests>)` returns `2` and stderr contains `file not found` (follows the existing test_main stub-runner pattern).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_expand.py -v`
Expected: FAIL — module does not exist. Then `tests/test_build.py -k body` and `tests/test_main.py -k file` also FAIL.

- [ ] **Step 3: Implement**

Create `expand.py` per Produces (docstring documents the deliberate loud-error choice: a typo'd path silently becoming literal content is worse downstream). Wire the handler loop and the main.py except clause. Keep the existing `if value is not None` drop AFTER expansion.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_expand.py tests/test_build.py tests/test_main.py -v`
Expected: PASS (existing tests too — `test_dispatch_required_and_default` etc. prove no regression on plain values).

- [ ] **Step 5: Update README + commit**

README "Notes for agents": add — string values expand: `--body @file.md` reads the file, `-` reads stdin (when piped), `@@x` passes a literal `@x`; a missing file is a usage error showing the escape. Commit: `git add src/mcp_atlassian_cli/expand.py src/mcp_atlassian_cli/build.py src/mcp_atlassian_cli/main.py tests/ README.md && git commit -m "feat: @file/stdin expansion for long string values"`

---

### Task 4: Command "did you mean" suggestions

**Files:**
- Modify: `src/mcp_atlassian_cli/main.py` (`_run`'s `except CycloptsError` handler, ~lines 103–115)
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `cyclopts.exceptions.UnknownCommandError` with public fields `unused_tokens: list[str]` (first element = the offending token) and `command_chain: tuple[str, ...]` (parent command names, e.g. `("jira",)`); `specs: list[ToolSpec]` already in scope in `_run`.
- Produces: when the caught error is `UnknownCommandError` and `error.unused_tokens` is non-empty: token = `unused_tokens[0]`; candidate set = `command_chain` non-empty and its last element is a service name (`"jira"`/`"confluence"`) → all `spec.command_name` for that service (sorted); otherwise → `["tools", "profiles", "prime"]` plus every distinct service name in `specs`. `difflib.get_close_matches(token, candidates, n=1, cutoff=0.6)` yields a suggestion → append ` Did you mean "{suggestion}"?` to the printed message. No match → message unchanged. The `--profile` UnknownOptionError hint (existing) is untouched.

- [ ] **Step 1: Write failing tests**

`tests/test_main.py` using the existing stub `runner_factory` pattern with two jira specs (`get-issue`, `get-issue-worklog`) plus one confluence spec: (1) `main(["jira", "get-isue", "--issue-key", "X"], ...)` → returns 2, stderr contains `Unknown command "get-isue"` AND `Did you mean "get-issue"?`; (2) `main(["jra", ...])` → stderr contains `Did you mean "jira"?` (root-level candidates include services); (3) `main(["jira", "zzzzzz"], ...)` → stderr contains the original `Unknown command` message and does NOT contain `Did you mean`; (4) regression: `--profile` after the subcommand still gets `before the subcommand` hint and NO `Did you mean`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_main.py -k "mean" -v`
Expected: FAIL — stderr lacks `Did you mean`.

- [ ] **Step 3: Implement**

In the `CycloptsError` handler, branch on `isinstance(error, UnknownCommandError)` before printing; compute candidates and suggestion per Produces; append to the message string. One `import difflib` at module top. Keep the existing `--profile` append logic for `UnknownOptionError` as the sibling branch.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

`git add src/mcp_atlassian_cli/main.py tests/test_main.py && git commit -m "feat: did-you-mean suggestions for unknown commands"`

---

### Task 5: First-contact help polish

**Files:**
- Modify: `src/mcp_atlassian_cli/build.py` (`_ROOT_HELP`, `create_app` service-app creation ~line 215, prime stub registration, `tools` zero-match messages already done in Task 1)
- Test: `tests/test_build.py`

**Interfaces:**
- Consumes: nothing new; `create_app(specs, dispatch, profiles_text)` signature unchanged.
- Produces:
  - Each service `cyclopts.App` is created with `help=f"{service.title()} tools — run 'atli {service} <tool> --help' for parameters and examples."` so the root Commands table shows descriptions (today bare).
  - `_ROOT_HELP` rewritten as a one-line description plus markdown bullets (bullets survive cyclopts' rich rendering; multi-line prose does not). Required content, one bullet per line: the `atli tools [--service S] [--search TEXT]` discovery entry; the `atli <service> <tool> --help` entry mentioning parameters, defaults, and examples; the `--profile NAME` global-flag entry with "before the subcommand".
  - A display-only `prime` stub: a function named `prime` registered on the root app with docstring `Print the AI-agent primer (SessionStart hooks; see 'atli prime --install').` whose body raises `RuntimeError("prime must dispatch via the fast path")`. It exists only so `--help` lists `prime`; `main._run` intercepts `rest_argv[:1] == ["prime"]` before `create_app` is ever built, so the stub is unreachable in production (the raise is defense in depth).

- [ ] **Step 1: Write failing tests**

`tests/test_build.py`: (1) root `--help` output contains `Jira tools` and `Confluence tools` (service descriptions in the Commands table); (2) root `--help` contains `--search` and `--profile` and `Examples` or `examples` (bullet content present); (3) root `--help` lists a `prime` row (assert `prime` appears inside the Commands box — e.g. output contains `prime` with the docstring fragment `AI-agent primer`); (4) dispatching the stub directly via `app(["prime"], exit_on_error=False)` raises `RuntimeError` (documents the defense; production never reaches it — covered by (5)); (5) in `tests/test_main.py`: `main(["prime"], runner_factory=raises-if-called)` returns 0 WITHOUT constructing the runner (proves the fast path still wins over the stub).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_build.py -k "root_help or service or prime" -v && .venv/bin/pytest tests/test_main.py -k prime -v`
Expected: FAIL — service descriptions and prime row absent.

- [ ] **Step 3: Implement**

Apply the three Produces changes. Keep `_NO_VERSION_FLAGS` on the stub-holding root app as-is (the stub is a plain command, no version flags needed). Verify by eye: `.venv/bin/atli --help` renders bullets as separate lines and the `--profile` text no longer runs into neighboring text.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ -v`
Expected: PASS (full suite — this task touches shared help rendering).

- [ ] **Step 5: Commit**

`git add src/mcp_atlassian_cli/build.py tests/ && git commit -m "feat: first-contact help — service descriptions, visible prime, bullet root help"`

---

### Task 6: `atli prime --install` (Claude Code)

**Files:**
- Create: `src/mcp_atlassian_cli/install.py`
- Modify: `src/mcp_atlassian_cli/build.py` (`create_prime_app`'s `prime_command`, ~line 163)
- Modify: `README.md` (Priming section)
- Test: `tests/test_install.py` (new), `tests/test_build.py`

**Interfaces:**
- Consumes: `mcp_atlassian_cli.config.ConfigError`; `Path.home()` / `Path.cwd()` passed in as parameters (testability).
- Produces (in `install.py`):
  - `HOOK_COMMAND = "atli prime --hook-json"` (module constant).
  - `Harness` dataclass: `name: str`, `detect_dir_name: str` (relative to home), `settings_relpath: dict[str, str]` mapping scope → path relative to home (`"user"`) or cwd (`"project"`), `supported: bool`, `note: str` (shown when unsupported).
  - `HARNESSES: dict[str, Harness]` — `"claude"` (detect `~/.claude`, user `~/.claude/settings.json`, project `./.claude/settings.json`, supported, empty note); `"gemini"` (detect `~/.gemini`, unsupported, note `"SessionStart additionalContext is not injected upstream (gemini-cli issue #15413)"`); `"codex"` (detect `~/.codex`, unsupported, note `"hooks are experimental behind [features] codex_hooks"`). Paths exist in the table so later support is a data change.
  - `merge_hook(settings: dict) -> tuple[dict, bool]` — pure. If any entry of `settings["hooks"]["SessionStart"]` (missing keys = absent, treated as empty) contains a hooks-item whose `command` equals `HOOK_COMMAND` → `(settings, False)`. Otherwise returns a copy with `{"hooks": [{"type": "command", "command": HOOK_COMMAND}]}` appended to the SessionStart list. Raises `ConfigError` if `hooks` is present but not a dict, or `SessionStart` is present but not a list (message names the file-independent problem: `settings 'hooks.SessionStart' must be a list`).
  - `install(name: str, scope: str, *, home: Path, cwd: Path) -> str` — resolves the settings path from `HARNESSES[name].settings_relpath[scope]`; unsupported harness → returns `not supported yet: {name} — {note}` without touching disk. Reads the file if it exists: `json.JSONDecodeError` → raise `ConfigError(f"{path}: not valid JSON. Fix or remove the file, then re-run.")` with NO write. Merges; when unchanged → returns `already installed: {path}`. When changed → writes `indent=2` + trailing newline, preserving key order, and returns `installed: {path} (SessionStart += {HOOK_COMMAND})`.
  - `run_install(names: list[str] | None, scope: str, *, home: Path, cwd: Path) -> list[str]` — `names is None` → every harness whose `home / detect_dir_name` exists; explicit names → those (unknown name → `ConfigError` listing valid names). Returns one message string per harness.
- Produces (build.py): `prime_command(hook_json: bool = False, export: bool = False, install: bool = False, scope: str = "user", harness: list[str] | None = None)`. When `install` is True: validate `scope in {"user", "project"}` (else `ConfigError("--scope must be 'user' or 'project'")`), print each line from `run_install(harness, scope, home=Path.home(), cwd=Path.cwd())`, and return — before override/export logic. `--install` stays on the fast path (no mcp-atlassian import). `ConfigError` raised here already maps to exit 2 via `main._run`'s prime branch.

- [ ] **Step 1: Write failing tests**

`tests/test_install.py` (`tmp_path` home/cwd, `json` round-trips): (1) `merge_hook({})` returns changed=True and the appended entry equals `{"hooks": [{"type": "command", "command": "atli prime --hook-json"}]}` nested under `hooks.SessionStart`; (2) idempotent: merging the result of (1) again returns changed=False and an equal dict; (3) settings with unrelated keys (`model`, existing SessionStart entry with a different command, other hook events) keep them untouched and in order; (4) `hooks` present but a string → `ConfigError`; (5) `install("claude", "user", home=tmp)` with NO settings file creates it containing exactly the hook structure (parse the written JSON); (6) with a pre-existing corrupt file (`"{"`), raises `ConfigError` mentioning `not valid JSON` and the file content is UNCHANGED on disk; (7) `install("gemini", ...)` returns the `not supported yet: gemini` message and creates nothing; (8) `run_install(None, ...)` with only `home/.claude` present returns exactly one message (claude); (9) `run_install(["bogus"], ...)` raises `ConfigError` naming `claude, gemini, codex`; (10) `install("claude", "project", home=tmp_home, cwd=tmp_cwd)` writes `tmp_cwd/.claude/settings.json`.

`tests/test_build.py`: (11) `create_prime_app` invoked with `["prime", "--install", "--harness", "claude"]` under a `monkeypatch`ed `Path.home` (tmp) prints `installed:` and exits 0 (SystemExit 0 via `invoke`-style helper); (12) `["prime", "--install", "--scope", "bogus"]` surfaces `ConfigError` (raised through the app call).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_install.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

Create `install.py` per Produces (module docstring records the harness matrix decision: Claude Code verified; Gemini blocked by upstream additionalContext bug; Codex experimental). Extend `prime_command` per Produces. No changes to `render_default`/`read_override` behavior.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ -v`
Expected: PASS.

- [ ] **Step 5: Update README + commit**

README Priming section: lead with `atli prime --install` (auto-detects Claude Code; `--scope project` for repo-shared; idempotent), keep the manual hook JSON as the alternative, and note Gemini/Codex are detected but not auto-installed (with the one-line reasons). Commit: `git add src/mcp_atlassian_cli/install.py src/mcp_atlassian_cli/build.py tests/test_install.py tests/test_build.py README.md && git commit -m "feat: prime --install — idempotent Claude Code SessionStart hook onboarding"`

---

### Task 7: `prime` default content additions

**Files:**
- Modify: `src/mcp_atlassian_cli/prime.py` (`_DISCOVERY`, `_NOTES`)
- Modify: `AGENTS.md`
- Test: `tests/test_prime.py`

**Interfaces:**
- Consumes: existing `render_default` / `render_export` / silence rule — behavior unchanged, only the static strings grow.
- Produces: `_DISCOVERY` gains two lines (after the existing two): `atli tools --search TEXT              # shortlist by keyword` and `atli prime --install                  # onboard: SessionStart hook`; the `--help` discovery line's trailing comment becomes `# params, types, defaults, examples`. `_NOTES` gains one bullet: `- Long values read files: --content @page.md ('-' = stdin; '@@' = literal '@').` Output stays a couple dozen lines.

- [ ] **Step 1: Write failing tests**

`tests/test_prime.py`: (1) `render_export` output (works unconfigured) contains `--search`, `prime --install`, and `@page.md`; (2) the silence rule holds — `render_default` with empty environ returns `""` (existing test, must still pass); (3) every `_DISCOVERY` line remains ≤ 78 chars (guards the compactness contract; assert in test by splitting on newlines).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_prime.py -v`
Expected: FAIL — new substrings absent.

- [ ] **Step 3: Implement**

Edit the two constants verbatim from Produces. Update existing assertions in `test_prime.py` that pin the old literal lines (they will name themselves by failing).

- [ ] **Step 4: Run tests + full suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: PASS.

- [ ] **Step 5: Update AGENTS.md + commit**

AGENTS.md: extend the prime bullet with `--install`, and add `--search` + `@file` one-liners to the discovery line (keep the file under ~15 lines — every word is gold). Commit: `git add src/mcp_atlassian_cli/prime.py tests/test_prime.py AGENTS.md && git commit -m "feat: prime teaches --search, @file, and --install"`

---

## Manual Smoke (after all tasks)

Non-blocking verification against the real binary, from the repo root:
1. `.venv/bin/atli --help` — bullets on separate lines, service descriptions, `prime` row.
2. `.venv/bin/atli jira search --help` (on a configured instance) — Examples block with the JQL line; no Examples on a non-corpus tool.
3. `.venv/bin/atli jira get-isue --issue-key X` — `Did you mean "get-issue"?`
4. `echo hi | .venv/bin/atli jira add-comment --issue-key P --body -` (configured instance or stub) — stdin flows through.
5. `HOME=$(mktemp -d) .venv/bin/atli prime --install` — prints `not supported yet`/detection lines sanely with an empty HOME; on a HOME with `~/.claude` present, writes settings.json and a second run prints `already installed`.

## Self-Review Notes

- Spec §1 → Task 1; §2 → Task 2; §3 → Task 3; §4 → Task 6; §5 → Tasks 4+5 (+ Task 1's empty-state message); §6 → Task 7; Documentation → per-task README/AGENTS.md steps. No spec requirement is task-less.
- Cross-task contracts checked: Task 2's `render_examples` name/signature matches its build.py consumption in the same task; Task 3's `expand_value`/`ConfigError` contract matches Task 3's own main.py wiring; Task 5's prime stub depends on nothing from Task 6 (docstring mentions `--install` before it exists — README ordering handles disclosure; stub text is final).
- Harness matrix (spec Open Question 1) resolved during planning: Claude Code supported; Gemini CLI blocked upstream (SessionStart `additionalContext` not injected — gemini-cli issue #15413); Codex experimental (`codex_hooks` feature flag, known firing issues). Recorded in `install.py`'s table and docstring.
