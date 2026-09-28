"""
Tests for the PR delta verb — diff-grained blast radius. Maps 1:1 to
acceptance criteria in specs/delta/SPEC.md. Red until codepulse/delta.py
exists.
"""
import difflib
import subprocess
from pathlib import Path

import pytest

from codepulse.engine import Addr, Engine
from codepulse.recipes import RECIPES_DIR, builtin_recipes, load_recipe
from codepulse import delta  # ImportError until implementation — expected
from codepulse.delta import (  # noqa: F401 — the contract under test
    LOAD_BEARING_FAN_IN,
    FileDiff,
    compute_delta,
    delta_text,
    git_delta,
    parse_diff,
)


# --- fixture repo: base and head states of one small system ------------
# The PR under review: total() modified, legacy() deleted, fresh() added,
# helper() untouched. orders.audit still calls the deleted legacy().

BASE = {
    "billing.py": (
        "def total(items):\n"
        "    return sum(items)\n"
        "\n"
        "def helper(items):\n"
        "    return list(items)\n"
        "\n"
        "def legacy(items):\n"
        "    return items\n"
    ),
    "orders.py": (
        "from billing import total, legacy\n"
        "\n"
        "def process(items):\n"
        "    return total(items)\n"
        "\n"
        "def audit(items):\n"
        "    return legacy(items)\n"
    ),
    "report.py": (
        "from orders import process\n"
        "\n"
        "def summary(items):\n"
        "    return process(items)\n"
    ),
    "alpha.py": (
        "from billing import total\n"
        "\n"
        "def use_alpha(items):\n"
        "    return total(items)\n"
    ),
    "beta.py": (
        "from billing import total\n"
        "\n"
        "def use_beta(items):\n"
        "    return total(items)\n"
    ),
}

HEAD = {
    **BASE,
    "billing.py": (
        "def total(items):\n"
        "    return sum(items) + 0\n"
        "\n"
        "def helper(items):\n"
        "    return list(items)\n"
        "\n"
        "def fresh(items):\n"
        "    return items[:1]\n"
    ),
}


def make_diff(base: dict, head: dict) -> str:
    """A valid unified diff between two file sets, difflib-style headers."""
    chunks = []
    for path in sorted(set(base) | set(head)):
        old, new = base.get(path), head.get(path)
        if old == new:
            continue
        chunks.append("".join(difflib.unified_diff(
            (old or "").splitlines(keepends=True),
            (new or "").splitlines(keepends=True),
            fromfile=f"a/{path}" if old is not None else "/dev/null",
            tofile=f"b/{path}" if new is not None else "/dev/null",
        )))
    return "".join(chunks)


@pytest.fixture
def python_recipe():
    return load_recipe(RECIPES_DIR / "python.yml")


@pytest.fixture
def engine(tmp_path, python_recipe):
    """Engine over the HEAD state — the map always reflects the working tree."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for rel, src in HEAD.items():
        (repo / rel).write_text(src)
    eng = Engine(tmp_path / "pulse.db", [python_recipe], root=repo)
    eng.apply()
    return eng


@pytest.fixture
def report(engine):
    return compute_delta(engine, make_diff(BASE, HEAD), base_files=BASE)


def touched_by_addr(report):
    return {t.addr: t for t in report.touched}


# === AC1: parse_diff yields added/removed runs, never context lines ===

def test_parse_diff_returns_changed_runs_excluding_context():
    text = (
        "--- a/billing.py\n"
        "+++ b/billing.py\n"
        "@@ -1,5 +1,5 @@\n"
        " def total(items):\n"
        "-    return sum(items)\n"
        "+    return sum(items) + 0\n"
        " \n"
        " def helper(items):\n"
    )

    [diff] = parse_diff(text)

    assert diff.added == ((2, 2),)
    assert diff.removed == ((2, 2),)


# === AC2: parse_diff classifies added / modified / deleted files ===

@pytest.mark.parametrize("text, path, status", [
    ("--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,1 @@\n+x = 1\n", "new.py", "added"),
    ("--- a/old.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-x = 1\n", "old.py", "deleted"),
    ("--- a/mod.py\n+++ b/mod.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n", "mod.py", "modified"),
])
def test_parse_diff_classifies_file_status(text, path, status):
    [diff] = parse_diff(text)

    assert (diff.path, diff.status) == (path, status)


# === AC3: only entities whose span the diff touches are reported ===

def test_diff_inside_one_function_does_not_touch_its_siblings(report):
    touched = touched_by_addr(report)

    assert Addr("billing.py", "total") in touched
    assert touched[Addr("billing.py", "total")].change == "modified"
    assert Addr("billing.py", "helper") not in touched


# === AC4: an entity only in head is added, with an empty radius ===

def test_new_function_is_classified_added_with_empty_radius(report):
    fresh = touched_by_addr(report)[Addr("billing.py", "fresh")]

    assert fresh.change == "added"
    assert fresh.radius == {}


# === AC5: a deleted entity flags head entities still referencing it ===

def test_deleted_function_lists_still_referencing_dependents(report):
    legacy = touched_by_addr(report)[Addr("billing.py", "legacy")]

    assert legacy.change == "deleted"
    assert legacy.radius.get(Addr("orders.py", "audit")) == 1


# === AC6: a modified entity carries its transitive radius by hops ===

def test_modified_entity_radius_matches_transitive_dependents(report):
    radius = touched_by_addr(report)[Addr("billing.py", "total")].radius

    assert radius[Addr("orders.py", "process")] == 1
    assert radius[Addr("report.py", "summary")] == 2


# === AC7: load_bearing flags direct fan-in >= LOAD_BEARING_FAN_IN ===

def test_load_bearing_flag_tracks_direct_fan_in(report):
    touched = touched_by_addr(report)

    # total: called directly by process, use_alpha, use_beta (= 3)
    assert touched[Addr("billing.py", "total")].load_bearing is True
    assert touched[Addr("billing.py", "fresh")].load_bearing is False


# === AC8: cross-language dependents of a changed file are surfaced ===

def test_cross_language_dependents_include_dockerfile_copy(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    base_app = "def run():\n    return 1\n"
    head_app = "def run():\n    return 2\n"
    (repo / "app.py").write_text(head_app)
    (repo / "Dockerfile").write_text("FROM python:3.11\nCOPY app.py /app/app.py\n")
    eng = Engine(tmp_path / "pulse.db", builtin_recipes(), root=repo)
    eng.apply()

    report = compute_delta(
        eng, make_diff({"app.py": base_app}, {"app.py": head_app}),
        base_files={"app.py": base_app})

    assert any("Dockerfile" in label for label in report.cross_language.get("app.py", ()))


# === AC9: at_risk unions the radii at min hops, touched excluded ===

def test_at_risk_unions_radii_at_min_hops_excluding_touched(report):
    touched_addrs = {t.addr for t in report.touched}

    assert report.at_risk[Addr("orders.py", "process")] == 1
    assert report.at_risk[Addr("report.py", "summary")] == 2
    assert not touched_addrs & set(report.at_risk)


# === AC10: delta_text speaks plain sentences; empty diff answers plainly ===

def test_delta_text_names_changes_and_answers_empty_diff_plainly(engine):
    out = delta_text(engine, make_diff(BASE, HEAD), base_files=BASE)
    empty = delta_text(engine, "")

    assert "billing.py :: total" in out
    assert "modified" in out
    assert empty == "The diff touches nothing on the map."


# === AC11: git_delta reads the diff and base contents from git itself ===

def test_git_delta_detects_modified_and_deleted_from_git(tmp_path, python_recipe):
    repo = tmp_path / "repo"
    repo.mkdir()
    for rel, src in BASE.items():
        (repo / rel).write_text(src)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    for rel, src in HEAD.items():
        (repo / rel).write_text(src)
    eng = Engine(tmp_path / "pulse.db", [python_recipe], root=repo)
    eng.apply()

    touched = touched_by_addr(git_delta(eng, base="HEAD"))

    assert touched[Addr("billing.py", "total")].change == "modified"
    assert touched[Addr("billing.py", "legacy")].change == "deleted"



@pytest.mark.parametrize("base", ["--output=written.txt", "no-such-rev"])
def test_git_delta_refuses_option_shaped_or_unknown_base(tmp_path, python_recipe, base):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "billing.py").write_text(BASE["billing.py"])
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    eng = Engine(tmp_path / "pulse.db", [python_recipe], root=repo)

    with pytest.raises(ValueError):
        git_delta(eng, base=base)
    assert not (repo / "written.txt").exists()

# === AC12: pulse_delta is listed and dispatches to the delta answer ===

def test_pulse_delta_is_listed_and_dispatches_diff(engine):
    from codepulse.mcp_server import dispatch, tool_names

    out = dispatch(engine, "pulse_delta", {"diff": make_diff(BASE, HEAD)})

    assert "pulse_delta" in tool_names()
    assert "billing.py :: total" in out


# === Traceability ===
# AC1:  test_parse_diff_returns_changed_runs_excluding_context
# AC2:  test_parse_diff_classifies_file_status (x3 parametrized)
# AC3:  test_diff_inside_one_function_does_not_touch_its_siblings
# AC4:  test_new_function_is_classified_added_with_empty_radius
# AC5:  test_deleted_function_lists_still_referencing_dependents
# AC6:  test_modified_entity_radius_matches_transitive_dependents
# AC7:  test_load_bearing_flag_tracks_direct_fan_in
# AC8:  test_cross_language_dependents_include_dockerfile_copy
# AC9:  test_at_risk_unions_radii_at_min_hops_excluding_touched
# AC10: test_delta_text_names_changes_and_answers_empty_diff_plainly
# AC11: test_git_delta_detects_modified_and_deleted_from_git
#       test_git_delta_refuses_option_shaped_or_unknown_base (x2)
# AC12: test_pulse_delta_is_listed_and_dispatches_diff
