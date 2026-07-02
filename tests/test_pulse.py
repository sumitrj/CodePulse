"""Integration tests for the delivery layer: store, verbs, hooks contract."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from codepulse import verbs
from codepulse.relationships import UnitRef
from codepulse.store import Store

REPO = {
    "config.py": "RATE = 0.1\n",
    "billing.py": (
        "from config import RATE\n\n\n"
        "def total(items):\n"
        '    """Sum of prices with rate applied."""\n'
        "    return sum(i.price for i in items) * (1 + RATE)\n"
    ),
    "orders.py": (
        "from billing import total\n\n\n"
        "def process(order):\n"
        "    order.amount = total(order.items)\n"
        "    audit.log(order)\n"
        "    return order\n"
    ),
}


@pytest.fixture
def repo(tmp_path):
    for name, src in REPO.items():
        (tmp_path / name).write_text(src)
    return tmp_path


@pytest.fixture
def store(repo):
    built, _ = Store.build(repo)
    built.save()
    return built


def test_store_round_trips_through_json(store, repo):
    loaded = Store.load(repo)

    assert loaded.units.keys() == store.units.keys()
    assert loaded.graph == store.graph
    assert loaded.file_hashes == store.file_hashes


def test_rebuild_preserves_identity_across_a_rename(store, repo):
    (repo / "billing.py").write_text(
        (repo / "billing.py").read_text().replace("def total", "def compute_total")
    )

    rebuilt, result = Store.build(repo, previous=store)

    renamed = [a for a in result.assignments if a.kind == "renamed"]
    assert len(renamed) == 1 and renamed[0].unit.qualname == "compute_total"
    assert renamed[0].unit_id in store.units


def test_what_is_answers_with_contract_and_dependents(store):
    card = verbs.what_is(store, "total")

    assert "billing.py :: total" in card
    assert "direct dependents: 1" in card


def test_radius_walks_transitively_through_state(store):
    report = verbs.radius(store, "RATE")

    assert "total" in report and "process" in report


def test_locate_finds_units_by_meaning(store):
    result = verbs.locate(store, "sum of prices")

    assert "billing.py :: total" in result.splitlines()[0]


def test_what_changed_flags_a_silent_promise_break(store, repo):
    (repo / "orders.py").write_text(
        "from billing import total\n\n\n"
        "def process(order):\n"
        "    order.amount = total(order.items)\n"
        "    return order\n"
    )

    report, _ = verbs.what_changed(store, use_model=False)

    assert "[major" in report and "process" in report


def test_system_map_reports_silhouette(store):
    silhouette = verbs.system_map(store)

    assert "2 units" in silhouette and "Load-bearing" in silhouette


def test_hook_pre_emits_additional_context(store, repo):
    entry = Path(__file__).resolve().parents[1] / "pulse.py"
    payload = json.dumps({"tool_input": {"file_path": str(repo / "billing.py")}})

    out = subprocess.run(
        [sys.executable, str(entry), "hook", "pre"],
        input=payload, capture_output=True, text=True, timeout=30,
    )

    reply = json.loads(out.stdout)
    context = reply["hookSpecificOutput"]["additionalContext"]
    assert reply["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "total" in context and "blast radius" in context
