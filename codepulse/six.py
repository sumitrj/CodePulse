"""The six verbs over the recipe graph — the query surface shared by MCP,
the panel, and the CLI. Every answer must survive being spoken as plain
sentences, and only claims what the graph knows. See specs/six/SPEC.md.
"""
from collections import defaultdict
from typing import Iterable

from .engine import MODULE_SCOPE, PACKAGE_SCOPE, SCOPES, Addr, Engine, External

# What counts as depending on something. `writes` is here because a writer is
# coupled to the state it writes; `implements` and `types` because changing a
# type contract puts everyone who names it at risk.
_IMPACT_KINDS = ("calls", "reads", "writes", "inherits", "implements", "types")
# What a container's dependents can be reached by. Importers live at module
# scope, so `imports` only earns its place on this tier — never on the direct
# one, where it would make every file's dependents look like every symbol's.
_CONTAINER_KINDS = _IMPACT_KINDS + ("imports",)
_MAX_DEPTH = 32          # containment is shallow; this is a cycle guard, not a limit


# ── target resolution ─────────────────────────────────────────────────

def find(engine: Engine, name: str) -> list[Addr]:
    """Resolve a spoken name: 'path::name' pins a file; else exact name,
    exact tail, then substring."""
    if "::" in name:
        path, _, qual = name.partition("::")
        return [Addr(path.strip(), qual.strip())]
    candidates = [e.addr for e in engine.entities() if e.addr.name not in SCOPES]
    for predicate in (
        lambda a: a.name == name,
        lambda a: a.name.split(".")[-1] == name,
        lambda a: name.lower() in a.name.lower(),
    ):
        hits = [a for a in candidates if predicate(a)]
        if hits:
            return sorted(hits, key=lambda a: (a.path, a.name))
    return []


def _also(hits: list[Addr]) -> str:
    """When a name is ambiguous, the answer is for the first match — say so,
    and name the others, rather than let a guess pass for the answer."""
    if len(hits) < 2:
        return ""
    others = ", ".join(f"{a.path}::{a.name}" for a in hits[1:6])
    more = f" (+{len(hits) - 6} more)" if len(hits) > 6 else ""
    return (f"\n\nAlso named '{hits[0].name.split('.')[-1]}': {others}{more}"
            f" — ask again as path::name for one of those.")


def _label(target) -> str:
    if isinstance(target, External):
        return f"external:{target.name}"
    if target.name == PACKAGE_SCOPE:
        return f"package {target.path}"
    if target.name == MODULE_SCOPE:
        return f"file {target.path}"
    return f"{target.path} :: {target.name}"


# ── containment: the geography above a symbol ─────────────────────────

def ancestry(engine: Engine, addr: Addr) -> list[Addr]:
    """Walk `contains` upward — method → class → file → package → package.

    The graph has always carried these edges; nothing read them. Answering
    "where does this sit" without them means re-deriving the tree from path
    strings, which is exactly the work the map exists to have already done.
    """
    chain: list[Addr] = []
    seen = {addr}
    current = addr
    while len(chain) < _MAX_DEPTH:
        parents = sorted(
            (e.src for e in engine.incoming(current, kind="contains")
             if e.src not in seen),
            key=lambda a: (a.path, a.name))
        if not parents:
            break
        current = parents[0]
        seen.add(current)
        chain.append(current)
    return chain


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
        says = ""
        if entity.doc and entity.meta:
            says = f"  says       : {entity.meta.splitlines()[0]}\n"
        chain = ancestry(engine, addr)
        inside = f"  inside     : {' < '.join(_label(a) for a in chain)}\n" if chain else ""
        hint = ("\n  note       : nothing on the map calls this — tests and "
                "dynamically-invoked functions look like this."
                if not dependents else "")
        cards.append(
            f"{addr.path} :: {addr.name}  [{entity.kind}]  via {entity.recipe}@{entity.recipe_version}\n"
            f"  line {entity.line}\n"
            + says
            + inside
            + f"  calls      : {', '.join(calls) or '-'}\n"
            f"  dependents : {len(dependents)}"
            + (f" — {', '.join(dependents)}" if dependents else "")
            + hint
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
        return f"{_label(addr)}: nothing touches it." + _also(hits)
    return "\n".join(
        f"{e.src.path} :: {e.src.name:<28} --{e.kind}--> {addr.name}  (line {e.line})"
        for e in edges
    ) + _also(hits)


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


def compute_container_radius(engine: Engine, target: Addr) -> dict[Addr, Addr]:
    """Who depends on the things that hold this one — mapped to which holder.

    Deliberately one hop off each container, and deliberately not merged into
    compute_radius: a caller of the enclosing class is a weaker claim than a
    caller of the function itself, and folding the two would let one method
    change appear to endanger every importer of its module. Separate tier,
    separate count, so the direct number stays worth reading.
    """
    direct = compute_radius(engine, target)
    reached: dict[Addr, Addr] = {}
    for ancestor in ancestry(engine, target):
        for kind in _CONTAINER_KINDS:
            for e in engine.incoming(ancestor, kind=kind):
                if e.src in (target, ancestor) or e.src in direct or e.src in reached:
                    continue
                reached[e.src] = ancestor
    return reached


def radius(engine: Engine, name: str) -> str:
    hits = find(engine, name)
    if not hits:
        return f"No entity matching '{name}'."
    addr = hits[0]
    depth_of = compute_radius(engine, addr)
    chain = ancestry(engine, addr)
    through = compute_container_radius(engine, addr)

    if depth_of:
        by_depth = defaultdict(list)
        for ref, d in depth_of.items():
            by_depth[d].append(_label(ref))
        count = len(depth_of)
        lines = [f"Blast radius of {_label(addr)}: {count} "
                 f"{'entity' if count == 1 else 'entities'} at risk."]
        for d in sorted(by_depth):
            for entry in sorted(by_depth[d]):
                lines.append(f"  {'  ' * (d - 1)}[{d} hop{'s' if d > 1 else ''}] {entry}")
    else:
        lines = [f"Blast radius of {_label(addr)}: nothing depends on it directly."]

    if chain:
        lines.append("")
        lines.append("Inside: " + " < ".join(_label(a) for a in chain))

    if through:
        by_holder = defaultdict(list)
        for ref, holder in through.items():
            by_holder[holder].append(_label(ref))
        total = len(through)
        lines.append(f"Through its container{'s' if len(by_holder) > 1 else ''}: "
                     f"{total} more {'entity' if total == 1 else 'entities'} "
                     f"touch what holds it (weaker claim — they may not use this one).")
        for holder in sorted(by_holder, key=lambda a: (a.path, a.name)):
            entries = sorted(by_holder[holder])
            shown = ", ".join(entries[:6])
            more = f" (+{len(entries) - 6} more)" if len(entries) > 6 else ""
            lines.append(f"  via {_label(holder)}: {shown}{more}")
    return "\n".join(lines) + _also(hits)


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
    from .search import search  # hybrid: exact + fuzzy + what docstrings say

    hits = search(engine, query, limit=5)
    if not hits:
        return f"Nothing in the map matches '{query}'."
    return "\n".join(
        f"[score {h['score']}] {h['path']} :: {h['name']}"
        + (f"  — {h['meta'].splitlines()[0][:80]}" if h["meta"] else "")
        for h in hits
    )


# ── the bonus verb: system silhouette ─────────────────────────────────

def system_map(engine: Engine) -> str:
    everything = engine.entities()
    entities = [e for e in everything if e.addr.name not in SCOPES]    # folders aren't entities
    files = {e.addr.path for e in everything if e.addr.name == MODULE_SCOPE}
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
