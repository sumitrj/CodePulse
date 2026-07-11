# Panel — the human rendering of the six verbs

## Intent
The same graph the agents read over MCP renders for the human inside the editor: a Thunder-Client-style panel — entity rail on the left, verb tabs and answers on the right — styled strictly under the mono-brutalist constitution (one ink, one paper, borders as structure, invert for active, judged on a 13" viewport). Served by a stdlib HTTP server over the engine; the webview (and any browser) is a thin fetch client. No capability exists here that MCP doesn't have, and vice versa.

## Interface
```python
# codepulse/panel.py
def answer(engine: Engine, verb: str, arg: str) -> str
# verb: what|who|radius|changed|locate|map -> routes to codepulse.six; unknown verb -> plain sentence

def tree(engine: Engine) -> dict
# {"stats": {"files": n, "entities": n, "edges": n},
#  "files": [{"path": str, "entities": [{"name", "kind", "line"}, ...]}, ...]}

def serve(root: Path, port: int) -> None
# GET /          -> the panel HTML (codepulse/webview/panel.html)
# GET /api/tree  -> tree() as JSON
# GET /api/verb?v=<verb>&arg=<arg> -> {"text": answer(...)} ; engine.refresh() first
```

## Inputs and outputs
- Input: repo root; recipes via `builtin_recipes()`; verb + arg strings from the client.
- Output: JSON over HTTP; one static HTML page.
- Errors: handler exceptions become `{"text": "codepulse error: ..."}`, HTTP 200 (the panel renders it).

## Acceptance criteria
1. `answer` routes each verb to its six-verb implementation (what/who/radius/locate/map verified by content).
2. `answer("changed", paths)` reports touched entities for the given paths.
3. An unknown verb answers with a plain sentence, not an exception.
4. `tree` lists files with their entities and correct stats counts.
5. The HTTP server serves `/` containing the panel markup (CODEPULSE, six tabs).
6. `/api/verb` and `/api/tree` answer JSON from a live server.

## Out of scope
- Write actions, settings UI, boards beyond the verb answers, websockets/live push (the client polls on focus).
- Packaging the webview into the VSIX (next slice).

## Open questions / assumptions
- Port default 7317 (the existing codepulse app port); the extension will pick a free port.
- `changed` with an empty arg falls back to git-modified files, same as MCP.
