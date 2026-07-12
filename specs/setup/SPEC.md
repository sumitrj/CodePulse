# Setup — one command from clone to working map

## Intent
A tester goes from "cloned CodePulse" to "Claude Code answers from the map" with one command. `setup.sh <repo>` wires a target repo: registers the MCP server project-scoped, installs the Claude skill that teaches agents to ask the map before grepping, warms the index so the first question is instant, and keeps `.codepulse/` out of git without touching tracked files. Idempotent — running it twice changes nothing. This is the front door of the A/B value proof (demo/benchmark.md): time, tokens, accuracy, decided by the tester.

## Interface
```
./setup.sh /path/to/target-repo
```
Effects on the target repo:
- `.mcp.json` — merged (never clobbered): a `codepulse` server entry using CodePulse's venv python, `--root` pinned to the repo.
- `.claude/skills/codepulse/SKILL.md` — copied from `skill/SKILL.md`.
- `.codepulse/pulse.db` — the map, warmed by a full refresh.
- `.git/info/exclude` — gains `.codepulse/` (local-only ignore; tracked files untouched; skipped if not a git repo).
Prints each effect and ends with the one manual step: approve the server in Claude Code.

## Acceptance criteria
1. After setup, the target repo's `.mcp.json` contains a `codepulse` server whose command and `--root` are absolute and correct.
2. An existing `.mcp.json` with other servers survives the merge untouched.
3. The skill lands at `.claude/skills/codepulse/SKILL.md` with a `description` frontmatter line.
4. The map is warm: `.codepulse/pulse.db` exists and holds the repo's entities.
5. `.git/info/exclude` contains `.codepulse/`, added once even across repeated runs.
6. Running setup twice is safe: same `.mcp.json`, no duplicate exclude lines.

## Out of scope
- Publishing codepulse to PyPI / `uv tool install` distribution (post-cohort).
- Windows; hooks; extension install.

## Open questions / assumptions
- The CodePulse repo's own `.venv` is the interpreter — created by the script via `uv sync` if missing.
- `.mcp.json` and `.claude/` are left for the tester to commit or not; only `.codepulse/` is auto-ignored.
