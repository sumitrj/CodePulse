"""The oracle: what did the gold patch actually have to change?

Deliberately built on stdlib `ast` and a diff parser of its own, and
deliberately *not* on the CodePulse engine or on `codepulse.delta`. A grader
that shares a parser with the thing it grades cannot catch that parser being
wrong — an entity the engine cannot see has to count against the engine, not
vanish from the denominator. See specs/bench/SPEC.md AC2.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

MODULE_SCOPE = "<module>"        # mirrors codepulse.engine.MODULE_SCOPE by value


class PatchError(Exception):
    """The gold patch did not apply against the checkout — instance is unusable."""


@dataclass(frozen=True)
class FileDiff:
    path: str                    # new-side path, or old-side for a deletion
    status: str                  # "added" | "modified" | "deleted"
    old_changed: frozenset[int]  # old-side line numbers the patch removed or replaced
    insert_anchors: frozenset[int]   # old-side lines an insertion sits against
    hunks: tuple[tuple[int, tuple[str, ...]], ...]   # (old_start, body lines)


@dataclass(frozen=True)
class Truth:
    files: frozenset[str]
    entities: frozenset[tuple[str, str]]
    skipped_files: frozenset[str]
    added_entities: frozenset[tuple[str, str]] = frozenset()
    # Entities the fix *created*. They are genuinely part of what changed, so
    # they stay in `entities` — but nothing that reads the base checkout can
    # retrieve a function that does not exist there yet. Scoring entity recall
    # against them would charge every arm for an impossibility, so metrics
    # takes them out of the denominator and reports them as unreachable.

    @property
    def reachable_entities(self) -> frozenset[tuple[str, str]]:
        return self.entities - self.added_entities


def is_test_path(path: str) -> bool:
    """AC6. Gold patches are supposed to be fix-only, but a few carry test
    edits; counting those as targets would credit an arm for finding tests."""
    parts = path.split("/")
    name = parts[-1]
    if any(part in ("tests", "test", "testing") for part in parts[:-1]):
        return True
    return (name.startswith("test_") or name.endswith("_test.py")
            or name == "conftest.py")


# ── diff parsing ──────────────────────────────────────────────────────

def parse_patch(text: str) -> list[FileDiff]:
    """Unified diff -> per-file old-side change coordinates.

    Only the old side is authoritative for "what had to change": the entity
    that must be edited is the one that exists in the base checkout (AC3).
    """
    files: list[FileDiff] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("--- "):
            index += 1
            continue
        old_path = line[4:].strip()
        if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
            index += 1
            continue
        new_path = lines[index + 1][4:].strip()
        index += 2
        status = ("added" if old_path == "/dev/null" else
                  "deleted" if new_path == "/dev/null" else "modified")
        path = _strip_prefix(old_path if status == "deleted" else new_path)
        old_changed: set[int] = set()
        anchors: set[int] = set()
        hunks: list[tuple[int, tuple[str, ...]]] = []
        while index < len(lines) and lines[index].startswith("@@"):
            old_start, index, body = _read_hunk(lines, index)
            hunks.append((old_start, tuple(body)))
            old_line = old_start
            for entry in body:
                marker = entry[:1]
                if marker == "-":
                    old_changed.add(old_line)
                    old_line += 1
                elif marker == "+":
                    # The insertion sits between old_line-1 and old_line. Anchor
                    # it on the preceding line: appending inside a body is far
                    # more common than inserting between two definitions, and a
                    # wholly new definition is caught by name instead (AC3a).
                    anchors.add(max(old_line - 1, 1))
                else:
                    old_line += 1
        files.append(FileDiff(path, status, frozenset(old_changed),
                             frozenset(anchors), tuple(hunks)))
    return files


def _strip_prefix(path: str) -> str:
    path = path.split("\t")[0].strip()
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _read_hunk(lines: list[str], index: int) -> tuple[int, int, list[str]]:
    header = lines[index]
    old_start = _hunk_old_start(header)
    index += 1
    body: list[str] = []
    while index < len(lines):
        entry = lines[index]
        if entry.startswith(("@@", "--- ", "diff --git", "index ")):
            break
        if entry.startswith("\\"):          # "\ No newline at end of file"
            index += 1
            continue
        if entry[:1] not in (" ", "+", "-", ""):
            break
        body.append(entry if entry else " ")
        index += 1
    return old_start, index, body


def _hunk_old_start(header: str) -> int:
    try:
        old = header.split("@@")[1].strip().split(" ")[0]      # "-242,7"
        return int(old.lstrip("-").split(",")[0])
    except (IndexError, ValueError):
        return 1


OFFSET_WINDOW = 400        # how far to hunt for a hunk that moved


def _locate(old_lines: list[str], old_start: int, body: tuple[str, ...],
            path: str) -> int:
    """Find where this hunk's old side really sits, 1-based.

    Hunk headers drift: a patch authored against a nearby revision still
    applies, just at an offset — the familiar "Hunk #1 succeeded at 968
    (offset 4 lines)". `git apply` and `patch(1)` both search for the context
    rather than trusting the header, and so must we, or every offset patch
    attributes its changes to whatever entity happens to live at the stated
    line. This searches for an *exact* match of the whole old side at a shifted
    position — it is offset tolerance, not fuzz: no context line is ever
    ignored, so a patch that genuinely does not belong to this file still fails.
    """
    expected = [entry[1:] for entry in body if entry[:1] in (" ", "-")]
    if not expected:
        return old_start
    declared = max(old_start - 1, 0)
    span = len(expected)
    for shift in range(OFFSET_WINDOW + 1):
        for start in ((declared,) if shift == 0 else (declared + shift, declared - shift)):
            if 0 <= start and start + span <= len(old_lines) \
                    and old_lines[start:start + span] == expected:
                return start + 1
    raise PatchError(
        f"{path}: hunk declared at line {old_start} matches nowhere within "
        f"{OFFSET_WINDOW} lines — the checkout does not correspond to this patch")


def locate_hunks(old_text: str, diff: FileDiff) -> FileDiff:
    """Re-anchor a diff onto the real file, correcting every line number."""
    old_lines = old_text.splitlines()
    hunks, changed, anchors = [], set(), set()
    for old_start, body in diff.hunks:
        start = _locate(old_lines, old_start, body, diff.path)
        hunks.append((start, body))
        line = start
        for entry in body:
            marker = entry[:1]
            if marker == "-":
                changed.add(line)
                line += 1
            elif marker == "+":
                anchors.add(max(line - 1, 1))
            else:
                line += 1
    return FileDiff(diff.path, diff.status, frozenset(changed),
                    frozenset(anchors), tuple(hunks))


def apply_hunks(old_text: str, diff: FileDiff) -> str:
    """Reconstruct the new-side content, so we can name entities the fix added.

    Strict about content, tolerant about position: every context and removed
    line must match the base exactly, but the hunk is allowed to have moved
    (see `_locate`). Anything else is skipped, never fuzzed.
    """
    old_lines = old_text.splitlines()
    out: list[str] = []
    cursor = 0                                     # 0-based index into old_lines
    for old_start, body in diff.hunks:
        start = max(_locate(old_lines, old_start, body, diff.path) - 1, 0)
        if start < cursor:
            raise PatchError(f"{diff.path}: overlapping hunks at {old_start}")
        out.extend(old_lines[cursor:start])
        cursor = start
        for entry in body:
            marker, payload = entry[:1], entry[1:]
            if marker == "+":
                out.append(payload)
            elif marker == "-":
                if cursor >= len(old_lines) or old_lines[cursor] != payload:
                    raise PatchError(
                        f"{diff.path}: removed line {cursor + 1} does not match base")
                cursor += 1
            else:
                if cursor >= len(old_lines) or old_lines[cursor] != payload:
                    raise PatchError(
                        f"{diff.path}: context line {cursor + 1} does not match base")
                out.append(old_lines[cursor])
                cursor += 1
    out.extend(old_lines[cursor:])
    return "\n".join(out) + ("\n" if old_text.endswith("\n") else "")


# ── ast attribution ───────────────────────────────────────────────────

@dataclass(frozen=True)
class Span:
    name: str        # dotted: "Class.method", "Outer.Inner.method"
    start: int
    end: int
    depth: int       # nesting depth; the innermost span wins attribution


def spans(source: str) -> list[Span]:
    """Every function and class in the file, dotted-named the way the engine
    names them (AC5), with the line range each one owns."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found: list[Span] = []

    def walk(node, prefix: str, depth: int) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                end = getattr(child, "end_lineno", child.lineno) or child.lineno
                found.append(Span(name, start, end, depth))
                walk(child, f"{name}.", depth + 1)
            else:
                walk(child, prefix, depth)

    walk(tree, "", 0)
    return found


def enclosing(spans_: list[Span], line: int) -> str:
    """Innermost function/class holding `line`, else MODULE_SCOPE (AC4).

    Innermost only: crediting `Class` as well as `Class.method` would inflate
    the truth set and make recall look better than it is.
    """
    best: Span | None = None
    for span in spans_:
        if span.start <= line <= span.end:
            if best is None or span.depth > best.depth:
                best = span
    return best.name if best else MODULE_SCOPE


def truth_for(instance, checkout_dir: Path) -> Truth:
    """Ground truth for one instance. Raises PatchError when unusable."""
    checkout_dir = Path(checkout_dir)
    diffs = parse_patch(instance.patch)
    if not diffs:
        raise PatchError(f"{instance.instance_id}: gold patch parsed to nothing")
    files: set[str] = set()
    entities: set[tuple[str, str]] = set()
    skipped: set[str] = set()
    added: set[tuple[str, str]] = set()
    for diff in diffs:
        if not diff.path.endswith(".py"):
            skipped.add(diff.path)          # the map covers more, the oracle needs ast
            continue
        if is_test_path(diff.path):
            skipped.add(diff.path)
            continue
        files.add(diff.path)
        if diff.status == "added":
            entities.add((diff.path, MODULE_SCOPE))
            continue
        source_path = checkout_dir / diff.path
        if not source_path.is_file():
            raise PatchError(f"{instance.instance_id}: {diff.path} missing from checkout")
        old_text = source_path.read_text(errors="replace")
        old_spans = spans(old_text)
        # Re-anchor before attributing: a hunk header that drifted would
        # otherwise blame whatever entity happens to sit at the stated line.
        diff = locate_hunks(old_text, diff)
        for line in sorted(diff.old_changed | diff.insert_anchors):
            entities.add((diff.path, enclosing(old_spans, line)))
        if diff.status == "modified":
            # Anything the fix introduced under a brand-new name is a target in
            # its own right, and anchoring can't find it — name it directly.
            new_names = {s.name for s in spans(apply_hunks(old_text, diff))}
            for name in sorted(new_names - {s.name for s in old_spans}):
                entities.add((diff.path, name))
                added.add((diff.path, name))
    if not files:
        raise PatchError(
            f"{instance.instance_id}: gold patch touches no non-test Python file")
    return Truth(frozenset(files), frozenset(entities), frozenset(skipped),
                 frozenset(added))
