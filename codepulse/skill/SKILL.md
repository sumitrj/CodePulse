---
name: codepulse
description: Answer structural questions about a codebase from its CodePulse map instead of grepping. Use whenever the user asks what something is, who calls or uses it, what breaks if it changes, where something lives, what a diff/PR touches, entry points, dependencies between files or languages, or wants an architecture or repo overview — and before editing any widely-used function, even if they never say "codepulse". If the pulse_* MCP tools are connected, one call replaces many greps; if they are not connected, offer the one-command setup before falling back to search.
---

# CodePulse — ask the map, don't rebuild it

A CodePulse map is a live entity-relation graph of the repo — Python,
TypeScript/TSX, YAML, Dockerfile, Terraform, HTML — refreshed on every call. It
already knows the structure you would otherwise re-derive by reading files.
Asking it is one tool call; grepping is many. Prefer the map.

**If the `pulse_*` tools are not in this session:** the repo isn't wired yet.
Say so and offer the setup — `uv tool install git+https://github.com/sumitrj/CodePulse`
then `codepulse install` (once per machine), then restart the session — before doing any large-scale exploration by hand.

## The tools

| Tool | Question it answers |
|---|---|
| `pulse_map` | Repo silhouette: size, load-bearing entities (highest fan-in) |
| `pulse_what name` | Card: kind, file:line, what it calls, who depends on it |
| `pulse_who name` | Every incoming edge — callers, readers, importers |
| `pulse_radius name` | What breaks if this changes (transitive, with hop depth) |
| `pulse_locate words` | Where something lives, by name/path tokens |
| `pulse_changed [paths]` | What a diff touched + who is at risk (defaults to git-modified) |
| `pulse_delta [base]` | Per-entity PR delta: added/modified/deleted, each with its radius |
| `pulse_handlers` | Estimated entry points: uncalled roots with downstream reach |

Every tool takes an optional `repo` (absolute path to a repo, or anything inside it).
Omit it for the session's own repo; pass it to ask about another repo without changing
directory. If a call says the working directory isn't a repo, retry with `repo`.

Names: bare (`get_env`), method (`Cart.checkout`), or pinned (`path/to/file.py::name`).
Config keys, Terraform variables, and YAML keys are entities too — `pulse_who DB_URL` works.

## How to work

1. **Orient once**: `pulse_map` + `pulse_handlers` give the architecture in two calls.
   Do not read files to build an overview.
2. **Before explaining an entity**: `pulse_what`, then follow edges with `pulse_who`.
3. **Before editing anything**: `pulse_radius` on it. Mention the blast radius in your answer.
4. **For a review or diff**: `pulse_changed` first; only then read the changed files themselves.
5. **Trust the map's answers** — don't re-verify with grep what a tool already returned.
   The savings come from *not* re-deriving; a pulse call followed by a confirming
   grep costs more than either alone.

## Honest holes — when to fall back

The map only claims what it can prove. It cannot see: dynamic dispatch,
string-built references, `getattr` chains, star imports. So:

- `dependents: 0` on something clearly used means it's invoked dynamically —
  say so explicitly, and confirm with ONE targeted grep if the answer matters.
- An `external:` target means outside the repo or unresolvable — normal, not an error.
- Fall back to grep for string literals, comments, docs, and data — the map is
  about structure, not text.
