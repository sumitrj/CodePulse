"""The six verbs over the recipe graph — the query surface shared by MCP,
the panel, and the CLI. Every answer must survive being spoken as plain
sentences, and only claims what the graph knows. See specs/six/SPEC.md.
"""
from collections import defaultdict
from typing import Iterable

from .engine import Addr, Engine, External

_IMPACT_KINDS = ("calls", "reads", "inherits")


# ── target resolution ─────────────────────────────────────────────────

def find(engine: Engine, name: str) -> list[Addr]:
    """Resolve a spoken name: 'path::name' pins a file; else exact name,
    exact tail, then substring."""
    if "::" in name:
        path, _, qual = name.partition("::")
        return [Addr(path.strip(), qual.strip())]
    candidates = [e.addr for e in engine.entities() if e.addr.name != "<module>"]
    for predicate in (
        lambda a: a.name == name,
        lambda a: a.name.split(".")[-1] == name,
        lambda a: name.lower() in a.name.lower(),
    ):
        hits = [a for a in candidates if predicate(a)]
        if hits:
            return sorted(hits, key=lambda a: (a.path, a.name))
    return []


def _label(target) -> str:
    if isinstance(target, External):
        return f"external:{target.name}"
    return f"{target.path} :: {target.name}"


# ── verb 1: what is this? ─────────────────────────────────────────────

def what_is(engine: Engine, name: str) -> str:
    hits = find(engine, name)
    if not hits:
        return f"No entity matching '{name}'."
    cards = []
    for addr in hits[:3]:
        entity = next(e for e in engine.entities(addr.path) if e.addr == addr)
        calls = sorted({
            _label(e.dst) for e in engine.outgoing(addr, kind="calls")
        })
        dependents = sorted({
            _label(e.src) for kind in _IMPACT_KINDS
            for e in engine.incoming(addr, kind=kind)
            if e.src.name != "<module>"
        })
        cards.append(
            f"{addr.path} :: {addr.name}  [{entity.kind}]  via {entity.recipe}@{entity.recipe_version}\n"
            f"  line {entity.line}\n"
            f"  calls      : {', '.join(calls) or '-'}\n"
            f"  dependents : {len(dependents)}"
            + (f" — {', '.join(dependents)}" if dependents else "")
        )
    if len(hits) > 3:
        cards.append(f"(+{len(hits) - 3} more matches)")
    return "\n\n".join(cards)


# ── verb 2: who touches it? ───────────────────────────────────────────

def who_touches(engine: Engine, name: str) -> str:
    hits = find(engine, name)
    if not hits:
        return f"No entity matching '{name}'."
    addr = hits[0]
    edges = sorted(engine.incoming(addr), key=lambda e: (e.kind, e.src.path, e.line))
    if not edges:
        return f"{_label(addr)}: nothing touches it."
    return "\n".join(
        f"{e.src.path} :: {e.src.name:<28} --{e.kind}--> {addr.name}  (line {e.line})"
        for e in edges
    )


# ── verb 3: what breaks if I change it? ───────────────────────────────

def compute_radius(engine: Engine, target: Addr) -> dict[Addr, int]:
    depth_of: dict[Addr, int] = {}
    frontier = [(target, 0)]
    while frontier:
        current, d = frontier.pop(0)
        for kind in _IMPACT_KINDS:
            for e in engine.incoming(current, kind=kind):
                if e.src.name == "<module>" or e.src in depth_of or e.src == target:
                    continue
                depth_of[e.src] = d + 1
                frontier.append((e.src, d + 1))
    return depth_of


def radius(engine: Engine, name: str) -> str:
    hits = find(engine, name)
    if not hits:
        return f"No entity matching '{name}'."
    addr = hits[0]
    depth_of = compute_radius(engine, addr)
    if not depth_of:
        return f"Blast radius of {_label(addr)}: nothing depends on it."
    by_depth = defaultdict(list)
    for ref, d in depth_of.items():
        by_depth[d].append(_label(ref))
    count = len(depth_of)
    lines = [f"Blast radius of {_label(addr)}: {count} {'entity' if count == 1 else 'entities'} at risk."]
    for d in sorted(by_depth):
        for entry in sorted(by_depth[d]):
            lines.append(f"  {'  ' * (d - 1)}[{d} hop{'s' if d > 1 else ''}] {entry}")
    return "\n".join(lines)


# ── verb 4: what changed? ─────────────────────────────────────────────

def what_changed(engine: Engine, paths: Iterable) -> str:
    report = engine.update(paths)
    if not report.extracted:
        return "No tracked files among the changed paths."
    lines = []
    for rel in report.extracted:
        touched = [e.addr for e in engine.entities(rel) if e.addr.name != "<module>"]
        lines.append(f"Changed: {rel} — {len(touched)} {'entity' if len(touched) == 1 else 'entities'}")
        for addr in touched:
            at_risk = sorted({
                _label(ref) for ref in compute_radius(engine, addr)
                if ref.path != rel
            })
            lines.append(
                f"  {addr.name}"
                + (f" — at risk: {', '.join(at_risk)}" if at_risk else " — nothing downstream")
            )
    return "\n".join(lines)


# ── verb 5: where does X live? ────────────────────────────────────────

def locate(engine: Engine, query: str) -> str:
    tokens = [t for t in query.lower().replace("_", " ").split() if len(t) > 2]
    if not tokens:
        return "Query too short."
    scored = []
    for entity in engine.entities():
        if entity.addr.name == "<module>":
            continue
        name = entity.addr.name.lower().replace("_", " ").replace(".", " ")
        path = entity.addr.path.lower()
        score = sum((3 if t in name else 0) + (1 if t in path else 0) for t in tokens)
        if score:
            scored.append((score, entity.addr.path, entity.addr.name))
    if not scored:
        return f"Nothing in the map matches '{query}'."
    scored.sort(key=lambda s: (-s[0], s[1], s[2]))
    return "\n".join(f"[score {s}] {p} :: {n}" for s, p, n in scored[:5])


# ── the bonus verb: system silhouette ─────────────────────────────────

def system_map(engine: Engine) -> str:
    entities = [e for e in engine.entities() if e.addr.name != "<module>"]
    files = {e.addr.path for e in engine.entities()}
    fan_in: dict[Addr, int] = defaultdict(int)
    edge_count = 0
    for entity in entities:
        for kind in _IMPACT_KINDS:
            incoming = engine.incoming(entity.addr, kind=kind)
            fan_in[entity.addr] += len(incoming)
        edge_count += len(engine.outgoing(entity.addr))
    load_bearing = sorted(fan_in.items(), key=lambda kv: (-kv[1], kv[0].path, kv[0].name))[:5]
    lines = [f"System: {len(files)} files, {len(entities)} entities, {edge_count} edges."]
    top = [f"{_label(a)} ({n})" for a, n in load_bearing if n]
    if top:
        lines.append("Load-bearing (highest fan-in): " + "; ".join(top))
    return "\n".join(lines)
