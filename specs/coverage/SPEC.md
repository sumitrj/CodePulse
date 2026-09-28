# Relationship coverage — no relation left behind

## Intent
An audit (2026-07-21) ran the engine over samples exercising every syntactic
relationship Python and TypeScript can express and read the edges table back.
The core kinds load well; a specific list does not: Python loses star imports
(which silently degrade other resolutions), decorators, attribute-style state
access, annotations, exception flow, class attributes, and pattern
assignments; TypeScript loses `new`-instantiation, `implements`, interfaces /
type aliases / enums, namespace and side-effect imports, re-exports, top-level
state (it tracks no state at all), `this.` resolution, and `.tsx` files — and
mis-extracts abstract-class methods as orphans with unqualified names. After
this change every one of those forms lands in the graph, resolution survives
them, and blast radius counts the new edge kinds.

## Interface
No new public API. Changes live in data and existing surfaces:
```python
# codepulse/recipes.py — three new rule fields, all optional, all data
BindingRule.star: bool = False        # binds every exported name of the module
ReferenceRule.attach: str = "enclosed"  # "following": src is the entity the
                                        # captured node sits directly above (decorators)
Recipe.self_name: str = ""            # "self" (py) / "this" (ts): instance-call prefix

# codepulse/engine.py — same signatures, three behaviors
# 1. one name node = one entity: overlapping entity rules dedupe by the @name
#    node's position, first rule wins (enables state-vs-arrow overlap; fixes
#    the abstract-class orphan-method bug)
# 2. star bindings resolve bare names through the starred module, including
#    the re-export chase
# 3. `<self_name>.x` resolves within the enclosing class, per recipe

# codepulse/six.py — impact kinds grow
_IMPACT_KINDS = ("calls", "reads", "writes", "inherits", "implements", "types")

# recipes/python.yml v7, recipes/typescript.yml v3, recipes/tsx.yml v1 (new)
```

## Inputs and outputs
- Input: the same repos; no config changes. Recipe version bumps trigger
  re-extraction on the next refresh.
- Output: additional entities (`state`, `interface`, `type`, `enum`, class
  attributes, enum members) and additional edges (`calls` from decorators and
  `new`, `reads`/`writes` through attributes, `types`, `implements`).
- Errors: unchanged — unresolvable new references drop or go external
  exactly like existing ones.

## Acceptance criteria
Python:
1. `from m import *` yields an imports edge to the module, and a bare call to
   a member of `m` resolves to its definition (incl. through re-exports).
2. `@deco` and `@mod.deco(arg)` yield calls edges from the decorated entity
   to the decorator.
3. `lib.STATE` reads, `lib.STATE = x` writes, and `lib.STATE += x` augmented
   writes resolve to the state entity from attribute position.
4. Parameter, return, and variable annotations yield `types` edges to
   in-repo classes (including inside `list[Base]`-style generics, one level).
5. A class passed as a call argument (`isinstance(x, Base)`, `register(Base)`,
   tuple forms included) yields a reads edge to the class.
6. `raise Err` (bare name) and `except Err:` / `except (A, B) as e:` yield
   reads edges to the exception class.
7. Class-level assignments become state entities (`K.attr`), and
   `self.attr` reads and writes resolve to them.
8. `a, b = ...` and `x = y = ...` at module level yield one state entity per
   bound name — no dotted ghosts.
TypeScript:
9. `new Foo()` yields a calls edge to the class.
10. `implements Shape` yields an implements edge; interfaces, type aliases,
    and enums (with their members) are entities.
11. Abstract classes and generator functions are entities, and an abstract
    class's methods carry `Class.method` qualnames.
12. Top-level `const`/`let`/`var` (exported or not) are state entities;
    bare-identifier and `ns.member` reads of state resolve.
13. `import * as ns` binds so `ns.member()` resolves to the definition;
    `import "./m"` yields an imports edge to the module.
14. `export { x } from "./m"` re-exports chase to the definition from an
    importer; `export * from "./m"` resolves like a star import.
15. `this.method()` resolves to `Class.method`.
16. Type annotations (`: Shape`) yield `types` edges to in-repo
    interfaces/classes/aliases.
17. `.tsx` files load through a tsx recipe, and a JSX element `<Comp/>`
    yields a calls edge to the component.
Cross-cutting:
18. `six.compute_radius` counts dependents over writes, implements, and
    types edges.
19. Overlapping entity rules mint exactly one entity per name node — no
    duplicate or nested-qualname ghosts (`VALUE.VALUE`, `f.f`).

## Out of scope
- Runtime-only relations: dynamic dispatch, string-built imports, monkey-
  patching, `getattr`, instance attributes created in `__init__`.
- `nonlocal` (never module state), deeply nested generic annotations
  (`Optional[list[dict[str, Base]]]` beyond one level), TS `property_signature`
  members, JS (`.js`/`.jsx`) files, decorator *factories'* internals.
- Panel rendering of new kinds (it already renders arbitrary edge kinds).

## Open questions / assumptions
- Decorator edges are `calls` (a decorator is literally called at definition
  time), not a new kind. Parameterized decorators may additionally leave the
  engine's ordinary scope-attributed call edge; both claims are true.
- `types` and `implements` join impact kinds: changing a type contract puts
  its users at risk. `writes` joins too — a writer is coupled to its state.
- Enum members are `state`; interface methods are `method` entities.
- tsx.yml mirrors typescript.yml plus JSX element references; it is a
  separate recipe because tree-sitter treats tsx as a separate grammar.
