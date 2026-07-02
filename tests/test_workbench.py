"""Tests for the Workbench. Maps 1:1 to acceptance criteria in specs/workbench/SPEC.md."""
import json
import subprocess
from pathlib import Path

import pytest

from codepulse.store import Store
from codepulse.workbench import (  # ImportError until implemented - expected
    GateConfig,
    Issue,
    gate,
    load_workpieces,
    make_brief,
    render_pr_body,
    run_workpiece,
    save_workpiece,
)

FILES = {
    "billing.py": (
        "def total(items):\n"
        '    """Sum of line-item prices."""\n'
        "    return sum(i.price for i in items)\n"
    ),
    "orders.py": (
        "from billing import total\n\n\n"
        "def process(order):\n"
        '    """Price and persist an order."""\n'
        "    order.amount = total(order.items)\n"
        "    audit.log(order)\n"
        "    return order\n"
    ),
    "tests/test_orders.py": (
        "import types\n"
        "import orders\n\n\n"
        "def test_process_prices_the_order():\n"
        "    orders.audit = types.SimpleNamespace(log=lambda o: None)\n"
        "    item = types.SimpleNamespace(price=5)\n"
        "    order = types.SimpleNamespace(items=[item])\n"
        "    assert orders.process(order).amount == 5\n"
    ),
}

ISSUE = Issue(number=7, title="Orders lose their discount",
              body="process should apply the order discount when pricing")


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    for name, src in FILES.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "wb@test")
    _git(tmp_path, "config", "user.name", "wb")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "seed")
    store, _ = Store.build(tmp_path)
    store.save()
    return tmp_path


def good_executor(worktree: Path, brief: str) -> str:
    """Adds discount handling to process(): a benign, promise-extending change."""
    p = worktree / "orders.py"
    p.write_text(p.read_text().replace(
        "    order.amount = total(order.items)\n",
        "    order.amount = total(order.items) - getattr(order, 'discount', 0)\n",
    ))
    return "applied discount fix"


def breaking_executor(worktree: Path, brief: str) -> str:
    """Silently drops the audit promise and touches no test."""
    p = worktree / "orders.py"
    p.write_text(p.read_text().replace("    audit.log(order)\n", ""))
    return "removed audit"


# === AC1: brief derives scored candidates with blast radii ===

def test_brief_derives_candidates_and_radius_from_issue_text(repo):
    store = Store.load(repo)

    brief = make_brief(store, ISSUE)

    top = brief["candidates"][0]
    assert top["qualname"] == "process"
    assert top["score"] >= 4
    assert "radius" in top


# === AC2: gate approves within bounds ===

def test_gate_approves_a_small_scoped_issue(repo):
    store = Store.load(repo)
    brief = make_brief(store, ISSUE)

    eligible, reasons = gate(store, brief, GateConfig())

    assert eligible and reasons == []


# === AC3: gate rejects with named reasons ===

def test_gate_rejects_when_bounds_are_exceeded(repo):
    store = Store.load(repo)
    brief = make_brief(store, ISSUE)

    eligible, reasons = gate(store, brief, GateConfig(max_radius=0, min_confidence=99))

    assert not eligible
    assert any("radius" in r for r in reasons)
    assert any("confidence" in r for r in reasons)


# === AC4: execution is isolated from the main working tree ===

def test_execution_leaves_the_main_tree_untouched(repo):
    before = (repo / "orders.py").read_text()

    run_workpiece(repo, ISSUE, GateConfig(), good_executor, dry_run=True)

    assert (repo / "orders.py").read_text() == before


# === AC5: prove selects radius-scoped tests and records the outcome ===

def test_prove_runs_the_dependent_test_and_passes(repo):
    wp = run_workpiece(repo, ISSUE, GateConfig(), good_executor, dry_run=True)

    assert wp["proof"]["selected"] == ["tests/test_orders.py"]
    assert wp["proof"]["passed"] is True
    assert wp["status"] == "delivered"


# === AC6: MAJOR verdict without a test change is rejected ===

def test_silent_promise_break_without_test_change_is_rejected(repo):
    wp = run_workpiece(repo, ISSUE, GateConfig(), breaking_executor, dry_run=True)

    assert wp["status"] == "rejected"
    assert "test" in wp["rejection"].lower()


# === AC7: verdict lists a classification per changed unit ===

def test_verdict_classifies_each_changed_unit(repo):
    wp = run_workpiece(repo, ISSUE, GateConfig(), good_executor, dry_run=True)

    entries = {v["qualname"]: v["classification"] for v in wp["verdict"]}
    assert "process" in entries
    assert entries["process"] in ("patch", "minor")


# === AC8: workpieces persist versioned, append-only ===

def test_reruns_version_the_workpiece(repo):
    wp1 = run_workpiece(repo, ISSUE, GateConfig(), good_executor, dry_run=True)
    save_workpiece(repo, wp1)
    wp2 = run_workpiece(repo, ISSUE, GateConfig(), good_executor, dry_run=True)
    path2 = save_workpiece(repo, wp2)

    assert path2.name == "v2.json"
    assert len(load_workpieces(repo)) == 1  # one issue, latest version surfaced
    assert load_workpieces(repo)[0]["version"] == 2


# === AC9: PR body carries all five artifact sections ===

def test_pr_body_renders_all_five_artifacts(repo):
    wp = run_workpiece(repo, ISSUE, GateConfig(), good_executor, dry_run=True)

    body = render_pr_body(wp)

    for section in ("Brief", "Change", "Proof", "Verdict", "Trace"):
        assert section in body


# === AC10: dry-run delivery returns the payload, no external calls ===

def test_dry_run_returns_delivery_payload(repo):
    wp = run_workpiece(repo, ISSUE, GateConfig(), good_executor, dry_run=True)

    assert wp["delivery"]["branch"] == "pulse/7"
    assert wp["delivery"]["pr_title"].startswith("pulse:")
    assert wp["delivery"]["mode"] == "dry-run"


# === Traceability ===
# AC1: test_brief_derives_candidates_and_radius_from_issue_text
# AC2: test_gate_approves_a_small_scoped_issue
# AC3: test_gate_rejects_when_bounds_are_exceeded
# AC4: test_execution_leaves_the_main_tree_untouched
# AC5: test_prove_runs_the_dependent_test_and_passes
# AC6: test_silent_promise_break_without_test_change_is_rejected
# AC7: test_verdict_classifies_each_changed_unit
# AC8: test_reruns_version_the_workpiece
# AC9: test_pr_body_renders_all_five_artifacts
# AC10: test_dry_run_returns_delivery_payload
