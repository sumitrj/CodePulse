"""PR delta — blast radius at diff grain.

`six.what_changed` answers at file grain: touch one line and every entity in
that file is reported changed. This verb reads the diff itself, so only the
entities whose lines the diff actually moved are reported — each classified
added / modified / deleted, each carrying its own radius, its cross-language
dependents, and a load-bearing warning.

Spans are not persisted (the map stores an entity's start line, not its
extent), so changed files are re-parsed through their own recipe to recover
them: the recipe is already the authority on what an entity is, and re-reading
two files costs less than a schema migration. Everything else — dependents,
fan-in, provenance — is read off the graph. See specs/delta/SPEC.md.
"""
from __future__ import annotations

import fnmatch
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import six
from .engine import SCOPES, Addr, Engine
from .recipes import Recipe, compile_query, parser_for, run_matches

LOAD_BEARING_FAN_IN = 3        # direct impact fan-in at or above this flags a warning

# The same three kinds six calls impact — a caller, a reader, an heir all break
# when the thing they name changes shape. `imports` is deliberately absent: it
# lives at module scope and would make every symbol look file-sized.
_IMPACT = ("calls", "reads", "inherits")

_DEV_NULL = "/dev/null"
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_QUERIES: dict[tuple[str, str], object] = {}     # (language, query source) -> compiled


@dataclass(frozen=True)
class FileDiff:
    path: str                              # new-side path; old-side when the file was deleted
    status: str                            # "added" | "modified" | "deleted"
    added: tuple[tuple[int, int], ...]     # inclusive runs of + lines, new-file coordinates
    removed: tuple[tuple[int, int], ...]   # inclusive runs of - lines, old-file coordinates


@dataclass(frozen=True)
class Touched:
    addr: Addr
    kind: str                              # entity kind from the graph
    change: str                            # "added" | "modified" | "deleted"
    radius: dict[Addr, int]                # dependent -> hops; for deleted: entities still
                                           # referencing the gone name, at hop 1
    load_bearing: bool


@dataclass(frozen=True)
class DeltaReport:
    files: tuple[FileDiff, ...]
    touched: tuple[Touched, ...]
    at_risk: dict[Addr, int]               # union of touched radii, min hops, touched excluded
    cross_language: dict[str, tuple[str, ...]]   # changed path -> labels of dependents
                                                 # whose file runs under a different recipe


# ── the diff ──────────────────────────────────────────────────────────

@dataclass
class _Draft:
    """One in-progress file block. Paths are None until their header is seen
    and "" for /dev/null, which is how added and deleted files announce
    themselves."""
    old: str | None = None
    new: str | None = None
    fallback: str = ""                     # path read off a `diff --git` header
    rename_from: str = ""
    rename_to: str = ""
    added: list[list[int]] = field(default_factory=list)
    removed: list[list[int]] = field(default_factory=list)
    hunks: int = 0
    old_line: int = 0
    new_line: int = 0
    left_old: int = 0                      # body lines the hunk header still promises
    left_new: int = 0


def _side(text: str) -> str:
    """A `---`/`+++` path: tab-terminated, optionally `a/`- or `b/`-prefixed."""
    path = text.split("\t")[0].strip()
    if path == _DEV_NULL:
        return ""
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _header_path(line: str) -> str:
    """The new-side path of a `diff --git a/x b/x` line. Only a fallback: every
    block that carries hunks also carries `---`/`+++`, which are unambiguous."""
    tail = line.split()[-1]
    return tail[2:] if tail.startswith("b/") else tail


def _extend(runs: list[list[int]], line: int) -> None:
    if runs and runs[-1][1] == line - 1:
        runs[-1][1] = line
    else:
        runs.append([line, line])


def _consume(draft: _Draft, line: str) -> None:
    """One hunk-body line. Context lines advance both counters and are recorded
    on neither side — a context line is what the diff is *not* about."""
    mark = line[:1]
    if mark == "\\":                       # "\ No newline at end of file"
        return
    if mark == "+":
        _extend(draft.added, draft.new_line)
        draft.new_line += 1
        draft.left_new -= 1
    elif mark == "-":
        _extend(draft.removed, draft.old_line)
        draft.old_line += 1
        draft.left_old -= 1
    else:
        draft.old_line += 1
        draft.new_line += 1
        draft.left_old -= 1
        draft.left_new -= 1


def _seal(draft: _Draft | None) -> list[FileDiff]:
    if draft is None:
        return []
    if not draft.hunks and draft.rename_to:
        # A pure rename carries no hunks, so no lines are touched either way.
        # Reported as delete + add: identity carry-over is out of scope.
        return [FileDiff(draft.rename_from, "deleted", (), ()),
                FileDiff(draft.rename_to, "added", (), ())]
    path = draft.new or draft.old or draft.fallback
    if not path:
        return []                          # metadata-only block: mode change, binary
    status = "added" if draft.old == "" else "deleted" if draft.new == "" else "modified"
    return [FileDiff(
        path=path, status=status,
        added=tuple(tuple(run) for run in draft.added),
        removed=tuple(tuple(run) for run in draft.removed),
    )]


def parse_diff(text: str) -> list[FileDiff]:
    """Unified diff -> per-file changed line runs.

    Header lines are only headers outside a hunk body: a removed line reading
    `-- still here` renders as `--- still here`, indistinguishable from a file
    header by shape alone. The hunk header's own line counts say where the body
    ends, so that ambiguity never has to be guessed at.
    """
    files: list[FileDiff] = []
    draft: _Draft | None = None
    for raw in text.splitlines():
        if draft is not None and (draft.left_old > 0 or draft.left_new > 0):
            _consume(draft, raw)
            continue
        if raw.startswith("@@"):
            match = _HUNK.match(raw)
            if match and draft is not None:
                old_start, old_count, new_start, new_count = match.groups()
                draft.old_line, draft.new_line = int(old_start), int(new_start)
                draft.left_old = 1 if old_count is None else int(old_count)
                draft.left_new = 1 if new_count is None else int(new_count)
                draft.hunks += 1
        elif raw.startswith("diff --git "):
            files += _seal(draft)
            draft = _Draft(fallback=_header_path(raw))
        elif raw.startswith("--- "):
            if draft is None or draft.old is not None or draft.hunks:
                files += _seal(draft)
                draft = _Draft()
            draft.old = _side(raw[4:])
        elif raw.startswith("+++ "):
            if draft is None:
                draft = _Draft()
            draft.new = _side(raw[4:])
        elif raw.startswith("rename from "):
            if draft is not None:
                draft.rename_from = _side(raw[len("rename from "):])
        elif raw.startswith("rename to "):
            if draft is not None:
                draft.rename_to = _side(raw[len("rename to "):])
        elif draft is not None and draft.hunks and raw[:1] in ("+", "-", " "):
            _consume(draft, raw)           # hunk header undercounted its body
    return files + _seal(draft)


def _touches(runs: tuple[tuple[int, int], ...], first: int, last: int) -> bool:
    return any(start <= last and first <= end for start, end in runs)


# ── spans, recovered from the recipe ──────────────────────────────────

def _recipe_for(engine: Engine, path: str) -> Recipe | None:
    """Which recipe owns a path, by name alone — a deleted file has no bytes on
    disk left to consult."""
    name = path.rsplit("/", 1)[-1]
    for recipe in engine.recipes:
        if any(fnmatch.fnmatch(name, pat) for pat in recipe.matches):
            return recipe
    return None


def _query(recipe: Recipe, source: str):
    key = (recipe.language, source)
    if key not in _QUERIES:
        _QUERIES[key] = compile_query(recipe.language, source)
    return _QUERIES[key]


def _spans(recipe: Recipe, source: str) -> dict[str, tuple[str, int, int]]:
    """qualname -> (kind, first line, last line).

    Nesting is derived exactly as the engine derives it — innermost enclosing
    span wins the parent slot — so the qualnames here are the same strings the
    graph stores, and an Addr built from one resolves.
    """
    root = parser_for(recipe.language).parse(source.encode()).root_node
    found = []
    for rule in recipe.entities:
        for captures in run_matches(_query(recipe, rule.query), root):
            name_nodes = captures.get("name") or []
            if not name_nodes:
                continue
            node = (captures.get("node") or [name_nodes[0]])[0]
            found.append((node.start_byte, node.end_byte,
                          ".".join(n.text.decode() for n in name_nodes), rule.kind,
                          node.start_point[0] + 1, node.end_point[0] + 1))
    found.sort(key=lambda f: (f[0], -f[1]))

    spans: dict[str, tuple[str, int, int]] = {}
    stack: list[tuple[int, int, str]] = []
    for start, end, simple, kind, first, last in found:
        while stack and not (stack[-1][0] <= start and end <= stack[-1][1]):
            stack.pop()
        parent = stack[-1][2] if stack else None
        qual = f"{parent}.{simple}" if parent else simple
        stack.append((start, end, qual))
        spans.setdefault(qual, (kind, first, last))
    return spans


def _head_spans(engine: Engine, path: str) -> dict[str, tuple[str, int, int]]:
    recipe = _recipe_for(engine, path)
    if recipe is None:
        return {}
    try:
        source = (engine.root / path).read_text()
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return _spans(recipe, source)


def _base_spans(engine: Engine, path: str, source: str) -> dict[str, tuple[str, int, int]]:
    recipe = _recipe_for(engine, path)
    return _spans(recipe, source) if recipe is not None else {}


# ── the graph's answers ───────────────────────────────────────────────

def _direct_dependents(engine: Engine, addr: Addr) -> set[Addr]:
    """Who names this entity directly. Module scope doesn't count: an import at
    the top of a file is the file depending on it, not a symbol."""
    return {
        edge.src for kind in _IMPACT for edge in engine.incoming(addr, kind=kind)
        if edge.src.name not in SCOPES and edge.src != addr
    }


def _still_referencing(engine: Engine, addr: Addr) -> set[Addr]:
    """Head entities whose references still name an entity the diff removed.

    The addr is gone from the graph, so there is nothing left to ask `incoming`
    about: its former callers now point at an external of the same name, or at
    whatever else that name happens to resolve to. Matching the reference text
    is the honest claim available — "something here still says `legacy`" — and
    it is the claim a reviewer needs.
    """
    simple = addr.name.rsplit(".", 1)[-1]
    found: set[Addr] = set()
    for src_path, src_name, external, target in engine.db.execute(
            "SELECT src_path, src_name, external, target FROM edges "
            "WHERE status!='dropped' AND kind IN ('calls','reads','inherits')"):
        if src_name in SCOPES:
            continue
        for text in (external, target):
            if not text:
                continue
            if text in (addr.name, simple) or text.endswith("." + simple):
                src = Addr(src_path, src_name)
                if src != addr:
                    found.add(src)
                break
    return found


def _cross_language(engine: Engine, paths) -> dict[str, tuple[str, ...]]:
    """Dependents of a changed file that a *different* recipe extracted.

    These are the ones a language-scoped review misses: nothing in a Python
    diff hints that a Dockerfile copies the file being renamed.
    """
    out: dict[str, tuple[str, ...]] = {}
    for path in paths:
        recipe = _recipe_for(engine, path)
        mine = recipe.name if recipe else ""
        labels = set()
        for entity in engine.entities(path):
            for edge in engine.incoming(entity.addr):
                if edge.kind == "contains" or edge.src.path == path:
                    continue
                theirs = _recipe_for(engine, edge.src.path)
                if (theirs.name if theirs else "") != mine:
                    labels.add(six._label(edge.src))
        if labels:
            out[path] = tuple(sorted(labels))
    return out


def compute_delta(engine: Engine, diff_text: str,
                  base_files: dict[str, str] | None = None) -> DeltaReport:
    files = tuple(parse_diff(diff_text))
    base_files = base_files or {}
    kinds = {entity.addr: entity.kind for entity in engine.entities()}

    touched: list[Touched] = []
    for diff in files:
        head = {} if diff.status == "deleted" else _head_spans(engine, diff.path)
        base = (_base_spans(engine, diff.path, base_files[diff.path])
                if diff.path in base_files else None)

        # An entity is touched from either side: the head span the + lines land
        # in, and the base span the - lines came out of. Both are needed — a
        # pure deletion inside a function adds no line to intersect, and a
        # removed function has no head span at all.
        change: dict[str, str] = {}
        for qual, (_kind, first, last) in head.items():
            if not _touches(diff.added, first, last):
                continue
            if diff.status == "added" or (base is not None and qual not in base):
                change[qual] = "added"
            else:
                change[qual] = "modified"
        if base is not None:
            for qual, (_kind, first, last) in base.items():
                if _touches(diff.removed, first, last):
                    change.setdefault(qual, "modified" if qual in head else "deleted")

        for qual in sorted(change):
            addr = Addr(diff.path, qual)
            kind = kinds.get(addr) or (head.get(qual) or base.get(qual, ("", 0, 0)))[0]
            if change[qual] == "added":
                # Nothing can be at risk from a name that did not exist before.
                radius: dict[Addr, int] = {}
            elif change[qual] == "deleted":
                radius = {src: 1 for src in _still_referencing(engine, addr)}
            else:
                radius = six.compute_radius(engine, addr)
            fan_in = (len(radius) if change[qual] == "deleted"
                      else len(_direct_dependents(engine, addr)))
            touched.append(Touched(addr=addr, kind=kind, change=change[qual],
                                   radius=radius,
                                   load_bearing=fan_in >= LOAD_BEARING_FAN_IN))

    at_risk: dict[Addr, int] = {}
    for item in touched:
        for ref, hops in item.radius.items():
            at_risk[ref] = min(hops, at_risk.get(ref, hops))
    for item in touched:                   # the changed entities are the cause, not the risk
        at_risk.pop(item.addr, None)

    return DeltaReport(
        files=files,
        touched=tuple(touched),
        at_risk=at_risk,
        cross_language=_cross_language(engine, [d.path for d in files]),
    )


# ── git as the source of both sides ───────────────────────────────────

def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(root), *args],
                                capture_output=True, text=True, timeout=60)
    except (OSError, UnicodeDecodeError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def git_delta(engine: Engine, base: str = "HEAD") -> DeltaReport:
    """The delta a reviewer actually has: working tree against a revision.

    Base contents come from git too, one `git show` per changed file, so
    deleted entities are detectable without the caller carrying the old tree
    around.
    """
    # `base` arrives from an agent: an option-shaped value like
    # "--output=<path>" would turn this read into a file write.
    if base.startswith("-") or _git(engine.root, "rev-parse", "--verify", "--quiet",
                                     "--end-of-options", f"{base}^{{commit}}") is None:
        raise ValueError(f"not a git revision: {base!r}")
    diff_text = _git(engine.root, "diff", "--no-color", "--no-ext-diff", base, "--") or ""
    base_files: dict[str, str] = {}
    for diff in parse_diff(diff_text):
        if diff.status == "added":
            continue
        source = _git(engine.root, "show", f"{base}:{diff.path}")
        if source is not None:
            base_files[diff.path] = source
    return compute_delta(engine, diff_text, base_files=base_files)


# ── the spoken answer ─────────────────────────────────────────────────

def delta_text(engine: Engine, diff_text: str | None = None, base: str = "HEAD",
               base_files: dict[str, str] | None = None) -> str:
    report = (compute_delta(engine, diff_text, base_files=base_files)
              if diff_text is not None else git_delta(engine, base))
    if not report.touched:
        return "The diff touches nothing on the map."

    count, file_count, risk = len(report.touched), len(report.files), len(report.at_risk)
    lines = [
        f"The diff touches {count} {'entity' if count == 1 else 'entities'} in "
        f"{file_count} {'file' if file_count == 1 else 'files'}; "
        + (f"{risk} {'entity' if risk == 1 else 'entities'} at risk."
           if risk else "nothing downstream is at risk.")
    ]
    for item in report.touched:
        lines.append(f"  {six._label(item.addr)} [{item.kind}] — {item.change}"
                     + (", load-bearing." if item.load_bearing else "."))
        if item.change == "deleted":
            if item.radius:
                named = ", ".join(sorted(six._label(a) for a in item.radius))
                lines.append(f"      still referenced by {named} — the name is gone.")
            else:
                lines.append("      nothing on the map still references it.")
            continue
        if not item.radius:
            lines.append("      new — nothing depended on it before this diff."
                         if item.change == "added"
                         else "      nothing on the map depends on it.")
            continue
        by_hop = defaultdict(list)
        for ref, hops in item.radius.items():
            by_hop[hops].append(six._label(ref))
        for hops in sorted(by_hop):
            lines.append(f"      [{hops} hop{'s' if hops > 1 else ''}] "
                         + ", ".join(sorted(by_hop[hops])))
    for path, labels in sorted(report.cross_language.items()):
        lines.append(f"  {path} is depended on across languages by "
                     + ", ".join(labels) + ".")
    return "\n".join(lines)
