# The mesh — five more recipes and cross-language edges

## Intent
The repo becomes one mesh: YAML, Dockerfile, TypeScript, Terraform, and HTML get recipes, and references cross language boundaries. Two new resolution strategies join `lexical`, both engine-generic and recipe-declared: `path` (the target text is a relative file path — a Dockerfile COPY, an HTML `src`, a TS `./` import) and `name` (the target text names a config entity by convention — a Python `os.environ["DB_URL"]` finds the Terraform variable `DB_URL`). Every recipe ships with a fixture repo under `fixtures/<name>/` and must pass `check_recipe` — the factory's validate gate, now enforced by the test suite.

## Interface
```python
# recipe format additions (codepulse/recipes.py)
BindingRule.style: "module" | "path"      # how the module text maps to a file
ReferenceRule.resolve: "lexical" | "path" | "name"
# engine (codepulse/engine.py): implements the two new strategies generically;
# entity names join multiple @name captures with "." (terraform: resource.type.label)
```
New recipes: `recipes/{yaml,dockerfile,typescript,terraform,html}.yml`. No new Python surface.

## Inputs and outputs
- Input: same repo root; files matched per recipe globs.
- Output: same 4 tables; cross-language rows look like any other row.
- Errors: unchanged (`RecipeError` at load; unreadable files skipped).

## Acceptance criteria
1. Every shipped recipe passes `check_recipe` against its fixture repo (parametrized over `recipes/*.yml`).
2. YAML: nested keys become entities with dotted names (`server.port`).
3. Dockerfile: `COPY app.py …` yields a `copies` edge to `app.py`'s module entity.
4. TypeScript: a cross-file call through `import { helper } from "./util"` resolves to `util.ts :: helper`.
5. Terraform: `variable "DB_URL"` becomes an entity addressable by name.
6. HTML: `<script src="./main.ts">` yields a `loads` edge to `web/main.ts`'s module entity.
7. Python: reading `os.environ["DB_URL"]` yields a `reads` edge to the Terraform variable — the cross-language convention edge.
8. Blast radius crosses languages: the radius of `util.ts :: helper` includes its TS caller; `who_touches("DB_URL")` shows the Python reader.
9. A `path` reference that points at nothing is dropped, never invented.

## Out of scope
- Directory-level COPY targets (edge to a folder of entities) — noted for the next slice.
- JS (`*.js`), TSX/JSX, HCL beyond variable/resource/output blocks, `os.getenv(...)` call form.
- Semantic identity for non-Python entities (still path-based).

## Open questions / assumptions
- The `name` strategy matches by exact entity name or dotted tail, filtered by `only` kinds; ambiguity resolves to the sorted-first match (deterministic, convention-driven — documented recipe behavior, not a guess).
- Any string subscript in Python may probe config names; unresolved ones drop, so noise only appears when a config entity genuinely shares the name — that is the convention working as declared.
