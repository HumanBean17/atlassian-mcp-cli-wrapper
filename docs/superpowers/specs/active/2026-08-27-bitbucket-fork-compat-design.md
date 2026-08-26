# Fork compatibility & Bitbucket integration (the `[bitbucket]` extra)

**Status:** in_progress

## Problem

`atli` hard-depends on `mcp-atlassian>=0.23,<0.24` and imports the
`mcp_atlassian` package in-process. Users of the fork
[mcp-atlassian-with-bitbucket](https://github.com/jellythomas/mcp-atlassian-with-bitbucket)
cannot install `atli` at all: the fork is a different distribution that ships
the *same* `mcp_atlassian` import package while pinning `fastmcp>=2.13,<2.15`
(2.x), whereas upstream 0.23.x brings fastmcp 3.4.x. pip's resolver refuses
any environment containing both, so fork users hit an install-time conflict —
the reported failure.

Even where the import resolved, atli's service plumbing only knows Jira and
Confluence:

- `discovery.SERVICE_PREFIXES` (`{"jira", "confluence"}`) would flatten
  `bitbucket_*` tools into prefix-less root commands.
- `config.SERVICE_ENV_PREFIXES` lacks a `BITBUCKET_` entry, so profiles cannot
  manage Bitbucket credentials with the stale-credential-isolation semantics
  the other services get.
- `prime.detect_services` and the primer copy know two services;
  `describe_profiles` shows only `JIRA_URL` / `CONFLUENCE_URL`.
- The runner's compatibility error steers users to the upstream pin.

## Goal

1. Fork users install and run `atli` with one documented command, getting the
   full `atli bitbucket <tool>` command group.
2. Existing Jira/Confluence users keep a one-command install via the
   `[atlassian]` extra; existing venvs keep working through upgrades
   (pip/pipx never uninstall an already-present provider).
3. Profiles, `atli profiles`, `atli prime`, and help text treat Bitbucket as a
   first-class service wherever it is configured.

## Non-goals

- Running both providers in one environment — impossible by construction
  (same import package, conflicting fastmcp pins); unsupported and refused.
- A subprocess/stdio transport for the server. atli keeps its in-process
  import architecture.
- Curated per-tool Bitbucket examples in `examples.py` (schema-derived help
  still renders; the corpus can grow later).
- Provider detection in atli's runtime copy. All service awareness is
  environment-driven; which dist supplies `mcp_atlassian` is never queried.
- Support for Bitbucket auth beyond the fork's environment-variable matrix
  (its per-request header auth is a server-side concern; atli never sees it).

## Provider model (packaging)

`pyproject.toml`:

- `dependencies` = `["cyclopts>=4.22,<5"]` — no provider in the base install.
- `[project.optional-dependencies]`, one provider each:
  - `atlassian` → `mcp-atlassian>=0.23,<0.24` (Jira + Confluence; fastmcp
    3.4.x)
  - `bitbucket` → `mcp-atlassian-with-bitbucket>=1.0.5,<1.1` (the fork: Jira +
    Confluence + Bitbucket; fastmcp 2.13–2.14)
- Version bumps to 0.4.0 in `src/mcp_atlassian_cli/__init__.py` (pyproject
  reads it dynamically; the release workflow's version guard is unchanged).

Install contract:

| Audience | Command | Resolves to |
|---|---|---|
| Jira/Confluence users | `pipx install "mcp-atlassian-cli[atlassian]"` | upstream |
| Fork users | `pipx install "mcp-atlassian-cli[bitbucket]"` | the fork |
| Bare install (no extra) | `pipx install mcp-atlassian-cli` | no provider — the first tool command fails with guidance naming both extras |
| Both providers requested | — | pip's resolver refuses; choose-one is enforced at install time |
| Existing users upgrading | `pipx upgrade mcp-atlassian-cli` | keeps working; the provider already in the venv is never uninstalled |

**Why no default-provider marker:** the rejected alternative kept upstream in
base dependencies behind `mcp-atlassian>=0.23,<0.24; extra != "bitbucket"`.
It cannot work: when pip resolves `pkg[extra]`, its resolver also evaluates
the package's base dependencies with `extra=""` (the `ExtrasCandidate` base
view), so the marker is always true for the base candidate and upstream is
demanded unconditionally — `pip install "mcp-atlassian-cli[bitbucket]"`
fails with `ResolutionImpossible` (uv implements the same union semantics).
Verified empirically on pip 26.1.2 and uv 0.11.6 and by the CI packaging leg;
there is no resolver-legal way to express "default dependency unless extra X"
in one distribution. Hence: the base ships no provider at all.

**Switching providers** requires a fresh environment
(`pipx uninstall mcp-atlassian-cli && pipx install "mcp-atlassian-cli[bitbucket]"`);
in-place switching dies on pip's resolver over the already-installed provider,
by design. Documented.

## Runner compatibility

`runner.py` keeps its lazy imports: `from mcp_atlassian.servers.main import
main_mcp` (the fork preserves that path) and `from fastmcp import Client`,
which resolves to whichever fastmcp the installed provider pulled in
(2.13–2.14 under `[bitbucket]`, 3.4.x under `[atlassian]`). The APIs atli
uses — in-memory `Client(app)`, `list_tools()`, `call_tool()` → `.content`,
`fastmcp.exceptions.ToolError` — exist in both majors; verification is a CI
duty (below), not an abstraction. If drift surfaces, the fix is a thin local
shim at the call site (in the spirit of `result_to_text`'s existing `getattr`
defensive rendering), never a fastmcp compatibility layer.

`_PIN_OR_UPDATE` is replaced by a provider-missing message naming both
install recipes (`[atlassian]` and `[bitbucket]`) plus the choose-one rule;
this is the expected path for every bare install, so the message is first-run
guidance. `ToolRunnerError`'s docstring follows (the fix is "install a
provider extra, or update the CLI"). `_silence_server_logging`
is provider-agnostic and unchanged.

Accepted quirk: the fork reports `mcp_atlassian.__version__ == "0.0.0"` (its
dist name differs, so the version lookup falls back). atli never reads that
attribute; nothing gates on it.

## Bitbucket service wiring

- **`discovery.SERVICE_PREFIXES`** gains `"bitbucket"`: the fork's
  `bitbucket_*` tools form the `atli bitbucket <tool>` command group
  (`atli bitbucket list-repositories`, `atli bitbucket get-pull-request`, …).
  Under the default (upstream) install no such tools exist, so the entry is
  inert — no
  installed-provider gating anywhere.
- **`config.SERVICE_ENV_PREFIXES`** gains `"BITBUCKET_"`: a profile defining
  any `BITBUCKET_*` key first drops every ambient `BITBUCKET_*` variable
  (stale-credential isolation), then writes its own — the same rule Jira and
  Confluence get — and triggers the cross-service OAuth credential cleanup.
- **`config.describe_profiles`** renders a `bitbucket:` row for
  `BITBUCKET_URL`, alongside the jira/confluence rows.
- **`atli tools`** empty-state hint names `BITBUCKET_URL` next to
  `JIRA_URL` / `CONFLUENCE_URL`.
- **Root help** headline becomes "atli — a CLI for Jira, Confluence &
  Bitbucket, powered by mcp-atlassian." Extras guidance stays out of the help
  screen; it lives in README and AGENTS.md.

## `atli prime`

`detect_services` generalizes from the `(jira, confluence)` pair to a
per-service mapping, each service with its own credential matrix evaluated
against the post-profile environment:

| Service | Counts as configured when |
|---|---|
| jira, confluence | unchanged: URL + (`USERNAME`+`API_TOKEN` \| `PERSONAL_TOKEN` \| `CLIENT_CERT`) |
| bitbucket (Cloud) | `BITBUCKET_URL` + `BITBUCKET_USERNAME` + (`BITBUCKET_APP_PASSWORD` or `BITBUCKET_API_TOKEN`) |
| bitbucket (Server/DC) | `BITBUCKET_URL` + `BITBUCKET_PERSONAL_TOKEN` |

Bitbucket has no mTLS combination (the fork has none). The `_configured`
predicate takes the per-service credential combinations instead of the shared
fixed tuple.

Primer copy: the "Configured:" line lists `bitbucket` when detected; a
`atli bitbucket list-repositories` example line appears only when Bitbucket is
configured (token-cost discipline, like the per-service examples today);
`--export`'s canonical template shows all three example lines; the silence
rule — empty output when nothing is configured — is unchanged.

## Error handling

| Situation | Behavior |
|---|---|
| No provider importable (`mcp_atlassian` missing) | Expected bare-install path; stderr message naming both extras and the choose-one rule; exit 1 |
| Both providers present in one env (force-installed) | Undefined behavior from file clobbering; unsupported, not detected |
| Bitbucket vars set under `[atlassian]` | Not a failure: upstream ignores them; bitbucket simply never appears in `atli tools` |
| Fork env with only `BITBUCKET_*` configured | `atli tools` lists the bitbucket service group only |
| `BITBUCKET_URL` set without credentials | Service not "configured" for prime; the fork itself decides tool mounting |
| Everything else | Unchanged contracts (exit codes 0/1/2, EPIPE, profile errors) |

## Testing

- **`tests/test_discovery.py`** — `split_service` on `bitbucket_*` names
  (service group, kebab command name); upstream-only names unaffected.
- **`tests/test_config.py`** — `apply_profile` replaces ambient `BITBUCKET_*`
  wholesale and triggers the OAuth-key cleanup; `describe_profiles` renders
  the bitbucket row.
- **`tests/test_prime.py`** — detection matrix: Cloud app-password, Cloud
  API_TOKEN alias, Server PAT, URL-only negative; Configured line and example
  line with bitbucket on/off; `--export` shows all three examples; silence
  rule with bitbucket absent.
- **`tests/test_runner.py`** — the provider-missing path: the lazy import
  raising surfaces the new message (e.g. a poisoned `sys.modules` entry), not
  a traceback.
- **`tests/test_build.py` / `test_main.py`** — root-help headline, tools
  empty-state hint; existing stub-runner flows unchanged.

## CI

Three legs prove the provider contract and the provider matrix:

- **Upstream leg (existing)** — installs `.[atlassian]`: asserts upstream
  `mcp-atlassian` is present and the fork absent, runs the full unit suite.
- **Packaging leg (new)** — pip-side, throwaway venvs: a bare `pip install .`
  asserts NO provider ships (the inert-bare contract); `.[atlassian]` and
  `.[bitbucket]` each resolve to exactly their provider. This is the leg that
  caught the broken marker variant before release.
- **Fork leg (new)** — uv editable `.[bitbucket]` install with a provider-swap
  assertion, the full unit suite, and a no-credentials smoke: `atli tools`
  exits 0 with the "No services configured" hint (naming `BITBUCKET_URL`) on
  stdout, `atli prime` exits 0 with empty stdout. This leg proves the
  fastmcp-2.x path end-to-end.

The `test_examples.py` corpus cross-check runs on both test legs — the fork
ships the same jira/confluence tool surface — and stays pinned to the
upstream leg if its source-path assumptions break on the fork.

## Documentation

- **README** — Install section rewritten around the default (upstream) and
  the `[bitbucket]` extra, with the choose-one and
  fresh-environment-to-switch warnings; authentication matrix gains a
  Bitbucket column (Cloud: `URL`+`USERNAME`+`APP_PASSWORD` or `API_TOKEN`;
  Server: `URL`+`PERSONAL_TOKEN`; `bitbucket.org` ⇒ Cloud, anything else ⇒
  Server/DC); Profiles section documents the `BITBUCKET_*` prefix; the
  dependency-pinning paragraph describes the extras model.
- **AGENTS.md** — headline mentions Bitbucket; one bullet notes the
  `[bitbucket]` extra and that `bitbucket_*` tools appear only with the fork.
