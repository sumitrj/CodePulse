# CodePulse

One live map of your repo — every function, class, config key, Terraform
variable, and Dockerfile edge, across languages — read by you in an editor
panel and by your agents over MCP. Same map, same answers, fresh within
seconds of a save.

Languages are **recipes**, not code: a YAML file of tree-sitter queries adds a
language. Six ship today: Python, TypeScript, YAML, Dockerfile, Terraform, HTML.

## Install

```sh
git clone <this repo>
uv tool install ./CodePulse            # once: puts `codepulse` on your PATH
```

## Use (one word per repo)

```sh
cd /path/to/your/repo
codepulse .                            # wire + build the map + start the panel
```

That one command registers the MCP server in the repo's `.mcp.json`, installs
the Claude Code skill, builds the map with live progress (`.codepulse/`, kept
out of git automatically), and serves the panel. Re-running it is always safe.
Then open Claude Code in the repo and approve the `codepulse` server when
prompted. `codepulse . --no-panel` wires without the panel; a
`.codepulse/ignore` file (one directory name per line) scopes huge repos.

**Prove it to yourself:** [demo/benchmark.md](demo/benchmark.md) — same five
questions with and without the map; you score time, tokens, and accuracy from
your own screen.

## What your agent gets (MCP + skill)

Seven tools, plain-sentence answers:

| Tool | Question |
|---|---|
| `pulse_map` | Repo silhouette: size, load-bearing entities |
| `pulse_what` | What is this? Card: kind, line, calls, dependents |
| `pulse_who` | Who touches it? All incoming edges |
| `pulse_radius` | What breaks if I change it? Transitive, by hops |
| `pulse_changed` | What did my diff touch, and who's at risk? |
| `pulse_locate` | Where does X live? |
| `pulse_handlers` | Estimated entry points (uncalled roots with reach) |

The installed skill teaches Claude to ask the map before grepping — an
architecture overview is two tool calls, not forty file reads.

## What you get (the panel)

```sh
.venv/bin/python -m codepulse.panel --root /path/to/your/repo --port 7319
```

Or install [extension/codepulse-0.1.0.vsix](extension/) in VS Code / Cursor /
Antigravity (activity bar → pulse icon; set `codepulse.python` to this repo's
`.venv/bin/python`). The panel is the same seven questions rendered visually:
the whole map layered by dependency depth, blast radius as hop columns,
click-to-focus, a handlers board. Settings are four rows — theme, entity
colors, font, size — instant, done.

## Cross-language edges — the point

`COPY app.py` in a Dockerfile points at the Python module. `<script src>`
points at the TypeScript file. `os.environ["DB_URL"]` in Python reaches
`variable "DB_URL"` in Terraform. Blast radius walks all of it — the edges no
single-language tool can see.

The map is honest: it only claims what it can prove. Dynamic dispatch and
string-built references don't get invented edges; the skill teaches agents to
say so instead of guessing.

## Add a language

Write `recipes/<lang>.yml` — tree-sitter queries for entities, references,
and bindings, plus a resolution strategy (`lexical`, `path`, or `name`).
Put a fixture repo in `fixtures/<lang>/` with an `expected.yml`; the test
suite refuses recipes that can't prove their own edges. No engine changes:
the engine is language-blind by construction (~500 lines, SQLite, stdlib).

## Repo layout

```
codepulse/engine.py     language-blind extraction + resolution -> SQLite
codepulse/recipes.py    recipe loading, validation, fixture checks
codepulse/six.py        the six verbs over the graph
codepulse/boards.py     saved views: handlers, hop diagrams, file graph
codepulse/panel.py      HTTP server for the webview panel
codepulse/mcp_server.py stdio MCP server (the agent surface)
recipes/*.yml           the six languages, as data
skill/SKILL.md          the Claude Code skill setup.sh installs
extension/              VSIX shell for VS Code / Cursor / Antigravity
specs/ + tests/         spec-first: every feature has a SPEC.md and its tests
```

Legacy V1 pipeline (`store.py`, `verbs.py`, `cli.py`, hooks, judge, workbench)
still works and awaits migration; the modules above are the current stack.

## Documents

- [PRD](PRD.md) — problem, thesis, the six-verb ceiling, phasing
- [specs/](specs/) — recipes, six, mesh, boards, panel, setup: one page each
- [demo/benchmark.md](demo/benchmark.md) — the A/B value proof
