# Relationship extraction pipeline

## Intent
Given a snapshot of source files, CodePulse produces the typed edges between units — who calls whom, who imports what, who reads or writes shared state, who inherits from whom, who contains whom — with cross-file references *resolved*, not just recorded as strings. This is the substrate for "who touches it" and "what breaks if I change it": `incoming(ref)` on this graph is the blast-radius primitive. Python source first; the edge model itself is language-agnostic.

## Interface
```python
# codepulse/relationships.py
@dataclass(frozen=True)
class UnitRef:
    path: str
    qualname: str            # "Cart.checkout"; "<module>" denotes module scope

@dataclass(frozen=True)
class StateRef:              # module-level variable (shared state)
    path: str
    name: str

@dataclass(frozen=True)
class ExternalRef:           # target outside the snapshot (stdlib, third-party)
    name: str                # dotted name as written, e.g. "json.loads"

Target = UnitRef | StateRef | ExternalRef
EdgeKind = Literal["calls", "imports", "reads", "writes", "inherits", "contains"]

@dataclass(frozen=True)
class Edge:
    src: UnitRef
    dst: Target
    kind: EdgeKind
    line: int                # evidence line in src's file

@dataclass(frozen=True)
class EdgeGraph:
    edges: frozenset[Edge]
    def outgoing(self, ref: UnitRef, kind: EdgeKind | None = None) -> frozenset[Edge]: ...
    def incoming(self, ref: Target, kind: EdgeKind | None = None) -> frozenset[Edge]: ...

def extract_edges(files: Mapping[str, str]) -> EdgeGraph
```

## Inputs and outputs
- Input: `{path: source}` for one snapshot. Module name derives from path (`billing.py` → `billing`, `a/b.py` → `a.b`).
- Output: `EdgeGraph`. Import bindings are resolved through all four forms (`import m`, `import m as x`, `from m import n`, `from m import n as x`); a resolved target inside the snapshot becomes a `UnitRef`/`StateRef`, anything else an `ExternalRef` preserving the dotted name as written.
- Errors: `SyntaxError` propagates, tagged with the offending path.

## Acceptance criteria
1. A same-file call yields a `calls` edge with correct src, dst, and line.
2. Cross-file calls resolve to the target `UnitRef` through all four import forms.
3. `self.method()` resolves to `Class.method`; instantiating a class yields a `calls` edge to the class unit.
4. An unresolvable call target is kept as `ExternalRef` with the dotted name as written.
5. Import statements yield `imports` edges from module scope (`<module>`) — resolved `UnitRef` for snapshot targets, `ExternalRef` otherwise.
6. Reading module-level state yields `reads` edges to its `StateRef`, both same-file and through a from-import.
7. Writing module-level state via `global` yields a `writes` edge to its `StateRef`.
8. Inheritance yields an `inherits` edge to the base class unit, same-file and cross-file.
9. Containment: module scope `contains` its top-level units; a class `contains` its methods.
10. `incoming`/`outgoing` return exactly the edges touching a ref and are filterable by kind (unfiltered incoming on a popular unit shows its callers, its importers, and its containing module — since AC9 makes containment an edge like any other).
11. Extraction is deterministic: two runs over the same snapshot produce equal graphs.

## Out of scope
- Type inference: attribute calls on instances other than `self`, duck-typed dispatch.
- Star imports, relative imports, conditional/dynamic imports, decorator effects.
- Instance-attribute data flow (`self.x`); only module-level state counts as shared state here.
- Persistence, incrementality, identity ids (edges use refs; the store joins refs to unit_ids).

## Open questions / assumptions
- Local variables shadowing imports are resolved lexically; when ambiguous, prefer the local (no edge).
- A read inside an augmented write (`counter += 1`) may yield both a reads and a writes edge; tests only pin the writes edge.
- Method-to-method resolution is within the lexical class only; inherited-method resolution deferred to graph query time.
