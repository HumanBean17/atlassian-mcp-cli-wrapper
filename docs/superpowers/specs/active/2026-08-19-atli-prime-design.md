# `atli prime` — SessionStart priming for AI agents

**Status:** draft

## Problem

Agent sessions with `atli` start cold: after context compaction (or in a fresh
session) the agent no longer remembers that atli exists, which services are
configured, or the usage quirks that make calls succeed (repeatable list
flags, exit codes, bulk-over-single advice). `AGENTS.md` helps only if the
agent re-reads it. `bd` solves this class of problem with `bd prime` — a
command designed for SessionStart hooks that re-injects a compact workflow
primer at every session start.

## Goal

`atli prime`: a subcommand that outputs an AI-optimized markdown primer of
atli usage, adapted to the machine's configuration (which services are
active, which profile is in effect), suitable for SessionStart hooks in
Claude Code, Gemini CLI, and Codex. `--hook-json` wraps the output in the
SessionStart hook JSON envelope (bd-identical). The command must be cheap:
it never imports mcp-atlassian, so a hook invocation costs tens of
milliseconds instead of the ~1–4 s of a normal atli start.

## Non-goals

- Adaptation beyond the services-configured axis — no `--full`/`--mcp`/
  `--memories-only` analogues (atli has no MCP mode or persistent memories).
- Tool inventory in the primer — exact counts would require the import prime
  exists to avoid; `atli tools` remains the discovery surface.
- Changes to any other command's behavior or to the normal dispatch path.

## Command surface

```
atli [--profile NAME] prime [--hook-json] [--export]
```

- **`--hook-json`** — wrap the output in the SessionStart hook envelope (see
  [Hook envelope](#hook-envelope)).
- **`--export`** — print the *default* primer content as plain markdown:
  ignores any override file and prints even when nothing is configured (it is
  the customization bootstrap). `--hook-json` has no effect alongside it.
- **`--profile`** — the global flag, as for every command (before the
  subcommand); the primer reflects the profile-applied environment.

## Output contract

Default content — a static usage core under a dynamic header, with example
commands filtered to the configured services:

```markdown
# atli — Jira & Confluence CLI

Configured: jira, confluence
Profile: work (~/.config/atli/config.toml)

## Usage
atli [--profile NAME] <service> <tool> [flags]
atli jira get-issue --issue-key PROJ-1
atli confluence search --query "deploy"

## Discovery
atli tools [--service jira]           # one line per tool
atli jira get-issue --help            # params, types, defaults

## Notes
- Tool output prints verbatim (LLM-ready markdown from mcp-atlassian).
- Repeatable list flags repeat: `--read-users alice --read-users bob`.
- Exit codes: 0 success, 1 tool/server failure, 2 usage/config error.
- Startup ~1 s warm; prefer one `search` over many single-item calls.
```

- **Configured line**: a service is listed iff mcp-atlassian would mount it —
  its URL plus a valid credential combination per the README auth matrix,
  evaluated against the environment after profile application. Tool counts
  are deliberately absent; `atli tools` remains the source of truth for the
  actual surface.
- **Profile line**: the effective profile name and config file path;
  `ambient environment` when credentials come from the environment; the line
  is omitted when no config file exists.
- **Silence rule**: no services configured and no override file → empty
  output (empty `additionalContext` in envelope mode), exit 0. Zero token
  cost in hooks on machines where atli cannot act anyway.

## Override file

A PRIME.md override replaces the default content **entirely** (no dynamic
header prepended), letting teams inject project-specific conventions — or
simply trim the default. Lookup order, first existing file wins:

1. `$ATLI_PRIME` — if set, must point at an existing file; an error
   otherwise (mirrors `ATLI_CONFIG` semantics)
2. `./.atli/PRIME.md` — current directory; teams check it into the repo
3. `~/.config/atli/PRIME.md` — personal default

An override prints even when no services are configured: explicit human
intent beats the silence heuristic. `--export` bypasses the override so the
default is always recoverable as a starting point.

## Hook envelope

`--hook-json` prints the content wrapped in the SessionStart hook envelope —
a single JSON line, identical in shape to `bd prime --hook-json`, served as-is
to Claude Code, Gemini CLI, and Codex:

```json
{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"<markdown, JSON-escaped>"}}
```

## Architecture

Fast path: `prime` dispatches before tool discovery, so no `mcp_atlassian`
import ever happens for it.

```
atli prime --hook-json
  → main._run   extract --profile, resolve config, apply profile env   (cheap; no MCP import)
  │   rest_argv[0] == "prime"?
  ├─ yes → build.create_prime_app(context) → prime.py → stdout, exit   (fast path)
  └─ no  → ToolRunner → create_app(specs) → dispatch                  (unchanged)
```

- **`prime.py` (new)** — pure content and decisions, no cyclopts or MCP
  imports: the default template constant, the service-configured predicate
  over the post-profile environment (mirrors the README auth matrix),
  override resolution, render (→ content or `""` for silence), and the
  envelope wrapper.
- **`build.py`** — gains `create_prime_app(...)`: a factory-built `prime`
  command closing over its context (same pattern as the `profiles` command),
  `version_flags` disabled like every other app. All cyclopts wiring stays
  in one module.
- **`main.py`** — the fast-path fork: after profile resolution, a `prime`
  first token dispatches through `create_prime_app` and returns before the
  runner import; exit-code mapping (`CycloptsError` → 2, `SystemExit` → its
  code) mirrors the existing dispatch block.
- **Untouched**: `config.py`, `discovery.py`, `runner.py` — the fast path
  reuses what `_run` already computed (`profile_name`, `config_data.path`,
  the applied environment).

Accepted edge: a hypothetical prefix-less mcp-atlassian tool named `prime`
would be shadowed by the fast path. All real tools are `jira_`/`confluence_`-
prefixed, and the existing built-ins (`tools`, `profiles`) carry the same
theoretical risk.

## Error handling

| Situation | Behavior |
|---|---|
| Config/profile resolution fails | Unchanged: message on stderr, exit 2 (before the fork) |
| `$ATLI_PRIME` set but missing | stderr message, exit 2 (mirrors `ATLI_CONFIG`) |
| Override file unreadable | stderr message, exit 2 — surfaced, never silent fallback |
| No services + no override | Empty stdout (empty `additionalContext`), exit 0 |
| Bad flags / extra positionals on `prime` | Cyclopts usage error → exit 2 |
| Closed stdout (`atli prime \| head`) | Existing EPIPE handling in `main()` |

## Testing

- **`tests/test_prime.py` (new)** — pure-function level: service-detection
  matrix (jira/confluence/both/none × cloud/PAT auth variants), profile-line
  variants (named / ambient / no config file), override resolution order,
  full replacement (no header prepended), override-prints-when-unconfigured,
  `$ATLI_PRIME` missing → error, silence rule, `--export` semantics
  (ignores override, prints when unconfigured), envelope JSON round-trip
  with quotes/newlines/unicode, `hookEventName == "SessionStart"`.
- **`tests/test_main.py` additions** — the fast path never constructs the
  runner (injected `runner_factory` that raises if called), end-to-end
  `prime` / `prime --hook-json` (exit 0, expected stdout), `prime --help`,
  bad flag → 2, `--profile` reflected in the Profile line. Existing conftest
  stubs reused.

## Documentation

- **README** — new "Priming AI agents (`atli prime`)" section: usage, a
  Claude Code SessionStart `settings.json` hook snippet, override lookup,
  silence rule.
- **AGENTS.md** — one line: `atli prime [--hook-json]` for hook priming.
