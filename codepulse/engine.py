"""The language-blind engine.

Runs recipes over files: extract entities and raw references, resolve
references through recipe-declared bindings into one address space, persist
everything in SQLite with recipe provenance. Contains zero knowledge of any
language — that lives in recipes. See specs/recipes/SPEC.md.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .recipes import Recipe, compile_query, parser_for, run_matches


@dataclass(frozen=True)
class Addr:
    """One address space for every language: a file path plus a dotted name."""
    path: str
    name: str  # "Cart.checkout", "<module>", "server.port"


@dataclass(frozen=True)
class External:
    """A target outside the repo, name preserved as written."""
    name: str


@dataclass(frozen=True)
class Entity:
    addr: Addr
    kind: str
    line: int
    recipe: str
    recipe_version: str


@dataclass(frozen=True)
class Edge:
    src: Addr
    dst: Addr | External
    kind: str
    line: int
    recipe: str
    recipe_version: str


@dataclass(frozen=True)
class ApplyReport:
    extracted: tuple[str, ...]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
    path TEXT PRIMARY KEY, hash TEXT, recipe TEXT);
CREATE TABLE IF NOT EXISTS entities(
    path TEXT, name TEXT, kind TEXT, line INTEGER,
    recipe TEXT, recipe_version TEXT,
    PRIMARY KEY(path, name));
CREATE TABLE IF NOT EXISTS edges(
    id INTEGER PRIMARY KEY,
    src_path TEXT, src_name TEXT, kind TEXT, target TEXT,
    dst_path TEXT, dst_name TEXT, external TEXT, status TEXT,
    line INTEGER, meta TEXT, recipe TEXT, recipe_version TEXT);
CREATE TABLE IF NOT EXISTS snapshots(
    id INTEGER PRIMARY KEY, label TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS edges_src ON edges(src_path, src_name);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst_path, dst_name);
"""


class Engine:
    def __init__(self, db_path: Path, recipes: Sequence[Recipe], root: Path):
        self.root = Path(root)
        self.recipes = list(recipes)
        self.db = sqlite3.connect(db_path)
        self.db.executescript(_SCHEMA)
        self._compiled = {}

    # ── extraction ──────────────────────────────────────────────────────

    def apply(self) -> ApplyReport:
        paths = sorted(
            p for p in self.root.rglob("*")
            if p.is_file() and self._recipe_for(p) is not None
        )
        return self._extract(paths)

    def update(self, changed: Iterable[Path]) -> ApplyReport:
        return self._extract(sorted(Path(p) for p in changed))

    def _recipe_for(self, path: Path) -> Recipe | None:
        for recipe in self.recipes:
            if any(fnmatch.fnmatch(path.name, pat) for pat in recipe.matches):
                return recipe
        return None

    def _extract(self, paths) -> ApplyReport:
        extracted = []
        for path in paths:
            abs_path = path if path.is_absolute() else self.root / path
            recipe = self._recipe_for(abs_path)
            if recipe is None:
                continue
            try:
                rel = abs_path.relative_to(self.root).as_posix()
                source = abs_path.read_text()
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            self.db.execute("DELETE FROM entities WHERE path=?", (rel,))
            self.db.execute("DELETE FROM edges WHERE src_path=?", (rel,))
            self._extract_file(rel, source, recipe)
            self.db.execute(
                "INSERT OR REPLACE INTO files(path, hash, recipe) VALUES(?,?,?)",
                (rel, hashlib.sha256(source.encode()).hexdigest(), recipe.name),
            )
            extracted.append(rel)
        self._resolve_all()
        self.db.commit()
        return ApplyReport(extracted=tuple(extracted))

    def _query(self, recipe: Recipe, source: str):
        key = (recipe.language, source)
        if key not in self._compiled:
            self._compiled[key] = compile_query(recipe.language, source)
        return self._compiled[key]

    def _extract_file(self, rel: str, source: str, recipe: Recipe) -> None:
        root_node = parser_for(recipe.language).parse(source.encode()).root_node
        prov = (recipe.name, recipe.version)

        # entities: collect (span, name, kind), derive qualnames from nesting
        found = []
        for rule in recipe.entities:
            for captures in run_matches(self._query(recipe, rule.query), root_node):
                name_nodes = captures.get("name") or []
                if not name_nodes:
                    continue
                name_node = name_nodes[0]
                span_node = (captures.get("node") or [name_node])[0]
                found.append((
                    span_node.start_byte, span_node.end_byte,
                    name_node.text.decode(), rule.kind,
                    name_node.start_point[0] + 1,
                ))
        found.sort(key=lambda f: (f[0], -f[1]))

        self._insert_entity(rel, "<module>", "module", 1, prov)
        spans = []          # (start, end, qualname) for src attribution
        stack = []          # enclosing entity spans
        seen = set()
        for start, end, simple, kind, line in found:
            while stack and not (stack[-1][0] <= start and end <= stack[-1][1]):
                stack.pop()
            parent = stack[-1][2] if stack else None
            qual = f"{parent}.{simple}" if parent else simple
            stack.append((start, end, qual))
            if qual in seen:
                continue
            seen.add(qual)
            spans.append((start, end, qual))
            self._insert_entity(rel, qual, kind, line, prov)
            self._insert_edge(rel, parent or "<module>", "contains", "", line,
                              dst=(rel, qual), meta="{}", prov=prov)

        # bindings: local name -> (module, member); each binding is an imports edge
        for rule in recipe.bindings:
            for captures in run_matches(self._query(recipe, rule.query), root_node):
                module_nodes = captures.get("module") or []
                if not module_nodes:
                    continue
                module = module_nodes[0].text.decode()
                member_node = (captures.get("member") or [None])[0]
                member = member_node.text.decode() if member_node is not None else None
                alias_node = (captures.get("alias") or [None])[0]
                alias = alias_node.text.decode() if alias_node is not None else None
                local = alias or (member.split(".")[-1] if member else module.split(".")[0])
                target = f"{module}.{member}" if member else module
                self._insert_edge(
                    rel, "<module>", "imports", target,
                    module_nodes[0].start_point[0] + 1,
                    meta=json.dumps({"local": local, "module": module, "member": member}),
                    prov=prov,
                )

        # references: raw rows now, resolved by _resolve_all
        for rule in recipe.references:
            dedupe = set()
            for captures in run_matches(self._query(recipe, rule.query), root_node):
                for tnode in captures.get("target") or []:
                    text = tnode.text.decode()
                    src = self._enclosing(spans, tnode.start_byte)
                    line = tnode.start_point[0] + 1
                    key = (src, rule.kind, text, line)
                    if key in dedupe:
                        continue
                    dedupe.add(key)
                    self._insert_edge(
                        rel, src, rule.kind, text, line,
                        meta=json.dumps({"only": list(rule.only),
                                         "unresolved": rule.unresolved}),
                        prov=prov,
                    )

    @staticmethod
    def _enclosing(spans, byte: int) -> str:
        best = None
        for start, end, qual in spans:
            if start <= byte < end and (best is None or end - start < best[0]):
                best = (end - start, qual)
        return best[1] if best else "<module>"

    def _insert_entity(self, path, name, kind, line, prov):
        self.db.execute(
            "INSERT OR IGNORE INTO entities(path,name,kind,line,recipe,recipe_version) "
            "VALUES(?,?,?,?,?,?)", (path, name, kind, line, *prov),
        )

    def _insert_edge(self, path, src, kind, target, line, meta, prov, dst=None):
        dst_path, dst_name, status = None, None, "raw"
        if dst is not None:
            dst_path, dst_name, status = dst[0], dst[1], "ok"
        self.db.execute(
            "INSERT INTO edges(src_path,src_name,kind,target,dst_path,dst_name,"
            "external,status,line,meta,recipe,recipe_version) "
            "VALUES(?,?,?,?,?,?,NULL,?,?,?,?,?)",
            (path, src, kind, target, dst_path, dst_name, status, line, meta, *prov),
        )

    # ── resolution ──────────────────────────────────────────────────────

    def _resolve_all(self) -> None:
        ents = {(p, n): k for p, n, k in
                self.db.execute("SELECT path, name, kind FROM entities")}
        file_recipe = dict(self.db.execute("SELECT path, recipe FROM files"))
        files = set(file_recipe)
        recipe_by_name = {r.name: r for r in self.recipes}

        def suffix_of(path: str) -> str:
            recipe = recipe_by_name.get(file_recipe.get(path, ""))
            return recipe.module_suffix if recipe else ""

        bindings: dict[str, dict[str, tuple[str, str | None]]] = {}
        for src_path, meta in self.db.execute(
                "SELECT src_path, meta FROM edges WHERE kind='imports'"):
            info = json.loads(meta)
            bindings.setdefault(src_path, {})[info["local"]] = (info["module"], info["member"])

        for row_id, src_path, target, meta in self.db.execute(
                "SELECT id, src_path, target, meta FROM edges WHERE kind='imports'").fetchall():
            info = json.loads(meta)
            mod_path = info["module"].replace(".", "/") + suffix_of(src_path)
            member = info["member"]
            if member and (mod_path, member) in ents:
                self._set_dst(row_id, mod_path, member)
            elif not member and mod_path in files:
                self._set_dst(row_id, mod_path, "<module>")
            else:
                self._set_external(row_id, target)

        for row_id, src_path, src_name, target, meta in self.db.execute(
                "SELECT id, src_path, src_name, target, meta FROM edges "
                "WHERE kind NOT IN ('imports','contains')").fetchall():
            cfg = json.loads(meta)
            addr = self._resolve_target(
                target, src_path, src_name,
                bindings.get(src_path, {}), ents, files, suffix_of(src_path),
            )
            only = tuple(cfg.get("only") or ())
            if addr is not None and only and ents.get(addr) not in only:
                addr = None
            if addr is not None:
                self._set_dst(row_id, *addr)
            elif cfg.get("unresolved", "external") == "external":
                self._set_external(row_id, target)
            else:
                self.db.execute("UPDATE edges SET status='dropped' WHERE id=?", (row_id,))

    @staticmethod
    def _resolve_target(target, src_path, src_name, binds, ents, files, suffix):
        if target.startswith("self.") and "." in src_name:
            cls = src_name.rsplit(".", 1)[0]
            cand = (src_path, f"{cls}.{target[5:]}")
            return cand if cand in ents else None
        head, _, rest = target.partition(".")
        if head in binds:
            module, member = binds[head]
            mod_path = module.replace(".", "/") + suffix
            name = f"{member}.{rest}" if member and rest else (member or rest or None)
            if name and (mod_path, name) in ents:
                return (mod_path, name)
            if not name and mod_path in files:
                return (mod_path, "<module>")
            return None
        if (src_path, target) in ents:
            return (src_path, target)
        return None

    def _set_dst(self, row_id, path, name):
        self.db.execute(
            "UPDATE edges SET dst_path=?, dst_name=?, external=NULL, status='ok' "
            "WHERE id=?", (path, name, row_id),
        )

    def _set_external(self, row_id, name):
        self.db.execute(
            "UPDATE edges SET dst_path=NULL, dst_name=NULL, external=?, status='ok' "
            "WHERE id=?", (name, row_id),
        )

    # ── reads ───────────────────────────────────────────────────────────

    def entities(self, path: str | None = None) -> list[Entity]:
        sql = "SELECT path,name,kind,line,recipe,recipe_version FROM entities"
        args: tuple = ()
        if path is not None:
            sql += " WHERE path=?"
            args = (path,)
        return [
            Entity(Addr(p, n), k, l, r, v)
            for p, n, k, l, r, v in sorted(self.db.execute(sql, args))
        ]

    def outgoing(self, src: Addr, kind: str | None = None) -> list[Edge]:
        sql = ("SELECT src_path,src_name,kind,dst_path,dst_name,external,line,"
               "recipe,recipe_version FROM edges "
               "WHERE src_path=? AND src_name=? AND status!='dropped'")
        args = [src.path, src.name]
        if kind is not None:
            sql += " AND kind=?"
            args.append(kind)
        return self._edges(sql, args)

    def incoming(self, dst: Addr | External, kind: str | None = None) -> list[Edge]:
        if isinstance(dst, External):
            sql = ("SELECT src_path,src_name,kind,dst_path,dst_name,external,line,"
                   "recipe,recipe_version FROM edges "
                   "WHERE external=? AND status!='dropped'")
            args = [dst.name]
        else:
            sql = ("SELECT src_path,src_name,kind,dst_path,dst_name,external,line,"
                   "recipe,recipe_version FROM edges "
                   "WHERE dst_path=? AND dst_name=? AND status!='dropped'")
            args = [dst.path, dst.name]
        if kind is not None:
            sql += " AND kind=?"
            args.append(kind)
        return self._edges(sql, args)

    def _edges(self, sql, args) -> list[Edge]:
        rows = sorted(self.db.execute(sql, args))
        return [
            Edge(
                src=Addr(sp, sn),
                dst=Addr(dp, dn) if dp is not None else External(ext),
                kind=k, line=line, recipe=r, recipe_version=v,
            )
            for sp, sn, k, dp, dn, ext, line, r, v in rows
        ]
