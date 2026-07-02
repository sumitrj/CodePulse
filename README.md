# CodePulse

A system of record for code as capabilities, not files. Every unit of logic gets an
identity, a promise, a version, and a map of dependents — so agents and humans stop
re-deriving structure and nothing changes silently.

## What exists (V1 core)

| Piece | Module | Status |
|---|---|---|
| Unit extraction (Python) | `codepulse.identity.extract` | 15/15 tests green |
| Identity fingerprint + 3-tier matcher | `codepulse.identity` | rename, move, refactor, split all survive |
| Relationship pipeline (6 edge kinds, cross-file resolution) | `codepulse.relationships` | 15/15 tests green |
| The Judge (refactor/patch/minor/major, Claude-backed) | `codepulse.judge` | mechanical + model + paranoid heuristic |
| Graph of record (persisted, identity-preserving refresh) | `codepulse.store` | `.codepulse/store.json` per repo |
| The six verbs (shared by CLI, MCP, hooks) | `codepulse.verbs` | what / who / radius / changed / locate / map |
| CLI | `codepulse.cli` via `pulse.py` | `index`, all verbs, `install-hooks`, `serve-mcp` |
| MCP server (stdio JSON-RPC, stdlib only) | `codepulse.mcp_server` | 6 tools: `pulse_what` ... `pulse_map` |
| Claude Code hooks (push surface) | `cli.cmd_hook_pre/post` | pre-edit context injection + post-edit MAJOR verification |
| Companion app (Material 3) | `codepulse.app` + `webapp/` | Silhouette / Explore / Ripple / Changes / Workbench |
| The Workbench (issue -> workpiece) | `codepulse.workbench` | brief, gate, isolated execute, radius-proved, versioned artifacts, GitHub via `gh` |

## The Workbench

```sh
python3 pulse.py brief 123                    # issue -> map-scoped brief + gate decision
python3 pulse.py work 123                     # hands-free: headless Claude works it, radius-tested, PR-ready
python3 pulse.py work 123 --live              # push branch, open PR, comment on the issue
```

Every run produces a versioned **workpiece** (`.codepulse/workpieces/<n>/vN.json`): Brief,
Change, Proof, Verdict, Trace. The gate escalates to a human when blast radius, fan-in, or
confidence exceed policy. The promise-test invariant auto-rejects any MAJOR verdict that
ships without a test change: when a promise moves, a test must move.

## Use it on a repo

```sh
python3 pulse.py --root /path/to/repo index          # build the map
python3 pulse.py --root /path/to/repo what get_env   # verb 1
python3 pulse.py --root /path/to/repo radius get_env # verb 3
python3 pulse.py --root /path/to/repo install-hooks  # Claude Code: map arrives before every edit
# MCP (project-scoped): add codepulse to the repo's .mcp.json pointing at `pulse.py serve-mcp`
```

## Try it

```sh
uv run --with pytest python -m pytest tests/ -q      # 30 tests
uv run --with anthropic --with pydantic python demo/demo.py
```

The demo diffs two snapshots of a toy shop codebase and shows: the six-verb query
surface, identity surviving a rename+move and a split, and the Judge catching a
**silent promise break** — `process()` kept its signature but quietly stopped
auditing orders. Set `ANTHROPIC_API_KEY` to have Claude (`claude-opus-4-8`) write
the behavioral summaries; without it a paranoid heuristic escalates anything unsure.

## Documents

- [PRD](PRD.md) — problem, thesis, six verbs, phasing, metrics
- [Identity spec](specs/identity/SPEC.md) + [architecture](specs/identity/ARCHITECTURE.md)
- [Relationships spec](specs/relationships/SPEC.md)

## Next (per PRD phasing)

MCP server exposing the six verbs, Claude Code pre-edit/post-edit hooks, incremental
watcher daemon, version ledger (V2), fork lineage and drift (V3).
