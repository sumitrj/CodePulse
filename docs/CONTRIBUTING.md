# Contributing

For developers adding a language to CodePulse or changing its code. If you
only want to use it, the [README](../README.md) is all you need.

## Set up

```sh
git clone https://github.com/sumitrj/CodePulse && cd CodePulse
uv sync --extra dev
uv run pytest -q          # about 20 seconds, no network
```

To use your working copy as the `codepulse` command, install it editable:
`uv tool install --force --editable .`

## Add a language

A language is one YAML file of [tree-sitter](https://tree-sitter.github.io/)
queries. The engine never learns a language; it runs whatever the recipe says.
Here is the whole Dockerfile recipe:

```yaml
name: dockerfile
version: "1"
language: dockerfile                  # the tree-sitter grammar
matches: ["Dockerfile", "Dockerfile.*", "*.dockerfile"]

references:
  - kind: copies                      # the edge's name, as the map will say it
    query: "(copy_instruction (path) @target)"
    resolve: path                     # how @target finds what it points at
    unresolved: drop                  # no match: no edge (never guess)
```

1. **Write** `codepulse/recipes/<lang>.yml`. A recipe declares `entities`
   (the things with names), `references` (edges), and optionally `bindings`
   (imports) and `docs`. [`python.yml`](../codepulse/recipes/python.yml) uses
   all of them.
2. **Pick how each reference resolves**:
   - `lexical`: through the file's own names and imports (calls, reads).
   - `path`: as a file path relative to the file (`COPY`, `<script src>`).
   - `name`: by name anywhere on the map (`os.environ["DB_URL"]` reaching a
     Terraform variable).
3. **Prove it.** Add a small repo at `fixtures/<lang>/` with an
   `expected.yml` listing the edges it must produce:

   ```yaml
   - src: Dockerfile:<module>
     kind: copies
     dst: app.py:<module>
   ```

   The test suite checks every recipe against its fixture, and it fails a
   recipe that has no fixture.
4. **Bump `version`** whenever you change a recipe. Maps built with the old
   version are rebuilt on the next question.

## How the code is laid out

```
codepulse/
  engine.py        reads files with recipes, resolves edges, stores the map (SQLite)
  recipes.py       loads and checks recipes
  recipes/*.yml    the languages
  six.py           the questions, answered as text
  delta.py         what a diff touched, entity by entity
  boards.py        the panel's views: handlers, radius columns, file graph
  mcp_server.py    the agent's tools (stdio MCP); picks the repo per call
  panel.py         the panel's local web server; webview/panel.html is the page
  main.py          the `codepulse` command
  skill/SKILL.md   the skill `codepulse install` gives Claude
examples/shop/     the README's example, asserted by tests/test_example.py
fixtures/<lang>/   one proof repo per recipe
specs/<feature>/   one SPEC.md per feature, with its acceptance criteria
bench/             the SWE-bench localization benchmark
extension/         the VS Code / Cursor sidebar
```

The first version (unit identity, a change judge, a workbench) was removed
once the map replaced it; it's in git history before the commit that says so.

## How changes are made

Spec first, then red tests, then code.

1. Write `specs/<feature>/SPEC.md`: intent, interface, numbered acceptance
   criteria, what's out of scope.
2. Write tests that map one-to-one to those criteria. Run them, and save the
   failing output as `specs/<feature>/RED_BASELINE.txt`.
3. Implement until they pass. Each test file ends with a traceability list
   from criterion to test.

If you change what the README shows about `examples/shop`, update
`tests/test_example.py` in the same change. That test keeps the docs honest.
