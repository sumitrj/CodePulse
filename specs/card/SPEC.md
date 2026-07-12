# The card — an entity you can understand at a glance

## Intent
The WHAT view stops being a key:value dump and becomes a card a stranger can
read: the entity's own words first (its docstring), plainly labeled sections
with microcopy explaining what each means, and — like a database ERD for
selected tables — a local semantic map drawn below it: who touches this, what
it touches, with the outside world grouped honestly. Every entity gains
**metadata**: its docstring when it has one, otherwise a capped slice of its
own source. Metadata is display (the card), search fodder (specs/search), and
a future slot for human/agent-written descriptions.

## Interface
```python
# recipe format: a new top-level `docs:` list of tree-sitter queries whose
# @doc capture attaches to the innermost enclosing entity (optional captures
# don't bind in tree-sitter, so documented entities match their own rule).

# codepulse/engine.py
Entity.meta: str              # docstring (dedented, capped) else source slice; "" for modules
# entities table gains a meta column; existing DBs migrate via ALTER on open.

# codepulse/boards.py
def card(engine, name: str) -> dict | None
# {"entity": {"path","name","kind","line","meta","recipe","recipe_version"},
#  "incoming": [{"path","name","kind","edge_kind"}],          # who touches it
#  "outgoing": [{"path","name","kind","edge_kind"}],          # internal targets
#  "external": ["json.loads", ...]}                           # outside the map, deduped

# codepulse/panel.py: GET /api/card?name=... -> the dict (404-style None -> {"entity": null})
# codepulse/six.py: what_is() gains the doc line and a plain-words hint when dependents == 0
```

## Acceptance criteria
1. A Python function's docstring first line lands in `Entity.meta`.
2. An undocumented entity's `meta` defaults to a slice of its own source.
3. `what_is` speaks the doc line, and a 0-dependent entity gets the hint
   ("nothing on the map calls this — tests and dynamically-invoked functions look like this").
4. `boards.card` splits the neighborhood: incoming, internal outgoing, external names.
5. `/api/card` serves the card as JSON from a live server.
6. An existing database without the meta column opens and works (migration).

## Out of scope
- TS/JSDoc docs rules (comment nodes sit outside declarations; next recipe rev).
- Human/agent-authored metadata overrides (the column is the slot; writing comes later).

## Open questions / assumptions
- meta caps at ~400 chars; the card shows the first line, the panel tooltip shows more.
- The ERD rendering is client-side; this spec covers only the data.
