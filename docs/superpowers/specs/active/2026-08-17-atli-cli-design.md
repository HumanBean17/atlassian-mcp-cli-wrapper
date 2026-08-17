# atli — CLI wrapper over mcp-atlassian

**Status:** draft

## Problem

Running `mcp-atlassian` as a connected MCP server costs tokens: ~95 tool schemas ride along in every request, though a typical session uses one or two operations. Users currently choose between reconnecting the server on demand (harness settings churn) or paying the description tax all session.

## Goal

`atli` — a CLI that exposes every mcp-atlassian operation as an ordinary shell command, so AI agents invoke it through their Bash tool only when needed. Agents discover the surface from compact help output (or a short AGENTS.md briefing) instead of schemas, and the MCP server stays disconnected from the harness.

Additionally: first-class multi-instance support. Corporate users often have several Jira/Confluence environments (e.g. internal + partner), each with its own URL and credentials. Long-running MCP servers force one process per instance; a fresh-process CLI switches instances per invocation with zero state leakage.

## Non-goals

- Publishing/release automation for PyPI (name `mcp-atlassian-cli` verified free; reserved for later use).
- Output transformation: no reformatting, truncation, or `--json` in v1.
- Write protection beyond what mcp-atlassian itself enforces.
- Daemon/server mode, result caching, retries, timeouts.

## Architecture

Single Python process per invocation. It resolves configuration, imports mcp-atlassian's FastMCP app in-process, enumerates its mounted tools, generates a cyclopts command tree from their JSON Schemas, and dispatches the invoked command through fastmcp's in-memory client.

```
atli invocation
  → config.py     resolve profile, inject env          (before any mcp_atlassian import)
  → runner.py     ToolRunner: list_tools / call_tool    (the only server-touching module)
  → discovery.py  ToolSpec: parse name, description, inputSchema
  → build.py      cyclopts app: one command per tool
  → main.py       entry point, exit codes, stderr
```

The `ToolRunner` seam is deliberate: it is the only module that knows the transport. If a future fastmcp release breaks in-memory clients, swapping to a subprocess stdio-MCP transport is a one-module change.

Chosen over alternatives because in-process is fastest (~0.5–1 s startup, imports dominate) and preserves byte-identical tool output (preprocessing, formatting, error messages) — subprocess MCP costs 1.5–3 s per call; direct `JiraClient`/`ConfluenceClient` calls lose the tool layer and its 1:1 operation surface.

## Command surface

```
atli [--profile NAME] tools [--service jira|confluence]
atli [--profile NAME] profiles
atli [--profile NAME] jira <tool> [flags]
atli [--profile NAME] confluence <tool> [flags]
```

- **Name mapping** (mechanical, no curation, no aliases): server tool `jira_get_issue` → command `atli jira get-issue`; argument `issue_key` → flag `--issue-key`. Namespace split follows the server's mounted prefixes (`jira_`, `confluence_`); any tool without a service prefix registers as a top-level command.
- **Arguments**: generated from each tool's JSON Schema. Types `string`, `integer`, `number`, `boolean`, arrays — coerced by cyclopts. Required vs optional per schema; defaults shown in `--help`.
- **`atli tools [--service]`**: one line per tool — name + first sentence of description. The agent's discovery surface (~95 lines) in place of ~95 schemas.
- **`atli profiles`**: lists profile names, their service URLs, and the default. Never prints tokens or secrets.

## Output contract

Tool text content prints verbatim to stdout — mcp-atlassian already preprocesses to LLM-friendly markdown. Non-text content blocks (rare; e.g. images) print as JSON. Nothing is added, filtered, or truncated.

## Configuration & profiles

No config file → behavior identical to bare mcp-atlassian: ambient env vars and `.env` apply.

Config file (TOML, stdlib `tomllib` — no new deps). Lookup order, first found wins:

1. `$ATLI_CONFIG`
2. `./.atli.toml` (project-local)
3. `~/.config/atli/config.toml` (user-global)

```toml
default_profile = "corp"

[profiles.corp]
JIRA_URL = "https://corp.atlassian.net"
JIRA_USERNAME = "user@corp.com"
JIRA_API_TOKEN = "..."
CONFLUENCE_URL = "https://corp.atlassian.net/wiki"
CONFLUENCE_PERSONAL_TOKEN = "..."

[profiles.partner]                     # service-scoped profiles are valid
CONFLUENCE_URL = "https://partners.example.com/wiki"
CONFLUENCE_PERSONAL_TOKEN = "..."
```

**Profile resolution**: `--profile` flag > `ATLI_PROFILE` env > `default_profile` > none (ambient env only). Named profile missing from config is a usage error.

**Injection semantics** — the safety-critical rule:

- Profile vars are injected into the process environment **before any `mcp_atlassian` module is imported** (the package reads env at import time). Import order is a hard constraint on `main.py`.
- **Per-service replacement, not merge**: if a profile defines any `JIRA_*` var, all ambient `JIRA_*` vars are cleared first (likewise `CONFLUENCE_*`, `MCP_ATLASSIAN_*`), then the profile's values apply. A Confluence-only profile can therefore never leak ambient Corp Jira credentials to the partner instance. A profile is the source of truth for every service it touches; services it doesn't touch fall back to ambient env.
- Tokens are plaintext in the config file — same trust level as `.env`. Docs must advise `chmod 600`. Env-var interpolation for secrets is a possible v2 addition.

## Runtime behavior

- Every invocation imports `main_mcp`, opens one fastmcp in-memory client session, enumerates tools, builds the command tree, dispatches. One `asyncio.run()` per process.
- No disk cache of tool metadata — in-process enumeration is sub-second and always fresh.
- Unconfigured services (e.g. no `JIRA_URL`) fail at call time with the server's own error, surfaced verbatim.
- Server-side write protection env semantics apply unchanged; the CLI adds no second gate.

## Error handling & exit codes

| Situation | Behavior | Exit |
|---|---|---|
| Tool error (API failure, not found, permissions) | Server's error text → stderr | 1 |
| Missing auth/env config | Server's config error → stderr | 1 |
| Unknown profile, bad args, unknown tool | Usage error → stderr, hint to run `atli tools` | 2 |
| fastmcp in-memory API incompatible | Clear message: pin deps or update CLI; blast radius = `runner.py` | 1 |

## Testing

- **Unit** (pure, no server): snake→kebab mapping, namespace split, JSON-Schema→cyclopts parameter building, profile resolution and per-service replacement semantics.
- **Integration**: `ToolRunner` against a locally registered stub FastMCP app (2–3 dummy tools) — list/call round-trip, error propagation. No network.
- **Manual smoke** (real creds, not automated): `atli tools`, `atli jira get-issue --issue-key X`, profile switching against two instances.

## Deliverables

| Artifact | Content |
|---|---|
| `pyproject.toml` | Package `mcp-atlassian-cli`, console script `atli`, deps: `mcp-atlassian>=0.23`, `cyclopts>=4` |
| `src/` package | `main.py`, `config.py`, `runner.py`, `discovery.py`, `build.py` |
| `AGENTS.md` | 5–10 line agent briefing: what `atli` is, `atli tools` for discovery, 2 example invocations, `--profile` for multi-instance |
| Tests | Unit + integration per above |

## Open Questions

None — all decisions settled during brainstorming (coverage, output, architecture, naming, profiles).

## TLDR

`atli` (package `mcp-atlassian-cli`) turns mcp-atlassian's full ~95-tool surface into a schema-generated cyclopts CLI that agents call via Bash on demand, keeping the MCP server disconnected and tool schemas out of context. In-process fastmcp client behind a single `ToolRunner` seam; verbatim MCP output; TOML profiles with per-service env replacement give safe multi-instance use that long-running MCP servers can't match. Exit codes 0/1/2; tested via unit + stub-FastMCP integration + manual smoke.
