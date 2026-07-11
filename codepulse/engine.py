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
import posixpath
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


EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".pytest_cache", "dist", "build", ".codepulse", ".claude",
}

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


def _join_module(module: str, member: str) -> str:
    return module + member if module.endswith(".") else f"{module}.{member}"


def _normalize(text: str, src_path: str) -> str | None:
    """Resolve a path-style reference relative to its file; never escape the repo."""
    p = posixpath.normpath(posixpath.join(posixpath.dirname(src_path), text))
    if p.startswith(("../", "/")) or p == "..":
        return None
    return p


class Engine:
    def __init__(self, db_path: Path, recipes: Sequence[Recipe], root: Path):
        self.root = Path(root)
        self.recipes = list(recipes)
        # one writer at a time by design; the panel server hands the engine
        # to its handler thread, so don't pin the connection to this one
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.executescript(_SCHEMA)
        self._compiled = {}

    # ── extraction ──────────────────────────────────────────────────────

    def apply(self) -> ApplyReport:
        return self._extract(self._scan())

    def update(self, changed: Iterable[Path]) -> ApplyReport:
        return self._extract(sorted(Path(p) for p in changed))

    def refresh(self) -> ApplyReport:
        """The freshness primitive: re-extract hash-changed files, forget deleted ones."""
        known = dict(self.db.execute("SELECT path, hash FROM files"))
        on_disk = {}
        for path in self._scan():
            rel = path.relative_to(self.root).as_posix()
            try:
                on_disk[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
        changed = [self.root / rel for rel, digest in sorted(on_disk.items())
                   if known.get(rel) != digest]
        for rel in sorted(set(known) - set(on_disk)):
            self.db.execute("DELETE FROM entities WHERE path=?", (rel,))
            self.db.execute("DELETE FROM edges WHERE src_path=?", (rel,))
            self.db.execute("DELETE FROM files WHERE path=?", (rel,))
        return self._extract(changed)

    def _scan(self) -> list[Path]:
        return sorted(
            p for p in self.root.rglob("*")
            if p.is_file()
            and not any(part in EXCLUDE_DIRS for part in p.relative_to(self.root).parts)
            and self._recipe_for(p) is not None
        )

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
                span_node = (captures.get("node") or [name_nodes[0]])[0]
                found.append((
                    span_node.start_byte, span_node.end_byte,
                    ".".join(n.text.decode() for n in name_nodes), rule.kind,
                    name_nodes[0].start_point[0] + 1,
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
                target = _join_module(module, member) if member else module
                self._insert_edge(
                    rel, "<module>", "imports", target,
                    module_nodes[0].start_point[0] + 1,
                    meta=json.dumps({"local": local, "module": module,
                                     "member": member, "style": rule.style}),
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
                                         "unresolved": rule.unresolved,
                                         "resolve": rule.resolve}),
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

        bindings: dict[str, dict[str, tuple[str, str | None, str]]] = {}
        for src_path, meta in self.db.execute(
                "SELECT src_path, meta FROM edges WHERE kind='imports'"):
            info = json.loads(meta)
            bindings.setdefault(src_path, {})[info["local"]] = (
                info["module"], info["member"], info.get("style", "module"))

        for row_id, src_path, target, meta in self.db.execute(
                "SELECT id, src_path, target, meta FROM edges WHERE kind='imports'").fetchall():
            info = json.loads(meta)
            suffix = suffix_of(src_path)
            style = info.get("style", "module")
            member = info["member"]
            dst = None
            for mod_path in self._module_candidates(info["module"], src_path, suffix, style):
                if member and (mod_path, member) in ents:
                    dst = (mod_path, member)
                    break
                if not member and mod_path in files:
                    dst = (mod_path, "<module>")
                    break
            if dst is None and member and style == "module":
                mod2 = self._module_path(_join_module(info["module"], member), src_path, suffix)
                if mod2 in files:
                    dst = (mod2, "<module>")
            if dst is not None:
                self._set_dst(row_id, *dst)
            else:
                self._set_external(row_id, target)

        for row_id, src_path, src_name, target, meta in self.db.execute(
                "SELECT id, src_path, src_name, target, meta FROM edges "
                "WHERE kind NOT IN ('imports','contains')").fetchall():
            cfg = json.loads(meta)
            strategy = cfg.get("resolve", "lexical")
            only = tuple(cfg.get("only") or ())
            if strategy == "path":
                addr = self._path_lookup(target, src_path, suffix_of(src_path), files)
            elif strategy == "name":
                addr = self._name_lookup(target, ents, only)
            else:
                addr = self._resolve_target(
                    target, src_path, src_name,
                    bindings.get(src_path, {}), ents, files, suffix_of(src_path),
                )
            if addr is not None and only and strategy != "path" \
                    and ents.get(addr) not in only:
                addr = None
            if addr is not None:
                self._set_dst(row_id, *addr)
            elif cfg.get("unresolved", "external") == "external":
                self._set_external(row_id, target)
            else:
                self.db.execute("UPDATE edges SET status='dropped' WHERE id=?", (row_id,))

    def _resolve_target(self, target, src_path, src_name, binds, ents, files, suffix):
        if target.startswith("self.") and "." in src_name:
            cls = src_name.rsplit(".", 1)[0]
            cand = (src_path, f"{cls}.{target[5:]}")
            return cand if cand in ents else None
        head, _, rest = target.partition(".")
        if head in binds:
            module, member, style = binds[head]
            name = f"{member}.{rest}" if member and rest else (member or rest or None)
            for mod_path in self._module_candidates(module, src_path, suffix, style):
                if name and (mod_path, name) in ents:
                    return (mod_path, name)
                if not name and mod_path in files:
                    return (mod_path, "<module>")
            if member and style == "module":  # member may itself be a module of the package
                mod2 = self._module_path(_join_module(module, member), src_path, suffix)
                if rest and (mod2, rest) in ents:
                    return (mod2, rest)
                if not rest and mod2 in files:
                    return (mod2, "<module>")
            return None
        if (src_path, target) in ents:
            return (src_path, target)
        return None

    def _module_candidates(self, module, src_path, suffix, style) -> list[str]:
        if style == "path":
            p = _normalize(module, src_path)
            if p is None:
                return []
            return [p, p + suffix] if suffix else [p]
        return [self._module_path(module, src_path, suffix)]

    def _path_lookup(self, target, src_path, suffix, files):
        """Path-style reference: resolve against the repo tree itself, so an
        edge to a real file holds even when no recipe extracts that file."""
        p = _normalize(target, src_path)
        if p is None:
            return None
        for candidate in ([p, p + suffix] if suffix else [p]):
            if candidate in files or (self.root / candidate).is_file():
                return (candidate, "<module>")
        return None

    @staticmethod
    def _name_lookup(target, ents, only):
        hits = sorted(
            (p, n) for (p, n), k in ents.items()
            if (n == target or n.endswith("." + target)) and (not only or k in only)
        )
        return hits[0] if hits else None

    @staticmethod
    def _module_path(module: str, src_path: str, suffix: str) -> str:
        """Map a module name to a repo path; dot-prefixed modules resolve
        relative to the importing file's directory (Python's `.`, TS's `./`)."""
        if not module.startswith("."):
            return module.replace(".", "/") + suffix
        stripped = module.lstrip(".")
        parts = Path(src_path).parent.parts
        up = len(module) - len(stripped) - 1
        base = parts[:len(parts) - up] if up <= len(parts) else ()
        tail = stripped.replace(".", "/") if stripped else ""
        joined = "/".join([*base, tail]) if tail else "/".join(base)
        return (joined + suffix) if joined else ""

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
        rows = sorted(
            self.db.execute(sql, args),
            key=lambda r: tuple("" if v is None else v for v in r),
        )
        return [
            Edge(
                src=Addr(sp, sn),
                dst=Addr(dp, dn) if dp is not None else External(ext),
                kind=k, line=line, recipe=r, recipe_version=v,
            )
            for sp, sn, k, dp, dn, ext, line, r, v in rows
        ]
