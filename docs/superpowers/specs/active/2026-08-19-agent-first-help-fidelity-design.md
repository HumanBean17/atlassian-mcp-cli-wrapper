# atli — schema-faithful parameter help & self-correcting `--profile` error

**Status:** implemented

## Problem

An audit of `atli`'s agent-first contract (discovery from compact output instead
of schemas in context, verbatim output, parseable stderr, stable exit codes)
found the core preserved, with two fidelity gaps:

1. **Parameter descriptions are dropped.** mcp-atlassian tool schemas carry
   per-parameter `Field(description=...)` text (accepted formats, semantics —
   e.g. `user_identifier`: "email, username, or account ID 'accountid:…'").
   `parse_tool` keeps only name/type/required/default, so `atli <tool> --help`
   shows the flag without the one piece of schema knowledge an agent cannot
   guess. The CLI's help *is* the agent's schema; dropping descriptions
   under-informs exactly where agents need it.
2. **A misplaced `--profile` dead-ends.** `--profile` is a global flag parsed
   before tool discovery, so placing it after the subcommand surfaces cyclopts'
   `"Unknown option: --profile."` — exit 2, loud (good: never silently
   ignored), but with no hint that the flag exists and belongs before the
   subcommand. An agent may conclude profiles are unsupported and abandon a
   correct plan.

## Goal

Close both gaps without touching any released contract: `--help` shows each
parameter's schema description verbatim, and the misplaced-`--profile` error
teaches the correct invocation. Exit codes, output contract, command surface,
and profile env-replacement semantics are unchanged.

## Non-goals

- Enum/`anyOf`/object-typed parameter rendering — the current tool surface has
  none (verified against mcp-atlassian 0.23.x); revisit if it grows them.
- Accepting `--profile` anywhere in argv. Architecturally blocked: the profile
  must be applied before `mcp_atlassian` is imported, while tool parameter
  names are known only after discovery — the flag-vs-parameter ambiguity
  cannot be resolved pre-import. The before-subcommand contract stands.
- Startup cost (daemon/caching), long-content input ergonomics (`@file`/stdin),
  output transformation — accepted v1 trade-offs, out of scope here.

## Design

### Parameter descriptions in `--help`

- `ToolParam` gains a `description: str | None` field (default `None`);
  `parse_tool` reads it from the schema property alongside type and default.
- `_make_handler` builds each generated parameter annotation as
  `Annotated[<type>, cyclopts.Parameter(help=...)]` when a description exists,
  plain type otherwise. The existing `T | None` default-None composition stays
  inside the `Annotated` first slot, preserving the pydantic default-validation
  behavior documented in `build.py`.
- Description text passes through **full and verbatim** — the output contract's
  verbatim principle extended to schema text. No sentence-truncation: tool
  descriptions keep their current split (first sentence in `atli tools`, full
  text at `<tool> --help`), parameter descriptions appear only at `--help`.
- cyclopts renders the help text in its existing Parameters box, wrapped,
  alongside the `[required]`/`[default: …]` markers (verified against
  cyclopts 4.22).

### Self-correcting `--profile` error

- In `main.py`'s existing `CycloptsError` handler: when the error message
  names `--profile`, append the placement hint ("Use --profile=NAME or
  --profile NAME (before the subcommand)."). The hint string currently lives
  as `config._PROFILE_USAGE` and is promoted to a public name for the second
  call site.
- The enrichment fires only on cyclopts' post-parse unknown-option verdict, so
  a flag *value* that merely contains the substring `--profile` (e.g. inside a
  JQL string) can never false-positive: cyclopts has already decided the token
  is an option, not a value.
- Exit code stays 2; a correctly placed `--profile` is unaffected.

## Error handling

No new error classes, exit codes, or streams. One existing message gains an
addendum; every other behavior — stdout verbatim output, silenced server
logging, exit codes 0/1/2 — is byte-identical.

## Testing

- **Unit**: `parse_tool` extracts a present description; `None` when the
  schema omits it; existing mapping tests stay valid via the defaulted field.
- **Integration** (stub FastMCP app through the `runner_factory` seam):
  `<tool> --help` output contains the parameter description text;
  `--profile` after the subcommand exits 2 with "before the subcommand" on
  stderr; the correctly-placed flag path is unchanged.
- **Manual smoke**: `atli jira get-user-profile --help` against a real
  instance shows the schema description.

## Documentation

README "Notes for agents" gains one line: parameter descriptions come straight
from the tool's schema. AGENTS.md already documents flag placement — no change.

## Open Questions

None — both fixes verified feasible against cyclopts 4.22 and the pinned
mcp-atlassian surface.

## TLDR

Two small contract-preserving fixes close the audit's fidelity gaps:
`ToolParam` carries the schema's parameter description into cyclopts'
`Parameter(help=…)` so `<tool> --help` becomes a faithful one-stop schema, and
the misplaced-`--profile` error teaches the correct placement instead of
dead-ending. No surface, output, or exit-code changes; verified by unit,
stub-server integration, and manual smoke tests.
