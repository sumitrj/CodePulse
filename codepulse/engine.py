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
import os
import posixpath
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .recipes import Recipe, compile_query, parser_for, run_matches

# The two synthetic scopes. Everything a recipe extracts hangs off a file's
# MODULE_SCOPE; every file hangs off its directory's PACKAGE_SCOPE. Together
# with dotted qualnames they form one unbroken chain of containment:
# package → package → module → class → method.
MODULE_SCOPE = "<module>"
PACKAGE_SCOPE = "<package>"
SCOPES = (MODULE_SCOPE, PACKAGE_SCOPE)

# Where an entity's description came from. Ranked, because one entity can be
# offered several and the most deliberate wins: a docstring is written to be
# read, a leading comment is often an aside, and DOC_NONE means the author left
# nothing and we are showing their code back to them.
DOC_NONE = "none"
DOC_RANK = {DOC_NONE: 0, "leading": 1, "jsdoc": 2, "docstring": 3}


# Keywords a language permits between a doc block and the declaration it
# describes. Not a grammar — just the tokens that don't change the answer to
# "what is this comment about".
_DECL_MODIFIERS = {
    "export", "default", "async", "public", "private", "protected", "static",
    "abstract", "readonly", "declare", "const", "final", "override", "@staticmethod",
    "@classmethod", "@property", "@abstractmethod", "@dataclass",
}

_COMMENT_MARKERS = ("#", "//", "/*", "*/", "*", "---", "\"\"\"")
_DIVIDER = re.compile(r"[-=*~_#─]{3,}")
_DIRECTIVE = re.compile(
    r"^(type:\s*ignore|noqa|pragma|pylint:|eslint-|prettier-|@ts-|coding[:=]|-\*-)")


def _clean_comment(text: str) -> str:
    """Strip comment syntax and drop the lines that aren't about anything.

    Three kinds of comment are noise as a description, and each would be worse
    than the source slice they displace: shebangs and encoding lines (about the
    file's execution, not its meaning), tool directives (about the linter), and
    section banners like `# --- helpers ---`, which head a *group* of
    definitions and would otherwise be misread as describing whichever one
    happens to come next.
    """
    kept = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#!"):
            continue
        if _DIVIDER.search(line):
            continue
        for marker in _COMMENT_MARKERS:
            while line.startswith(marker):
                line = line[len(marker):].lstrip()
        line = line.rstrip("*/").rstrip()
        if not line or _DIRECTIVE.match(line):
            continue
        if not any(ch.isalpha() for ch in line):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _comment_runs(nodes, source_bytes):
    """Fold consecutive comment lines into one block.

    A four-line `#` preamble is one thought, not four; attaching only the last
    line would keep the least informative one. Nodes separated by anything but
    whitespace start a new run.

    A run must begin its own line. A comment trailing code — `x = {}  # note` —
    annotates the statement it sits on, so letting it start a run would hand it
    to whatever happens to be defined underneath.
    """
    ordered = sorted({(n.start_byte, n.end_byte) for n in nodes
                      if not source_bytes[source_bytes.rfind(b"\n", 0, n.start_byte) + 1:
                                          n.start_byte].strip()})
    runs = []
    for start, end in ordered:
        if runs and not source_bytes[runs[-1][1]:start].strip():
            runs[-1][1] = end
        else:
            runs.append([start, end])
    return [(start, end, _clean_comment(source_bytes[start:end].decode(errors="ignore")))
            for start, end in runs]


@dataclass(frozen=True)
class Addr:
    """One address space for every language: a file path plus a dotted name."""
    path: str
    name: str  # "Cart.checkout", "<module>", "<package>", "server.port"


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
    meta: str = ""             # what the author said about it, else a slice of its own source
    doc_kind: str = DOC_NONE   # which docs rule spoke: docstring / jsdoc / leading / none

    @property
    def doc(self) -> bool:
        """True when meta is authored prose rather than scraped source."""
        return self.doc_kind != DOC_NONE


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
    failed: tuple[str, ...] = ()      # files the recipe couldn't digest; skipped, not fatal


EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".pytest_cache", "dist", "build", ".codepulse", ".claude",
    ".tox", ".mypy_cache", ".ruff_cache", ".terraform", ".ipynb_checkpoints",
    "site-packages", "target", "vendor",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
    path TEXT PRIMARY KEY, hash TEXT, recipe TEXT,
    recipe_version TEXT DEFAULT '', stat TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS entities(
    path TEXT, name TEXT, kind TEXT, line INTEGER,
    recipe TEXT, recipe_version TEXT,
    meta TEXT DEFAULT '', doc INTEGER DEFAULT 0,
    doc_kind TEXT DEFAULT 'none',
    PRIMARY KEY(path, name));
CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(path, name, meta);
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


# `data:`, `javascript:`, `https:`, `mailto:` … — anything with a URL scheme
# names a resource, not a file in this tree.
_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_MAX_PATH_REF = 1024


def _path_ref(text: str) -> str | None:
    """The file a path-style reference names, or None when it names no file.

    Attribute values are arbitrary text. A 100 KB `data:image/png;base64,…`
    in an <img src> used to be stored as an edge target and then stat'ed,
    which the OS rejects with ENAMETOOLONG. Query strings and fragments are
    cache-busters and anchors: `app.js?v=3#top` still loads `app.js`.
    """
    text = text.strip()
    if (not text or len(text) > _MAX_PATH_REF or _URL_SCHEME.match(text)
            or text.startswith(("//", "#", "?")) or any(c.isspace() for c in text)):
        return None
    return text.split("#", 1)[0].split("?", 1)[0] or None


def _normalize(text: str, src_path: str) -> str | None:
    """Resolve a path-style reference relative to its file; never escape the repo."""
    text = _path_ref(text)
    if text is None:
        return None
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
        self._migrate()
        self.db.executescript(_SCHEMA)
        self._compiled = {}
        self.progress = None          # optional callable(done, total) for long indexes
        # precompute a fast matcher: most files are matched by a plain suffix
        # (*.py, *.ts…), so a dict lookup replaces fnmatch on every walked file.
        self._suffix_map: dict[str, Recipe] = {}
        self._other_pats: list[tuple[str, Recipe]] = []
        for recipe in self.recipes:
            for pat in recipe.matches:
                stem = pat[1:]
                if pat.startswith("*.") and not any(c in stem for c in "*?["):
                    self._suffix_map.setdefault(stem, recipe)
                else:
                    self._other_pats.append((pat, recipe))
        self._excludes = set(EXCLUDE_DIRS)
        ignore = self.root / ".codepulse" / "ignore"
        if ignore.is_file():          # repo-owner scoping: one directory name per line
            self._excludes |= {
                line.strip().rstrip("/") for line in ignore.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            }

    def _migrate(self) -> None:
        """Older databases predate the meta/doc columns; add them in place."""
        try:
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(entities)")}
        except sqlite3.Error:
            return
        if columns and "meta" not in columns:
            self.db.execute("ALTER TABLE entities ADD COLUMN meta TEXT DEFAULT ''")
            self.db.execute("ALTER TABLE entities ADD COLUMN doc INTEGER DEFAULT 0")
            columns |= {"meta", "doc"}
        if columns and "doc_kind" not in columns:
            # Older rows only recorded that prose existed, not where it came
            # from. Back-fill the one thing we can still infer, and let the
            # next extraction pass replace it with the real provenance.
            self.db.execute("ALTER TABLE entities ADD COLUMN doc_kind TEXT DEFAULT 'none'")
            self.db.execute("UPDATE entities SET doc_kind='docstring' WHERE doc=1")
        try:
            file_columns = {row[1] for row in self.db.execute("PRAGMA table_info(files)")}
        except sqlite3.Error:
            return
        if file_columns and "recipe_version" not in file_columns:
            self.db.execute("ALTER TABLE files ADD COLUMN recipe_version TEXT DEFAULT ''")
        if file_columns and "stat" not in file_columns:
            self.db.execute("ALTER TABLE files ADD COLUMN stat TEXT DEFAULT ''")

    # ── extraction ──────────────────────────────────────────────────────

    def apply(self) -> ApplyReport:
        return self._extract(self._scan())

    def update(self, changed: Iterable[Path]) -> ApplyReport:
        return self._extract(sorted(Path(p) for p in changed))

    def refresh(self) -> ApplyReport:
        """The freshness primitive. Fast path: a file whose size+mtime match
        what we stored is never opened — this is what keeps re-runs sub-second
        and the write-lock held for milliseconds. Only files whose stat or
        recipe version moved get hashed, and only a real content change
        (or a better recipe) triggers re-extraction."""
        known = {path: (stat, digest, version) for path, stat, digest, version in
                 self.db.execute("SELECT path, stat, hash, recipe_version FROM files")}
        scanned = self._scan()
        total = len(scanned)
        on_disk = set()
        changed = []
        restat = []          # content identical, only mtime moved — refresh the stamp
        for index, path in enumerate(scanned):
            if self.progress and index and index % 500 == 0:
                self.progress(index, total)
            rel = path.relative_to(self.root).as_posix()
            recipe = self._recipe_for(path)
            version = recipe.version if recipe else ""
            try:
                st = path.stat()
            except OSError:
                continue
            on_disk.add(rel)
            sig = f"{st.st_mtime_ns}:{st.st_size}"
            prior = known.get(rel)
            if prior and prior[0] == sig and prior[2] == version:
                continue                              # unchanged — never read the file
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            if prior and prior[1] == digest and prior[2] == version:
                restat.append((sig, rel))             # same bytes, just re-stamp
            else:
                changed.append(self.root / rel)
        for sig, rel in restat:
            self.db.execute("UPDATE files SET stat=? WHERE path=?", (sig, rel))
        for rel in sorted(set(known) - on_disk):
            self.db.execute("DELETE FROM entities WHERE path=?", (rel,))
            self.db.execute("DELETE FROM entities_fts WHERE path=?", (rel,))
            self.db.execute("DELETE FROM edges WHERE src_path=?", (rel,))
            self.db.execute("DELETE FROM files WHERE path=?", (rel,))
        if restat or set(known) - on_disk:
            self.db.commit()
        return self._extract(changed)

    def _scan(self) -> list[Path]:
        # Prefer git: it already knows what's source vs ignored (.gitignore),
        # returns the list from its index in milliseconds, and doesn't descend
        # into nested repos — so vendored clones are skipped for free. Walking
        # a big tree by hand was the 45s bug. Non-git repos fall back to os.walk.
        return self._git_scan() if self._is_git() else self._walk_scan()

    def _is_git(self) -> bool:
        return (self.root / ".git").exists()

    def _git_scan(self) -> list[Path]:
        try:
            res = subprocess.run(
                ["git", "-C", str(self.root), "ls-files", "-z",
                 "--cached", "--others", "--exclude-standard"],
                capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return self._walk_scan()
        if res.returncode != 0:
            return self._walk_scan()
        out = []
        for rel in res.stdout.decode("utf-8", "ignore").split("\0"):
            if not rel or rel.startswith(".."):
                continue
            parts = rel.split("/")
            if any(part in self._excludes for part in parts):
                continue
            if self._recipe_for_name(parts[-1]) is not None:
                out.append(self.root / rel)
        return sorted(out)

    def _walk_scan(self) -> list[Path]:
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in self._excludes]
            for name in filenames:
                if self._recipe_for_name(name) is not None:
                    out.append(Path(dirpath) / name)
        return sorted(out)

    def nested_repos(self) -> list[str]:
        """Vendored sub-repos under root — skipped by the git scan, surfaced so
        the user can map one directly by running codepulse inside it."""
        if not self._is_git():
            return []
        try:
            res = subprocess.run(
                ["git", "-C", str(self.root), "ls-files", "-z", "--others",
                 "--directory", "--no-empty-directory", "--exclude-standard"],
                capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return []
        found = []
        for rel in res.stdout.decode("utf-8", "ignore").split("\0"):
            rel = rel.rstrip("/")
            if rel and (self.root / rel / ".git").exists():
                found.append(rel)
        return sorted(found)

    def _recipe_for_name(self, name: str) -> Recipe | None:
        dot = name.rfind(".")
        if dot != -1:
            recipe = self._suffix_map.get(name[dot:])
            if recipe is not None:
                return recipe
        for pat, recipe in self._other_pats:      # Dockerfile, Dockerfile.* …
            if fnmatch.fnmatch(name, pat):
                return recipe
        return None

    def _recipe_for(self, path: Path) -> Recipe | None:
        return self._recipe_for_name(path.name)

    def _extract(self, paths) -> ApplyReport:
        extracted, failed = [], []
        paths = list(paths)
        for index, path in enumerate(paths):
            if self.progress and index and index % 200 == 0:
                self.progress(index, len(paths))
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
            self.db.execute("DELETE FROM entities_fts WHERE path=?", (rel,))
            self.db.execute("DELETE FROM edges WHERE src_path=?", (rel,))
            # one file the recipe chokes on costs that file, never the map
            self.db.execute("SAVEPOINT extract_file")
            ok = True
            try:
                self._extract_file(rel, source, recipe)
            except Exception:
                self.db.execute("ROLLBACK TO extract_file")
                ok = False
            self.db.execute("RELEASE extract_file")
            try:
                st = abs_path.stat()
                sig = f"{st.st_mtime_ns}:{st.st_size}"
            except OSError:
                sig = ""
            self.db.execute(
                "INSERT OR REPLACE INTO files(path, hash, recipe, recipe_version, stat) "
                "VALUES(?,?,?,?,?)",
                (rel, hashlib.sha256(source.encode()).hexdigest(),
                 recipe.name, recipe.version, sig),
            )
            # recorded either way, so a bad file is retried when it changes,
            # not on every refresh
            (extracted if ok else failed).append(rel)
        self._sync_packages()
        self._resolve_all()
        self.db.commit()
        return ApplyReport(extracted=tuple(extracted), failed=tuple(failed))

    def _query(self, recipe: Recipe, source: str):
        key = (recipe.language, source)
        if key not in self._compiled:
            self._compiled[key] = compile_query(recipe.language, source)
        return self._compiled[key]

    def _extract_file(self, rel: str, source: str, recipe: Recipe) -> None:
        root_node = parser_for(recipe.language).parse(source.encode()).root_node
        prov = (recipe.name, recipe.version)

        # entities: collect (span, name, kind), derive qualnames from nesting.
        # One name node is one entity. Rules legitimately overlap — a TS arrow
        # const is both a lexical declaration and a function — so the first rule
        # to claim a @name node keeps it and later rules are dropped before
        # nesting runs. Without this the loser lands inside the winner's span and
        # mints a ghost: `VALUE.VALUE`, `arrow.arrow`.
        found = []
        claimed = set()
        for rule in recipe.entities:
            for captures in run_matches(self._query(recipe, rule.query), root_node):
                name_nodes = captures.get("name") or []
                if not name_nodes:
                    continue
                claim = tuple((n.start_byte, n.end_byte) for n in name_nodes)
                if claim in claimed:
                    continue
                claimed.add(claim)
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
        placed = []         # (qual, kind, line, start, end, parent)
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
            placed.append((qual, kind, line, start, end, parent))

        # docs: what the author said about each entity, in their own words.
        # Two attachment modes, because prose sits in two different places:
        # docstrings live *inside* the thing they describe, while comments and
        # JSDoc sit *above* it. Ranked by DOC_RANK — a real docstring always
        # beats a comment scraped from the line above, and both beat the source
        # slice we fall back to when the author wrote nothing.
        source_bytes = source.encode()
        docs: dict[str, tuple[str, str]] = {}        # qual -> (text, doc_kind)

        def offer(owner: str, text: str, doc_kind: str) -> None:
            text = text.strip()[:400]
            if not text:
                return
            current = docs.get(owner)
            if current is None or DOC_RANK[doc_kind] > DOC_RANK[current[1]]:
                docs[owner] = (text, doc_kind)

        for rule in recipe.docs:
            nodes = [n for captures in run_matches(self._query(recipe, rule.query), root_node)
                     for n in captures.get("doc") or []]
            if rule.attach == "preceding":
                for group_start, group_end, text in _comment_runs(nodes, source_bytes):
                    owner = self._following(placed, group_end, source_bytes)
                    if owner is None and rule.scope and group_start == 0:
                        owner = MODULE_SCOPE      # file-header comment describes the file
                    if owner is not None:
                        offer(owner, text, rule.kind)
                continue
            for doc_node in nodes:
                owner = self._enclosing(spans, doc_node.start_byte)
                if owner == MODULE_SCOPE and not rule.scope:
                    continue
                offer(owner, doc_node.text.decode(), rule.kind)

        if MODULE_SCOPE in docs:
            text, doc_kind = docs[MODULE_SCOPE]
            self._set_doc(rel, MODULE_SCOPE, text, doc_kind)

        for qual, kind, line, start, end, parent in placed:
            found_doc = docs.get(qual)
            if found_doc is not None:
                meta, doc_kind = found_doc
            else:
                meta = source_bytes[start:end].decode(errors="ignore").strip()[:400]
                doc_kind = DOC_NONE
            self._insert_entity(rel, qual, kind, line, prov,
                                meta=meta, doc_kind=doc_kind)
            self._insert_edge(rel, parent or MODULE_SCOPE, "contains", "", line,
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
                # plain `import a.b.c` binds the full dotted name so a.b.c.x resolves
                local = alias or (member.split(".")[-1] if member else module)
                target = _join_module(module, member) if member else module
                self._insert_edge(
                    rel, "<module>", "imports", target,
                    module_nodes[0].start_point[0] + 1,
                    meta=json.dumps({"local": local, "module": module,
                                     "member": member, "style": rule.style,
                                     "star": rule.star}),
                    prov=prov,
                )

        # references: raw rows now, resolved by _resolve_all
        for rule in recipe.references:
            dedupe = set()
            for captures in run_matches(self._query(recipe, rule.query), root_node):
                anchor = (captures.get("node") or [None])[0]
                for tnode in captures.get("target") or []:
                    text = tnode.text.decode()
                    if rule.resolve == "path" and _path_ref(text) is None:
                        continue
                    if rule.attach == "following":
                        # written above its subject, not inside it: a decorator
                        # is called by the definition it sits on top of.
                        src = self._following(placed, (anchor or tnode).end_byte,
                                              source_bytes)
                        if src is None:
                            continue
                    else:
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

    def _following(self, placed, after: int, source_bytes: bytes) -> str | None:
        """The entity a block of prose sits directly above.

        Directly is the whole point: only whitespace may separate them. A
        comment with a blank-separated statement under it is talking about the
        section, not the next definition, and guessing wrong is worse than
        leaving the entity undescribed.
        """
        best = None
        for qual, _kind, _line, start, _end, _parent in placed:
            if start >= after and (best is None or start < best[0]):
                best = (start, qual)
        if best is None:
            return None
        gap = source_bytes[after:best[0]].decode(errors="ignore")
        # An entity's span starts at the declaration keyword, so anything the
        # language lets you write in front of one — `export`, `async`, a
        # decorator — lands in the gap. Those still mean "the very next thing",
        # so tolerate them; any other token means the prose was about something
        # in between and this entity is not its subject.
        if set(gap.split()) - _DECL_MODIFIERS:
            return None
        return best[1]

    def _set_doc(self, path: str, name: str, meta: str, doc_kind: str) -> None:
        """Describe an already-inserted entity — the synthetic <module>, which
        exists before its file-header comment has been read."""
        self.db.execute(
            "UPDATE entities SET meta=?, doc_kind=?, doc=? WHERE path=? AND name=?",
            (meta, doc_kind, int(doc_kind != DOC_NONE), path, name),
        )
        self.db.execute("INSERT INTO entities_fts(path,name,meta) VALUES(?,?,?)",
                        (path, name, meta))

    def _insert_entity(self, path, name, kind, line, prov, meta="", doc_kind=DOC_NONE):
        self.db.execute(
            "INSERT OR IGNORE INTO entities"
            "(path,name,kind,line,recipe,recipe_version,meta,doc,doc_kind) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (path, name, kind, line, *prov, meta, int(doc_kind != DOC_NONE), doc_kind),
        )
        if name not in SCOPES:          # synthetic scopes carry no text to match
            self.db.execute(
                "INSERT INTO entities_fts(path,name,meta) VALUES(?,?,?)",
                (path, name, meta),
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

    # ── packages ────────────────────────────────────────────────────────

    def _sync_packages(self) -> None:
        """Give directories a node, so containment reaches above the file.

        Extraction is per-file, so nothing in it can see that cart.py sits in
        billing/ which sits in app/. Without these nodes the geography stops at
        <module> and "what breaks" can never say which subsystem it is in.
        Derived, not extracted: rebuilt wholesale from the file table on every
        pass, since a deleted file can empty a directory. The repo root is
        deliberately skipped — a node containing everything explains nothing.
        """
        prov = ("codepulse", "1")
        self.db.execute("DELETE FROM entities WHERE name=?", (PACKAGE_SCOPE,))
        self.db.execute("DELETE FROM edges WHERE src_name=? AND kind='contains'",
                        (PACKAGE_SCOPE,))
        children: dict[str, set[tuple[str, str]]] = {}
        for (path,) in self.db.execute("SELECT path FROM files").fetchall():
            parts = path.split("/")[:-1]
            if not parts:
                continue                              # root-level file: no package
            children.setdefault("/".join(parts), set()).add((path, MODULE_SCOPE))

            for depth in range(len(parts) - 1, 0, -1):
                parent, child = "/".join(parts[:depth]), "/".join(parts[:depth + 1])
                children.setdefault(parent, set()).add((child, PACKAGE_SCOPE))
        for package in sorted(children):
            self._insert_entity(package, PACKAGE_SCOPE, "package", 0, prov)
        for package, kids in sorted(children.items()):
            for dst in sorted(kids):
                self._insert_edge(package, PACKAGE_SCOPE, "contains", "",
                                  0, "{}", prov, dst=dst)

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

        def index_of(path: str) -> str:
            recipe = recipe_by_name.get(file_recipe.get(path, ""))
            return recipe.package_index if recipe else ""

        def self_of(path: str) -> str:
            recipe = recipe_by_name.get(file_recipe.get(path, ""))
            return recipe.self_name if recipe else ""

        bindings: dict[str, dict[str, tuple[str, str | None, str]]] = {}
        # a star binding names no member, so it can't sit in the local-name
        # table; it is a whole module a bare name may have come from.
        stars: dict[str, list[tuple[str, str]]] = {}
        for src_path, meta in self.db.execute(
                "SELECT src_path, meta FROM edges WHERE kind='imports'"):
            info = json.loads(meta)
            if info.get("star"):
                stars.setdefault(src_path, []).append(
                    (info["module"], info.get("style", "module")))
                continue
            bindings.setdefault(src_path, {})[info["local"]] = (
                info["module"], info["member"], info.get("style", "module"))

        # context the re-export chaser needs, shared across both passes
        self._ctx = (ents, files, bindings, stars, suffix_of, index_of)

        for row_id, src_path, target, meta in self.db.execute(
                "SELECT id, src_path, target, meta FROM edges WHERE kind='imports'").fetchall():
            info = json.loads(meta)
            suffix, index = suffix_of(src_path), index_of(src_path)
            style = info.get("style", "module")
            member = info["member"]
            dst = None
            for mod_path in self._module_candidates(info["module"], src_path, suffix, style, index):
                if member and (mod_path, member) in ents:
                    dst = (mod_path, member)
                    break
                if member and mod_path in files:
                    dst = self._chase(mod_path, member, set())   # re-export through this module
                    if dst:
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
                    bindings.get(src_path, {}), ents, files,
                    suffix_of(src_path), index_of(src_path), self_of(src_path),
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

    def _resolve_target(self, target, src_path, src_name, binds, ents, files,
                        suffix, index="", self_name=""):
        # the instance prefix is a recipe's word (`self`, `this`): inside a
        # method it means the class the method hangs off.
        if self_name and target.startswith(self_name + ".") and "." in src_name:
            cls = src_name.rsplit(".", 1)[0]
            cand = (src_path, f"{cls}.{target[len(self_name) + 1:]}")
            return cand if cand in ents else None
        # longest bound prefix wins: `pkg.defs` beats `pkg` for `pkg.defs.alpha`
        parts = target.split(".")
        for cut in range(len(parts), 0, -1):
            prefix = ".".join(parts[:cut])
            if prefix not in binds:
                continue
            module, member, style = binds[prefix]
            rest = ".".join(parts[cut:])
            name = f"{member}.{rest}" if member and rest else (member or rest or None)
            for mod_path in self._module_candidates(module, src_path, suffix, style, index):
                if name and (mod_path, name) in ents:
                    return (mod_path, name)
                if name and mod_path in files:                # re-exported name
                    chased = self._chase(mod_path, name, set())
                    if chased:
                        return chased
                if not name and mod_path in files:
                    return (mod_path, "<module>")
            if member and style == "module":  # member may itself be a submodule
                mod2 = self._module_path(_join_module(module, member), src_path, suffix)
                if rest and (mod2, rest) in ents:
                    return (mod2, rest)
                if not rest and mod2 in files:
                    return (mod2, "<module>")
            return None
        if (src_path, target) in ents:
            return (src_path, target)
        return self._through_stars(src_path, target, {src_path})

    def _through_stars(self, mod_path, name, visited):
        """A bare name a star import could have brought in.

        `from lib import *` binds no local name the engine can see, so a call to
        one of lib's members looks unbound. Ask each starred module whether it
        defines the name — or re-exports it, which is why this goes through the
        same chaser."""
        ents, files, _bindings, stars, suffix_of, index_of = self._ctx
        if len(visited) > 8:
            return None
        for module, style in stars.get(mod_path, ()):
            for cand in self._module_candidates(module, mod_path, suffix_of(mod_path),
                                                style, index_of(mod_path)):
                if (cand, name) in ents:
                    return (cand, name)
                if cand in files and cand not in visited:
                    deeper = self._chase(cand, name, set(visited) | {mod_path})
                    if deeper:
                        return deeper
        return None

    def _chase(self, mod_path, name, visited):
        """Follow a re-exported name through a module's own imports to the real
        definition. `from pkg import X` where pkg/__init__ does `from .d import X`
        lands on d.py::X. Bounded to avoid import cycles."""
        ents, files, bindings, _stars, suffix_of, index_of = self._ctx
        if (mod_path, name) in ents:
            return (mod_path, name)
        if mod_path in visited or len(visited) > 8:
            return None
        visited.add(mod_path)
        binding = bindings.get(mod_path, {}).get(name)
        if binding is None:
            # `export * from "./m"` re-exports without naming anything
            return self._through_stars(mod_path, name, visited)
        module, member, style = binding
        for cand in self._module_candidates(module, mod_path, suffix_of(mod_path),
                                             style, index_of(mod_path)):
            if member and (cand, member) in ents:
                return (cand, member)
            if member and cand in files:
                deeper = self._chase(cand, member, visited)
                if deeper:
                    return deeper
            if not member and cand in files:
                return (cand, "<module>")
        return None

    def _module_candidates(self, module, src_path, suffix, style, index="") -> list[str]:
        if style == "path":
            p = _normalize(module, src_path)
            if p is None:
                return []
            cands = [p, p + suffix] if suffix else [p]
            if index:
                cands.append(f"{p}/{index}{suffix}")     # dir/index.ts
            return cands
        base = self._module_path(module, src_path, suffix)      # pkg.py
        cands = [base]
        if index and suffix:
            stem = self._module_path(module, src_path, "")      # pkg
            cands.append(f"{stem}/{index}{suffix}")             # pkg/__init__.py
        return cands

    def _path_lookup(self, target, src_path, suffix, files):
        """Path-style reference: resolve against the repo tree itself, so an
        edge to a real file holds even when no recipe extracts that file."""
        p = _normalize(target, src_path)
        if p is None:
            return None
        for candidate in ([p, p + suffix] if suffix else [p]):
            if candidate in files or self._is_file(candidate):
                return (candidate, "<module>")
        return None

    def _is_file(self, candidate: str) -> bool:
        """A reference target is arbitrary text, not a path we chose. Django's
        admindocs template carries a `javascript:(function(){…})()` bookmarklet
        in an href, and asking the filesystem about it raises ENAMETOOLONG —
        which used to take the whole index down. An unanswerable candidate is
        simply not a file."""
        try:
            return (self.root / candidate).is_file()
        except (OSError, ValueError):
            return False

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
        sql = ("SELECT path,name,kind,line,recipe,recipe_version,meta,doc_kind "
               "FROM entities")
        args: tuple = ()
        if path is not None:
            sql += " WHERE path=?"
            args = (path,)
        rows = sorted(self.db.execute(sql, args),
                      key=lambda r: tuple("" if v is None else v for v in r[:2]))
        return [
            Entity(Addr(p, n), k, l, r, v, meta=m or "", doc_kind=d or DOC_NONE)
            for p, n, k, l, r, v, m, d in rows
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
