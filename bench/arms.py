"""The things being compared.

Every arm gets the issue text and the repo, and none of them gets the gold
patch (specs/bench/SPEC.md AC1). Three arms ship, because a benchmark that
reports only the map is marketing:

  bm25       lexical retrieval over file text. This is the baseline because it
             is what a grep-driven agent approximates when it reads an issue
             and starts searching for words from it.
  pulse_map  seed symbols out of the issue, resolve them in the map, then walk
             the graph. The claim under test is that the walk reaches files the
             issue never names.
  hybrid     reciprocal-rank fusion of the two. If the map only helps when
             fused, that is the honest finding and this arm is how it shows up.

Candidate sets are identical across arms: non-test Python files. Tests are
excluded everywhere because the oracle excludes them, and letting an arm spend
top-k slots on files that can never be right would measure tidiness, not recall.
"""
from __future__ import annotations

import hashlib
import heapq
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from codepulse.engine import SCOPES, Addr, Engine

from .truth import is_test_path

BM25_K1 = 1.2            # standard Robertson/Sparck-Jones settings; not tuned
BM25_B = 0.75            # on this dataset, which would be fitting to the test set
RRF_K = 60               # Cormack et al.'s reciprocal-rank-fusion constant
MAX_SEEDS = 40
RADIUS_DECAY = 0.5       # a dependent two hops out is worth half a direct one
MAX_HOPS = 2
RADIUS_BUDGET = 30       # how many resolved entities get the graph walk (see below)


@dataclass(frozen=True)
class Ranking:
    files: tuple[str, ...]
    entities: tuple[tuple[str, str], ...] = ()
    seconds: float = 0.0
    detail: dict = field(default_factory=dict)


# ── shared corpus ─────────────────────────────────────────────────────

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox",
              "build", "dist", ".codepulse", ".mypy_cache", ".pytest_cache",
              "site-packages", "doc", "docs", "examples", "benchmarks"}


def candidates(checkout_dir: Path) -> list[str]:
    """Non-test Python files, repo-relative, sorted for determinism."""
    root = Path(checkout_dir)
    found: list[str] = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if any(part in _SKIP_DIRS for part in rel.split("/")[:-1]):
            continue
        if is_test_path(rel):
            continue
        found.append(rel)
    return sorted(found)


_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Identifier-aware: `get_axis_limits` and `getAxisLimits` both yield
    get/axis/limits, so an issue written in prose still matches code."""
    out: list[str] = []
    for word in _WORD.findall(text):
        lowered = word.lower()
        out.append(lowered)
        pieces = [p.lower() for p in _CAMEL.findall(word) if len(p) > 1]
        if len(pieces) > 1:
            out.extend(pieces)
        parts = [p for p in lowered.split("_") if len(p) > 1]
        if len(parts) > 1:
            out.extend(parts)
    return out


# ── arm 1: BM25 ───────────────────────────────────────────────────────

def bm25(instance, checkout_dir: Path, engine: Engine | None = None) -> Ranking:
    started = time.perf_counter()
    root = Path(checkout_dir)
    paths = candidates(root)
    docs: dict[str, Counter] = {}
    lengths: dict[str, int] = {}
    document_freq: Counter = Counter()
    for rel in paths:
        try:
            text = (root / rel).read_text(errors="replace")
        except OSError:
            continue
        # The path itself is signal — an issue about "the QDP reader" should
        # reward astropy/io/ascii/qdp.py even before its contents are read.
        counts = Counter(tokenize(rel.replace("/", " ").replace(".py", "")) * 3)
        counts.update(tokenize(text))
        docs[rel] = counts
        lengths[rel] = sum(counts.values()) or 1
        document_freq.update(counts.keys())
    if not docs:
        return Ranking((), (), time.perf_counter() - started, {"arm": "bm25"})
    total = len(docs)
    avg_len = sum(lengths.values()) / total
    query = [t for t in tokenize(instance.problem_statement) if len(t) > 2]
    weights = {
        term: math.log(1 + (total - document_freq[term] + 0.5) / (document_freq[term] + 0.5))
        for term in set(query) if document_freq.get(term)
    }
    scores: dict[str, float] = {}
    for rel, counts in docs.items():
        length = lengths[rel]
        total_score = 0.0
        for term, weight in weights.items():
            freq = counts.get(term, 0)
            if not freq:
                continue
            total_score += weight * (freq * (BM25_K1 + 1)) / (
                freq + BM25_K1 * (1 - BM25_B + BM25_B * length / avg_len))
        if total_score:
            scores[rel] = total_score
    ranked = _rank(scores)
    return Ranking(ranked, (), time.perf_counter() - started,
                   {"arm": "bm25", "corpus_files": total, "query_terms": len(weights)})


# ── arm 2: the map ────────────────────────────────────────────────────

_TRACEBACK = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')
_BACKTICK = re.compile(r"`([^`\n]{2,80})`")
_FENCED = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_INDENTED = re.compile(r"^(?: {4}|\t)(\S.*)$", re.MULTILINE)
_DOTTED = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b")
_DEFINITION = re.compile(r"\b(?:def|class)\s+([A-Za-z_]\w*)")
_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_CAMEL_WORD = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b")
_SNAKE_WORD = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def seeds(text: str) -> list[tuple[str, float]]:
    """Symbols the issue names, strongest first.

    Weights are ordered by how much the author committed to the symbol: a
    traceback frame is a fact about execution, a snake_case word in prose is a
    guess. Values are coarse on purpose — tuning them against SWE-bench would
    be fitting the benchmark.
    """
    scored: dict[str, float] = {}

    def offer(symbol: str, weight: float) -> None:
        symbol = symbol.strip().strip("().,:'\"")
        if not symbol or len(symbol) < 3 or symbol.lower() in _STOP_SYMBOLS:
            return
        scored[symbol] = max(scored.get(symbol, 0.0), weight)

    for _, _, func in _TRACEBACK.findall(text):
        offer(func, 1.0)
    for span in _BACKTICK.findall(text):
        for match in _DOTTED.findall(span) or _WORD.findall(span):
            offer(match, 0.8)
    for symbol in _DEFINITION.findall(text):
        offer(symbol, 0.8)
    # A bare lowercase name is invisible to the dotted/camel/snake patterns —
    # `ccode`, `sinc`, `sin` — and those are exactly the names a bug report is
    # about. Call syntax and code blocks are where they can be picked up
    # without dragging in every English word in the prose.
    for block in _FENCED.findall(text) + _INDENTED.findall(text):
        for symbol in _CALL.findall(block):
            offer(symbol, 0.75)
        for symbol in _DOTTED.findall(block):
            offer(symbol, 0.7)
            offer(symbol.split(".")[-1], 0.55)
    for symbol in _CALL.findall(text):
        offer(symbol, 0.65)
    for symbol in _DOTTED.findall(text):
        offer(symbol, 0.6)
        offer(symbol.split(".")[-1], 0.45)
    for symbol in _CAMEL_WORD.findall(text):
        offer(symbol, 0.5)
    for symbol in _SNAKE_WORD.findall(text):
        offer(symbol, 0.4)
    return sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_SEEDS]


_STOP_SYMBOLS = {
    "self", "none", "true", "false", "python", "traceback", "error", "exception",
    "the", "and", "not", "for", "with", "this", "that", "print", "return",
    "expected", "actual", "output", "input", "version", "import", "module",
    # builtins and REPL furniture, which code blocks are full of and which
    # resolve to something in almost any repo
    "int", "str", "len", "list", "dict", "set", "tuple", "float", "bool",
    "range", "type", "super", "isinstance", "assert", "raise", "def", "class",
    "out", "cell", "line", "file", "test", "main", "run", "get", "set_",
}


def pulse_map(instance, checkout_dir: Path, engine: Engine) -> Ranking:
    """Resolve the issue's symbols in the map, then let the graph carry us."""
    from codepulse.search import search
    from codepulse.six import compute_radius

    started = time.perf_counter()
    entity_score: dict[Addr, float] = defaultdict(float)
    direct_score: dict[Addr, float] = defaultdict(float)
    resolved = 0
    found = seeds(instance.problem_statement)
    for symbol, weight in found:
        hits = search(engine, symbol, limit=5)
        if not hits:
            continue
        resolved += 1
        top = max((h.get("score") or 1) for h in hits) or 1
        for rank, hit in enumerate(hits):
            addr = Addr(hit["path"], hit["name"])
            if addr.name in SCOPES:
                continue
            # Normalising by the best hit keeps one loud seed from drowning the
            # rest; the rank term breaks ties the search score leaves flat.
            direct = weight * ((hit.get("score") or 1) / top) / (1 + rank)
            entity_score[addr] += direct
            direct_score[addr] += direct
    # The graph step, and the whole point of the arm: whoever depends on a named
    # symbol is a candidate even if the issue never names them. Walking from all
    # ~200 resolved entities costs a BFS each on a repo the size of django, so
    # spend the walk on the strongest seeds — a weak seed's dependents were never
    # going to survive the decay anyway. The budget is reported in `detail`.
    walked = sorted(direct_score.items(), key=lambda kv: (-kv[1], kv[0].path,
                                                          kv[0].name))[:RADIUS_BUDGET]
    for addr, direct in walked:
        for dependent, hops in compute_radius(engine, addr).items():
            if hops <= MAX_HOPS:
                entity_score[dependent] += direct * (RADIUS_DECAY ** hops)
        for edge in engine.outgoing(addr):
            if isinstance(edge.dst, Addr) and edge.dst.name not in SCOPES:
                entity_score[edge.dst] += direct * RADIUS_DECAY
    file_score: dict[str, float] = defaultdict(float)
    for addr, value in entity_score.items():
        if is_test_path(addr.path):
            continue
        file_score[addr.path] += value
    ranked_entities = tuple(
        (a.path, a.name) for a, _ in
        sorted(entity_score.items(), key=lambda kv: (-kv[1], kv[0].path, kv[0].name))
        if not is_test_path(a.path)
    )
    return Ranking(_rank(file_score), ranked_entities[:200],
                   time.perf_counter() - started,
                   {"arm": "pulse_map", "seeds": len(found), "seeds_resolved": resolved,
                    "entities_scored": len(entity_score), "entities_walked": len(walked),
                    "walk_budget": RADIUS_BUDGET})


# ── arm 3: fusion ─────────────────────────────────────────────────────

def hybrid(instance, checkout_dir: Path, engine: Engine) -> Ranking:
    started = time.perf_counter()
    lexical = bm25(instance, checkout_dir, engine)
    graph = pulse_map(instance, checkout_dir, engine)
    fused: dict[str, float] = defaultdict(float)
    for ranking in (lexical, graph):
        for rank, path in enumerate(ranking.files):
            fused[path] += 1.0 / (RRF_K + rank + 1)
    return Ranking(_rank(fused), graph.entities,
                   time.perf_counter() - started,
                   {"arm": "hybrid", "bm25_files": len(lexical.files),
                    "map_files": len(graph.files),
                    "bm25_seconds": lexical.seconds, "map_seconds": graph.seconds})


def hybrid_walk(instance, checkout_dir: Path, engine: Engine) -> Ranking:
    """The configuration the grid search actually argues for.

    `pulse_walk` loses at file ranking and wins at entity ranking, so use each
    where it is strong: BM25 orders the files, the walk supplies the
    function-level answer, and RRF fuses the two file rankings. This is
    `hybrid` with the slow arm swapped out — same idea, 7.6x less time in the
    graph step.
    """
    started = time.perf_counter()
    lexical = bm25(instance, checkout_dir, engine)
    graph = pulse_walk(instance, checkout_dir, engine)
    fused: dict[str, float] = defaultdict(float)
    for ranking in (lexical, graph):
        for rank, path in enumerate(ranking.files):
            fused[path] += 1.0 / (RRF_K + rank + 1)
    return Ranking(_rank(fused), graph.entities,
                   time.perf_counter() - started,
                   {"arm": "hybrid_walk", "bm25_seconds": lexical.seconds,
                    "walk_seconds": graph.seconds})


def _rank(scores: dict) -> tuple[str, ...]:
    """Path name breaks score ties, so a rerun cannot reorder the output."""
    return tuple(path for path, _ in
                 sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])))


# ── arm 4: the greedy walk ────────────────────────────────────────────
#
# `pulse_map` seeds from ~40 regex-extracted names and blurs one-shot radii
# outward. Miss the name and you miss everything, and summing mass lets one
# loud symbol drown the rest. This arm fixes both: it seeds *every* entity
# lexically, then lets activation travel greedily along edges — and it ranks
# on how many DISTINCT issue terms reached an entity, not on total mass.
#
# Co-activation is the whole idea. If `ccode` and `sinc` independently
# propagate into `CCodePrinter`, that meeting is strong evidence; one keyword
# shouting ten times is not. Summation cannot tell those apart.

WALK_DEFAULTS = {
    "damping": 0.35,        # fraction of a node's score passed to neighbours
    "pops": 400,            # greedy expansions per instance
    "coactivation": 0.6,    # weight on distinct-terms-reached
    "seed_top": 200,        # lexically-scored entities that enter the heap
    "reverse_weight": 0.7,  # dependents are weaker evidence than dependencies
    "max_terms": 32,        # distinct issue terms tracked, by IDF
    # Unusual direct mention: a term the issue names verbatim that occurs in
    # only a handful of entities repo-wide is closer to a fingerprint than to
    # a keyword. BM25's log-damped IDF treats "appears in 2 entities" and
    # "appears in 40" as merely different; here the rare one is decisive. It
    # is also how loosely-related objects get reached at all — an entity no
    # edge connects to the issue still surfaces if it is the only thing in the
    # repo that says `qdp`.
    "unusual_df": 3,        # document frequency at or below this is "unusual"
    "unusual_boost": 2.0,   # added per unusual term matched, multiplicatively
}
_WALK_KIND_WEIGHTS = {
    "calls": 1.0, "inherits": 1.0, "implements": 0.9, "reads": 0.8,
    "types": 0.7, "writes": 0.6, "imports": 0.3, "loads": 0.5,
    "copies": 0.5, "contains": 0.15,
}
_ADJACENCY_CACHE: dict[str, dict] = {}
_DOCS_CACHE: dict[str, dict] = {}


def config_hash(cfg: dict) -> str:
    """Fingerprint of the tuned parameters, recorded in every result row.

    A tuned arm is only honest if the reader can tell which parameters
    produced a number, and that the Verified run used the config frozen on
    Lite rather than one refitted afterwards.
    """
    payload = repr(sorted((k, v) for k, v in cfg.items() if k != "config"))
    return hashlib.blake2b(payload.encode(), digest_size=6).hexdigest()


def _adjacency(engine: Engine) -> dict[Addr, list[tuple[Addr, float]]]:
    """Whole edge table as a weighted, bidirectional adjacency map, once.

    Cached one repo deep, not accumulated: a 500-instance run walks 500
    repos, and django alone is ~48k entities — keeping them all would make
    the benchmark the memory hog the tool claims not to be.
    """
    key = str(engine.root)
    if key in _ADJACENCY_CACHE:
        return _ADJACENCY_CACHE[key]
    _ADJACENCY_CACHE.clear()
    adjacency: dict[Addr, list[tuple[Addr, float]]] = defaultdict(list)
    rows = engine.db.execute(
        "SELECT src_path, src_name, kind, dst_path, dst_name FROM edges "
        "WHERE dst_path IS NOT NULL AND dst_name IS NOT NULL")
    for src_path, src_name, kind, dst_path, dst_name in rows:
        weight = _WALK_KIND_WEIGHTS.get(kind, 0.4)
        src, dst = Addr(src_path, src_name), Addr(dst_path, dst_name)
        if src == dst:
            continue
        adjacency[src].append((dst, weight))
        adjacency[dst].append((src, weight * WALK_DEFAULTS["reverse_weight"]))
    _ADJACENCY_CACHE[key] = adjacency
    return adjacency


def _entity_docs(engine: Engine) -> dict:
    """Tokenised entity documents plus corpus statistics, once per repo."""
    key = str(engine.root)
    if key in _DOCS_CACHE:
        return _DOCS_CACHE[key]
    _DOCS_CACHE.clear()                 # one repo deep; see _adjacency
    docs: dict[Addr, Counter] = {}
    document_freq: Counter = Counter()
    for entity in engine.entities():
        addr = entity.addr
        if addr.name in SCOPES or is_test_path(addr.path):
            continue
        # The name is the strongest signal an entity carries, so it counts
        # three times against its own docstring and path.
        counts = Counter(tokenize(addr.name.replace(".", " ")) * 3)
        counts.update(tokenize(addr.path.replace("/", " ")))
        counts.update(tokenize(entity.meta or ""))
        if not counts:
            continue
        docs[addr] = counts
        document_freq.update(counts.keys())
    built = {"docs": docs, "df": document_freq, "n": max(len(docs), 1),
             "avg_len": (sum(sum(c.values()) for c in docs.values())
                         / max(len(docs), 1)) or 1.0}
    _DOCS_CACHE[key] = built
    return built


def pulse_walk(instance, checkout_dir: Path, engine: Engine,
               config: dict | None = None) -> Ranking:
    cfg = {**WALK_DEFAULTS, **(config or {})}
    started = time.perf_counter()
    index = _entity_docs(engine)
    docs, document_freq = index["docs"], index["df"]
    total, avg_len = index["n"], index["avg_len"]
    if not docs:
        return Ranking((), (), time.perf_counter() - started, {"arm": "pulse_walk"})

    # ── seed: every entity, scored lexically, with the terms that hit it ──
    query = [t for t in tokenize(instance.problem_statement) if len(t) > 2]
    weights = {
        term: math.log(1 + (total - document_freq[term] + 0.5) / (document_freq[term] + 0.5))
        for term in set(query) if document_freq.get(term)
    }
    # Only the most discriminating terms get a co-activation bit; common words
    # would otherwise light up every entity and flatten the signal.
    tracked = sorted(weights, key=lambda t: -weights[t])[:cfg["max_terms"]]
    bit_of = {term: 1 << i for i, term in enumerate(tracked)}

    # Terms the issue mentions that barely exist in this repo. Sorted only so
    # the count is reproducible; membership is what matters.
    unusual = {term for term in weights
               if document_freq.get(term, 0) <= cfg["unusual_df"]}

    score: dict[Addr, float] = {}
    mask: dict[Addr, int] = defaultdict(int)
    for addr, counts in docs.items():
        length = sum(counts.values()) or 1
        value = 0.0
        bits = 0
        rare_hits = 0
        for term, weight in weights.items():
            freq = counts.get(term, 0)
            if not freq:
                continue
            value += weight * (freq * (BM25_K1 + 1)) / (
                freq + BM25_K1 * (1 - BM25_B + BM25_B * length / avg_len))
            bits |= bit_of.get(term, 0)
            if term in unusual:
                rare_hits += 1
        if value > 0:
            # Linear in the number of fingerprints matched, not exponential —
            # two rare terms is much better evidence than one, but a long tail
            # of them should not run away with the ranking.
            if rare_hits:
                value *= 1 + cfg["unusual_boost"] * rare_hits
            score[addr] = value
            mask[addr] = bits
    if not score:
        return Ranking((), (), time.perf_counter() - started,
                       {"arm": "pulse_walk", "seeded": 0})

    # ── travel: greedy best-first, activation spreading along edges ──
    adjacency = _adjacency(engine)
    frontier = [(-value, addr.path, addr.name, addr) for addr, value
                in sorted(score.items(), key=lambda kv: -kv[1])[:cfg["seed_top"]]]
    heapq.heapify(frontier)
    expanded: set[Addr] = set()
    pops = 0
    while frontier and pops < cfg["pops"]:
        negative, _, _, current = heapq.heappop(frontier)
        if current in expanded:
            continue
        expanded.add(current)
        pops += 1
        neighbours = adjacency.get(current, ())
        if not neighbours:
            continue
        # Degree normalisation: without it a load-bearing type absorbs every
        # walk and ranks first for every issue in the repo.
        share = cfg["damping"] * (-negative) / len(neighbours)
        for neighbour, kind_weight in neighbours:
            if is_test_path(neighbour.path) or neighbour.name in SCOPES:
                continue
            gain = share * kind_weight
            if gain <= 1e-9:
                continue
            score[neighbour] = score.get(neighbour, 0.0) + gain
            mask[neighbour] |= mask[current]
            if neighbour not in expanded:
                heapq.heappush(frontier, (-score[neighbour], neighbour.path,
                                          neighbour.name, neighbour))

    # ── rank: distinct terms reached, not accumulated mass ──
    final: dict[Addr, float] = {}
    for addr, value in score.items():
        reached = bin(mask[addr]).count("1")
        final[addr] = value * (1 + cfg["coactivation"] * max(reached - 1, 0))
    ordered = sorted(final.items(), key=lambda kv: (-kv[1], kv[0].path, kv[0].name))

    file_score: dict[str, float] = defaultdict(float)
    for addr, value in final.items():
        file_score[addr.path] += value
    return Ranking(_rank(file_score),
                   tuple((a.path, a.name) for a, _ in ordered[:200]),
                   time.perf_counter() - started,
                   {"arm": "pulse_walk", "seeded": len(score), "expanded": pops,
                    "tracked_terms": len(tracked), "unusual_terms": len(unusual),
                    "config_hash": config_hash(cfg)})


# ── control: the floor ────────────────────────────────────────────────

def random_arm(instance, checkout_dir: Path, engine: Engine | None = None) -> Ranking:
    """Zero skill, for calibration.

    Without this, "recall@10 = 0.85" is unreadable — the reader cannot tell
    whether that is skill or whether the candidate set is simply small enough
    that anything scores well. Ordering is a hash of (instance, path) rather
    than an RNG, so it is stable across runs and machines while carrying no
    information about the issue.
    """
    started = time.perf_counter()
    paths = candidates(checkout_dir)
    seed = instance.instance_id.encode()
    ordered = sorted(paths, key=lambda p: hashlib.blake2b(
        seed + b"\x00" + p.encode(), digest_size=8).digest())
    return Ranking(tuple(ordered), (), time.perf_counter() - started,
                   {"arm": "random", "corpus_files": len(paths)})


ARMS: dict[str, Callable[..., Ranking]] = {
    "random": random_arm,
    "bm25": bm25,
    "pulse_map": pulse_map,
    "pulse_walk": pulse_walk,
    "hybrid": hybrid,
    "hybrid_walk": hybrid_walk,
}
