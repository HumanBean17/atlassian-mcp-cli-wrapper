# Schema-Faithful Param Help & Self-Correcting `--profile` Error — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show each tool parameter's schema description in `atli <service> <tool> --help`, and teach the correct placement when `--profile` appears after the subcommand — without changing any released contract.

**Architecture:** Two seams, both already isolated. `discovery.py` (pure schema parsing) gains a field; `build.py` (pure spec→cyclopts wiring) renders it via cyclopts' per-parameter help; `main.py`'s existing `CycloptsError` handler appends a placement hint that `config.py` already owns as a constant. Spec: `docs/superpowers/specs/active/2026-08-19-agent-first-help-fidelity-design.md`.

**Tech Stack:** Python 3.11 stdlib + installed pins (`mcp-atlassian>=0.23,<0.24`, `cyclopts>=4.22,<5`, fastmcp 3.4.x, pytest). No new dependencies.

## Global Constraints

- Work happens in the worktree `.claude/worktrees/agent-first-help-fidelity` (branch `worktree-agent-first-help-fidelity`); run everything from its root. Test runner: `.venv/bin/python -m pytest` (the worktree has its own venv; do NOT reuse the main checkout's).
- No new dependencies; no changes to `pyproject.toml`.
- Output contract unchanged: tool output verbatim on stdout, single-line errors on stderr, exit codes 0 success / 1 tool failure / 2 usage-config error.
- No `--version` flag anywhere: every `cyclopts.App` keeps `version_flags=[]` (real `version` tool params must reach the tool).
- Schema text (parameter descriptions) passes through verbatim — no truncation, no sentence-splitting.
- `mcp_atlassian` must never be imported at module import time of `mcp_atlassian_cli.main` (env is applied first); `build.py`/`discovery.py` stay MCP-free.
- Conventional commit style (`feat:`, `tests:`, `docs:`), matching repo history.
- Baseline at plan time: 68 tests passing.

---

### Task 1: `ToolParam.description` parsed from the tool schema

**Files:**
- Modify: `src/mcp_atlassian_cli/discovery.py:20-27` (the `ToolParam` frozen dataclass) and `src/mcp_atlassian_cli/discovery.py:64-72` (the params tuple inside `parse_tool`)
- Test: `tests/test_discovery.py`

**Interfaces:**
- Consumes: existing `parse_tool(tool) -> ToolSpec` over MCP-`Tool`-shaped objects (`SimpleNamespace` in tests); schema properties are dicts with optional `"type"`, `"default"`, and now `"description"` keys.
- Produces: `ToolParam` gains one field, after `default`: `description: str | None = None`. Because it defaults, every existing construction (keyword args in `tests/test_main.py`'s `SpyRunner`, positional in other tests) stays valid unchanged. `parse_tool` sets it from the property's `"description"` string when present, `None` when the key is absent. No other field, function, or module changes in this task.

- [ ] **Step 1: Write the failing test**

In `tests/test_discovery.py`, extend `test_parse_tool_full` (its tool already has four properties): give the `issue_key` property a `"description": "The issue key, e.g. PROJ-123."` entry and leave the other three properties without one. Add one assertion to the test: the list of each param's `description` equals `["The issue key, e.g. PROJ-123.", None, None, None]` in param order. This verifies present-string extraction, absent-key → `None`, and order preservation in one scenario.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_discovery.py -v`
Expected: FAIL — either `TypeError` from the dataclass rejecting an unexpected data path or, more likely, an `AttributeError`/assertion failure because `ToolParam` has no `description` attribute.

- [ ] **Step 3: Write minimal implementation**

In `discovery.py`: add `description: str | None = None` as the last field of `ToolParam` (frozen dataclass, so field order with a default last keeps all existing constructions legal). In `parse_tool`'s params tuple, pass the property's `"description"` value (`prop.get("description")`) into the new field. Keep everything else in the function byte-identical.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_discovery.py -v`
Expected: PASS (all discovery tests, including the extended one).

- [ ] **Step 5: Run the full suite (regression gate)**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS, 68 tests + 0 new (extended in place), 0 failures.

- [ ] **Step 6: Commit**

Run: `git add src/mcp_atlassian_cli/discovery.py tests/test_discovery.py`
Run: `git commit -m "feat: parse per-param schema descriptions into ToolParam"`

---

### Task 2: Render parameter descriptions in `--help`

**Files:**
- Modify: `src/mcp_atlassian_cli/build.py:52-89` (`_make_handler` — the generated-signature block) and its module docstring/comment context
- Modify: `README.md:127-135` ("Notes for agents" section)
- Test: `tests/test_build.py`

**Interfaces:**
- Consumes: `ToolParam.description: str | None` from Task 1 (same import path `mcp_atlassian_cli.discovery.ToolParam`, constructed by keyword in this file's tests).
- Produces: `_make_handler`'s per-parameter annotation composition — the contract later tasks and production rely on:
  - Base annotation rule (unchanged from current code): `param.type` when `param.required` or `param.default is not None`; otherwise `param.type | None` (the pydantic default-validation quirk documented in the comment at `build.py:76-83` — this rule must survive intact).
  - Final annotation: when `param.description` is a non-empty string, the base is wrapped as `typing.Annotated[base, cyclopts.Parameter(help=param.description)]`; otherwise the base is used exactly as today. Nothing else about the generated signature changes (still `KEYWORD_ONLY`, same names, same defaults).
  - Observable behavior: `atli <service> <tool> --help` prints each description next to its flag inside cyclopts' Parameters box (word-wrapped by cyclopts), while `[required]` and `[default: …]` markers keep printing; dispatch behavior is completely unchanged.
- README addition (exact content, appended as a new bullet in "Notes for agents"): `Parameter descriptions in a tool's --help come verbatim from the tool's schema — accepted formats and semantics, straight from the source.`

- [ ] **Step 1: Write the failing tests**

Two new tests in `tests/test_build.py`, using the file's existing helpers (`jira_get_issue_spec`, `DispatchSpy`, `invoke`) and the help-invocation pattern of `test_help_lists_tool` (`pytest.raises(SystemExit)` around `app([...], exit_on_error=True)`, then read `capsys`):

1. `test_help_shows_param_descriptions` — build params: `issue_key` (required, `description="The issue key, e.g. PROJ-123."`), `compact` (bool, default `False`, `description="Return a compact view."`), `comment_limit` (int, default `10`, **no** description). Invoke `["jira", "get-issue", "--help"]`. Assert captured stdout contains both description strings verbatim, still contains `--issue-key`, still contains `[required]`, and still contains `[default: 10]` (markers survive alongside descriptions; the no-description param renders as before).
2. `test_dispatch_intact_with_annotated_optional_none` — mirrors `test_optional_none_default_with_pydantic_loaded`: params `issue_key` (required), `expand` (str, optional, default `None`, **with** a description — the `T | None` base must compose inside `Annotated` or pydantic rejects the `None` default at parse time), `comment_limit` (int, optional, default `10`, no description). Import `pydantic` first (same noqa trick as the existing test), dispatch `["jira", "get-issue", "--issue-key", "P"]`, assert the spy receives exactly `("jira_get_issue", {"issue_key": "P", "comment_limit": 10})` — `expand` absent because `None`-valued optionals are dropped, per the existing handler contract.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_build.py -v -k "param_descriptions or annotated_optional_none"`
Expected: test 1 FAILS (descriptions absent from help output). Test 2 PASSES before the change (today's plain annotations parse fine) — it is the regression guard that must STILL pass after the `Annotated` wrapping; a post-implementation FAIL there means the composition broke the `T | None` base rule.

- [ ] **Step 3: Write minimal implementation**

In `_make_handler`: keep the handler body and `__doc__` untouched. Compute each parameter's annotation per the Produces contract: existing `T | None` rule produces the *base*; when the param carries a non-empty description, wrap that base with `typing.Annotated` carrying `cyclopts.Parameter(help=...)`; otherwise use the base alone. Add the needed imports (`Annotated` from `typing`, `Parameter` from `cyclopts`). Update the block comment at `build.py:76-83` so it continues to explain the pydantic default-validation quirk *and* notes that the `T | None` base must stay the first `Annotated` slot (the actual type) with `Parameter(help=…)` as metadata.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_build.py -v`
Expected: PASS — all existing tests (dispatch, list flags, no-version, one-line listing) plus the two new ones.

- [ ] **Step 5: Update README**

Add the exact bullet from the Produces contract to the "Notes for agents" list in `README.md` (after the `--help`-shows-parameters bullet, which it extends).

- [ ] **Step 6: Run the full suite (regression gate)**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS, 70 tests, 0 failures.

- [ ] **Step 7: Commit**

Run: `git add src/mcp_atlassian_cli/build.py tests/test_build.py README.md`
Run: `git commit -m "feat: render schema param descriptions in tool --help"`

---

### Task 3: Placement hint on misplaced `--profile`

**Files:**
- Modify: `src/mcp_atlassian_cli/config.py:48` (constant) and its four use sites at `config.py:159`, `config.py:225`, `config.py:229`, `config.py:234`
- Modify: `src/mcp_atlassian_cli/main.py:82-84` (the `except CycloptsError` branch)
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: nothing from Tasks 1–2 (independent seam). Existing: `main(argv, runner_factory) -> int`, `config.ConfigError`, and cyclopts raising `CycloptsError` subclasses for unknown options (verified message today: `Unknown option: --profile.` — the hint must NOT be coupled to this exact wording, only to the substring `--profile` appearing in the error text).
- Produces:
  - `config.PROFILE_USAGE: str` — the existing private `_PROFILE_USAGE` promoted to public by rename; text unchanged, verbatim: `Use --profile=NAME or --profile NAME (before the subcommand).` All four existing raise sites keep their exact current messages (they already end with this constant).
  - `main()` behavior in the `CycloptsError` branch: render the error message; if it contains the substring `--profile`, print the error and the hint as one stderr line — error text, single space, `config.PROFILE_USAGE` — otherwise print the error exactly as today. Return code stays `2` in both cases. Correctly-placed `--profile` (before the subcommand) is untouched: the flag never reaches cyclopts.
  - Safety property (why substring match cannot false-positive): cyclopts has already parsed argv when it raises, so a token named in an unknown-option error is certainly an option, never a consumed flag *value* — a JQL string containing `--profile=` stays a value and never triggers the hint.

- [ ] **Step 1: Write the failing tests**

In `tests/test_main.py`, two tests using the existing `stub_app` fixture and `stub_factory`:

1. `test_main_profile_after_subcommand_gets_hint` — call `main(["jira", "get-issue", "--profile", "work", "--issue-key", "X"], runner_factory=stub_factory(stub_app))`. Assert exit code `2`, stdout empty, and stderr contains both `"--profile"` and `"before the subcommand"`. (Asserting on our hint text, not cyclopts' wording, keeps the test robust across cyclopts 4.x message tweaks.)
2. `test_main_unrelated_usage_error_gets_no_hint` — call `main(["jira", "get-issue", "--issue-key", "P", "--bogus", "1"], runner_factory=stub_factory(stub_app))`. Assert exit code `2` and stderr does NOT contain `"before the subcommand"` (the hint is reserved for the `--profile` case, not appended to every usage error).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_main.py -v -k "hint"`
Expected: test 1 FAILS (stderr has cyclopts' bare `Unknown option: --profile.` — no placement hint); test 2 PASSES already (guard test: it locks in the negative case before any change).

- [ ] **Step 3: Write minimal implementation**

In `config.py`: rename `_PROFILE_USAGE` to `PROFILE_USAGE` and update its four f-string references (lines 159, 225, 229, 234) — no message text changes. In `main.py`'s `except CycloptsError` branch: build the message per the Produces contract (hint appended, single space separator, only when `--profile` appears in the error text), print to stderr, return `2` as today.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_main.py -v`
Expected: PASS — all existing tests (including `test_main_empty_profile_value_exit_2`, which pins the "requires a profile name" prefix that must survive the rename) plus the two new ones.

- [ ] **Step 5: Run the full suite (regression gate)**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS, 72 tests, 0 failures.

- [ ] **Step 6: Commit**

Run: `git add src/mcp_atlassian_cli/config.py src/mcp_atlassian_cli/main.py tests/test_main.py`
Run: `git commit -m "feat: hint correct placement when --profile follows the subcommand"`

---

## Final Verification (after Task 3)

- Full suite: `.venv/bin/python -m pytest tests/ -q` → 72 passed, 0 failed.
- All three commits present on `worktree-agent-first-help-fidelity` (Task 1, 2, 3 in order).
- Contract spot-check (no credentials needed): `.venv/bin/atli --help` still lists `tools`/`profiles`, still shows the `--profile` global-flag doc, and exits 0; `.venv/bin/atli jira --version` is still a usage error (exit 2, no version banner).
- Manual smoke (needs real credentials; run after merge, not a task gate): `atli jira get-user-profile --help` shows the schema description for `--user-identifier` (email/username/account-ID formats) verbatim.
