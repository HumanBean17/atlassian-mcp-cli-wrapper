# atli

`atli` is a command-line interface for Jira and Confluence. It exposes every
operation of [mcp-atlassian](https://pypi.org/project/mcp-atlassian/) as an
ordinary shell command — no MCP client, server process, or daemon. Tools are
discovered at startup from the mcp-atlassian server itself, so new tools appear
automatically with pinned versions.

```
atli tools                              # list what your credentials unlock
atli jira get-issue --issue-key PROJ-1  # markdown, verbatim from the tool
atli confluence search --query "deploy"
atli --profile work jira search --jql "assignee = currentUser()"
```

## Install

```console
$ pipx install mcp-atlassian-cli
```

Or from a checkout:

```console
$ git clone <this-repo> && cd mcp-atlassian-cli
$ python -m venv .venv
$ .venv/bin/pip install -e .
$ .venv/bin/atli tools
```

Requires Python 3.11+. The package pins `mcp-atlassian>=0.23,<0.24` (which
resolves to fastmcp 3.4.x today) and `cyclopts>=4.22,<5`.

## Authentication

`atli` authenticates with the same environment variables as mcp-atlassian.
Tools appear only for services you have configured — 63 Jira commands with
`JIRA_*` set, 35 Confluence commands with `CONFLUENCE_*` set, 98 with both,
none with neither.

| Deployment | Jira | Confluence |
|---|---|---|
| **Cloud** (basic auth) | `JIRA_URL` + `JIRA_USERNAME` + `JIRA_API_TOKEN` | `CONFLUENCE_URL` + `CONFLUENCE_USERNAME` + `CONFLUENCE_API_TOKEN` |
| **Data Center / Server** (PAT) | `JIRA_URL` + `JIRA_PERSONAL_TOKEN` | `CONFLUENCE_URL` + `CONFLUENCE_PERSONAL_TOKEN` |
| **Data Center / Server** (mTLS) | `JIRA_URL` + `JIRA_CLIENT_CERT` (+ `JIRA_CLIENT_KEY`) | `CONFLUENCE_URL` + `CONFLUENCE_CLIENT_CERT` (+ `CONFLUENCE_CLIENT_KEY`) |

Notes:

- On Cloud, username is the Atlassian account email; the API token comes from
  <https://id.atlassian.com/manage-profile/security/api-tokens>.
- On Data Center/Server, the personal token is created under *Profile → Personal Access Tokens*.
- mTLS with an **encrypted** private key is not supported (the underlying
  library rejects it). Decrypt the key first:
  `openssl rsa -in key.enc -out key`.
- Data Center/Server also accepts username + API token via the same
  `*_USERNAME`/`*_API_TOKEN` variables if basic auth is enabled.
- `*URL` may include `/wiki` for Confluence. The URL decides Cloud vs Data Center: hosts ending in `.atlassian.net` (also `.jira.com`, `.jira-dev.com`, `.atlassian.com`, and exact-match `api.atlassian.com`, plus the US-Gov domains) mean Cloud; everything else, including `localhost` and private IPs, means Data Center/Server.

```console
$ export JIRA_URL="https://your-company.atlassian.net"
$ export JIRA_USERNAME="you@your-company.com"
$ export JIRA_API_TOKEN="..."
$ atli tools | head -3
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
least one service-prefixed key (`JIRA_*`/`CONFLUENCE_*`/`MCP_ATLASSIAN_*`) —
a `TOOLSETS`-only profile changes nothing.

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
  dc
    jira: https://jira.internal.example.com
$ atli --profile dc jira get-issue --issue-key OPS-42
```

## Priming AI agents (`atli prime`)

`atli prime` prints a compact primer of the local setup — configured services,
active profile, usage patterns, quirks — as AI-optimized markdown. It is
designed for SessionStart hooks, so agents re-learn atli after context
compaction. It never imports mcp-atlassian and costs milliseconds.

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

- `atli <service> <tool> --help` shows every parameter with its type and
  default, straight from the tool's schema.
- Parameter descriptions in a tool's `--help` come verbatim from the tool's
  schema — accepted formats and semantics, straight from the source.
- Repeatable list flags repeat: `--read-users alice --read-users bob` (on
  `confluence set-page-restrictions`) gives `["alice", "bob"]`;
  `--read-users alice,bob` gives one element `"alice,bob"`.
- Startup takes ~1 s warm, a few seconds cold (the mcp-atlassian import
  dominates). For bulk work, prefer one `search` over many single-item calls.
