# atli

`atli` is a CLI over mcp-atlassian — the MCP server stays disconnected from the harness; invoke it via Bash.
- Discover the surface with `atli tools` (only configured services appear) or `atli tools --search TEXT` to shortlist; `atli <service> <tool> --help` shows typed params, defaults, and example invocations.
- `atli jira get-issue --issue-key PROJ-1`
- `atli confluence search --query "deploy"`
- Long values read files: `--content @page.md` (`-` = stdin when piped; `@@x` = literal `@x`).
- Multi-instance: `atli --profile NAME <command>` (flag goes before the subcommand).
- `atli prime [--hook-json]`: compact primer of this setup (configured services, profile, usage) for SessionStart hooks; `atli prime --install` writes the hook (Claude Code); override via `.atli/PRIME.md`.
- Startup takes ~1 s warm, a few seconds cold (mcp-atlassian import dominates); the tool's markdown/JSON is printed verbatim to stdout.
- Repeatable list flags repeat: `--read-users alice --read-users bob` on `confluence set-page-restrictions` (a comma inside one flag makes a single element).
- Exit codes: 0 success, 1 tool/server failure, 2 usage or config error.
