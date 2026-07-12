---
name: codepulse
description: Use for any structural question about this repo — what something is, who calls or uses it, what breaks if it changes, where something lives, what a diff touches, entry points, or an architecture overview — and before editing any widely-used function. Ask the CodePulse map via the pulse_* MCP tools INSTEAD of grep/glob/reading files; one tool call replaces many searches.
---

# CodePulse — ask the map, don't rebuild it

This repo has a live entity-relation graph covering Python, TypeScript, YAML,
Dockerfile, Terraform, and HTML, refreshed on every call. It already knows the
structure you would otherwise re-derive by grepping. Asking it is one tool
call; grepping is many. Prefer the map.

## The tools

| Tool | Question it answers |
|---|---|
| `pulse_map` | Repo silhouette: size, load-bearing entities (highest fan-in) |
| `pulse_what name` | Card: kind, file:line, what it calls, who depends on it |
| `pulse_who name` | Every incoming edge — callers, readers, importers |
| `pulse_radius name` | What breaks if this changes (transitive, with hop depth) |
| `pulse_locate words` | Where something lives, by name/path tokens |
| `pulse_changed [paths]` | What a diff touched + who is at risk (defaults to git-modified) |
| `pulse_handlers` | Estimated entry points: uncalled roots with downstream reach |

Names: bare (`get_env`), method (`Cart.checkout`), or pinned (`path/to/file.py::name`).
Config keys, Terraform variables, and YAML keys are entities too — `pulse_who DB_URL` works.

## How to work

1. **Orient once**: `pulse_map` + `pulse_handlers` give the architecture in two calls.
   Do not read files to build an overview.
2. **Before explaining an entity**: `pulse_what`, then follow edges with `pulse_who`.
3. **Before editing anything**: `pulse_radius` on it. Mention the blast radius in your answer.
4. **For a review or diff**: `pulse_changed` first; only then read the changed files themselves.
5. **Trust the map's answers** — don't re-verify with grep what a tool already returned.

## Honest holes — when to fall back

The map only claims what it can prove. It cannot see: dynamic dispatch,
string-built references, `getattr` chains, star imports. So:

- `dependents: 0` on something clearly used means it's invoked dynamically —
  say so, and confirm with ONE targeted grep if it matters.
- An `external:` target means outside the repo or unresolvable — normal, not an error.
- Fall back to grep for string literals, comments, docs, and data — the map is
  about structure, not text.
