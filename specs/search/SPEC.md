# Hybrid search — find it even when you misspell it

## Intent
The top bar becomes SEARCH, and search becomes real: exact and prefix name
hits rank first, fuzzy matching forgives typos, and full-text search over
metadata finds entities by what their docstrings *say* — combined into one
score. The backend is SQLite's built-in FTS5 plus stdlib difflib: no new
dependencies, available everywhere the map is. The same engine answers the
panel (`/api/search`), agents (`pulse_locate`), and the six verbs
(`six.locate`) — one search, two readers, as always.

## Interface
```python
# codepulse/search.py
def search(engine, query: str, limit: int = 20) -> list[dict]
# [{"path","name","kind","line","meta","score"}] sorted by score desc, deterministic ties.
# Score blends: exact name (strongest), name prefix, fuzzy name ratio (difflib),
# token hits on name/path (the old locate signal), FTS5 bm25 over name+meta.

# engine: entities_fts (FTS5: path, name, meta) maintained alongside entities
# panel: GET /api/search?q=... -> the list as JSON; the bar button reads SEARCH
# six.locate() and pulse_locate answer from search() (plain-sentence rendering)
```

## Acceptance criteria
1. An exact name match ranks first.
2. A misspelled query ("proces") still finds `process`, scored below an exact hit.
3. A word that appears only in an entity's docstring finds that entity.
4. Results carry numeric scores, sorted descending.
5. `six.locate`'s top line agrees with `search()`'s top hit (MCP parity).
6. `/api/search` serves scored JSON from a live server.

## Out of scope
- Embeddings/semantic vectors (litellm+Ollama later — the score blend leaves room).
- Cross-repo search, regex search (grep exists).

## Open questions / assumptions
- FTS5 assumed present in the bundled SQLite (standard on macOS/Linux Python ≥3.11).
- Fuzzy compares the query to the name's last segment, case-insensitive.
