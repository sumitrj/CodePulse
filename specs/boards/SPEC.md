# Boards — structured readings of the graph

## Intent
Boards are saved views over the six verbs, returned as data so the panel can draw them and MCP can speak them — no new verbs, no new graph machinery. Three boards: the **whole map** (file-level dependency graph, layered by depth), the **hop diagram** (a blast radius as nodes-with-hops plus the edges among them), and **handlers** — an estimate of handler-level functions derived purely from graph shape: a function or method that no other entity calls (module-scope registrations like decorators don't count as callers) but that transitively reaches real work downstream. Entry points fall out of the topology in any language; no framework knowledge is used.

## Interface
```python
# codepulse/boards.py
def handlers(engine) -> list[dict]
# [{"path", "name", "kind", "reach", "direct"}] — roots (internal fan-in 0 among
# calls/reads/inherits, module-scope srcs ignored) with reach >= 1,
# sorted by reach desc then addr. reach = size of transitive outgoing calls
# closure over internal entities; direct = first-hop count.

def handlers_text(engine) -> str          # the same board as plain sentences (MCP parity)

def radius_graph(engine, name: str, direction: str = "in") -> dict
# {"target": {"path","name"}, "direction", "nodes": [{"path","name","hop"}],
#  "edges": [{"src","dst","kind"}]} — direction "in": blast radius (who breaks),
# hops from six.compute_radius over impact edges. direction "out": reach (what
# the target calls, transitively, calls only) — a handler's radius is empty by
# definition, so the handlers board links here. Edges are among {target} ∪ nodes.

def file_graph(engine) -> dict
# {"mode": "file"|"dir", "nodes": [{"id","entities","depth"}],
#  "edges": [{"src","dst","n"}]} — cross-file edges aggregated (contains excluded,
# self-loops dropped); depth = BFS layer from files nobody depends on (cycles safe).
# mode flips to "dir" (top-level directory grouping) above 150 files.

# codepulse/panel.py additions
# GET /api/handlers, /api/radius?name=..., /api/graph -> the dicts above as JSON
# answer(engine, "handlers", "") -> handlers_text
# codepulse/mcp_server.py: pulse_handlers tool -> handlers_text (shared surface parity)
```

## Acceptance criteria
1. A function called by no other entity, with downstream calls, appears in `handlers` with correct `reach` and `direct`; the functions it calls do not appear.
2. A decorator-registered function (its only incoming call edges come from module scope) still counts as a handler.
3. `handlers_text` speaks the estimate as plain sentences including the count.
4. `radius_graph` returns the target, nodes at hop 1 and hop 2, and the connecting edges.
5. `file_graph` aggregates cross-file edges with counts, excludes self-loops, and assigns depths.
6. The three API routes answer JSON from a live server, and `pulse_handlers` answers over MCP dispatch.

## Out of scope
- Layout (the client draws), persistence of boards, custom board definitions, framework-specific handler hints.

## Open questions / assumptions
- reach counts entities, not files, and follows `calls` only (reads/inherits are dependency signals for fan-in, not reach).
- Classes are not handler candidates; methods are.
