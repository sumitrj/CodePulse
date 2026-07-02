"""The graph of record: a persisted snapshot of units + edges for one repo.

Lives at <root>/.codepulse/store.json. Rebuilds preserve unit identity by
resolving new units against the stored ones, so ids survive rename/move/
refactor across refreshes.
"""
import hashlib
import json
from pathlib import Path

from .identity import Param, Signature, Unit, extract_units, resolve
from .identity.model import IdentityResult
from .relationships import Edge, EdgeGraph, ExternalRef, StateRef, UnitRef, extract_edges

EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".pytest_cache", "dist", "build", ".codepulse", ".claude",
}
STORE_DIR = ".codepulse"
STORE_FILE = "store.json"


def scan(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        try:
            files[str(rel)] = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
    return files


def find_root(start: Path) -> Path | None:
    """Walk up from `start` to the nearest directory holding a store."""
    for candidate in [start, *start.parents]:
        if (candidate / STORE_DIR / STORE_FILE).exists():
            return candidate
    return None


class Store:
    def __init__(self, root: Path, units: dict[str, Unit], graph: EdgeGraph,
                 file_hashes: dict[str, str]):
        self.root = root
        self.units = units
        self.graph = graph
        self.file_hashes = file_hashes

    # -- lifecycle -----------------------------------------------------

    @classmethod
    def build(cls, root: Path, previous: "Store | None" = None) -> tuple["Store", IdentityResult]:
        files = scan(root)
        parseable: dict[str, str] = {}
        new_units: list[Unit] = []
        for path, src in files.items():
            try:
                new_units.extend(extract_units(src, path))
                parseable[path] = src
            except SyntaxError:
                continue
        old = previous.units if previous else {}
        result = resolve(old, new_units)
        units = {a.unit_id: a.unit for a in result.assignments}
        graph = extract_edges(parseable)
        hashes = {p: hashlib.sha256(s.encode()).hexdigest()[:16] for p, s in parseable.items()}
        return cls(root, units, graph, hashes), result

    def save(self) -> Path:
        target = self.root / STORE_DIR / STORE_FILE
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(self._to_dict(), indent=1, sort_keys=True))
        return target

    @classmethod
    def load(cls, root: Path) -> "Store":
        data = json.loads((root / STORE_DIR / STORE_FILE).read_text())
        units = {uid: _unit_from(d) for uid, d in data["units"].items()}
        edges = frozenset(_edge_from(e) for e in data["edges"])
        return cls(root, units, EdgeGraph(edges), data["files"])

    # -- serialization -------------------------------------------------

    def _to_dict(self) -> dict:
        return {
            "version": 1,
            "files": self.file_hashes,
            "units": {uid: _unit_to(u) for uid, u in self.units.items()},
            "edges": sorted(_edge_to(e) for e in self.graph.edges),
        }


def _unit_to(u: Unit) -> dict:
    return {
        "name": u.name, "kind": u.kind, "path": u.path, "qualname": u.qualname,
        "params": [[p.name, p.annotation, p.default] for p in u.signature.params],
        "returns": u.signature.returns,
        "body": u.body,
        "calls": sorted(u.calls),
    }


def _unit_from(d: dict) -> Unit:
    return Unit(
        name=d["name"], kind=d["kind"], path=d["path"], qualname=d["qualname"],
        signature=Signature(
            params=tuple(Param(*p) for p in d["params"]), returns=d["returns"]
        ),
        body=d["body"], calls=frozenset(d["calls"]),
    )


def _ref_to(ref) -> list:
    if isinstance(ref, UnitRef):
        return ["unit", ref.path, ref.qualname]
    if isinstance(ref, StateRef):
        return ["state", ref.path, ref.name]
    return ["external", ref.name, ""]


def _ref_from(kind: str, a: str, b: str):
    if kind == "unit":
        return UnitRef(a, b)
    if kind == "state":
        return StateRef(a, b)
    return ExternalRef(a)


def _edge_to(e: Edge) -> list:
    return [e.src.path, e.src.qualname, *_ref_to(e.dst), e.kind, e.line]


def _edge_from(row: list) -> Edge:
    src_path, src_qual, dkind, da, db, kind, line = row
    return Edge(UnitRef(src_path, src_qual), _ref_from(dkind, da, db), kind, line)
