"""
Tests for the six verbs over the recipe graph. Maps 1:1 to acceptance
criteria in specs/six/SPEC.md. Red until codepulse/six.py exists.
"""
from pathlib import Path

import pytest

from codepulse.recipes import load_recipe, RECIPES_DIR
from codepulse.engine import Addr, Engine
from codepulse import six  # ImportError until implementation — expected


ROOT = Path(__file__).resolve().parents[1]

FILES = {
    "billing.py": "def total(items):\n    return sum(items)\n",
    "orders.py": "from billing import total\n\ndef process(items):\n    return total(items)\n",
    "report.py": "from orders import process\n\ndef summary(items):\n    return process(items)\n",
}


@pytest.fixture
def python_recipe():
    return load_recipe(RECIPES_DIR / "python.yml")


@pytest.fixture
def engine(tmp_path, python_recipe):
    repo = tmp_path / "repo"
    repo.mkdir()
    for rel, src in FILES.items():
        (repo / rel).write_text(src)
    eng = Engine(tmp_path / "pulse.db", [python_recipe], root=repo)
    eng.apply()
    return eng


# === AC1: what_is shows the card with kind, provenance, dependents ===

def test_what_is_shows_card_with_provenance_and_dependents(engine, python_recipe):
    out = six.what_is(engine, "total")

    assert "billing.py :: total" in out
    assert "[function]" in out
    assert f"python@{python_recipe.version}" in out
    assert "orders.py :: process" in out


# === AC2: who_touches lists incoming edges as src --kind--> target lines ===

def test_who_touches_lists_incoming_edges(engine):
    out = six.who_touches(engine, "total")

    assert "orders.py :: process" in out
    assert "--calls-->" in out


# === AC3: radius walks transitive dependents by hops ===

def test_radius_reports_callers_by_hop_depth(engine):
    out = six.radius(engine, "total")

    assert "[1 hop] orders.py :: process" in out
    assert "[2 hops] report.py :: summary" in out


# === AC4: what_changed re-extracts paths, reports touched + at risk ===

def test_what_changed_reports_touched_entities_and_at_risk(engine):
    (engine.root / "orders.py").write_text("def process(items):\n    return len(items)\n")

    out = six.what_changed(engine, [engine.root / "orders.py"])

    assert "orders.py" in out
    assert "process" in out
    assert "summary" in out          # the hidden impact two files away


# === AC5: locate ranks by name/path tokens ===

def test_locate_ranks_the_named_entity_first(engine):
    out = six.locate(engine, "process orders")

    assert "orders.py :: process" in out.splitlines()[0]


# === AC6: system_map reports counts and load-bearing entities ===

def test_system_map_reports_counts_and_load_bearing(engine):
    out = six.system_map(engine)

    assert "3 files" in out
    assert "entities" in out
    assert "billing.py :: total" in out


# === AC7: a miss answers with a plain sentence ===

def test_miss_answers_with_a_plain_sentence(engine):
    out = six.what_is(engine, "nonexistent")

    assert out == "No entity matching 'nonexistent'."


# === AC8: refresh re-extracts only hash-changed files ===

def test_refresh_reextracts_only_changed_files(engine):
    (engine.root / "orders.py").write_text("def process(items):\n    return 0\n")
    (engine.root / "billing.py").write_text(FILES["billing.py"])  # same content, same hash

    report = engine.refresh()

    assert list(report.extracted) == ["orders.py"]


# === AC8 addendum (the 45s bug): an unchanged repo reads zero files ===

def test_refresh_on_unchanged_repo_opens_no_files(engine, monkeypatch):
    import codepulse.engine as eng_mod
    reads = {"n": 0}
    real = eng_mod.hashlib.sha256

    def counting(data=b""):
        reads["n"] += 1
        return real(data)

    monkeypatch.setattr(eng_mod.hashlib, "sha256", counting)
    report = engine.refresh()          # nothing touched since apply()

    assert report.extracted == ()
    assert reads["n"] == 0             # size+mtime matched — no file was hashed


# === AC9: excluded directories are never scanned ===

def test_excluded_directories_are_not_scanned(tmp_path, python_recipe):
    repo = tmp_path / "repo"
    (repo / ".venv" / "lib").mkdir(parents=True)
    (repo / ".venv" / "lib" / "junk.py").write_text("def hidden():\n    pass\n")
    (repo / "app.py").write_text("def visible():\n    pass\n")
    eng = Engine(tmp_path / "pulse.db", [python_recipe], root=repo)

    eng.apply()

    paths = {e.addr.path for e in eng.entities()}
    assert paths == {"app.py"}


# === AC10: the MCP dispatcher answers from the engine graph ===

def test_mcp_dispatch_answers_pulse_what_from_engine(engine):
    from codepulse.mcp_server import dispatch

    out = dispatch(engine, "pulse_what", {"name": "total"})

    assert "billing.py :: total" in out


# === Traceability ===
# AC1:  test_what_is_shows_card_with_provenance_and_dependents
# AC2:  test_who_touches_lists_incoming_edges
# AC3:  test_radius_reports_callers_by_hop_depth
# AC4:  test_what_changed_reports_touched_entities_and_at_risk
# AC5:  test_locate_ranks_the_named_entity_first
# AC6:  test_system_map_reports_counts_and_load_bearing
# AC7:  test_miss_answers_with_a_plain_sentence
# AC8:  test_refresh_reextracts_only_changed_files
# AC9:  test_excluded_directories_are_not_scanned
# AC10: test_mcp_dispatch_answers_pulse_what_from_engine
