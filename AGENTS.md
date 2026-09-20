# atli

`atli` is a CLI over mcp-atlassian (Jira, Confluence, and Bitbucket via the `[bitbucket]` extra) — the MCP server stays disconnected from the harness; invoke it via Bash.
- Discover the surface with `atli tools` (only configured services appear) or `atli tools --search TEXT` to shortlist; `atli <service> <tool> --help` shows typed params, defaults, and example invocations.
- `atli jira get-issue --issue-key PROJ-1`
- `atli confluence search --query "deploy"`
- `atli bitbucket list-repositories` (`bitbucket_*` tools appear only when the fork provider is installed: `pip install "mcp-atlassian-cli[bitbucket]"`; the two providers cannot coexist)
- Long values read files: `--content @page.md` (`-` = stdin when piped; `@@x` = literal `@x`).
- Multi-instance: `atli --profile NAME <command>` (flag goes before the subcommand).
- `atli prime [--hook-json]`: compact primer of this setup (configured services, profile, usage) for SessionStart hooks; `atli prime --install` writes the hook (claude, codex, qwen, gigacode); override via `.atli/PRIME.md`.
- `atli init <service>` (jira/confluence/bitbucket): onboarding wizard — arrow-key menus on a terminal (numbered text prompts when stdin is piped), collects URL + credentials (re-runs prefill from the existing profile), verifies with a live read-only call BEFORE the summary, writes a merged comment-preserving `chmod 600` profile (`./.atli.toml` or `~/.config/atli/config.toml`), and installs the SessionStart hook for the chosen harness.
- Startup takes ~1 s warm, a few seconds cold (mcp-atlassian import dominates); the tool's markdown/JSON is printed verbatim to stdout.
- Repeatable list flags repeat: `--read-users alice --read-users bob` on `confluence set-page-restrictions` (a comma inside one flag makes a single element).
- Exit codes: 0 success, 1 tool/server failure, 2 usage or config error.
