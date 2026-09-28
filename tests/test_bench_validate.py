"""Our patch handling, checked against git's — specs/bench/SPEC.md AC3b.

`bench/truth.py` is the one component whose bug would invalidate every number
this benchmark produces, so it is not allowed to be the only implementation
that agrees with itself. Each case here builds a file, mutates it, generates a
real unified diff with `difflib`, applies it with real `git apply`, and demands
our reconstruction be byte-identical to git's.

Patches are generated rather than hand-written on purpose: a hand-written
fixture is a third implementation of the diff format and gets it subtly wrong
(the first draft of this file did, and git caught it). These need no network,
so they run anywhere. The 300-instance version is `python -m bench.validate`.
"""
import difflib
import shutil
import subprocess

import pytest

from bench.truth import PatchError, apply_hunks, locate_hunks, parse_patch
from bench.validate import git_reconstruct

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git required")

PATH = "shop/cart.py"
BASE = """\
import os


def helper(value):
    return value + 1


class Cart:
    def __init__(self):
        self.items = []

    def checkout(self, user):
        total = 0
        for item in self.items:
            total += item.price
        return total


def farewell():
    return "bye"
"""


def _patch(base: str, new: str, context: int = 3) -> str:
    """A real unified diff, the way git itself would write one."""
    return "".join(difflib.unified_diff(
        base.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{PATH}", tofile=f"b/{PATH}", n=context))


def _agree(tmp_path, new: str, context: int = 3) -> str:
    """Reconstruct with git and with us; assert byte-identical; return the text."""
    patch = _patch(BASE, new, context)
    theirs = git_reconstruct(patch, {PATH: BASE}, tmp_path / "scratch")[PATH]
    [diff] = [d for d in parse_patch(patch) if d.path == PATH]
    ours = apply_hunks(BASE, diff)
    assert ours == theirs, f"we and git disagree\n--ours--\n{ours}\n--git--\n{theirs}"
    assert ours == new
    return ours


def test_replacement_agrees_with_git(tmp_path):
    _agree(tmp_path, BASE.replace("return value + 1", "return value + 2"))


def test_insertion_agrees_with_git(tmp_path):
    _agree(tmp_path, BASE.replace(
        "def farewell():", "def brand_new(x):\n    return x * 2\n\n\ndef farewell():"))


def test_deletion_agrees_with_git(tmp_path):
    _agree(tmp_path, BASE.replace("\n\ndef farewell():\n    return \"bye\"\n", "\n"))


def test_first_line_change_agrees_with_git(tmp_path):
    _agree(tmp_path, BASE.replace("import os", "import os.path", 1))


def test_last_line_change_agrees_with_git(tmp_path):
    _agree(tmp_path, BASE.replace('return "bye"', 'return "farewell"'))


def test_two_distant_hunks_agree_with_git(tmp_path):
    new = (BASE.replace("return value + 1", "return value + 7")
               .replace('return "bye"', 'return "adieu"'))
    assert len(parse_patch(_patch(BASE, new))[0].hunks) == 2
    _agree(tmp_path, new)


def test_adjacent_hunks_merge_the_way_git_expects(tmp_path):
    new = (BASE.replace("total = 0", "total = 0.0")
               .replace("total += item.price", "total += item.price * item.qty"))
    _agree(tmp_path, new)


def test_method_body_rewrite_agrees_with_git(tmp_path):
    new = BASE.replace(
        "        total = 0\n        for item in self.items:\n"
        "            total += item.price\n        return total\n",
        "        return sum(i.price for i in self.items)\n")
    _agree(tmp_path, new)


@pytest.mark.parametrize("context", [0, 1, 3, 5])
def test_agreement_holds_at_every_context_width(tmp_path, context):
    """SWE-bench patches are not all `-U3`; zero-context hunks are the tricky
    case because there is nothing to re-anchor on but the removed lines."""
    _agree(tmp_path, BASE.replace("return value + 1", "return value + 5"), context)


def test_a_drifted_header_recovers_the_answer_git_gives_for_the_true_one(tmp_path):
    """Drift must not change our answer.

    We are deliberately *more* tolerant than `git apply` here: git will refuse
    a header that has moved far enough, while we search a wide window for an
    exact content match. So the claim under test is not "git accepts this too"
    — it is that our re-anchored reconstruction equals what git produces from
    the undrifted patch, and that the corrected coordinates point at the real
    changed line rather than the stated one. Attributing a change to whatever
    entity happens to sit at a stale line number is the bug this prevents.
    """
    new = BASE.replace("return value + 1", "return value + 42")
    honest = _patch(BASE, new)
    reference = git_reconstruct(honest, {PATH: BASE}, tmp_path / "scratch")[PATH]

    drifted = "\n".join(_shift_header(line, -3) if line.startswith("@@") else line
                        for line in honest.splitlines()) + "\n"
    [diff] = [d for d in parse_patch(drifted) if d.path == PATH]
    assert apply_hunks(BASE, diff) == reference == new
    assert min(locate_hunks(BASE, diff).old_changed) == 5      # not the stated line


def _shift_header(header: str, by: int) -> str:
    old = header.split("@@")[1].strip().split(" ")[0]          # "-4,7"
    start, _, count = old.lstrip("-").partition(",")
    return header.replace(f"-{start},{count}", f"-{max(int(start) + by, 1)},{count}", 1)


def test_we_reject_what_git_also_rejects(tmp_path):
    """Symmetry matters as much as agreement: a patch that does not belong to
    this file must fail for both of us, or our skip counts are dishonest."""
    patch = ("--- a/shop/cart.py\n+++ b/shop/cart.py\n@@ -1,3 +1,3 @@\n"
             " import nothing\n-this line is not in the base\n+replacement\n rubbish\n")
    with pytest.raises(PatchError):
        git_reconstruct(patch, {PATH: BASE}, tmp_path / "g")
    [diff] = parse_patch(patch)
    with pytest.raises(PatchError):
        apply_hunks(BASE, diff)


def test_git_apply_is_actually_being_exercised(tmp_path):
    """Guard against the differential check silently degrading into a no-op —
    then every case above would pass vacuously."""
    assert subprocess.run(["git", "--version"], capture_output=True).returncode == 0
    theirs = git_reconstruct(
        _patch("a = 1\n", "a = 2\n").replace(f"a/{PATH}", "a/f.py")
                                    .replace(f"b/{PATH}", "b/f.py"),
        {"f.py": "a = 1\n"}, tmp_path / "probe")
    assert theirs["f.py"] == "a = 2\n"
