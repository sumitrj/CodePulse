# Anywhere — the map from any working directory, and no file can sink it

## Intent
Wiring one repo pinned the MCP server to it with `--root`, so the map only worked when Claude Code's working directory was that repo. One user-level registration should serve every repo: each call names its repo, or falls back to where the session started. And mapping a real tree must survive whatever text sits in it — a 100 KB `data:` URI in an `<img src>` once stat'ed as a file (ENAMETOOLONG) and took the whole server down.

## Interface
```
codepulse install                    # once per machine
codepulse serve-mcp [--root <path>]  # what Claude Code runs; --root is only the default
```
- Every tool takes an optional `repo`: an absolute path to the repo or anything inside it.
- Repo resolution: git top level → a folder wired with `.codepulse/` → the named folder itself (only when named). The home directory and `/` are never mapped whole.
- Map location: `<repo>/.codepulse/pulse.db` if the repo is wired, else `$XDG_CACHE_HOME/codepulse/<name>-<hash>/pulse.db` (default `~/.cache`), so asking never writes into a repo.
- `install` runs `claude mcp add --scope user` (unpinned), copies the skill to `~/.claude/skills/codepulse/`, and merges the read-only allowlist into `~/.claude/settings.json`.

## Acceptance criteria
1. A call's `repo` selects that repo regardless of the server's working directory; a path inside the repo resolves to its top level.
2. Without `repo`: the `--root` pin, else the working directory.
3. A non-git, unwired folder is refused unless named; naming it maps it as itself, not a wired parent. Home and `/` are always refused.
4. An unwired repo's map lives in the user cache; a wired repo keeps `.codepulse/pulse.db`.
5. Every tool advertises `repo` as optional.
6. The stdio server launched outside any repo answers for a named one, and returns an error, not a crash, for an unnamed one.
7. `install` writes the skill and allowlist under the home directory and registers a server with no `--root`.
8. Path-style references with a URL scheme (`data:`, `javascript:`, `https:`, `mailto:` …), protocol-relative `//`, bare `#`/`?`, whitespace, or over 1024 chars are dropped before storage; `?query` and `#fragment` are stripped from real paths.
9. A file whose extraction raises is rolled back, skipped, reported in `ApplyReport.failed`, and retried only once it changes.

## Out of scope
- MCP `roots/list` negotiation (clients differ; `repo` + cwd covers Claude Code).
- Evicting old maps from the cache.
