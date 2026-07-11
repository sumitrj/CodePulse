# The six verbs over the recipe graph

## Intent
The six questions answer from the new engine's SQLite graph instead of the old JSON store, so agents (via MCP) read recipe-built, multi-language data. Answers stay plain sentences. The verbs only claim what the graph knows — no signatures or docstrings yet, but always: what a thing is, who touches it, what breaks, what a change touched, where something lives, and the system silhouette. The engine also gains the freshness primitive: `refresh()` re-extracts only hash-changed files and forgets deleted ones, and scanning skips vendored/hidden directories.

## Interface
```python
# codepulse/six.py — every verb takes the Engine and returns a plain string
def find(engine, name: str) -> list[Addr]          # exact name, tail, substring; "path::name" pins a file
def what_is(engine, name: str) -> str              # card: addr, kind, recipe@version, calls, dependents
def who_touches(engine, name: str) -> str          # incoming edges, one line each
def radius(engine, name: str) -> str               # transitive dependents by hops (calls/reads/inherits)
def what_changed(engine, paths: Iterable[Path]) -> str  # re-extract paths; touched entities + who's at risk
def locate(engine, query: str) -> str              # token score over names and paths
def system_map(engine) -> str                      # counts, load-bearing fan-in

# codepulse/engine.py additions
Engine.refresh() -> ApplyReport                    # hash-diff scan: changed re-extracted, deleted forgotten
# scanning (apply/refresh) skips EXCLUDE_DIRS (.git, .venv, node_modules, .codepulse, ...)

# codepulse/recipes.py addition
def builtin_recipes() -> list[Recipe]              # every recipes/*.yml shipped with codepulse

# codepulse/mcp_server.py — same six tools, answering from the Engine
```

## Inputs and outputs
- Input: an `Engine` over an applied repo; names/queries as spoken strings; changed paths.
- Output: plain-sentence strings; misses read "No entity matching '<name>'."
- Errors: none raised to callers; MCP wraps exceptions as tool errors (unchanged).

## Acceptance criteria
1. `what_is` shows the card: `path :: name`, `[kind]`, `recipe@version`, and its direct dependents.
2. `who_touches` lists incoming edges as `src --kind--> target` lines.
3. `radius` walks transitively: a caller at 1 hop, its caller at 2 hops.
4. `what_changed(paths)` re-extracts the paths and reports touched entities plus dependents at risk.
5. `locate` ranks an entity top by tokens from its name and path.
6. `system_map` reports file/entity/edge counts and load-bearing (highest fan-in) entities.
7. A miss answers with a plain sentence, not an empty string or an error.
8. `Engine.refresh()` re-extracts only files whose content hash changed.
9. Files under excluded directories (e.g. `.venv`) are never scanned.
10. The MCP dispatcher answers `pulse_what` from the engine graph.

## Out of scope
- Semantic locate (embeddings), signatures/docstrings on cards, the Judge, drift (verb 6), panel rendering.
- Old store/CLI/hooks migration — they stay on `store.py` until the panel slice.

## Open questions / assumptions
- Impact kinds for radius stay `calls`, `reads`, `inherits`; module-scope srcs are skipped as noise.
- `pulse_changed` without explicit paths falls back to git-modified files; untested here (thin shell).
- `builtin_recipes()` reads the repo's `recipes/` dir; packaging recipes into wheels is deferred.
