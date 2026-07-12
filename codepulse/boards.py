"""Boards — saved views over the six verbs, returned as data.

The panel draws them; MCP speaks them. No new verbs: handlers is
"who touches it" composed with "what breaks", the map is the silhouette,
the hop diagram is a blast radius with its connecting edges.
See specs/boards/SPEC.md.
"""
from collections import Counter, deque

from . import six
from .engine import Addr, Engine

_IMPACT = ("calls", "reads", "inherits")


def handlers(engine: Engine) -> list[dict]:
    """Estimate handler-level functions from graph shape alone: a function or
    method no other entity calls (module-scope registrations don't count as
    callers) whose calls transitively reach real work."""
    board = []
    for entity in engine.entities():
        if entity.kind not in ("function", "method"):
            continue
        addr = entity.addr
        fan_in = [
            edge for kind in _IMPACT for edge in engine.incoming(addr, kind=kind)
            if edge.src.name != "<module>" and edge.src != addr
        ]
        if fan_in:
            continue
        seen: set[Addr] = set()
        frontier = deque([addr])
        direct = 0
        while frontier:
            current = frontier.popleft()
            targets = {
                edge.dst for edge in engine.outgoing(current, kind="calls")
                if isinstance(edge.dst, Addr)
            }
            if current == addr:
                direct = len(targets)
            for target in targets:
                if target not in seen and target != addr:
                    seen.add(target)
                    frontier.append(target)
        if not seen:
            continue
        board.append({
            "path": addr.path, "name": addr.name, "kind": entity.kind,
            "reach": len(seen), "direct": direct,
        })
    board.sort(key=lambda h: (-h["reach"], h["path"], h["name"]))
    return board


def handlers_text(engine: Engine) -> str:
    board = handlers(engine)
    if not board:
        return "No handler-level functions found."
    count = len(board)
    lines = [
        f"Estimated {count} handler-level "
        f"{'function' if count == 1 else 'functions'} — uncalled roots with downstream reach:"
    ]
    for h in board[:20]:
        lines.append(f"  {h['path']} :: {h['name']}  (reach {h['reach']}, direct {h['direct']})")
    if count > 20:
        lines.append(f"  (+{count - 20} more)")
    return "\n".join(lines)


def _reach_depths(engine: Engine, target: Addr) -> dict[Addr, int]:
    """Downstream mirror of six.compute_radius: what the target calls, by hop."""
    depth_of: dict[Addr, int] = {}
    frontier = deque([(target, 0)])
    while frontier:
        current, d = frontier.popleft()
        for edge in engine.outgoing(current, kind="calls"):
            dst = edge.dst
            if not isinstance(dst, Addr) or dst in depth_of or dst == target:
                continue
            depth_of[dst] = d + 1
            frontier.append((dst, d + 1))
    return depth_of


def radius_graph(engine: Engine, name: str, direction: str = "in") -> dict:
    """The hop diagram. direction "in" = blast radius (who breaks if the target
    changes); "out" = reach (what the target calls, transitively)."""
    hits = six.find(engine, name)
    if not hits:
        return {"target": None, "direction": direction, "nodes": [], "edges": []}
    target = hits[0]
    if direction == "out":
        depth_of = _reach_depths(engine, target)
    else:
        depth_of = six.compute_radius(engine, target)
    members = {target} | set(depth_of)
    kinds = {e.addr: e.kind for e in engine.entities()}
    nodes = [
        {"path": addr.path, "name": addr.name, "hop": hop, "kind": kinds.get(addr, "")}
        for addr, hop in sorted(depth_of.items(), key=lambda kv: (kv[1], kv[0].path, kv[0].name))
    ]
    kinds = ("calls",) if direction == "out" else _IMPACT
    edges = set()
    for addr in members:
        for kind in kinds:
            for edge in engine.incoming(addr, kind=kind):
                if edge.src in members:
                    edges.add((f"{edge.src.path}::{edge.src.name}",
                               f"{addr.path}::{addr.name}", kind))
    return {
        "target": {"path": target.path, "name": target.name},
        "direction": direction,
        "nodes": nodes,
        "edges": [{"src": s, "dst": d, "kind": k} for s, d, k in sorted(edges)],
    }


def file_graph(engine: Engine) -> dict:
    entities = engine.entities()
    files = sorted({e.addr.path for e in entities})
    mode = "file" if len(files) <= 150 else "dir"

    def gid(path: str) -> str:
        if mode == "file":
            return path
        return path.split("/")[0] if "/" in path else "."

    counts: Counter = Counter()
    for e in entities:
        if e.addr.name != "<module>":
            counts[gid(e.addr.path)] += 1

    agg: Counter = Counter()
    for src, dst in engine.db.execute(
            "SELECT src_path, dst_path FROM edges "
            "WHERE status!='dropped' AND dst_path IS NOT NULL AND kind!='contains'"):
        a, b = gid(src), gid(dst)
        if a != b:
            agg[(a, b)] += 1

    ids = sorted({gid(p) for p in files})
    indegree = Counter(b for (_, b) in agg)
    depth = {i: 0 for i in ids}
    frontier = deque(i for i in ids if indegree[i] == 0)
    seen = set(frontier)
    while frontier:
        current = frontier.popleft()
        for (a, b) in agg:
            if a == current and b not in seen:
                depth[b] = depth[current] + 1
                seen.add(b)
                frontier.append(b)

    return {
        "mode": mode,
        "nodes": [{"id": i, "entities": counts.get(i, 0), "depth": depth[i]} for i in ids],
        "edges": [{"src": a, "dst": b, "n": n} for (a, b), n in sorted(agg.items())],
    }
