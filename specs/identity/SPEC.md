# Unit identity fingerprint

## Intent
Given two snapshots of source code, CodePulse can say which units (functions, classes, methods) in the new snapshot are *the same unit* as in the old one — surviving rename, move, and moderate refactor — and which are genuinely new or deleted. Every unit carries a stable `unit_id` forward; nothing else in V1 works until this does. Python source only in this component; other languages later, behind the same interface.

## Interface
```python
# codepulse/identity.py
@dataclass(frozen=True)
class Param:
    name: str
    annotation: str | None
    default: str | None

@dataclass(frozen=True)
class Signature:
    params: tuple[Param, ...]
    returns: str | None

@dataclass(frozen=True)
class Unit:
    name: str
    kind: Literal["function", "class", "method"]
    path: str                  # file path relative to repo root
    qualname: str              # e.g. "Cart.checkout"
    signature: Signature
    body: str                  # definition body source
    calls: frozenset[str]      # names called from the body

@dataclass(frozen=True)
class Fingerprint:
    contract_hash: str         # signature shape, normalized
    body_hash: str             # AST-normalized body: names kept, comments/whitespace/docstrings excluded
    neighborhood_hash: str     # sorted callee set

@dataclass(frozen=True)
class Assignment:
    unit: Unit
    unit_id: str
    kind: Literal["exact", "renamed", "moved", "renamed+moved", "refactored", "new"]
    confidence: float          # in [0,1]; 1.0 iff exact

@dataclass(frozen=True)
class IdentityResult:
    assignments: tuple[Assignment, ...]   # exactly one per new unit
    removed: frozenset[str]               # old unit_ids with no successor

def extract_units(source: str, path: str) -> list[Unit]
def fingerprint(unit: Unit) -> Fingerprint
def resolve(old: Mapping[str, Unit], new: Sequence[Unit]) -> IdentityResult
```

## Inputs and outputs
- Input: Python source text per file (old and new snapshots). `resolve({}, units)` bootstraps a first snapshot (all `"new"`, fresh opaque string ids).
- Output: `IdentityResult` — a total, unambiguous mapping (see AC10).
- Errors: `extract_units` propagates `SyntaxError` on invalid source. `resolve` raises nothing on well-formed input.

## Acceptance criteria
1. `extract_units` yields one `Unit` per function/class/method with correct name, kind, path, and signature (param names, return annotation).
2. Fingerprints are deterministic and formatting-insensitive: comment, whitespace, and docstring changes leave the fingerprint equal.
3. An unchanged unit keeps its id across snapshots with kind `"exact"` and confidence 1.0.
4. A unit renamed, moved to another file, or both — body intact — keeps its id; kind reports which (`"renamed"` / `"moved"` / `"renamed+moved"`).
5. A moderate body refactor with intact contract (same name, path, signature) keeps its id with kind `"refactored"` and 0 < confidence < 1.
6. In-place contract evolution (param added, same name and path) keeps the id — versioning it is the Judge's job, not identity's.
7. A split (part of F extracted into new G) keeps F's id on the continuing unit; G gets a fresh id with kind `"new"` — G must not steal F's identity.
8. Same-named units in different files are never confused: after both files move, each follows its own body.
9. A brand-new unit gets a fresh id (`"new"`); a deleted unit's id appears in `removed`.
10. Resolution is a function: every new unit gets exactly one assignment, no old id is assigned twice, and old ids are conserved (matched ∪ removed = old).

## Out of scope
- Version-event classification (refactor/patch/minor/major) — the Judge, V2.
- Promises, edges beyond `calls`, storage, incremental watching, TS/other languages.
- Nested functions, lambdas, module-level statements as units.

## Open questions / assumptions
- Match acceptance threshold is internal (assumed default ~0.6), not part of the public contract.
- `unit_id` is an opaque unique string; stability across machines matters, ordering does not.
- Class identity vs. its methods: methods are units of their own; a class matches on its own contract plus member roster.
