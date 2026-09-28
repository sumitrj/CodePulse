"""Tests for the resolve-rate scaffold — specs/bench-exec/SPEC.md.

Everything here runs against fakes. The harness itself is unvalidated end to
end by design (it needs containers and a paid model run); what these tests
pin down is that the plumbing is right and the gate actually gates.
"""
import json

import pytest

from bench import exec_harness
from bench.dataset import Instance
from bench.exec_harness import Prediction, _strip_test_files, write_predictions


def _instance(n: int = 1) -> Instance:
    return Instance(f"demo__demo-{n}", "demo/demo", "0" * 40, "it breaks", "GOLD")


def test_predictions_file_is_exactly_the_official_schema(tmp_path):
    path = write_predictions(
        [Prediction("demo__demo-1", "diff --git a/x b/x\n", "with-map")],
        tmp_path / "p.jsonl")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 1
    assert set(rows[0]) == {"instance_id", "model_patch", "model_name_or_path"}


def test_preflight_names_every_blocker_not_just_the_first(monkeypatch):
    monkeypatch.setattr(exec_harness, "_container_runtime", lambda: "")
    monkeypatch.setattr(exec_harness.shutil, "which", lambda name: None)
    blockers = exec_harness.preflight(solver_binary="definitely-not-installed")
    assert len(blockers) >= 2
    assert any("container runtime" in b for b in blockers)
    assert any("not on PATH" in b for b in blockers)


def test_a_real_run_refuses_to_start_when_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(exec_harness, "preflight", lambda *a, **k: ["no docker"])
    with pytest.raises(RuntimeError, match="no docker"):
        exec_harness.run([_instance()], {"arm": lambda i, d: ""},
                         tmp_path / "out", tmp_path / "cache", dry_run=False)


def test_dry_run_writes_predictions_without_solving(tmp_path, monkeypatch):
    monkeypatch.setattr(exec_harness, "preflight", lambda *a, **k: ["would block"])
    monkeypatch.setattr("bench.repos.checkout", lambda repo, commit, cache: tmp_path)

    def explode(instance, checkout):
        raise AssertionError("dry run must not call the solver")

    summary = exec_harness.run([_instance()], {"with-map": explode},
                               tmp_path / "out", tmp_path / "cache", dry_run=True)
    assert summary["dry_run"] is True
    assert summary["arms"]["with-map"]["patched"] == 0
    assert (tmp_path / "out" / "predictions-with-map.jsonl").is_file()


def test_solver_patches_drop_test_file_edits():
    diff = (
        "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
        "@@ -1 +1 @@\n-a\n+b\n"
        "diff --git a/tests/test_app.py b/tests/test_app.py\n"
        "--- a/tests/test_app.py\n+++ b/tests/test_app.py\n@@ -1 +1 @@\n-c\n+d\n"
    )
    from bench.truth import is_test_path
    stripped = _strip_test_files(diff, is_test_path)
    assert "src/app.py" in stripped
    assert "tests/test_app.py" not in stripped


def test_both_arms_share_the_solver_and_differ_only_in_wiring():
    """AC5: the arms must not diverge on model, turns, or prompt."""
    with_map = exec_harness.claude_solver(with_map=True, model="m", turns=7)
    without = exec_harness.claude_solver(with_map=False, model="m", turns=7)
    a, b = with_map.__closure__, without.__closure__
    bound_a = {c.cell_contents for c in a if not callable(c.cell_contents)}
    bound_b = {c.cell_contents for c in b if not callable(c.cell_contents)}
    # The only difference in the captured configuration is the True/False flag.
    assert bound_a - bound_b == {True} and bound_b - bound_a == {False}
