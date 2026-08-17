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
atli --profile work jira search-jql --jql "assignee = currentUser()"
```

## Install

```console
$ pipx install mcp-atlassian-cli          # isolated, recommended
```

or, from a checkout:

```console
$ python -m venv .venv && . .venv/bin/activate
$ pip install -e .
```

Requires Python 3.11+. The package pins `mcp-atlassian>=0.23,<0.24` (which
brings fastmcp 3.4.x) and `cyclopts>=4.22,<5`.

## Authentication

`atli` authenticates with the same environment variables as mcp-atlassian.
Tools appear only for services you have configured — 63 Jira commands with
`JIRA_*` set, 35 Confluence commands with `CONFLUENCE_*` set, 98 with both,
none with neither.

| Deployment | Jira | Confluence |
|---|---|---|
| **Cloud** (basic auth) | `JIRA_URL` + `JIRA_USERNAME` + `JIRA_API_TOKEN` | `CONFLUENCE_URL` + `CONFLUENCE_USERNAME` + `CONFLUENCE_API_TOKEN` |
| **Data Center / Server** (PAT) | `JIRA_URL` + `JIRA_PERSONAL_TOKEN` | `CONFLUENCE_URL` + `CONFLUENCE_PERSONAL_TOKEN` |
| **Data Center / Server** (mTLS) | `JIRA_CLIENT_CERT` (+ `JIRA_CLIENT_KEY`, optional `JIRA_CLIENT_KEY_PASSWORD`) | `CONFLUENCE_CLIENT_CERT` (+ `CONFLUENCE_CLIENT_KEY`, optional `CONFLUENCE_CLIENT_KEY_PASSWORD`) |

Notes:

- On Cloud, username is the Atlassian account email; the API token comes from
  <https://id.atlassian.com/manage-profile/security/api-tokens>.
- On Data Center/Server, the personal token is created under *Profile → Personal Access Tokens*.
- Data Center/Server also accepts username + API token via the same
  `*_USERNAME`/`*_API_TOKEN` variables if basic auth is enabled.
- `*URL` may include `/wiki` for Confluence. The URL decides Cloud vs Data Center: hosts ending in `.atlassian.net` (also `.jira.com`, `.atlassian.com`, the US-Gov domains) mean Cloud; everything else, including `localhost` and private IPs, means Data Center/Server.

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

The first existing file wins. Any key you set in a profile (including
`MCP_ATLASSIAN_*` options such as `TOOLSETS = "all"`) replaces the ambient
environment for that service prefix; prefixes the profile doesn't mention are
left untouched.

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

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (tool output on stdout) |
| 1 | Tool or server failure — the tool's error message on stderr |
| 2 | Usage or configuration error — bad flags, missing/invalid config file, unknown profile |

## Notes for agents and scripts

- `atli <service> <tool> --help` shows every parameter with its type and
  default, straight from the tool's schema.
- Repeatable list flags repeat: `--labels a --labels b` gives `["a", "b"]`;
  `--labels a,b` gives one element `"a,b"`.
- Startup takes ~5 s per invocation; the mcp-atlassian import dominates. For
  bulk work, prefer one `search` over many single-item calls.
