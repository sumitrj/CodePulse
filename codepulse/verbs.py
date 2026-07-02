"""The six verbs - the entire query surface, shared by CLI, MCP, and hooks.

Every answer must survive being spoken as plain sentences.
"""
from collections import defaultdict
from pathlib import Path

from .judge import classify_change
from .relationships import StateRef, UnitRef
from .store import Store, scan

_IMPACT_KINDS = ("calls", "reads", "inherits")


# -- target resolution --------------------------------------------------

def find_units(store: Store, name: str) -> list[tuple[str, object]]:
    """Resolve a spoken name to units: exact qualname, exact name, substring."""
    items = store.units.items()
    for predicate in (
        lambda u: u.qualname == name or f"{u.path}::{u.qualname}" == name,
        lambda u: u.name == name,
        lambda u: name.lower() in u.qualname.lower(),
    ):
        hits = [(uid, u) for uid, u in items if predicate(u)]
        if hits:
            return sorted(hits, key=lambda h: h[1].path)
    return []


def _state_refs(store: Store, name: str) -> list[StateRef]:
    refs = {e.dst for e in store.graph.edges if isinstance(e.dst, StateRef)}
    return sorted((r for r in refs if r.name == name), key=str)


# -- verb 1: what is this? ----------------------------------------------

def what_is(store: Store, name: str) -> str:
    hits = find_units(store, name)
    if not hits:
        return f"No unit matching '{name}'."
    lines = []
    for uid, u in hits[:3]:
        params = ", ".join(
            p.name + (f": {p.annotation}" if p.annotation else "") for p in u.signature.params
        )
        dependents = {
            e.src for k in _IMPACT_KINDS
            for e in store.graph.incoming(UnitRef(u.path, u.qualname), kind=k)
        }
        doc = _docstring(u.body)
        lines.append(
            f"{u.path} :: {u.qualname}  [{u.kind}]  id={uid}\n"
            f"  contract : ({params})" + (f" -> {u.signature.returns}" if u.signature.returns else "") + "\n"
            + (f"  promise  : {doc}\n" if doc else "")
            + f"  calls    : {', '.join(sorted(u.calls)) or '-'}\n"
            f"  direct dependents: {len(dependents)}"
        )
    if len(hits) > 3:
        lines.append(f"(+{len(hits) - 3} more matches)")
    return "\n\n".join(lines)


# -- verb 2: who touches it? ---------------------------------------------

def who_touches(store: Store, name: str) -> str:
    targets: list = [UnitRef(u.path, u.qualname) for _, u in find_units(store, name)[:1]]
    targets += _state_refs(store, name)
    if not targets:
        return f"No unit or state matching '{name}'."
    lines = []
    for target in targets[:1]:
        label = getattr(target, "qualname", getattr(target, "name", "?"))
        edges = sorted(store.graph.incoming(target), key=lambda e: (e.kind, e.src.path, e.line))
        if not edges:
            lines.append(f"{label}: nothing touches it.")
        for e in edges:
            lines.append(f"{e.src.path} :: {e.src.qualname:<28} --{e.kind}--> {label}  (line {e.line})")
    return "\n".join(lines)


# -- verb 3: what breaks if I change it? ----------------------------------

def radius(store: Store, name: str) -> str:
    hits = find_units(store, name)
    targets: list = [UnitRef(u.path, u.qualname) for _, u in hits[:1]] or _state_refs(store, name)[:1]
    if not targets:
        return f"No unit or state matching '{name}'."
    label = getattr(targets[0], "qualname", getattr(targets[0], "name", "?"))
    depth_of = compute_radius(store, targets[0])
    if not depth_of:
        return f"Blast radius of {label}: nothing depends on it."
    by_depth = defaultdict(list)
    for ref, d in depth_of.items():
        by_depth[d].append(f"{ref.path} :: {ref.qualname}")
    lines = [f"Blast radius of {label}: {len(depth_of)} unit(s) at risk."]
    for d in sorted(by_depth):
        for entry in sorted(by_depth[d]):
            lines.append(f"  {'  ' * (d - 1)}[{d} hop{'s' if d > 1 else ''}] {entry}")
    return "\n".join(lines)


def compute_radius(store: Store, target) -> dict:
    depth_of: dict = {}
    frontier = [(target, 0)]
    while frontier:
        current, d = frontier.pop(0)
        for kind in _IMPACT_KINDS:
            for e in store.graph.incoming(current, kind=kind):
                if e.src.qualname == "<module>" or e.src in depth_of or e.src == target:
                    continue
                depth_of[e.src] = d + 1
                frontier.append((e.src, d + 1))
    return depth_of


# -- verb 4: what changed? ------------------------------------------------

def what_changed(store: Store, use_model: bool = False) -> tuple[str, Store]:
    new_store, result = Store.build(store.root, previous=store)
    lines = []
    for a in result.assignments:
        if a.kind in ("exact", "new") and a.kind != "new":
            continue
        if a.kind == "new":
            lines.append(f"[new       ] {a.unit.path} :: {a.unit.qualname}")
            continue
        old = store.units.get(a.unit_id)
        if old is None:
            continue
        verdict = classify_change(old, a.unit, use_model=use_model)
        origin = (
            f"  (was {old.path} :: {old.qualname})"
            if (old.path, old.qualname) != (a.unit.path, a.unit.qualname) else ""
        )
        lines.append(
            f"[{verdict.classification:<9}] {a.unit.path} :: {a.unit.qualname}"
            f"  ({a.kind}, via {verdict.source}){origin}\n"
            f"             {verdict.summary}"
        )
        if verdict.classification == "major":
            impacted = compute_radius(store, UnitRef(old.path, old.qualname))
            if impacted:
                names = ", ".join(sorted(f"{r.qualname}" for r in impacted))
                lines.append(f"             !! check dependents: {names}")
    for uid in sorted(result.removed):
        old = store.units[uid]
        lines.append(f"[removed   ] {old.path} :: {old.qualname}")
    return ("\n".join(lines) if lines else "No unit-level changes since last index."), new_store


# -- verb 5: where does X live? --------------------------------------------

def locate(store: Store, query: str) -> str:
    tokens = [t for t in query.lower().replace("_", " ").split() if len(t) > 2]
    if not tokens:
        return "Query too short."
    scored = []
    for uid, u in store.units.items():
        doc = (_docstring(u.body) or "").lower()
        qual = u.qualname.lower().replace("_", " ")
        path = u.path.lower()
        body = u.body.lower()
        score = sum(
            (3 if t in qual else 0) + (2 if t in doc else 0)
            + (1 if t in path else 0) + (1 if t in body else 0)
            for t in tokens
        )
        if score:
            scored.append((score, u.path, u.qualname, _docstring(u.body)))
    if not scored:
        return f"Nothing in the map matches '{query}'."
    scored.sort(key=lambda s: (-s[0], s[1]))
    return "\n".join(
        f"[score {s}] {p} :: {q}" + (f"  - {d}" if d else "")
        for s, p, q, d in scored[:5]
    )


# -- verb 6 (drift) ships with fork lineage in V3; map is the bonus verb ---

def system_map(store: Store, brief: bool = False) -> str:
    by_dir = defaultdict(int)
    for u in store.units.values():
        top = u.path.split("/")[0] if "/" in u.path else "."
        by_dir[top] += 1
    fan_in = defaultdict(int)
    for e in store.graph.edges:
        if isinstance(e.dst, UnitRef) and e.kind in _IMPACT_KINDS:
            fan_in[e.dst] += 1
    load_bearing = sorted(fan_in.items(), key=lambda kv: -kv[1])[:5]
    twins = defaultdict(list)
    from .identity import fingerprint
    for uid, u in store.units.items():
        twins[fingerprint(u).body_hash].append(u)
    duplicates = [g for g in twins.values() if len(g) > 1]

    lines = [
        f"System: {len(store.file_hashes)} files, {len(store.units)} units, {len(store.graph.edges)} edges."
    ]
    lines.append("Regions: " + ", ".join(f"{d} ({n})" for d, n in sorted(by_dir.items(), key=lambda kv: -kv[1])))
    if load_bearing:
        lines.append("Load-bearing (highest fan-in): "
                     + "; ".join(f"{r.qualname} ({n})" for r, n in load_bearing))
    if duplicates and not brief:
        lines.append("Duplicate implementations (simplicity check):")
        for group in duplicates[:5]:
            lines.append("  " + "  ==  ".join(f"{u.path}::{u.qualname}" for u in group))
    return "\n".join(lines)


def _docstring(body: str) -> str | None:
    import ast
    try:
        node = ast.parse(body).body[0]
        doc = ast.get_docstring(node)
        return doc.splitlines()[0] if doc else None
    except (SyntaxError, IndexError, AttributeError):
        return None
