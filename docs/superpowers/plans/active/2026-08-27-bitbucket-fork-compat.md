# Fork Compatibility & Bitbucket Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `atli` installable alongside the `mcp-atlassian-with-bitbucket` fork via a `[bitbucket]` extra, and treat Bitbucket as a first-class service (commands, profiles, prime).

**Architecture:** Packaging selects the provider with a PEP 508 marker on the base dependency (`mcp-atlassian>=0.23,<0.24; extra != "bitbucket"` — upstream by default, the fork when `[bitbucket]` is requested). The runner keeps its in-process lazy import and must work under whichever fastmcp the provider brings (2.13–2.14 for the fork, 3.4.x upstream). Service awareness stays environment-driven: `bitbucket` joins the service name set in discovery, the profile env prefixes, prime's detection matrix, and static copy.

**Tech Stack:** Python 3.11+, setuptools (pyproject PEP 621), cyclopts, fastmcp (2.x or 3.x via provider), pytest, uv, GitHub Actions.

**Spec:** `docs/superpowers/specs/active/2026-08-27-bitbucket-fork-compat-design.md`

## Global Constraints

- Extra name is exactly `bitbucket`; there is no `[atlassian]` extra.
- The base dependency line is exactly `mcp-atlassian>=0.23,<0.24; extra != "bitbucket"`.
- The extra pins `mcp-atlassian-with-bitbucket>=1.0.5,<1.1`.
- Version bumps to `0.4.0` in `src/mcp_atlassian_cli/__init__.py` only (pyproject reads it dynamically; never hardcode the version in a test).
- Root-help headline copy: `atli — a CLI for Jira, Confluence & Bitbucket, powered by mcp-atlassian.`
- Prime title copy: `# atli — Jira, Confluence & Bitbucket CLI`
- Prime bitbucket example line copy: `atli bitbucket list-repositories`
- Exit codes unchanged: 0 success, 1 tool/server failure, 2 usage/config error.
- No new runtime dependencies; `cyclopts>=4.22,<5` stays.
- Tests run with `.venv/bin/python -m pytest` from the repo root (create the venv first if absent: `uv venv && uv pip install -e . pytest`).
- Work happens on branch `bitbucket-fork-compat`; one commit per task.
- Do NOT install the `[bitbucket]` extra into the local dev venv (it conflicts with the upstream already installed there); fork-provider verification is CI-only.

---

### Task 1: Packaging — provider marker, `[bitbucket]` extra, version 0.4.0

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/mcp_atlassian_cli/__init__.py`
- Test: `tests/test_packaging.py` (new)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: pyproject `dependencies` = `["cyclopts>=4.22,<5", "mcp-atlassian>=0.23,<0.24; extra != \"bitbucket\""]`; `[project.optional-dependencies] bitbucket = ["mcp-atlassian-with-bitbucket>=1.0.5,<1.1"]`; `__version__ = "0.4.0"`; module docstring updated to "atli - a CLI for Jira, Confluence & Bitbucket, powered by mcp-atlassian."

- [ ] **Step 1: Write the failing test**

New `tests/test_packaging.py`: parse `pyproject.toml` with `tomllib` and assert (a) `project.dependencies` contains the exact marker line `mcp-atlassian>=0.23,<0.24; extra != "bitbucket"` and `cyclopts>=4.22,<5`; (b) `project.optional-dependencies["bitbucket"]` equals `["mcp-atlassian-with-bitbucket>=1.0.5,<1.1"]`; (c) there is no other `mcp-atlassian` dependency line lacking the marker (guards against regression to the unconditional pin). Locate pyproject relative to the test file (`Path(__file__).resolve().parent.parent / "pyproject.toml"` — same pattern as `REPO_ROOT` in the other test modules).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_packaging.py -v`
Expected: FAIL — current pyproject has the unconditional `mcp-atlassian>=0.23,<0.24` dependency and no extras.

- [ ] **Step 3: Write minimal implementation**

In `pyproject.toml`: replace the `mcp-atlassian>=0.23,<0.24` entry with the marker line from Global Constraints; add the `[project.optional-dependencies]` table with the `bitbucket` extra. In `src/mcp_atlassian_cli/__init__.py`: set `__version__ = "0.4.0"` and update the docstring per Interfaces. Leave everything else (build-system, dynamic version, scripts, pytest config) untouched.

- [ ] **Step 4: Run test to verify it passes — and the venv still resolves**

Run: `.venv/bin/python -m pytest tests/test_packaging.py tests/test_cli_entry.py -v`
Expected: PASS (including the existing semver-format version test).
Then run `uv pip install -e .` and confirm it succeeds (uv parsing the marker is the first resolver sanity check) and that `mcp-atlassian` upstream is still installed (`uv pip show mcp-atlassian`).

- [ ] **Step 5: Commit**

Run: `git add pyproject.toml src/mcp_atlassian_cli/__init__.py tests/test_packaging.py`
Run: `git commit -m "feat: provider marker packaging — [bitbucket] extra swaps in the fork"`

---

### Task 2: Runner — provider-missing error contract

**Files:**
- Modify: `src/mcp_atlassian_cli/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: module constant (rename `_PIN_OR_UPDATE` to `_NO_PROVIDER`) whose message: states the mcp-atlassian server is missing/broken in this environment; gives both install commands — plain `pip install mcp-atlassian-cli` (Jira+Confluence) and `pip install "mcp-atlassian-cli[bitbucket]"` (adds Bitbucket via the fork); states the two providers cannot coexist; suggests updating atli as the alternative fix. `ToolRunnerError` docstring updated to match ("repair the install — reinstall with or without `[bitbucket]` — or update this CLI"). The constant remains the message used by all three failure wrap-sites (app import, list, call) — behavior otherwise unchanged.

- [ ] **Step 1: Write the failing test**

In `tests/test_runner.py`, extend `test_default_app_import_failure_is_runner_error` (which already simulates the broken import via a patched `builtins.__import__` and asserts `"mcp-atlassian"` in the message): additionally assert the message contains `pip install mcp-atlassian-cli`, contains `mcp-atlassian-cli[bitbucket]`, and mentions the cannot-coexist rule (assert a distinctive substring of the choose-one sentence).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_runner.py::test_default_app_import_failure_is_runner_error -v`
Expected: FAIL — the message still references only the old upstream pin.

- [ ] **Step 3: Write minimal implementation**

Replace the `_PIN_OR_UPDATE` constant in `src/mcp_atlassian_cli/runner.py` with `_NO_PROVIDER` carrying the message content above; update the two `f"{_PIN_OR_UPDATE} ({error})"` wrap sites and the `ToolRunnerError` docstring. No behavioral change to exception types, wrapping order, or the silence-logging logic.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_runner.py -v`
Expected: PASS (all existing transport tests unaffected).

- [ ] **Step 5: Commit**

Run: `git add src/mcp_atlassian_cli/runner.py tests/test_runner.py`
Run: `git commit -m "feat: provider-missing error names both install recipes"`

---

### Task 3: Discovery — `bitbucket` service prefix

**Files:**
- Modify: `src/mcp_atlassian_cli/discovery.py:9`
- Test: `tests/test_discovery.py`, `tests/test_build.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `discovery.SERVICE_PREFIXES = frozenset({"jira", "confluence", "bitbucket"})`; `split_service("bitbucket_get_pull_request")` returns `("bitbucket", "get_pull_request")`; `parse_tool` over a tool named `bitbucket_list_repositories` yields `ToolSpec(service="bitbucket", command_name="list-repositories")`. Downstream (`build.create_app`) needs no change — service grouping is driven by `spec.service`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_discovery.py`: (a) extend `test_split_service_known` (or add `test_split_service_bitbucket`): `split_service("bitbucket_list_repositories") == ("bitbucket", "list_repositories")` and `split_service("bitbucket_x")` never matches when the head isn't a known service (existing `test_split_service_unknown` covers the pattern — keep it green). (b) Add a `parse_tool` case: a `SimpleNamespace` tool named `bitbucket_get_pull_request` with one required string param `repo_slug` produces `spec.service == "bitbucket"`, `spec.command_name == "get-pull-request"`.
In `tests/test_build.py`: add one test building the app from a hand-constructed `ToolSpec(service="bitbucket", command_name="list-repositories", tool_name="bitbucket_list_repositories", description="List repos.", params=())` with a dispatch stub, asserting `app(["bitbucket", "list-repositories", "--help"], exit_on_error=True)` renders help containing the tool name, and that root `--help` lists a `bitbucket` service group (mirror the assertion style of `test_root_help_lists_services_with_descriptions`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discovery.py tests/test_build.py -v`
Expected: FAIL on the new cases (bitbucket splits to `(None, ...)` today).

- [ ] **Step 3: Write minimal implementation**

One-line change: add `"bitbucket"` to the `SERVICE_PREFIXES` frozenset in `src/mcp_atlassian_cli/discovery.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_discovery.py tests/test_build.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/mcp_atlassian_cli/discovery.py tests/test_discovery.py tests/test_build.py`
Run: `git commit -m "feat: bitbucket service prefix — atli bitbucket <tool> command group"`

---

### Task 4: Config — `BITBUCKET_` profile prefix + profiles row

**Files:**
- Modify: `src/mcp_atlassian_cli/config.py:19,257`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `config.SERVICE_ENV_PREFIXES = ("JIRA_", "CONFLUENCE_", "MCP_ATLASSIAN_", "BITBUCKET_")` — `apply_profile` gains the same replace-per-prefix and cross-service-OAuth-cleanup semantics for `BITBUCKET_*` as for the other services. `describe_profiles` iterates the URL rows `(("JIRA_URL", "jira"), ("CONFLUENCE_URL", "confluence"), ("BITBUCKET_URL", "bitbucket"))`, rendering `    bitbucket: <url>` when present, never any credential values.

- [ ] **Step 1: Write the failing tests**

In `tests/test_config.py`:
(a) `test_apply_profile_replaces_bitbucket_prefix`: ambient env has `BITBUCKET_URL`, `BITBUCKET_USERNAME`, `BITBUCKET_APP_PASSWORD`, and `ATLASSIAN_OAUTH_ACCESS_TOKEN`; profile defines `BITBUCKET_URL` + `BITBUCKET_PERSONAL_TOKEN`. After `apply_profile`, ambient `BITBUCKET_USERNAME`/`BITBUCKET_APP_PASSWORD` are gone, the profile's two values are set, and `ATLASSIAN_OAUTH_ACCESS_TOKEN` was cleared.
(b) `test_apply_profile_leaves_bitbucket_untouched_when_unmentioned`: a Jira-only profile leaves ambient `BITBUCKET_*` variables intact.
(c) Extend the `describe_profiles` coverage (near `test_describe_profiles_hides_secrets`): a profile containing `BITBUCKET_URL` renders a `bitbucket:` line with that URL; a secret like `BITBUCKET_API_TOKEN` never appears in the output.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: FAIL on (a) and (c) — `BITBUCKET_` isn't a known prefix; no bitbucket row renders.

- [ ] **Step 3: Write minimal implementation**

Append `"BITBUCKET_"` to `SERVICE_ENV_PREFIXES`; add the `("BITBUCKET_URL", "bitbucket")` entry to the row tuple in `describe_profiles`. Update the `SERVICE_ENV_PREFIXES` docstring's "one per service" wording only if it enumerates services (it doesn't — no change needed beyond the tuple).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS (all existing profile tests unaffected).

- [ ] **Step 5: Commit**

Run: `git add src/mcp_atlassian_cli/config.py tests/test_config.py`
Run: `git commit -m "feat: BITBUCKET_ profile env prefix and profiles row"`

---

### Task 5: Prime — per-service auth matrix + Bitbucket copy

**Files:**
- Modify: `src/mcp_atlassian_cli/prime.py`
- Test: `tests/test_prime.py`
- Modify: `docs/superpowers/specs/active/2026-08-19-atli-prime-design.md` (output-contract snippet only)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `detect_services(environ: Mapping[str, str]) -> dict[str, bool]` with keys in order `("jira", "confluence", "bitbucket")` — replaces the `(bool, bool)` tuple return.
  - A declarative per-service auth table replaces the shared `_configured` credential tuple: each service maps to its URL variable plus a tuple of combinations; each combination is a tuple of clauses; a clause is either one env-var name (counts when set non-empty) or a tuple of alternative names (counts when ANY is set non-empty). Exact tables: jira and confluence keep today's three combinations — (`<S>_USERNAME`, `<S>_API_TOKEN`), (`<S>_PERSONAL_TOKEN`,), (`<S>_CLIENT_CERT`,); bitbucket gets two — (`BITBUCKET_USERNAME`, (`BITBUCKET_APP_PASSWORD`, `BITBUCKET_API_TOKEN`)) and (`BITBUCKET_PERSONAL_TOKEN`,); no client-cert combination.
  - `_assemble(services: Mapping[str, bool], profile_name: str | None, config_path: Path | None)` — Configured line lists configured services in jira, confluence, bitbucket order; title constant becomes `# atli — Jira, Confluence & Bitbucket CLI`; new example constant `atli bitbucket list-repositories` renders (after the confluence example) iff bitbucket is configured; with NOTHING configured (export-only path) all three example lines render.
  - `render_default` silence rule unchanged: empty output iff all three services are False.

- [ ] **Step 1: Write the failing tests**

In `tests/test_prime.py`:
(a) Rework `test_detect_services` parametrize to the dict return — every expected value becomes `{"jira": …, "confluence": …, "bitbucket": False}` for the existing nine cases (bitbucket env absent in all of them), plus new cases: Cloud app-password (`BITBUCKET_URL`+`BITBUCKET_USERNAME`+`BITBUCKET_APP_PASSWORD` → bitbucket True), Cloud API_TOKEN alias (same with `BITBUCKET_API_TOKEN` → True), Server PAT (`BITBUCKET_URL`+`BITBUCKET_PERSONAL_TOKEN` → True), `BITBUCKET_URL` alone → False, `BITBUCKET_URL`+`BITBUCKET_USERNAME` without any password → False, and a jira-style `BITBUCKET_CLIENT_CERT` alone → False (no mTLS combination exists).
(b) Update `CANONICAL_PRIMER`'s title line to the new title (rest unchanged — the canonical case configures jira+confluence only, so no bitbucket example line there).
(c) New `test_render_default_bitbucket_only`: Cloud app-password env → output contains `Configured: bitbucket` and `atli bitbucket list-repositories`; the jira and confluence example lines are absent.
(d) New `test_render_default_all_three`: jira+confluence+bitbucket env → `Configured: jira, confluence, bitbucket` (exact ordering) and all three example lines present.
(e) Extend `test_render_export_when_unconfigured` to also assert the bitbucket example line appears.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_prime.py -v`
Expected: FAIL — detect_services still returns a 2-tuple; title/example copy unchanged.

- [ ] **Step 3: Write minimal implementation**

Rework `src/mcp_atlassian_cli/prime.py` per Interfaces: the declarative auth table + generic `_configured(environ, service)` reading it; dict-returning `detect_services`; `_assemble` over the mapping; title and example-constant copy updates. Keep the module stdlib-only (prime's fast-path constraint). Update the output-contract snippet in `docs/superpowers/specs/active/2026-08-19-atli-prime-design.md` to the new canonical primer so the implemented spec stays truthful.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_prime.py tests/test_main.py -v`
Expected: PASS (`test_main.py` covers the prime fast path end-to-end; if any of its assertions pin the old title line, update them to the new title).

- [ ] **Step 5: Commit**

Run: `git add src/mcp_atlassian_cli/prime.py tests/test_prime.py docs/superpowers/specs/active/2026-08-19-atli-prime-design.md tests/test_main.py`
Run: `git commit -m "feat: prime detects bitbucket — per-service auth matrix, new title/example copy"`

---

### Task 6: Build — root-help headline + tools empty-state hint

**Files:**
- Modify: `src/mcp_atlassian_cli/build.py:23,118-120`
- Test: `tests/test_build.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `_ROOT_HELP` first line becomes exactly `atli — a CLI for Jira, Confluence & Bitbucket, powered by mcp-atlassian.` (the three usage bullets stay verbatim); the `tools` command's empty-state message becomes `No services configured — set JIRA_URL / CONFLUENCE_URL / BITBUCKET_URL or a profile (see atli --help).`

- [ ] **Step 1: Write the failing tests**

In `tests/test_build.py`: (a) update the empty-state assertion in the tools test (currently lines ~241-242) to the new message including `BITBUCKET_URL`; (b) extend `test_root_help_documents_globals` to assert `Bitbucket` appears in root `--help` output (the headline).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_build.py -v`
Expected: FAIL on both updated assertions.

- [ ] **Step 3: Write minimal implementation**

Two copy edits in `src/mcp_atlassian_cli/build.py` per Interfaces. No structural changes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_build.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/mcp_atlassian_cli/build.py tests/test_build.py`
Run: `git commit -m "feat: root help and tools hint mention Bitbucket"`

---

### Task 7: Docs — README, AGENTS.md

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: final copy decisions from Tasks 1–6 (extra name, headline, auth matrix).
- Produces: documentation matching the spec's Documentation section.

- [ ] **Step 1: Update README**

Content requirements: (1) Intro — "a command-line interface for Jira, Confluence, and Bitbucket" plus one sentence noting Bitbucket requires the `[bitbucket]` extra (links to the fork repo `jellythomas/mcp-atlassian-with-bitbucket`). (2) Install — default `pipx install mcp-atlassian-cli` keeps upstream (Jira+Confluence); fork recipe `pipx install "mcp-atlassian-cli[bitbucket]"`; a choose-one warning (the two providers cannot coexist — same import package, conflicting fastmcp pins) and the switching rule (fresh env: `pipx uninstall mcp-atlassian-cli && pipx install "mcp-atlassian-cli[bitbucket]"`). (3) The "Requires Python 3.11+" dependency sentence rewritten for the marker model (base pins `cyclopts`; provider: upstream `mcp-atlassian>=0.23,<0.24` by default, fork `mcp-atlassian-with-bitbucket>=1.0.5,<1.1` with `[bitbucket]`). (4) Auth matrix — add a Bitbucket column: Cloud (`bitbucket.org`): `BITBUCKET_URL` + `BITBUCKET_USERNAME` + `BITBUCKET_APP_PASSWORD` (or `BITBUCKET_API_TOKEN`); Data Center/Server PAT: `BITBUCKET_URL` + `BITBUCKET_PERSONAL_TOKEN`; mTLS row: "—" for Bitbucket; note that the URL decides Cloud vs Server (`bitbucket.org` ⇒ Cloud). (5) Profiles section — list `BITBUCKET_*` among the service prefixes and in the `TOOLSETS` note. (6) Any sentence claiming tool counts per service stays upstream-scoped (e.g. "with the default provider").

- [ ] **Step 2: Update AGENTS.md**

Add one bullet near the jira/confluence examples: `atli bitbucket list-repositories` with a note that `bitbucket_*` tools appear only when the fork provider is installed (`pip install "mcp-atlassian-cli[bitbucket]"`); adjust the headline sentence to mention Bitbucket.

- [ ] **Step 3: Verify docs consistency**

Run: `grep -n "bitbucket\|BITBUCKET" README.md AGENTS.md` and read both changed sections; confirm every install command, extra name, and env var name matches Global Constraints verbatim. Also confirm no stale claim that atli is "for Jira and Confluence" only.

- [ ] **Step 4: Commit**

Run: `git add README.md AGENTS.md`
Run: `git commit -m "docs: bitbucket extra — install recipes, auth matrix, profiles"`

---

### Task 8: CI — fork leg + marker-contract verification + cross-major test fix

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: packaging from Task 1 (the `[bitbucket]` extra must exist for the CI jobs to install).
- Produces: three CI jobs — (1) the existing `test` matrix unchanged except a provider assertion step; (2) a `packaging` job verifying the marker under pip via two throwaway venvs; (3) a `bitbucket` job installing `.[bitbucket]` under uv, asserting the provider swap, running the suite, and smoke-testing the CLI. `tests/conftest.py` mounts sub-apps positionally so the stub works under fastmcp 2.x and 3.x.

- [ ] **Step 1: Fix the conftest mount for cross-major fastmcp**

In `tests/conftest.py`, change `app.mount(jira, namespace="jira")` and `app.mount(confluence, namespace="confluence")` to the positional form (`app.mount(jira, "jira")`, `app.mount(confluence, "confluence")`) — the `namespace=` keyword does not exist in fastmcp 2.x (`prefix=` positional there), while the positional form works on both majors. Run the suite to confirm green under the locally installed fastmcp 3.x: `.venv/bin/python -m pytest` → PASS.

- [ ] **Step 2: Add the provider assertion to the existing test job**

In `.github/workflows/ci.yml`, after the existing `Install` step of the `test` job, add a step running a Python one-liner with `importlib.metadata`: assert distribution `mcp-atlassian` is present AND `mcp-atlassian-with-bitbucket` is absent (catch via `PackageNotFoundError`). This is the uv-side marker verification (uv resolved the bare install to upstream).

- [ ] **Step 3: Add the `packaging` job (pip-side marker verification)**

New job on one Python (3.11), two steps, each in a throwaway venv (NOT the uv venv): (a) bare — `python -m venv /tmp/bare`, `/tmp/bare/bin/pip install .`, then assert via `importlib.metadata` that `mcp-atlassian` is present and `mcp-atlassian-with-bitbucket` absent; (b) fork — `python -m venv /tmp/fork`, `/tmp/fork/bin/pip install ".[bitbucket]"`, then assert `mcp-atlassian-with-bitbucket` present and `mcp-atlassian` absent. Both installs run from the repo root. A failure here is the spec's trigger to fall back to the inert-bare design — surface that in the step's failure message text.

- [ ] **Step 4: Add the `bitbucket` job (fork leg)**

New job on Python 3.11: `uv venv` + `uv pip install -e ".[bitbucket]" pytest`; a provider assertion step (fork present, upstream absent); run `.venv/bin/python -m pytest`; then a smoke step — `.venv/bin/atli tools` exits 0 with stdout containing `No services configured` and `BITBUCKET_URL`, and `.venv/bin/atli prime` exits 0 with empty stdout. `do-not-skip`: `test_examples.py` must pass on this leg (the fork mirrors the upstream server layout its AST walk depends on); if a fork-leg test fails on provider drift (e.g. a logging-setup location difference), fix the TEST to tolerate both providers (guard with a capability check), never the production code — per the spec's thin-shim philosophy.

- [ ] **Step 5: Validate the workflow file locally**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"` (or `uv run python …`) to confirm the YAML parses; run the full suite once more: `.venv/bin/python -m pytest` → PASS.

- [ ] **Step 6: Commit**

Run: `git add .github/workflows/ci.yml tests/conftest.py`
Run: `git commit -m "ci: fork leg + pip/uv marker-contract verification"`

---

## Final verification (after all tasks)

- [ ] Full suite green: `.venv/bin/python -m pytest`
- [ ] `git log --oneline main..HEAD` shows one commit per task on `bitbucket-fork-compat`
- [ ] Spec still matches implementation; flip spec `Status:` to `implemented` when the branch merges (handled by finishing-a-development-branch)
