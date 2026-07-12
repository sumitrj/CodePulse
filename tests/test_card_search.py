"""
Tests for the card (specs/card/SPEC.md) and hybrid search (specs/search/SPEC.md).
Red until entities carry meta, boards.card exists, and codepulse/search.py exists.
"""
import json
import sqlite3
import threading
import urllib.request
from pathlib import Path

import pytest

from codepulse.recipes import load_recipe, RECIPES_DIR
from codepulse.engine import Addr, Engine
from codepulse import boards, panel, six
from codepulse import search as pulse_search  # ImportError until implementation — expected


ROOT = Path(__file__).resolve().parents[1]

FILES = {
    "billing.py": (
        "def total(items):\n"
        "    \"\"\"Sum the priced items into one invoice amount.\"\"\"\n"
        "    return sum(items)\n"
        "\n\n"
        "def rebate(items):\n"
        "    return len(items)\n"
    ),
    "orders.py": (
        "import json\n"
        "from billing import total\n"
        "\n\n"
        "def process(items):\n"
        "    \"\"\"Turn a raw basket into an invoiced order.\"\"\"\n"
        "    return total(json.loads(items))\n"
    ),
}


@pytest.fixture
def engine(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for rel, src in FILES.items():
        (repo / rel).write_text(src)
    eng = Engine(tmp_path / "pulse.db", [load_recipe(RECIPES_DIR / "python.yml")], root=repo)
    eng.apply()
    return eng


# === card AC1: a docstring lands in Entity.meta ===

def test_docstring_first_line_lands_in_meta(engine):
    total = next(e for e in engine.entities("billing.py") if e.addr.name == "total")

    assert "Sum the priced items" in total.meta
    assert total.doc is True          # from the docs rule, not the source fallback


# === card AC2: undocumented entities default meta to their source ===

def test_undocumented_entity_meta_defaults_to_source(engine):
    rebate = next(e for e in engine.entities("billing.py") if e.addr.name == "rebate")

    assert "return len(items)" in rebate.meta


# === card AC3: what_is speaks the doc line and the 0-dependent hint ===

def test_what_is_speaks_doc_and_zero_dependent_hint(engine):
    out = six.what_is(engine, "process")

    assert "Turn a raw basket" in out
    assert "nothing on the map calls this" in out


# === card AC4: boards.card splits the neighborhood ===

def test_card_splits_incoming_outgoing_external(engine):
    data = boards.card(engine, "total")

    assert data["entity"]["name"] == "total"
    assert "Sum the priced items" in data["entity"]["meta"]
    assert {"path": "orders.py", "name": "process"}.items() <= data["incoming"][0].items()
    outgoing_names = {o["name"] for o in boards.card(engine, "process")["outgoing"]}
    assert "total" in outgoing_names
    assert "json.loads" in boards.card(engine, "process")["external"]


# === card AC6: an old database without meta migrates on open ===

def test_old_database_without_meta_column_migrates(tmp_path):
    db_path = tmp_path / "old.db"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE entities(path TEXT, name TEXT, kind TEXT, line INTEGER,"
               " recipe TEXT, recipe_version TEXT, PRIMARY KEY(path, name))")
    db.commit()
    db.close()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n")

    engine = Engine(db_path, [load_recipe(RECIPES_DIR / "python.yml")], root=repo)
    engine.apply()

    assert any(e.addr.name == "f" for e in engine.entities("a.py"))


# === card addendum (found live): a recipe upgrade re-maps unchanged files ===

def test_refresh_reextracts_when_recipe_version_changes(tmp_path):
    import dataclasses
    recipe = load_recipe(RECIPES_DIR / "python.yml")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n")
    Engine(tmp_path / "pulse.db", [recipe], root=repo).apply()
    upgraded = dataclasses.replace(recipe, version="999")

    report = Engine(tmp_path / "pulse.db", [upgraded], root=repo).refresh()

    assert list(report.extracted) == ["a.py"]


# === search AC1+AC2: exact first, fuzzy forgives typos ===

def test_exact_name_ranks_first_and_fuzzy_finds_misspelling(engine):
    exact = pulse_search.search(engine, "process")
    fuzzy = pulse_search.search(engine, "proces")

    assert (exact[0]["path"], exact[0]["name"]) == ("orders.py", "process")
    assert any(r["name"] == "process" for r in fuzzy[:3])
    assert fuzzy[0]["score"] <= exact[0]["score"]


# === search AC3: docstring words find the entity ===

def test_docstring_word_finds_the_entity(engine):
    hits = pulse_search.search(engine, "invoice")

    assert any(r["name"] == "total" for r in hits[:3])


# === search AC4: scored and sorted ===

def test_results_are_scored_and_sorted(engine):
    hits = pulse_search.search(engine, "billing total")

    assert all(isinstance(r["score"], (int, float)) for r in hits)
    assert [r["score"] for r in hits] == sorted((r["score"] for r in hits), reverse=True)


# === search AC5: six.locate agrees with search (MCP parity) ===

def test_locate_top_line_agrees_with_search(engine):
    top = pulse_search.search(engine, "invoiced order")[0]

    out = six.locate(engine, "invoiced order")

    assert f"{top['path']} :: {top['name']}" in out.splitlines()[0]


# === card AC5 + search AC6: the API serves both ===

@pytest.fixture
def live(engine):
    server = panel.make_server(engine, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode())


def test_api_serves_card_and_search(live):
    card = _get(live + "/api/card?name=total")
    hits = _get(live + "/api/search?q=invoice")

    assert card["entity"]["name"] == "total"
    assert any(r["name"] == "total" for r in hits[:3])


# === Traceability ===
# card AC1: test_docstring_first_line_lands_in_meta
# card AC2: test_undocumented_entity_meta_defaults_to_source
# card AC3: test_what_is_speaks_doc_and_zero_dependent_hint
# card AC4: test_card_splits_incoming_outgoing_external
# card AC5 + search AC6: test_api_serves_card_and_search
# card AC6: test_old_database_without_meta_column_migrates
# search AC1+AC2: test_exact_name_ranks_first_and_fuzzy_finds_misspelling
# search AC3: test_docstring_word_finds_the_entity
# search AC4: test_results_are_scored_and_sorted
# search AC5: test_locate_top_line_agrees_with_search
