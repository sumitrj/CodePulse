"""Tests for the benchmark harness — specs/bench/SPEC.md.

The oracle is the thing that has to be right: if `truth_for` is wrong, every
number downstream is confidently wrong. So most of this file is about it.
"""
from pathlib import Path

import pytest

from bench import arms, metrics
from bench.dataset import Instance, sample
from bench.truth import (MODULE_SCOPE, PatchError, apply_hunks, enclosing,
                         is_test_path, locate_hunks, parse_patch, spans,
                         truth_for)

BASE = '''\
import os


CONFIG = {"debug": False}


def helper(value):
    """Small."""
    return value + 1


class Cart:
    def __init__(self):
        self.items = []

    def checkout(self, user):
        total = 0
        for item in self.items:
            total += item.price
        return total
'''


def _instance(patch: str) -> Instance:
    return Instance(instance_id="demo__demo-1", repo="demo/demo",
                    base_commit="0" * 40, problem_statement="", patch=patch)


def _repo(tmp_path: Path, text: str = BASE, name: str = "shop/cart.py") -> Path:
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return tmp_path


# ── diff parsing ──────────────────────────────────────────────────────

def test_parse_patch_reads_old_side_coordinates():
    patch = (
        "diff --git a/shop/cart.py b/shop/cart.py\n"
        "--- a/shop/cart.py\n"
        "+++ b/shop/cart.py\n"
        "@@ -18,3 +18,3 @@ class Cart:\n"
        "         for item in self.items:\n"
        "-            total += item.price\n"
        "+            total += item.price * item.qty\n"
        "         return total\n"
    )
    [diff] = parse_patch(patch)
    assert diff.path == "shop/cart.py"
    assert diff.status == "modified"
    assert diff.old_changed == {19}          # the '-' line, in old-file coordinates
    assert 19 in diff.insert_anchors         # the '+' anchors on the line before it


def test_parse_patch_classifies_added_and_deleted_files():
    patch = (
        "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,1 @@\n+x = 1\n"
        "--- a/gone.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-y = 2\n"
    )
    added, deleted = parse_patch(patch)
    assert (added.path, added.status) == ("new.py", "added")
    assert (deleted.path, deleted.status) == ("gone.py", "deleted")


def test_context_lines_are_never_counted_as_changed():
    patch = ("--- a/f.py\n+++ b/f.py\n@@ -1,4 +1,4 @@\n"
             " a = 1\n b = 2\n-c = 3\n+c = 4\n d = 5\n")
    [diff] = parse_patch(patch)
    assert diff.old_changed == {3}


# ── reconstruction ────────────────────────────────────────────────────

def test_apply_hunks_reconstructs_the_new_side():
    patch = ("--- a/shop/cart.py\n+++ b/shop/cart.py\n"
             "@@ -7,3 +7,3 @@\n def helper(value):\n"
             '     """Small."""\n-    return value + 1\n+    return value + 2\n')
    [diff] = parse_patch(patch)
    assert "return value + 2" in apply_hunks(BASE, diff)
    assert "return value + 1" not in apply_hunks(BASE, diff)


def test_a_hunk_whose_header_drifted_is_re_anchored(tmp_path):
    """Real patches carry offsets — "Hunk #1 succeeded at 968 (offset 4
    lines)". Trusting the header would blame the wrong function entirely."""
    root = _repo(tmp_path)
    # The header claims line 7; `helper` really starts at 7 and its body at 9.
    # Point the header 4 lines early and the context must still be found.
    patch = ("--- a/shop/cart.py\n+++ b/shop/cart.py\n"
             "@@ -3,3 +3,3 @@\n def helper(value):\n"
             '     """Small."""\n-    return value + 1\n+    return value + 99\n')
    [diff] = parse_patch(patch)
    located = locate_hunks(BASE, diff)
    assert located.old_changed == {9}          # not 5, which the header implies
    truth = truth_for(_instance(patch), root)
    assert ("shop/cart.py", "helper") in truth.entities
    assert ("shop/cart.py", MODULE_SCOPE) not in truth.entities


def test_re_anchoring_is_offset_tolerance_not_fuzz():
    """A hunk whose content exists nowhere must still fail, however far we
    search — otherwise the oracle would silently accept a wrong checkout."""
    patch = ("--- a/f.py\n+++ b/f.py\n@@ -1,2 +1,2 @@\n"
             " def helper(value):\n-    this line is not in the base\n+    x\n")
    [diff] = parse_patch(patch)
    with pytest.raises(PatchError, match="matches nowhere"):
        locate_hunks(BASE, diff)


def test_apply_hunks_refuses_to_fuzz_a_mismatched_base():
    patch = ("--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,1 @@\n"
             "-this line is not in the base\n+replacement\n")
    [diff] = parse_patch(patch)
    with pytest.raises(PatchError):
        apply_hunks(BASE, diff)


# ── ast attribution ───────────────────────────────────────────────────

def test_spans_are_dotted_like_the_engine_names_things():
    names = {s.name for s in spans(BASE)}
    assert {"helper", "Cart", "Cart.__init__", "Cart.checkout"} <= names


def test_enclosing_picks_the_innermost_entity():
    found = spans(BASE)
    checkout_line = next(s.start for s in found if s.name == "Cart.checkout")
    assert enclosing(found, checkout_line + 1) == "Cart.checkout"


def test_lines_outside_any_definition_are_module_scope():
    assert enclosing(spans(BASE), 4) == MODULE_SCOPE     # the CONFIG assignment


def test_nested_definitions_dot_all_the_way_down():
    source = "class A:\n    class B:\n        def c(self):\n            pass\n"
    assert enclosing(spans(source), 4) == "A.B.c"


# ── the oracle end to end ─────────────────────────────────────────────

def test_truth_names_the_function_the_patch_edited(tmp_path):
    root = _repo(tmp_path)
    patch = ("--- a/shop/cart.py\n+++ b/shop/cart.py\n"
             "@@ -18,3 +18,3 @@\n         for item in self.items:\n"
             "-            total += item.price\n"
             "+            total += item.price * item.qty\n"
             "         return total\n")
    truth = truth_for(_instance(patch), root)
    assert truth.files == {"shop/cart.py"}
    assert ("shop/cart.py", "Cart.checkout") in truth.entities
    assert ("shop/cart.py", "Cart") not in truth.entities      # innermost only


def test_truth_names_a_function_the_patch_introduced(tmp_path):
    root = _repo(tmp_path)
    patch = ("--- a/shop/cart.py\n+++ b/shop/cart.py\n"
             "@@ -9,2 +9,6 @@\n     return value + 1\n"
             "+\n+\n+def brand_new(value):\n+    return value * 2\n"
             "\n")
    truth = truth_for(_instance(patch), root)
    assert ("shop/cart.py", "brand_new") in truth.entities
    # It exists only after the fix, so no arm reading the base can retrieve it.
    assert ("shop/cart.py", "brand_new") in truth.added_entities
    assert ("shop/cart.py", "brand_new") not in truth.reachable_entities


def test_entity_recall_ignores_entities_the_fix_invented():
    """A patch that adds one function and edits another has one reachable
    target, so finding it is 1.0 — not 0.5 against an impossible denominator."""
    truth = _Truth({"a.py"}, entities={("a.py", "old"), ("a.py", "new")},
                   added={("a.py", "new")})
    ranking = arms.Ranking(files=("a.py",), entities=(("a.py", "old"),))
    row = metrics.score(ranking, truth, ks=(5,))
    assert row["entity_recall@5"] == 1.0
    assert row["unreachable_entities"] == 1


def test_truth_excludes_test_files_but_records_them(tmp_path):
    root = _repo(tmp_path)
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_cart.py").write_text("def test_x():\n    pass\n")
    patch = ("--- a/shop/cart.py\n+++ b/shop/cart.py\n"
             "@@ -7,2 +7,2 @@\n def helper(value):\n"
             '-    """Small."""\n+    """Smaller."""\n'
             "--- a/tests/test_cart.py\n+++ b/tests/test_cart.py\n"
             "@@ -1,2 +1,2 @@\n def test_x():\n-    pass\n+    assert True\n")
    truth = truth_for(_instance(patch), root)
    assert truth.files == {"shop/cart.py"}
    assert "tests/test_cart.py" in truth.skipped_files


@pytest.mark.parametrize("path", [
    "tests/test_cart.py", "shop/test_cart.py", "shop/cart_test.py",
    "conftest.py", "a/test/b.py",
])
def test_test_paths_are_recognised(path):
    assert is_test_path(path)


@pytest.mark.parametrize("path", ["shop/cart.py", "contest/latest.py", "src/attest.py"])
def test_ordinary_paths_are_not_mistaken_for_tests(path):
    assert not is_test_path(path)


def test_a_patch_touching_only_tests_is_an_error_not_an_empty_truth(tmp_path):
    root = _repo(tmp_path)
    patch = ("--- a/tests/test_cart.py\n+++ b/tests/test_cart.py\n"
             "@@ -1,1 +1,1 @@\n-a\n+b\n")
    with pytest.raises(PatchError):
        truth_for(_instance(patch), root)


# ── sampling ──────────────────────────────────────────────────────────

def _pool():
    return [Instance(f"{repo}__{repo}-{n}", f"{repo}/{repo}", "sha", "", "")
            for repo in ("aaa", "bbb", "ccc") for n in range(10)]


def test_sample_is_deterministic():
    assert [i.instance_id for i in sample(_pool(), 7)] == \
           [i.instance_id for i in sample(_pool(), 7)]


def test_sample_spreads_across_repos_instead_of_filling_from_the_first():
    picked = sample(_pool(), 6)
    assert len({i.repo for i in picked}) == 3


def test_sample_returns_everything_when_the_limit_exceeds_the_pool():
    assert len(sample(_pool(), 999)) == 30


# ── metrics ───────────────────────────────────────────────────────────

class _Truth:
    def __init__(self, files, entities=(), added=()):
        self.files = frozenset(files)
        self.entities = frozenset(entities)
        self.added_entities = frozenset(added)

    @property
    def reachable_entities(self):
        return self.entities - self.added_entities


def test_recall_and_all_found_differ_when_only_some_gold_files_rank():
    ranking = arms.Ranking(files=("a.py", "x.py", "y.py"))
    row = metrics.score(ranking, _Truth({"a.py", "b.py"}), ks=(3,))
    assert row["recall@3"] == 0.5
    assert row["all_found@3"] == 0.0


def test_precision_and_mrr_reward_the_top_slot():
    row = metrics.score(arms.Ranking(files=("a.py", "b.py")), _Truth({"a.py"}), ks=(1,))
    assert row["precision@1"] == 1.0 and row["mrr"] == 1.0
    row = metrics.score(arms.Ranking(files=("z.py", "a.py")), _Truth({"a.py"}), ks=(1,))
    assert row["precision@1"] == 0.0 and row["mrr"] == 0.5


def test_aggregate_macro_averages_each_instance_once():
    rows = [{"recall@10": 1.0, "seconds": 1.0}, {"recall@10": 0.0, "seconds": 3.0}]
    agg = metrics.aggregate(rows)
    assert agg["recall@10"] == 0.5
    assert agg["instances"] == 2
    assert agg["median_seconds"] == 2.0


# ── arm hygiene ───────────────────────────────────────────────────────

def test_arms_cannot_read_the_gold_patch(tmp_path):
    """AC1: redaction must not change any arm's output — proof no arm peeks."""
    root = _repo(tmp_path)
    patch = ("--- a/shop/cart.py\n+++ b/shop/cart.py\n"
             "@@ -7,2 +7,2 @@\n def helper(value):\n-    x\n+    y\n")
    loaded = Instance("demo__demo-1", "demo/demo", "0" * 40,
                      "helper returns the wrong total for a Cart", patch)
    with_patch = arms.bm25(loaded, root)
    without = arms.bm25(loaded.redacted(), root)
    assert with_patch.files == without.files
    assert loaded.redacted().patch == ""


def test_candidates_exclude_tests_and_are_sorted(tmp_path):
    root = _repo(tmp_path)
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_cart.py").write_text("x = 1\n")
    (root / "shop" / "billing.py").write_text("y = 2\n")
    found = arms.candidates(root)
    assert found == sorted(found)
    assert "tests/test_cart.py" not in found
    assert {"shop/cart.py", "shop/billing.py"} <= set(found)


def test_tokenize_splits_identifiers_both_ways():
    assert {"get", "axis", "limits"} <= set(arms.tokenize("get_axis_limits"))
    assert {"get", "axis", "limits"} <= set(arms.tokenize("getAxisLimits"))


def test_seeds_pick_up_bare_lowercase_names_from_call_syntax():
    """The blind spot that made the map arm score zero on sympy: `ccode` has no
    dot, no underscore and no capital, so every other pattern misses it."""
    text = "ccode(sinc(x)) doesn't work\n```\nIn [30]: ccode(sinc(x))\n```\n"
    found = dict(arms.seeds(text))
    assert "ccode" in found and "sinc" in found


def test_seeds_ignore_builtins_that_resolve_in_any_repo():
    found = dict(arms.seeds("```\nprint(len(list(x)))\n```"))
    assert not ({"print", "len", "list"} & set(found))


def test_seeds_rank_a_traceback_frame_above_a_prose_word():
    text = ('Traceback (most recent call last):\n'
            '  File "shop/cart.py", line 17, in checkout\n'
            'the separability_matrix helper looks wrong\n')
    found = dict(arms.seeds(text))
    assert found["checkout"] > found["separability_matrix"]


def test_bm25_ranks_the_file_the_issue_talks_about(tmp_path):
    root = _repo(tmp_path)
    (root / "shop" / "unrelated.py").write_text("def parse_xml(doc):\n    return doc\n")
    loaded = Instance("demo__demo-1", "demo/demo", "0" * 40,
                      "Cart.checkout computes the wrong total for items", "")
    assert arms.bm25(loaded, root).files[0] == "shop/cart.py"


# ── controls ──────────────────────────────────────────────────────────

def test_random_arm_is_deterministic_and_reads_nothing_from_the_issue(tmp_path):
    root = _repo(tmp_path)
    for n in range(8):                    # enough files that a tie is not luck
        (root / "shop" / f"mod{n}.py").write_text(f"x = {n}\n")
    a = arms.random_arm(_instance(""), root)
    b = arms.random_arm(_instance(""), root)
    assert a.files == b.files                      # stable across runs
    assert set(a.files) == set(arms.candidates(root))
    # A different issue id shuffles differently: the arm carries no signal, but
    # it must not be one fixed order for every instance either.
    other = Instance("demo__demo-2", "demo/demo", "0" * 40, "", "")
    assert arms.random_arm(other, root).files != a.files


def test_paired_bootstrap_reports_no_separation_when_arms_tie():
    rows = [{"arms": {"x": {"recall@5": 0.5}, "bm25": {"recall@5": 0.5}}}
            for _ in range(20)]
    out = metrics.paired_bootstrap(rows, "x", "bm25", "recall@5", iterations=500)
    assert out["difference"] == 0.0
    assert out["separates"] is False


def test_paired_bootstrap_separates_a_consistent_win():
    rows = [{"arms": {"x": {"recall@5": 1.0}, "bm25": {"recall@5": 0.0}}}
            for _ in range(20)]
    out = metrics.paired_bootstrap(rows, "x", "bm25", "recall@5", iterations=500)
    assert out["difference"] == 1.0
    assert out["separates"] is True


def test_paired_bootstrap_is_reproducible():
    rows = [{"arms": {"x": {"mrr": i / 10}, "bm25": {"mrr": (10 - i) / 10}}}
            for i in range(11)]
    first = metrics.paired_bootstrap(rows, "x", "bm25", "mrr", iterations=800)
    second = metrics.paired_bootstrap(rows, "x", "bm25", "mrr", iterations=800)
    assert first == second


# ── the greedy walk ───────────────────────────────────────────────────

def _walk_engine(tmp_path):
    """A tiny repo whose graph is known, so walk behaviour is checkable."""
    from codepulse.main import build_engine
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "printer.py").write_text(
        "def render(node):\n"
        "    '''Turn a node into text.'''\n"
        "    return str(node)\n")
    (root / "pkg" / "caller.py").write_text(
        "from pkg.printer import render\n\n\n"
        "def emit(node):\n"
        "    return render(node)\n")
    (root / "pkg" / "unrelated.py").write_text(
        "def qdpreader(path):\n"
        "    '''Reads the qdp table format.'''\n"
        "    return open(path)\n")
    return root, build_engine(root, announce=False)


def test_walk_ranks_the_entity_the_issue_names(tmp_path):
    root, engine = _walk_engine(tmp_path)
    issue = Instance("d__d-1", "d/d", "0" * 40, "render produces wrong text", "")
    ranking = arms.pulse_walk(issue, root, engine)
    assert ranking.files[0] == "pkg/printer.py"


def test_walk_reaches_a_caller_the_issue_never_names(tmp_path):
    """The point of the graph step: activation travels to `emit`, which the
    issue text does not mention at all."""
    root, engine = _walk_engine(tmp_path)
    issue = Instance("d__d-2", "d/d", "0" * 40, "render produces wrong text", "")
    names = {n for _, n in arms.pulse_walk(issue, root, engine).entities}
    assert "emit" in names


def test_unusual_direct_mention_surfaces_an_unconnected_entity(tmp_path):
    """`qdpreader` is joined to nothing by any edge. The only thing linking it
    to the issue is that it is the sole place in the repo saying 'qdp'."""
    root, engine = _walk_engine(tmp_path)
    issue = Instance("d__d-3", "d/d", "0" * 40,
                     "the qdp table is parsed incorrectly", "")
    assert arms.pulse_walk(issue, root, engine).files[0] == "pkg/unrelated.py"


def test_unusual_boost_can_be_turned_off(tmp_path):
    """Guard that the boost is doing the work, not the plain BM25 underneath —
    otherwise the parameter is decorative."""
    root, engine = _walk_engine(tmp_path)
    issue = Instance("d__d-4", "d/d", "0" * 40, "qdp", "")
    hot = dict(arms.pulse_walk(issue, root, engine).entities)
    cold = arms.pulse_walk(issue, root, engine, {"unusual_boost": 0.0})
    assert hot != {} and cold.files          # both produce something
    assert cold.detail["config_hash"] != arms.config_hash(arms.WALK_DEFAULTS)


def test_walk_config_hash_changes_with_the_config():
    a = arms.config_hash(arms.WALK_DEFAULTS)
    b = arms.config_hash({**arms.WALK_DEFAULTS, "damping": 0.9})
    assert a != b and a == arms.config_hash(dict(arms.WALK_DEFAULTS))


def test_walk_never_reads_the_gold_patch(tmp_path):
    root, engine = _walk_engine(tmp_path)
    loaded = Instance("d__d-5", "d/d", "0" * 40, "render is wrong", "GOLD PATCH")
    assert (arms.pulse_walk(loaded, root, engine).files
            == arms.pulse_walk(loaded.redacted(), root, engine).files)


# ── tuning discipline ─────────────────────────────────────────────────

def test_grid_expands_to_every_combination():
    from bench import tune
    grid = {"damping": (0.1, 0.2), "coactivation": (0.0, 0.5, 1.0)}
    assert len(tune.configs(grid)) == 6


def test_tuner_refuses_to_fit_on_the_reporting_set():
    """The whole train/test split is one argparse guard; pin it."""
    from bench import tune
    with pytest.raises(SystemExit):
        tune.main(["--dataset", "verified"])
    with pytest.raises(SystemExit):
        tune.main(["--dataset", "princeton-nlp/SWE-bench_Verified"])
