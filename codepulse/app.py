"""The companion app: the human rendering of the six verbs.

Stdlib HTTP server; one Material-3 page; JSON endpoints that answer the same
verbs the CLI, MCP, and hooks answer - one language, two renderings.
"""
import json
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import verbs
from .identity import fingerprint
from .relationships import UnitRef
from .store import Store, STORE_DIR, STORE_FILE

_IMPACT_KINDS = ("calls", "reads", "inherits")


class _Cache:
    def __init__(self, root: Path):
        self.root = root
        self.mtime = 0.0
        self.store: Store | None = None

    def get(self) -> Store:
        target = self.root / STORE_DIR / STORE_FILE
        mtime = target.stat().st_mtime
        if self.store is None or mtime != self.mtime:
            self.store = Store.load(self.root)
            self.mtime = mtime
        return self.store


# -- JSON views of the verbs ------------------------------------------------

def summary_data(store: Store) -> dict:
    regions: dict[str, int] = defaultdict(int)
    for u in store.units.values():
        regions[u.path.split("/")[0] if "/" in u.path else "."] += 1
    fan_in: dict = defaultdict(int)
    for e in store.graph.edges:
        if isinstance(e.dst, UnitRef) and e.kind in _IMPACT_KINDS:
            fan_in[e.dst] += 1
    load_bearing = [
        {"path": r.path, "qualname": r.qualname, "fan_in": n}
        for r, n in sorted(fan_in.items(), key=lambda kv: -kv[1])[:8]
    ]
    twins = defaultdict(list)
    for u in store.units.values():
        twins[fingerprint(u).body_hash].append({"path": u.path, "qualname": u.qualname})
    duplicates = sorted(
        (g for g in twins.values() if len(g) > 1), key=len, reverse=True
    )[:8]
    return {
        "files": len(store.file_hashes),
        "units": len(store.units),
        "edges": len(store.graph.edges),
        "regions": sorted(
            ({"name": k, "count": v} for k, v in regions.items()),
            key=lambda r: -r["count"],
        ),
        "load_bearing": load_bearing,
        "duplicates": duplicates,
    }


def search_data(store: Store, query: str) -> dict:
    tokens = [t for t in query.lower().replace("_", " ").split() if len(t) > 1]
    results = []
    for uid, u in store.units.items():
        doc = (verbs._docstring(u.body) or "")
        haystacks = (u.qualname.lower().replace("_", " "), doc.lower(), u.path.lower(), u.body.lower())
        weights = (4, 3, 1, 1)
        score = sum(w for t in tokens for h, w in zip(haystacks, weights) if t in h)
        if score:
            results.append({"score": score, "path": u.path, "qualname": u.qualname,
                            "kind": u.kind, "doc": doc})
    results.sort(key=lambda r: (-r["score"], r["path"]))
    return {"results": results[:12]}


def unit_data(store: Store, path: str, qualname: str) -> dict:
    hits = [u for u in store.units.values() if u.path == path and u.qualname == qualname]
    if not hits:
        return {"error": "not found"}
    u = hits[0]
    ref = UnitRef(u.path, u.qualname)
    dependents = sorted(
        {f"{e.src.path}::{e.src.qualname}"
         for k in _IMPACT_KINDS for e in store.graph.incoming(ref, kind=k)}
    )
    return {
        "path": u.path, "qualname": u.qualname, "kind": u.kind,
        "params": [p.name + (f": {p.annotation}" if p.annotation else "") for p in u.signature.params],
        "returns": u.signature.returns,
        "doc": verbs._docstring(u.body),
        "calls": sorted(u.calls),
        "dependents": dependents,
        "radius": len(verbs.compute_radius(store, ref)),
    }


def radius_data(store: Store, name: str) -> dict:
    hits = verbs.find_units(store, name)
    targets = [UnitRef(u.path, u.qualname) for _, u in hits[:1]] or verbs._state_refs(store, name)[:1]
    if not targets:
        return {"error": f"no unit or state matching '{name}'"}
    target = targets[0]
    depth_of = verbs.compute_radius(store, target)
    hops = defaultdict(list)
    for ref, d in depth_of.items():
        hops[d].append({"path": ref.path, "qualname": ref.qualname})
    return {
        "target": getattr(target, "qualname", getattr(target, "name", "?")),
        "total": len(depth_of),
        "hops": [{"depth": d, "units": sorted(hops[d], key=lambda x: x["qualname"])}
                 for d in sorted(hops)],
    }


def changed_data(store: Store) -> dict:
    from .judge import classify_change
    new_store, result = Store.build(store.root, previous=store)
    items = []
    for a in result.assignments:
        if a.kind == "exact":
            continue
        if a.kind == "new":
            items.append({"classification": "new", "path": a.unit.path,
                          "qualname": a.unit.qualname, "summary": "New unit.", "match": "new"})
            continue
        old = store.units.get(a.unit_id)
        if old is None:
            continue
        v = classify_change(old, a.unit, use_model=False)
        items.append({"classification": v.classification, "path": a.unit.path,
                      "qualname": a.unit.qualname, "summary": v.summary, "match": a.kind})
    for uid in result.removed:
        old = store.units[uid]
        items.append({"classification": "removed", "path": old.path,
                      "qualname": old.qualname, "summary": "Unit deleted.", "match": "removed"})
    order = {"major": 0, "removed": 1, "minor": 2, "patch": 3, "refactor": 4, "new": 5}
    items.sort(key=lambda i: order.get(i["classification"], 9))
    return {"items": items}


# -- server ------------------------------------------------------------------

def serve_app(root: Path, port: int = 7317):
    cache = _Cache(root)
    page = (Path(__file__).parent / "webapp" / "index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: dict):
            self._send(json.dumps(data).encode(), "application/json")

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/":
                    self._send(page, "text/html; charset=utf-8")
                elif url.path == "/api/summary":
                    self._json({"root": str(root), **summary_data(cache.get())})
                elif url.path == "/api/search":
                    self._json(search_data(cache.get(), q.get("q", "")))
                elif url.path == "/api/unit":
                    self._json(unit_data(cache.get(), q.get("path", ""), q.get("qualname", "")))
                elif url.path == "/api/radius":
                    self._json(radius_data(cache.get(), q.get("name", "")))
                elif url.path == "/api/changed":
                    self._json(changed_data(cache.get()))
                elif url.path == "/api/workpieces":
                    from .workbench import load_workpieces
                    self._json({"items": load_workpieces(root)})
                else:
                    self._send(b"not found", "text/plain", 404)
            except BrokenPipeError:
                pass
            except Exception as exc:
                self._json({"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"CodePulse companion app: http://127.0.0.1:{port}  (root: {root})")
    server.serve_forever()
