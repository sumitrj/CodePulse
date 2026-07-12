"""Hybrid search over the map — exact, prefix, fuzzy, and full-text, one score.

Backends are already on every machine: SQLite's FTS5 for what docstrings say,
stdlib difflib for typos. The same function answers the panel (/api/search),
agents (pulse_locate), and six.locate. See specs/search/SPEC.md.
"""
import difflib
import sqlite3

from .engine import Engine


def _fts_scores(engine: Engine, tokens: list[str]) -> dict[tuple[str, str], float]:
    if not tokens:
        return {}
    match = " OR ".join(f'"{t}"' for t in tokens)
    scores: dict[tuple[str, str], float] = {}
    try:
        rows = engine.db.execute(
            "SELECT path, name, bm25(entities_fts) FROM entities_fts "
            "WHERE entities_fts MATCH ? LIMIT 200", (match,)).fetchall()
    except sqlite3.OperationalError:
        return {}
    for path, name, rank in rows:
        strength = 1.0 + min(1.0, abs(rank or 0) / 10.0)
        key = (path, name)
        scores[key] = max(scores.get(key, 0.0), strength)
    return scores


def search(engine: Engine, query: str, limit: int = 20) -> list[dict]:
    q = query.strip()
    if not q:
        return []
    ql = q.lower()
    tokens = [t for t in ql.replace("_", " ").split() if len(t) > 1]
    fts = _fts_scores(engine, tokens)

    results = []
    for entity in engine.entities():
        name = entity.addr.name
        if name == "<module>":
            continue
        tail = name.split(".")[-1].lower()
        score = 0.0
        if name.lower() == ql or tail == ql:
            score += 6.0                                      # exact name
        elif tail.startswith(ql):
            score += 3.0                                      # prefix
        ratio = difflib.SequenceMatcher(None, tail, ql).ratio()
        if ratio > 0.6:
            score += 2.0 * ratio                              # fuzzy: typos forgiven
        name_l = name.lower().replace("_", " ").replace(".", " ")
        path_l = entity.addr.path.lower()
        meta_l = entity.meta.lower()
        score += sum(
            (2.0 if t in name_l else 0.0)
            + (1.0 if t in path_l else 0.0)
            + (0.5 if t in meta_l else 0.0)
            for t in tokens
        )
        score += 2.0 * fts.get((entity.addr.path, name), 0.0)  # what the docstring says
        if score > 0.9:
            results.append({
                "path": entity.addr.path, "name": name, "kind": entity.kind,
                "line": entity.line, "meta": entity.meta[:160],
                "score": round(score, 2),
            })
    results.sort(key=lambda r: (-r["score"], r["path"], r["name"]))
    return results[:limit]
