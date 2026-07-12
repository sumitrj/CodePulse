"""
Tests for the panel server. Maps 1:1 to acceptance criteria in
specs/panel/SPEC.md. Red until codepulse/panel.py exists.
"""
import json
import threading
import urllib.request
from pathlib import Path

import pytest

from codepulse.recipes import load_recipe, RECIPES_DIR
from codepulse.engine import Engine
from codepulse import panel  # ImportError until implementation — expected


ROOT = Path(__file__).resolve().parents[1]

FILES = {
    "billing.py": "def total(items):\n    return sum(items)\n",
    "orders.py": "from billing import total\n\ndef process(items):\n    return total(items)\n",
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


# === AC1: answer routes each verb ===

@pytest.mark.parametrize("verb,arg,expect", [
    ("what", "total", "billing.py :: total"),
    ("who", "total", "--calls-->"),
    ("radius", "total", "[1 hop] orders.py :: process"),
    ("locate", "process orders", "orders.py :: process"),
    ("map", "", "2 files"),
])
def test_answer_routes_each_verb(engine, verb, arg, expect):
    assert expect in panel.answer(engine, verb, arg)


# === AC2: changed reports touched entities for given paths ===

def test_answer_changed_reports_touched_entities(engine):
    (engine.root / "orders.py").write_text("def process(items):\n    return len(items)\n")

    out = panel.answer(engine, "changed", "orders.py")

    assert "orders.py" in out
    assert "process" in out


# === AC3: an unknown verb answers with a plain sentence ===

def test_unknown_verb_answers_plainly(engine):
    out = panel.answer(engine, "explode", "x")

    assert "verb" in out.lower()


# === AC4: tree lists files, entities, and stats ===

def test_tree_lists_files_entities_and_stats(engine):
    data = panel.tree(engine)

    assert data["stats"]["files"] == 2
    paths = {f["path"] for f in data["files"]}
    assert paths == {"billing.py", "orders.py"}
    billing = next(f for f in data["files"] if f["path"] == "billing.py")
    assert {"name": "total", "kind": "function", "line": 1} in billing["entities"]


# === AC5+AC6: the HTTP server serves the panel and the API ===

@pytest.fixture
def live(engine):
    server = panel.make_server(engine, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read().decode()


def test_server_serves_panel_markup(live):
    html = _get(live + "/")

    assert "CODEPULSE" in html
    assert "WHAT" in html and "BREAKS" in html and "MAP" in html


def test_server_answers_verb_and_tree_as_json(live):
    verb = json.loads(_get(live + "/api/verb?v=what&arg=total"))
    tree = json.loads(_get(live + "/api/tree"))

    assert "billing.py :: total" in verb["text"]
    assert tree["stats"]["files"] == 2


# === Traceability ===
# AC1: test_answer_routes_each_verb (x5)
# AC2: test_answer_changed_reports_touched_entities
# AC3: test_unknown_verb_answers_plainly
# AC4: test_tree_lists_files_entities_and_stats
# AC5: test_server_serves_panel_markup
# AC6: test_server_answers_verb_and_tree_as_json
