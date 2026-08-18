# `atli prime` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `atli prime [--hook-json] [--export]` — an AI-agent primer of the local atli setup, cheap enough for SessionStart hooks (never imports mcp-atlassian).

**Architecture:** A new pure module `prime.py` (content, service detection, override resolution, hook envelope) + a `create_prime_app` factory in `build.py` + a fast-path fork in `main.py` that dispatches `prime` before the `ToolRunner` is ever imported. `config.py`, `discovery.py`, `runner.py` are untouched.

**Tech Stack:** Python 3.11+ stdlib (prime.py), cyclopts 4.22 (build wiring), pytest (tests). No new dependencies.

**Spec:** `docs/superpowers/specs/active/2026-08-19-atli-prime-design.md`

## Global Constraints

- No new dependencies; `prime.py` imports only the standard library (plus `mcp_atlassian_cli.config` for `ConfigError`).
- The `prime` dispatch path must never import `mcp_atlassian` or `fastmcp` — guarded by a subprocess test.
- Exit codes: 0 success (including silence), 2 usage/config errors. No new codes.
- Every `cyclopts.App` is built with `version_flags=[]` (project convention — see `build._NO_VERSION_FLAGS`).
- Tests run with `.venv/bin/pytest` from the repo root; the suite is hermetic (each new test neutralizes `ATLI_PRIME`, `ATLI_CONFIG`, `HOME`, and service env vars it does not set itself — the `hermetic_env` fixture in `tests/test_main.py` does **not** clear `ATLI_PRIME`).
- Primer copy is product content: reproduce the em-dashes, punctuation, and wording of Task 3 verbatim.
- Commit style: `feat:` / `test:` / `docs:` prefixes (match `git log`).

## File Structure

- **Create `src/mcp_atlassian_cli/prime.py`** — pure primer logic: `detect_services`, `read_override`, `render_default`, `render_export`, `wrap_hook_json`, and the default template. No cyclopts, no MCP imports. One responsibility: decide and render the primer.
- **Create `tests/test_prime.py`** — unit tests for everything in `prime.py`.
- **Modify `src/mcp_atlassian_cli/build.py`** — add `create_prime_app` (factory-built `prime` command; mirrors `_make_profiles_command`). All cyclopts wiring stays here.
- **Modify `tests/test_build.py`** — tests for `create_prime_app` dispatch semantics.
- **Modify `src/mcp_atlassian_cli/main.py`** — the fast-path fork in `_run`.
- **Modify `tests/test_main.py`** — fast-path integration tests.
- **Modify `README.md`, `AGENTS.md`** — user/agent documentation.

---

### Task 1: Service detection — `prime.detect_services`

**Files:**
- Create: `src/mcp_atlassian_cli/prime.py`
- Test: `tests/test_prime.py`

**Interfaces:**
- Consumes: `mcp_atlassian_cli.config.ConfigError` (not needed yet — imported in Task 2).
- Produces: `detect_services(environ: Mapping[str, str]) -> tuple[bool, bool]` — `(jira_configured, confluence_configured)`. A variable counts as set when present and non-empty. A service is configured iff `<SVC>_URL` is set AND at least one credential combination is set: `<SVC>_USERNAME`+`<SVC>_API_TOKEN` (cloud/DC basic), or `<SVC>_PERSONAL_TOKEN` (PAT), or `<SVC>_CLIENT_CERT` (mTLS; `+KEY` optional). OAuth-only setups (`ATLASSIAN_OAUTH_*`) are deliberately not detected — documented limitation, the README auth matrix is the contract.

Module docstring: pure primer logic for SessionStart hooks — no cyclopts, no MCP imports; the prime path must stay cheap.

- [ ] **Step 1: Write failing tests**

In `tests/test_prime.py`, table-driven over plain dicts passed straight to `detect_services` (no env fixtures needed):
1. Jira cloud combo (`JIRA_URL`+`JIRA_USERNAME`+`JIRA_API_TOKEN`) → `(True, False)`.
2. Jira PAT (`JIRA_URL`+`JIRA_PERSONAL_TOKEN`) → `(True, False)`.
3. Jira mTLS (`JIRA_URL`+`JIRA_CLIENT_CERT`, no `JIRA_CLIENT_KEY`) → `(True, False)`.
4. Confluence PAT (`CONFLUENCE_URL`+`CONFLUENCE_PERSONAL_TOKEN`) → `(False, True)`.
5. Both services configured (any combos) → `(True, True)`.
6. URL only, no credentials → `(False, False)`.
7. `JIRA_USERNAME` without `JIRA_API_TOKEN` (plus URL) → `(False, ...)`.
8. Empty-string values (`JIRA_URL=""` + full cloud combo) → not configured.
9. Empty dict → `(False, False)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_prime.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_atlassian_cli.prime'` (or `AttributeError`).

- [ ] **Step 3: Minimal implementation**

Create `src/mcp_atlassian_cli/prime.py` with the module docstring and `detect_services` per the Produces contract: per service, URL-set AND any-of-three credential combos, empty strings counting as unset.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_prime.py -v`
Expected: PASS (all detection cases).

- [ ] **Step 5: Commit**

Run: `git add src/mcp_atlassian_cli/prime.py tests/test_prime.py`
Run: `git commit -m "feat: prime service detection from post-profile env"`

---

### Task 2: Override resolution — `prime.read_override`

**Files:**
- Modify: `src/mcp_atlassian_cli/prime.py`
- Test: `tests/test_prime.py`

**Interfaces:**
- Consumes: `detect_services` (already in module); `mcp_atlassian_cli.config.ConfigError` — `prime.py` imports `ConfigError` from `config` (stdlib-only module, keeps the fast path cheap).
- Produces: `read_override(environ: Mapping[str, str]) -> str | None` — the override file's content with trailing whitespace stripped, or `None` when no override exists. Resolution order, first existing file wins:
  1. `$ATLI_PRIME` — if set and non-empty: must be an existing file, else raise `ConfigError("ATLI_PRIME is set to '<value>', but that file does not exist. Point ATLI_PRIME at an existing markdown file, or unset it.")` (mirrors `ATLI_CONFIG` wording in `config.find_config_file`).
  2. `Path.cwd() / ".atli" / "PRIME.md"`
  3. `Path.home() / ".config" / "atli" / "PRIME.md"`

  Read errors (any `OSError` or `UnicodeDecodeError`) raise `ConfigError("Could not read PRIME override '<path>': <error>")`. An existing-but-empty file yields `""` (an explicit override that prints nothing).

- [ ] **Step 1: Write failing tests**

Using `tmp_path`, `monkeypatch.chdir`, `monkeypatch.setenv("HOME", ...)`, and `monkeypatch.delenv("ATLI_PRIME", raising=False)` (the `hermetic_env` fixture does not clear it):
1. `ATLI_PRIME` set to an existing file → returns its content, trailing newlines stripped; files at the cwd/home candidate paths are ignored.
2. `ATLI_PRIME` set to a missing path → raises `ConfigError` whose message contains `ATLI_PRIME` and the bad value.
3. No `ATLI_PRIME`, `.atli/PRIME.md` under cwd exists, `~/.config/atli/PRIME.md` under fake HOME exists → cwd file wins.
4. No `ATLI_PRIME`, no cwd file, home file exists → home file returned.
5. Nothing anywhere → `None`.
6. Existing override with mode `0o000` → `ConfigError` containing `Could not read PRIME override` (skip via `pytest.mark.skipif(os.geteuid() == 0, ...)` to stay honest on root CI).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_prime.py -v`
Expected: FAIL — `ImportError: cannot import name 'read_override'`.

- [ ] **Step 3: Minimal implementation**

Add `read_override` per the Produces contract: explicit-env check first (error on set-but-missing), then candidate scan (`is_file()`), read with errors mapped to `ConfigError`, return stripped content or `None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_prime.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/mcp_atlassian_cli/prime.py tests/test_prime.py`
Run: `git commit -m "feat: prime PRIME.md override resolution ($ATLI_PRIME > .atli/ > ~/.config/)"`

---

### Task 3: Default content — `prime.render_default` / `prime.render_export`

**Files:**
- Modify: `src/mcp_atlassian_cli/prime.py`
- Test: `tests/test_prime.py`

**Interfaces:**
- Consumes: `detect_services` from Task 1.
- Produces:
  - `render_default(environ: Mapping[str, str], profile_name: str | None, config_path: Path | None) -> str` — the full primer; **`""` when no service is configured** (the silence rule).
  - `render_export(environ, profile_name, config_path) -> str` — the same template, never silenced: when no service is configured the Configured line reads `Configured: (none)` and both example lines appear.

  Both share one template assembly (the only difference is the silence rule and the none-case rendering). Exact content contract — dynamic lines first, then the static core:

  - Line 1 (static): `# atli — Jira & Confluence CLI`
  - Line 3: `Configured: ` + comma-space-joined configured service names in fixed order `jira, confluence` (export, none configured: `Configured: (none)`)
  - Line 4 (Profile line), one of:
    - omitted entirely when `config_path is None`
    - `Profile: <profile_name> (<display>)` when `profile_name` is not None; `<display>` is `str(config_path)` with a leading `str(Path.home())` replaced by `~`
    - `Profile: ambient environment` when `profile_name` is None but a config file exists
  - Usage block: the generic pattern line `atli [--profile NAME] <service> <tool> [flags]`, then `atli jira get-issue --issue-key PROJ-1` iff jira is configured, then `atli confluence search --query "deploy"` iff confluence is configured (in export's none-case both appear).
  - Discovery block (static):
    ```
    atli tools [--service jira]           # one line per tool
    atli jira get-issue --help            # params, types, defaults
    ```
  - Notes block (static):
    ```
    - Tool output prints verbatim (LLM-ready markdown from mcp-atlassian).
    - Repeatable list flags repeat: `--read-users alice --read-users bob`.
    - Exit codes: 0 success, 1 tool/server failure, 2 usage/config error.
    - Startup ~1 s warm; prefer one `search` over many single-item calls.
    ```

  Section headers are `## Usage`, `## Discovery`, `## Notes`; blank lines separate every block exactly as in the spec's Output contract sketch.

- [ ] **Step 1: Write failing tests**

1. **Canonical full-output test** (both services configured via cloud combos, `profile_name="work"`, `config_path=Path.home()/"work.toml"` under a faked HOME): assert the returned string equals the exact expected document (write the expected literal in the test from the contract above — this pins the copy).
2. Jira only → contains `Configured: jira`; the confluence example line is absent; the jira example line is present.
3. Confluence only → mirror image.
4. Neither service → `render_default` returns `""`.
5. `profile_name=None`, `config_path` set → contains `Profile: ambient environment`.
6. `config_path=None` → output contains no `Profile:` line at all.
7. Config path under HOME → displayed tilde-abbreviated (`Profile: work (~/.config/atli/config.toml)` shape); config path outside HOME → displayed verbatim.
8. `render_export` with nothing configured → non-empty, contains `Configured: (none)` and both example lines.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_prime.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_default'`.

- [ ] **Step 3: Minimal implementation**

Add the template and both render functions per the Produces contract: assemble header lines from `detect_services`/profile args, filter example lines by configured services, apply the silence rule in `render_default` only.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_prime.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/mcp_atlassian_cli/prime.py tests/test_prime.py`
Run: `git commit -m "feat: prime default content rendering (header + filtered examples)"`

---

### Task 4: Hook envelope — `prime.wrap_hook_json`

**Files:**
- Modify: `src/mcp_atlassian_cli/prime.py`
- Test: `tests/test_prime.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `wrap_hook_json(content: str) -> str` — one line of JSON, bd-identical shape:
  `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":<content>}}`
  Compact separators (no spaces), keys in exactly that order, non-ASCII characters raw (`ensure_ascii=False`), no newline inside the returned string.

- [ ] **Step 1: Write failing tests**

1. Round-trip: for a content string containing double quotes, backslashes, newlines, tabs, and non-ASCII (e.g. `# atli — "quoted"\n\ttab \\ end`), `json.loads` of the output gives `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": <that exact string>}}`.
2. `"\n"` not in output (single line).
3. Output starts with `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":`.
4. Empty content → `additionalContext == ""`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_prime.py -v`
Expected: FAIL — `ImportError: cannot import name 'wrap_hook_json'`.

- [ ] **Step 3: Minimal implementation**

Add `wrap_hook_json` per the Produces contract (a `json.dumps` of the nested dict with compact separators and `ensure_ascii=False`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_prime.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/mcp_atlassian_cli/prime.py tests/test_prime.py`
Run: `git commit -m "feat: prime SessionStart hook envelope"`

---

### Task 5: Command wiring — `build.create_prime_app`

**Files:**
- Modify: `src/mcp_atlassian_cli/build.py`
- Test: `tests/test_build.py`

**Interfaces:**
- Consumes: `prime.read_override`, `prime.render_default`, `prime.render_export`, `prime.wrap_hook_json` (Tasks 2–4); existing `build._NO_VERSION_FLAGS`.
- Produces: `create_prime_app(environ: Mapping[str, str], profile_name: str | None, config_path: Path | None) -> cyclopts.App` — an `App(name="atli", help="atli prime — AI-agent primer (SessionStart hooks)", version_flags=[])` registering one command from a factory function `prime(hook_json: bool = False, export: bool = False) -> None` (docstring: `Print an AI-agent primer for this atli setup (SessionStart-hook friendly).`) that closes over the three context arguments, mirroring how `_make_profiles_command` closes over its text. Command behavior, in order:

  1. `export=True` → content is `prime.render_export(environ, profile_name, config_path)`; print it as plain markdown regardless of `hook_json`.
  2. `export=False` → content is `prime.read_override(environ)` when that is not `None` (may raise `ConfigError`), else `prime.render_default(...)`.
  3. Output: with `hook_json` (and not export) print `prime.wrap_hook_json(content)`; otherwise print `content` **only when non-empty** — silent content plus no `--hook-json` produces completely empty stdout (no blank line).

  `--help` must render through cyclopts normally (flag docs from the signature).

- [ ] **Step 1: Write failing tests**

In `tests/test_build.py`, reusing the file's `invoke` helper and `capsys`; pass plain dict envs and control override lookup with `monkeypatch` (`delenv("ATLI_PRIME")`, `chdir(tmp_path)`, HOME to tmp):
1. Both services configured (Jira cloud combo + Confluence PAT), `invoke(app, ["prime"])` → exit fine; stdout contains the title line `# atli — Jira & Confluence CLI`, the line `Configured: jira, confluence`, and both example lines (copy itself is pinned by Task 3's exact-equality test; these tests pin dispatch semantics).
2. Same env, `invoke(app, ["prime", "--hook-json"])` → stdout is one line; `json.loads` gives non-empty `additionalContext` containing the title line.
3. Nothing configured, `invoke(app, ["prime"])` → stdout is exactly `""` (completely empty).
4. Nothing configured, `--hook-json` → one line whose `additionalContext` is `""`.
5. **Override beats silence:** nothing configured, override file at `.atli/PRIME.md` under cwd with marker content `TEAM PRIME OVERRIDE` → plain `prime` prints exactly the override and nothing else (no header lines); `--hook-json` wraps the override; `--export` ignores the override and prints the default export rendering (contains `Configured: (none)`, not the marker).
6. `ATLI_PRIME` pointing at a missing file → invoking `prime` raises `ConfigError` (pytest.raises around `invoke`).
7. `prime --help` → `SystemExit` code 0, output mentions `--hook-json` and `--export`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_build.py -v -k prime`
Expected: FAIL — `ImportError: cannot import name 'create_prime_app'`.

- [ ] **Step 3: Minimal implementation**

Add `create_prime_app` to `build.py` per the Produces contract (import `mcp_atlassian_cli.prime` at module top — it is stdlib-cheap), following the `_make_profiles_command` factory pattern.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_build.py -v`
Expected: PASS (new and all existing build tests).

- [ ] **Step 5: Commit**

Run: `git add src/mcp_atlassian_cli/build.py tests/test_build.py`
Run: `git commit -m "feat: create_prime_app — prime command with --hook-json and --export"`

---

### Task 6: Fast path — `main._run` dispatches `prime` before the runner

**Files:**
- Modify: `src/mcp_atlassian_cli/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `build.create_prime_app` (Task 5); `config.ConfigError`, `cyclopts.exceptions.CycloptsError` (already imported in `main`); the existing `_run` locals after profile resolution: `rest_argv`, `profile_name`, `config_data` (with `.path`), and the post-application `os.environ`.
- Produces: behavior — in `_run`, after the config try/except block and **before** `from mcp_atlassian_cli.build import create_app` / the runner import: if `rest_argv[:1] == ["prime"]`, build the prime app (`create_prime_app(os.environ, profile_name, config_data.path)`), invoke it as `app(rest_argv, exit_on_error=False, print_error=False)`, and map exceptions exactly like the existing dispatch block: `CycloptsError` → stderr + return 2; `config.ConfigError` → stderr + return 2; `SystemExit` → its int code (or 0 for `None`); otherwise return 0. Every other command path is byte-identical to before.

- [ ] **Step 1: Write failing tests**

In `tests/test_main.py` (the `hermetic_env` fixture already isolates HOME/config; add `monkeypatch.delenv("ATLI_PRIME", raising=False)` per test):
1. **Runner never constructed:** with a full Jira cloud combo and a Confluence PAT set (`monkeypatch.setenv`), `main(["prime"], runner_factory=<a function that raises AssertionError("runner must not be constructed")>)` → exit 0, primer on stdout, factory uncalled.
2. `main(["prime", "--hook-json"])` with services configured → exit 0; stdout parses as JSON with non-empty `additionalContext`.
3. `main(["prime", "--help"])` with a stub factory → exit 0, no stderr, `--hook-json` documented in stdout.
4. `main(["prime", "--bogus"])` with a stub factory → exit 2, non-empty stderr, empty stdout.
5. Profile flow: write `.atli.toml` with a `work` profile holding a FULL cloud combo (`JIRA_URL` + `JIRA_USERNAME` + `JIRA_API_TOKEN` — a URL+token-only profile does not count as configured and would trigger silence), then `main(["--profile", "work", "prime"], ...)` → exit 0, `Configured: jira` in stdout, `Profile: work` in stdout (displayed config path is the tmp cwd's `.atli.toml`).
6. Silence: no service env, no config → `main(["prime"], ...)` → exit 0, stdout exactly `""`.
7. `ATLI_PRIME` set to a missing file → exit 2, stderr contains `ATLI_PRIME`.
8. **No server import (subprocess, mirrors `test_no_server_import_at_module_import`):** run `python -c` that calls `main(["prime", "--hook-json"])` with a minimal env (`JIRA_URL`/`JIRA_USERNAME`/`JIRA_API_TOKEN` set, `ATLI_CONFIG`/`ATLI_PRIME` unset, HOME pointed at an empty tmp dir, cwd outside any `.atli.toml`), then asserts `"mcp_atlassian" not in sys.modules` and exit code 0.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_main.py -v -k prime`
Expected: FAIL — cases 1–8 fail because `prime` reaches the normal dispatch (case 1 raises the factory's AssertionError; others exit 2 as unknown commands).

- [ ] **Step 3: Minimal implementation**

Insert the fast-path branch in `_run` per the Produces contract: guard on the first token, import only `create_prime_app`, dispatch, map exits, return.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: PASS (new and all existing main tests).

- [ ] **Step 5: Full suite + commit**

Run: `.venv/bin/pytest -q`
Expected: all pass, no regressions.

Run: `git add src/mcp_atlassian_cli/main.py tests/test_main.py`
Run: `git commit -m "feat: main fast path — atli prime dispatches without importing mcp-atlassian"`

---

### Task 7: Documentation — README + AGENTS.md

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: the finished command surface from Tasks 1–6.
- Produces: documentation only — no behavior.

- [ ] **Step 1: README — new section**

Add `## Priming AI agents (\`atli prime\`)` between the **Profiles** section and **Exit codes**, containing:
- One intro sentence: prints a compact primer of the local setup (configured services, active profile, usage, quirks) for AI agents; designed for SessionStart hooks; never imports mcp-atlassian so it costs milliseconds.
- Usage block: `atli prime [--hook-json] [--export]`.
- Claude Code hook snippet (a `settings.json` fragment): `{"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "atli prime --hook-json"}]}]}}` — with one sentence noting the same envelope serves Gemini CLI and Codex.
- Flag semantics: `--hook-json` wraps output in the SessionStart envelope; `--export` prints the default content (ignores overrides, works unconfigured).
- Override lookup order as a numbered list (`$ATLI_PRIME` must exist if set; `./.atli/PRIME.md`; `~/.config/atli/PRIME.md`) and the replacement rule (replaces the default entirely, prints even when unconfigured).
- Silence rule sentence: nothing configured and no override → empty output, exit 0.
- One limitation sentence: the Configured line reads exported variables and profiles only — `.env` files (consumed inside mcp-atlassian) are invisible to prime.

- [ ] **Step 2: AGENTS.md — one line**

Add to the bullet list: `- \`atli prime [--hook-json]\`: compact primer of this setup (configured services, profile, usage) for SessionStart hooks; override via \`.atli/PRIME.md\`.`

- [ ] **Step 3: Verify docs render sensibly**

Run: `.venv/bin/pip install -e . -q && .venv/bin/atli prime --export | head -5`
Expected: the default primer prints (title + Configured/Profile lines + Usage).

- [ ] **Step 4: Commit**

Run: `git add README.md AGENTS.md`
Run: `git commit -m "docs: atli prime — SessionStart priming, overrides, hook snippet"`

---

## Verification (whole plan)

After Task 7: `.venv/bin/pytest -q` green; manual smoke of `atli prime`, `atli prime --hook-json`, `atli prime --export`, and `ATLI_PRIME=/missing atli prime` (exit 2). Then `superpowers:finishing-a-development-branch` handles merge + spec/plan archival.
