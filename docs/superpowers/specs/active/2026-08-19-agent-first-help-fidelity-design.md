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

- Enum/object-typed parameter rendering — the current tool surface has no enum
  or object params. It does carry `anyOf` params (104 of 322 on mcp-atlassian
  0.23.x), all `Optional[primitive]`; they degrade through the existing `str`
  type-map fallback exactly as before this change (which is also why the four
  `array|null` params don't get repeat-flag behavior — pre-existing, out of
  scope).
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
  verbatim principle extended to schema text, at content level: cyclopts/rich
  wrap long lines and may restyle list bullets while preserving the text. No
  sentence-truncation: tool descriptions keep their current split (first
  sentence in `atli tools`, full text at `<tool> --help`), parameter
  descriptions appear only at `--help`.
- cyclopts renders the help text in its existing Parameters box, wrapped,
  alongside the `[required]`/`[default: …]` markers (verified against cyclopts
  4.22 and 4.23, the resolved versions under the `>=4.22,<5` pin).

### Self-correcting `--profile` error

- In `main.py`'s existing `CycloptsError` handler: when the error is an
  `UnknownOptionError` whose token keyword is exactly `--profile` (the form
  cyclopts reports for both `--profile NAME` and `--profile=NAME` after the
  subcommand), append the placement hint ("Use --profile=NAME or --profile
  NAME (before the subcommand)."). The hint string lives as
  `config.PROFILE_USAGE` (promoted from private for the second call site).
- The trigger is the error class plus token identity, never a message
  substring: other `CycloptsError` subclasses embed raw user values (a
  coercion failure on `--comment-limit "5 --profile=x"`, an unused stray
  token), and a substring match would wrongly append the placement hint to
  those — steering an agent toward flag placement when its real problem is a
  bad value.
- Exit code stays 2; a correctly placed `--profile` is unaffected.

## Error handling

No new error classes, exit codes, or streams. Exactly one message — the
unknown-option-for-`--profile` error — gains an addendum; every other
behavior — stdout verbatim output, silenced server logging, exit codes
0/1/2, all other error messages — is byte-identical.

## Testing

- **Unit** (build level, `create_app` directly — the stub FastMCP tools carry
  no per-param descriptions, so the build layer is the right seam): `<tool>
  --help` output contains the parameter description text, with
  `[required]`/`[default: …]` markers surviving; an explicitly-passed value
  binds through the `Annotated` metadata; `parse_tool` extracts a present
  description, passes `""` through, degrades a non-string to `None`, and
  yields `None` when the schema omits the key.
- **Integration** (stub FastMCP app through the `runner_factory` seam):
  `--profile` after the subcommand exits 2 with "before the subcommand" on
  stderr; usage errors not involving `--profile`, and values/stray tokens that
  merely *contain* the substring, get no hint; the correctly-placed flag path
  is unchanged.
- **Manual smoke**: `atli jira get-user-profile --help` against a real
  instance shows the schema description.

## Documentation

README "Notes for agents" gains one line: parameter descriptions come straight
from the tool's schema. AGENTS.md already documents flag placement — no change.

## Open Questions

None — both fixes verified feasible against cyclopts 4.22 and 4.23 (the
versions resolved under the `>=4.22,<5` pin) and the pinned mcp-atlassian
surface.

## TLDR

Two small contract-preserving fixes close the audit's fidelity gaps:
`ToolParam` carries the schema's parameter description into cyclopts'
`Parameter(help=…)` so `<tool> --help` becomes a faithful one-stop schema, and
the misplaced-`--profile` error teaches the correct placement instead of
dead-ending. No surface, output, or exit-code changes; verified by unit,
stub-server integration, and manual smoke tests.
