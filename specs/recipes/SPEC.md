# Recipe system — languages as data

## Intent
A language is authored, not engineered. A *recipe* is a YAML file declaring what counts as an entity in one language, what counts as a reference, and how references resolve — via tree-sitter queries plus a small fixed vocabulary of resolution strategies the engine implements generically. The engine is language-blind: adding a language is adding a file. The first recipe (`recipes/python.yml`) reproduces the hand-written extractor's behavior (specs/relationships/SPEC.md); a toy second recipe (yaml keys) proves the engine never learned Python. Everything lands in SQLite with provenance: every row remembers which recipe version put it there. LLM calls, when they arrive in later slices, go through litellm (default: local Ollama) — no LLM is involved in extraction.

## Interface
```python
# codepulse/recipes.py
class RecipeError(Exception): ...          # invalid recipe; message names the offending field/query

@dataclass(frozen=True)
class Recipe:
    name: str                              # "python"
    version: str                           # "1"
    language: str                          # tree-sitter grammar name
    matches: tuple[str, ...]               # file globs, e.g. ("*.py",)
    # entity/reference declarations carried opaquely; engine interprets them

def load_recipe(path: Path) -> Recipe

@dataclass(frozen=True)
class CheckReport:
    ok: bool
    failures: tuple[str, ...]              # one line per unmet expectation

def check_recipe(recipe: Recipe, fixture_dir: Path) -> CheckReport
# fixture_dir holds source files + expected.yml (list of {src, kind, dst} expectations)

# codepulse/engine.py
@dataclass(frozen=True)
class Addr:                                # one address space for every language
    path: str
    name: str                              # "Cart.checkout", "<module>", "server.port"

@dataclass(frozen=True)
class External:                            # target outside the repo, name as written
    name: str

@dataclass(frozen=True)
class Entity:
    addr: Addr
    kind: str                              # "function", "class", "state", "key", ...
    line: int
    recipe: str
    recipe_version: str

@dataclass(frozen=True)
class Edge:
    src: Addr
    dst: Addr | External
    kind: str                              # "calls", "imports", "reads", "writes", "inherits", "contains", ...
    line: int
    recipe: str
    recipe_version: str

@dataclass(frozen=True)
class ApplyReport:
    extracted: tuple[str, ...]             # paths (re-)extracted in this pass

class Engine:
    def __init__(self, db_path: Path, recipes: Sequence[Recipe], root: Path): ...
    def apply(self) -> ApplyReport                          # full scan of root
    def update(self, changed: Iterable[Path]) -> ApplyReport # incremental: changed files only
    def entities(self, path: str | None = None) -> list[Entity]
    def outgoing(self, src: Addr, kind: str | None = None) -> list[Edge]
    def incoming(self, dst: Addr | External, kind: str | None = None) -> list[Edge]
```

## Inputs and outputs
- Input: recipe YAML files; a repo root of source files.
- Output: SQLite at `db_path` (tables: entities, edges, files, snapshots); read API above.
- Errors: `RecipeError` on invalid recipe (missing field, uncompilable query). Unreadable source files are skipped, never fatal.

## Acceptance criteria
1. Loading a valid recipe YAML returns a `Recipe` with its name, version, and matches.
2. Loading a recipe missing a required field raises `RecipeError` naming the field.
3. Loading a recipe with a malformed tree-sitter query raises `RecipeError` naming the query.
4. Python recipe: a same-file call yields a `calls` edge with correct src, dst, and line.
5. Python recipe: cross-file calls resolve to the target through all four import forms.
6. Python recipe: `self.method()` resolves to `Class.method`; instantiating a class yields a `calls` edge to the class.
7. Python recipe: an unresolvable call target becomes `External` with the dotted name as written.
8. Python recipe: import statements yield `imports` edges from module scope — resolved `Addr` for in-repo targets, `External` otherwise.
9. Python recipe: reading module-level state yields a `reads` edge; writing via `global` yields a `writes` edge.
10. Python recipe: inheritance yields an `inherits` edge to the base class, cross-file.
11. Python recipe: a module `contains` its top-level units; a class `contains` its methods.
12. Every entity and edge carries the recipe name and version that produced it.
13. `update(changed)` re-extracts only the changed files and replaces their stale edges; untouched files are not re-extracted.
14. Extraction is deterministic: two engines over the same tree give identical entities and edges.
15. A second-language recipe (yaml top-level keys) yields entities using the same engine, no engine change.
16. `check_recipe` passes on a conforming fixture repo; on a fixture whose `expected.yml` claims an edge that doesn't exist, it fails and names the unmet expectation.

## Out of scope
- Cross-language resolution rules (the `convention` strategy) — next slice, with the dockerfile/yaml recipes.
- Snapshots behavior, the six-verb read layer, MCP/panel surfaces, watcher daemon, identity fingerprints.
- Judge/ledger; any LLM involvement.

## Open questions / assumptions
- Resolution strategies start as a fixed engine vocabulary: `lexical` (import-binding table), `path`, `convention` (reserved). Recipes pick strategies; they don't define new ones.
- Qualified names keep the old convention: `<module>` for module scope, `Class.method` for methods.
- Grammars come from `tree-sitter-language-pack` (prebuilt wheels); `recipe.language` is the grammar key.
- Deleted-file handling in `update` is assumed (path absent on disk → rows removed); pinned in a later slice.
