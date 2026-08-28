# atli

`atli` is a command-line interface for Jira, Confluence, and Bitbucket. It
exposes every operation of [mcp-atlassian](https://pypi.org/project/mcp-atlassian/)
as an ordinary shell command — no MCP client, server process, or daemon. Tools
are discovered at startup from the mcp-atlassian server itself, so new tools
appear automatically with pinned versions. The server comes from an install
extra: `[atlassian]` (Jira + Confluence) or `[bitbucket]`
([mcp-atlassian-with-bitbucket](https://github.com/jellythomas/mcp-atlassian-with-bitbucket)
— Jira, Confluence, **and** Bitbucket).

```
atli tools                              # list what your credentials unlock
atli jira get-issue --issue-key PROJ-1  # markdown, verbatim from the tool
atli confluence search --query "deploy"
atli bitbucket list-repositories        # with the [bitbucket] extra
atli --profile work jira search --jql "assignee = currentUser()"
```

## Install

Jira + Confluence (upstream mcp-atlassian):

```console
$ pipx install "mcp-atlassian-cli[atlassian]"
```

Bitbucket support (via the fork — Jira, Confluence, **and** Bitbucket):

```console
$ pipx install "mcp-atlassian-cli[bitbucket]"
```

A bare `pipx install mcp-atlassian-cli` (no extra) installs the CLI without a
server: the first tool command fails with a message naming both extras. The
two providers are mutually exclusive — both ship the same `mcp_atlassian`
package with conflicting fastmcp pins, so they cannot coexist in one
environment and pip will refuse the combination. To switch providers, start
fresh: `pipx uninstall mcp-atlassian-cli && pipx install "mcp-atlassian-cli[bitbucket]"`.

With plain `pip` (no pipx venv to recreate), also uninstall the fastmcp
distributions before switching: upstream `mcp-atlassian` uses `fastmcp>=3`, a
meta-package that installs `fastmcp-slim` into the same `fastmcp` package
directory that `fastmcp` 2.x (the fork's pin) uses. pip cannot undo that
overlap when downgrading — the leftover files break imports with errors like
`cannot import name 'PrivateKeyJWTClientAuthenticator' from
'fastmcp.server.auth.auth'`, and no reinstall repairs it:

```console
$ pip uninstall -y fastmcp fastmcp-slim
$ pip install "mcp-atlassian-cli[bitbucket]"
```

Or from a checkout:

```console
$ git clone <this-repo> && cd mcp-atlassian-cli
$ python -m venv .venv
$ .venv/bin/pip install -e ".[atlassian]"
$ .venv/bin/atli tools
```

Requires Python 3.11+. The package pins `cyclopts>=4.22,<5`; the mcp-atlassian
provider ships only via an extra — `mcp-atlassian>=0.23,<0.24` with
`[atlassian]` (fastmcp 3.4.x), or `mcp-atlassian-with-bitbucket>=1.0.5,<1.1`
with `[bitbucket]` (fastmcp 2.13–2.14). A base dependency cannot be
suppressed by an extra marker, so the provider cannot also be a default.

## Authentication

`atli` authenticates with the same environment variables as mcp-atlassian.
Tools appear only for services you have configured — with the `[atlassian]`
provider, 63 Jira commands with `JIRA_*` set, 35 Confluence commands with
`CONFLUENCE_*` set, 98 with both, none with neither. With the `[bitbucket]`
extra, configuring `BITBUCKET_*` adds the `bitbucket` service group (and the
fork's jira/confluence surface matches upstream's).

| Deployment | Jira | Confluence | Bitbucket |
|---|---|---|---|
| **Cloud** (basic auth) | `JIRA_URL` + `JIRA_USERNAME` + `JIRA_API_TOKEN` | `CONFLUENCE_URL` + `CONFLUENCE_USERNAME` + `CONFLUENCE_API_TOKEN` | `BITBUCKET_URL` + `BITBUCKET_USERNAME` + `BITBUCKET_API_TOKEN` (app passwords stopped working 2026-06-09) |
| **Data Center / Server** (PAT) | `JIRA_URL` + `JIRA_PERSONAL_TOKEN` | `CONFLUENCE_URL` + `CONFLUENCE_PERSONAL_TOKEN` | `BITBUCKET_URL` + `BITBUCKET_PERSONAL_TOKEN` |
| **Data Center / Server** (mTLS) | `JIRA_URL` + `JIRA_CLIENT_CERT` (+ `JIRA_CLIENT_KEY`) | `CONFLUENCE_URL` + `CONFLUENCE_CLIENT_CERT` (+ `CONFLUENCE_CLIENT_KEY`) | — |

Notes:

- On Cloud, username is the Atlassian account email; the API token comes from
  <https://id.atlassian.com/manage-profile/security/api-tokens>.
- On Bitbucket Cloud, use a scoped API token (`bb_pat_…`): *avatar →
  Personal settings → Security → Create and manage API tokens*. Scopes follow
  the toolsets you use — Repositories: Read + Pull requests: Read covers
  read-only repository and pull-request work. `BITBUCKET_USERNAME` is the
  Atlassian account email here. The older app-password method
  (`BITBUCKET_APP_PASSWORD` + the Bitbucket username, not the email) is dead:
  app passwords could not be created after 2025-09-09 and stopped working on
  2026-06-09 — and the fork reads `BITBUCKET_APP_PASSWORD` first, so unset
  any leftover or it shadows a valid API token.
- `BITBUCKET_WORKSPACE` optionally pins a default workspace slug (Cloud
  only) — the part after `bitbucket.org/` in repository URLs. The Bitbucket
  URL decides Cloud vs Server: `bitbucket.org` (or any host serving
  `api.bitbucket.org`) means Cloud, everything else means Server/Data
  Center.
- On Data Center/Server, the personal token is created under *Profile → Personal Access Tokens*.
- mTLS with an **encrypted** private key is not supported (the underlying
  library rejects it). Decrypt the key first:
  `openssl rsa -in key.enc -out key`.
- Data Center/Server Jira and Confluence also accept username + API token via
  the same `*_USERNAME`/`*_API_TOKEN` variables if basic auth is enabled.
  Bitbucket Server/DC has no live basic-auth path: `BITBUCKET_API_TOKEN` is
  Cloud-only, and its basic auth used the app password that stopped working
  2026-06-09 — use `BITBUCKET_PERSONAL_TOKEN` there.
- `*URL` may include `/wiki` for Confluence. The URL decides Cloud vs Data Center: hosts ending in `.atlassian.net` (also `.jira.com`, `.jira-dev.com`, `.atlassian.com`, and exact-match `api.atlassian.com`, plus the US-Gov domains) mean Cloud; everything else, including `localhost` and private IPs, means Data Center/Server.

```console
$ export JIRA_URL="https://your-company.atlassian.net"
$ export JIRA_USERNAME="you@your-company.com"
$ export JIRA_API_TOKEN="..."
$ atli tools | head -3
```

Bitbucket Cloud (with the `[bitbucket]` extra):

```console
$ export BITBUCKET_URL="https://bitbucket.org"
$ export BITBUCKET_USERNAME="you@your-company.com"
$ export BITBUCKET_API_TOKEN="bb_pat_..."
$ atli bitbucket list-repositories
```

## Profiles (multiple instances)

Storing credentials in a TOML file lets you switch instances with
`--profile NAME` and keep several side by side. Config lookup order:

1. `$ATLI_CONFIG` — if set, must point at an existing file (an error otherwise)
2. `./.atli.toml` in the current directory
3. `~/.config/atli/config.toml`

The first existing file wins. Any key you set in a profile (including options
such as `TOOLSETS = "all"`, which is unprefixed) replaces the ambient
environment for that service prefix; prefixes the profile doesn't mention are
left untouched. `TOOLSETS` only takes effect in a profile that also sets at
least one service-prefixed key (`JIRA_*`/`CONFLUENCE_*`/`MCP_ATLASSIAN_*`/
`BITBUCKET_*`) — a `TOOLSETS`-only profile changes nothing.

```toml
# ~/.config/atli/config.toml
default_profile = "work"

[profiles.work]
JIRA_URL = "https://your-company.atlassian.net"
JIRA_USERNAME = "you@your-company.com"
JIRA_API_TOKEN = "..."
CONFLUENCE_URL = "https://your-company.atlassian.net/wiki"
CONFLUENCE_USERNAME = "you@your-company.com"
CONFLUENCE_API_TOKEN = "..."
BITBUCKET_URL = "https://bitbucket.org"
BITBUCKET_USERNAME = "you@your-company.com"
BITBUCKET_API_TOKEN = "bb_pat_..."

[profiles.dc]
JIRA_URL = "https://jira.internal.example.com"
JIRA_PERSONAL_TOKEN = "..."
```

**Warning: profiles are plaintext credentials.** After creating the file, run:

```console
$ chmod 600 ~/.config/atli/config.toml
```

Profile selection order: `--profile NAME` flag > `$ATLI_PROFILE` > the
`default_profile` key. The flag must appear before the subcommand; `atli
--profile=work tools` and `atli --profile work tools` both work.

```console
$ atli profiles            # lists profiles and URLs — never tokens
* work (default)
    jira: https://your-company.atlassian.net
    confluence: https://your-company.atlassian.net/wiki
    bitbucket: https://bitbucket.org
  dc
    jira: https://jira.internal.example.com
$ atli --profile dc jira get-issue --issue-key OPS-42
```

## Priming AI agents (`atli prime`)

`atli prime` prints a compact primer of the local setup — configured services,
active profile, usage patterns, quirks — as AI-optimized markdown. It is
designed for SessionStart hooks, so agents re-learn atli after context
compaction. It never imports mcp-atlassian and costs milliseconds.

The one-command onboarding installs the hook for you — idempotent, never
clobbering existing settings:

```console
$ atli prime --install               # detect harnesses, user scope
$ atli prime --install --scope project  # .claude/settings.json in the repo
```

- **Claude Code** is supported (user or project scope).
- **Gemini CLI** and **Codex** are detected but not auto-installed: Gemini
  runs SessionStart hooks without injecting their context (gemini-cli
  issue #15413); Codex hooks are experimental. `atli prime --hook-json`
  output remains compatible with both if you wire them manually.

Manual Claude Code hook, if you prefer (same envelope as the installer
writes; the `--hook-json` envelope is what Gemini CLI and Codex would need
too, wired by hand until their hook support lands):

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [{ "type": "command", "command": "atli prime --hook-json" }] }
    ]
  }
}
```

```console
$ atli prime [--hook-json] [--export]
```

Claude Code hook (same envelope serves Gemini CLI and Codex):

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [{ "type": "command", "command": "atli prime --hook-json" }] }
    ]
  }
}
```

- `--hook-json` wraps the output in the SessionStart hook envelope.
- `--export` prints the default content (ignores overrides, works even when
  nothing is configured) — the starting point for customization.
- With nothing configured and no override file, `prime` prints nothing and
  exits 0 — zero token cost on machines where atli cannot act anyway.
- The Configured line reads exported variables and profiles only; `.env`
  files (consumed inside mcp-atlassian) are invisible to prime.

**Override** — a PRIME.md file replaces the default content entirely (no
dynamic header, prints even when unconfigured). Lookup order, first existing
file wins:

1. `$ATLI_PRIME` — must point at an existing file (an error otherwise)
2. `./.atli/PRIME.md` — current directory; check it into the repo for
   project-specific conventions
3. `~/.config/atli/PRIME.md` — personal default

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (tool output on stdout) |
| 1 | Tool or server failure — the tool's error message on stderr |
| 2 | Usage or configuration error — bad flags, missing/invalid config file, unknown profile |

## Notes for agents and scripts

- `atli tools --search TEXT` shortlists tools by keyword across names and
  full descriptions.
- `atli <service> <tool> --help` shows every parameter with its type and
  default, straight from the tool's schema.
- Parameter descriptions in a tool's `--help` come verbatim from the tool's
  schema — accepted formats and semantics, straight from the source.
- Popular tools show real invocations under `Example invocations:` in
  `--help` — identifiers like `--page-id`, JQL and relative-date formats,
  `@file` for long content.
- Repeatable list flags repeat: `--read-users alice --read-users bob` (on
  `confluence set-page-restrictions`) gives `["alice", "bob"]`;
  `--read-users alice,bob` gives one element `"alice,bob"`.
- String values expand: `--body @comment.md` reads the file, `-` reads
  stdin (when piped), `@@text` passes a literal `@text`. A missing file is
  a usage error (exit 2) whose message shows the escape.
- Startup takes ~1 s warm, a few seconds cold (the mcp-atlassian import
  dominates). For bulk work, prefer one `search` over many single-item calls.
