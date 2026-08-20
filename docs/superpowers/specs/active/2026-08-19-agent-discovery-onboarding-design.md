# atli — cold-agent discovery, examples, long content, and prime installer

**Status:** in_progress

## Problem

An audit of the cold-agent experience (a zero-context agent meeting `atli` for
the first time) against the current CLI found the agent-first contract sound at
the mechanical layer — schema-faithful `<tool> --help`, self-correcting flag
typos, verbatim output, stable exit codes — while agents still struggle in
three observed ways:

1. **Tool selection.** `atli tools` is a ~98-line wall; an agent with a task
   ("comment on PROJ-1") has no way to shortlist candidate tools and guesses
   wrong or reads everything.
2. **Flag & format failures.** Two distinct pains, roughly equal:
   - *Semantic formats* — JQL syntax, date formats, identifier forms
     (`accountid:…`) — live in tool descriptions but agents miss or misread
     them and learn only through failed calls.
   - *Shell quoting of long content* — descriptions, page bodies, and comments
     with newlines/quotes/backticks are hostile to pass through argv (the
     `@file`/stdin ergonomics deferred as a v1 non-goal).
   A concrete amplifier: tools whose schemas mark **nothing required** while
   the server requires an identifier — `atli confluence update-page --help`
   shows no `[required]` flags at all, so the usage line is
   `update-page [OPTIONS]` and an agent calling bare learns nothing until the
   server rejects it. The "which optional flags are actually needed" knowledge
   exists nowhere in the CLI.
3. **Cold start.** On machines/projects without the SessionStart hook or a
   checked-in AGENTS.md, agents have never heard of atli: `prime` is invisible
   in `--help` (fast-path command, not in the tree) and there is no onboarding
   verb — the README's hook JSON must be copied by hand.

Not observed (explicitly out of scope): the unconfigured-machine
`Unknown command "jira"` dead-end, grammar discovery (agents find
`atli <service> <tool>` fine), and startup latency.

## Goal

A zero-context agent succeeds on first contact, without prime installed:

- `atli tools --search QUERY` turns the tool wall into a shortlist.
- `<tool> --help` carries a curated example invocation that teaches which
  flags a tool actually wants (`--page-id`) and the canonical value formats
  (JQL, dates, `accountid:…`).
- Long content flows through `@file` / stdin instead of shell quoting.
- `atli prime --install` onboards a machine/project in one command, and the
  CLI's own first-contact surfaces (root help, service help, command-typo
  errors) teach the shape.

Exit codes and the output-verbatim contract are unchanged; the discovered
tool commands (every `atli <service> <tool>`) are untouched — all additions
are new flags on built-ins and new help/error text.

## Non-goals

- Agent-writable memory (learnings persisted across sessions) — deferred by
  decision; PRIME.md stays human-curated.
- Startup cost (daemon/caching) — accepted v1 trade-off.
- Grammar teaching (`atli <service> <tool>`) — not an observed failure.
- The unconfigured-machine `Unknown command "jira"` dead-end — not an observed
  failure; candidate for a later pass.
- `--json` output for `tools`; an uninstall flag for the installer; enum/object
  param rendering (pre-existing).

## Design

### 1. `atli tools --search QUERY`

`_make_tools_command` (build.py) gains a `search` option; cyclopts exposes it
as `--search`. Matching is case-insensitive substring over service, command
name, and the **full description** (not just the first sentence). Output is
the existing table format, filtered; zero matches prints guidance ("no match
for 'X' — try a broader term or `atli tools`") and exits 0 — a successful
empty query, not an error.

### 2. Curated examples corpus → `<tool> --help`

A new in-package module (`examples.py`) maps `tool_name → 1–2 example
invocation lines` with realistic values. `_make_handler` appends an
`Examples:` block to the handler docstring so cyclopts renders it directly
under the parameter table — the exact place an agent looks before calling.
Two rules govern corpus content:

- Examples teach identifiers and formats: `--page-id 123456`,
  `--jql "assignee = currentUser() AND updated > -7d"`,
  `--user-identifier "accountid:5b10…"`. Semantic-format teaching happens by
  putting canonical formats inside examples, not more prose.
- Long-content examples use `@file` (`--content @page.md`), teaching the
  expansion mechanism by demonstration.

The corpus starts at ~12 high-traffic tools (jira: search, get-issue,
create-issue, add-comment, transition-to-issue; confluence: search, get-page,
create-page, update-page; final list during implementation). Tools without an
entry render exactly as today — graceful degradation, no drift risk for the
long tail.

### 3. `@file` / stdin value expansion

For **string-typed** values, applied in the generated handler after flag
binding and before the arguments dict is assembled:

| Input | Value |
|---|---|
| `@path`, file exists | file contents (UTF-8) |
| `@path`, file missing | exit-2 usage error: `--body @foo.md: file not found. Use @@foo.md to pass a literal '@' value` |
| `@@x` | literal `@x` |
| `-`, stdin not a TTY | stdin contents |

Erroring on missing files (rather than passing the string through) is
deliberate: a typo'd path silently becoming literal content is more confusing
downstream than a loud usage error with the escape shown. Arrays expand
element-wise (string elements only); bool/int/float values are untouched.

### 4. `atli prime --install` — the hook installer

A new flag on the prime app (fast path — never pays the mcp-atlassian
import), `--install [--scope user|project] [--harness NAME …]`:

- **Detection**: with no `--harness`, detect by config-dir presence
  (`~/.claude`, `~/.gemini`, `~/.codex`); install for every detected harness,
  one output line per action. A harness without a SessionStart hook mechanism
  prints "not supported for X" — not a failure.
- **Merge, never clobber**: read the existing settings JSON, parse, append the
  hook entry to the SessionStart list only if the exact command
  (`atli prime --hook-json`) is absent, write back. An unparseable existing
  file aborts with a clear message and **no write**. Idempotent by
  construction.
- **Scope**: default `user` (machine-wide — credentials are per-machine and
  prime's silence rule makes it free where unconfigured); `--scope project`
  writes the project-level settings file in the cwd for sharing via repo.
- Prints exactly what was written (path + entry) so a human can undo by hand.
- PRIME.md customization stays opt-in via `atli prime --export > .atli/PRIME.md`;
  the installer never seeds it.

### 5. First-contact polish (mechanical)

- **Service group help**: each service app (`jira`, `confluence`) gets a
  one-line help string, so the root Commands table stops showing bare names.
- **`prime` visible in root help**: a display-only stub command registered in
  the main app tree so `--help` lists it ("Print AI-agent primer —
  SessionStart hook; see `prime --install`"). The fast path still intercepts
  real `prime` invocations before the app is built; the stub raises if ever
  dispatched (defense in depth).
- **Command "did you mean"**: `main.py`'s `CycloptsError` handler already
  intercepts `UnknownCommandError`; with the tool specs in scope it appends a
  nearest-sibling suggestion (`Did you mean "get-page"?`, difflib with a
  closeness threshold) — the same self-correcting pattern flag typos already
  have.
- **Root help re-flow**: `_ROOT_HELP` is restructured (or cyclopts'
  `help_format` tuned) so rich stops justifying the multi-line help into a
  blob; verified by rendering at implementation time.
- **`tools` empty-state message** mentions `--search`.

### 6. `prime` default content additions

The static core gains: `--search` in the Discovery block, one line for
`@file`, and `prime --install` as the onboarding verb. Still a couple dozen
lines; the silence rule (empty output when unconfigured, no override) is
untouched.

## Error handling

No new exit codes or streams. Additions to exit-2 usage errors: missing
`@file` (with the `@@` escape hint) and installer abort on unparseable
settings. The installer writes only on a successful merge. The tool-call
paths stay byte-identical (stdout verbatim output, stderr one-line errors,
exit codes 0/1/2); help text and unknown-command messages change exactly as
§5 describes, by design.

## Testing

- **Unit**: search filtering and empty-result guidance; corpus examples
  rendered in `<tool> --help` and absent for unknown tools; expansion rules
  (existing file, missing file → exit 2 + hint, `@@` escape, stdin, array
  element-wise, non-strings untouched); installer merge idempotency,
  no-clobber, and abort-on-corrupt against fixture settings files;
  did-you-mean selection; service help text; prime stub listed in root help.
- **Integration** (existing `runner_factory` seam): end-to-end `--search`
  through a stub server; `@file` end-to-end through dispatch.
- **Manual smoke**: `prime --install` against a temp `HOME`; real `--help`
  rendering after the polish pass.

## Documentation

README: `--search` in the discovery notes, `@file`/stdin in "Notes for
agents", the examples-in-help guarantee, and the `prime --install` flow
replacing the copy-the-JSON instructions. AGENTS.md: the `--search` and
`@file` one-liners.

## Open Questions

1. **Harness support matrix for the installer.** Claude Code's SessionStart
   hook is verified; Gemini CLI's and Codex's hook mechanisms need
   confirmation at implementation time. Resolution: verify each harness's
   settings schema before writing its installer branch; unsupported harnesses
   fall back to the "not supported" line, which is already the designed
   behavior.
2. **Final corpus list (~12 tools).** Candidates are named in the design;
   implementation confirms each example's values against the live schema so
   every flag in an example exists on that tool.

## TLDR

Four additions make atli usable by zero-context agents: `tools --search`
shortlists the 98-tool wall, curated examples inside `<tool> --help` teach
which flags a tool really wants and the canonical formats, `@file`/stdin
replaces shell-quoting pain, and `prime --install` onboards machines with an
idempotent, never-clobbering hook merge — plus mechanical polish (service
help text, visible prime, command did-you-mean). No contract changes; memory
and latency stay out of scope.
