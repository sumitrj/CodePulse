"""Resolve rate — the expensive number. See specs/bench-exec/SPEC.md.

We generate patches; the official `swebench` harness grades them. That split is
deliberate and not negotiable: a resolve rate produced by our own grading code
would be a number only this repo can reproduce, which is not a number at all.

Status: scaffolded and unit-tested against fakes, NOT yet validated end to end
— that needs a container runtime and a paid model run. `preflight()` is the
gate. Until it runs green on real hardware, this project publishes the
localization score and claims no resolve rate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence

from .dataset import Instance

SOLVE_TIMEOUT = 1800


@dataclass(frozen=True)
class Prediction:
    instance_id: str
    model_patch: str
    model_name_or_path: str


Solver = Callable[[Instance, Path], str]


# ── gate ──────────────────────────────────────────────────────────────

def _container_runtime() -> str:
    """colima or plain docker — whichever answers. Never starts one: bringing
    up a VM is the user's call, not a side effect of asking a question."""
    if shutil.which("docker"):
        probe = subprocess.run(["docker", "info"], capture_output=True, timeout=30)
        if probe.returncode == 0:
            return "docker"
    return ""


def preflight(solver_binary: str = "claude") -> list[str]:
    """Every blocker at once — a gate that reveals one problem per run wastes
    a person's afternoon (AC1)."""
    blockers: list[str] = []
    if not _container_runtime():
        blockers.append(
            "no reachable container runtime — start one first (e.g. `colima start "
            "--cpu 4 --memory 16 --disk 100`), the official harness needs it")
    try:
        __import__("swebench")
    except ImportError:
        blockers.append(
            "`swebench` is not importable — `uv pip install swebench`; we do not "
            "grade our own patches (specs/bench-exec/SPEC.md AC4)")
    if not shutil.which(solver_binary):
        blockers.append(f"`{solver_binary}` is not on PATH — no solver to drive")
    return blockers


# ── solvers ───────────────────────────────────────────────────────────

_PROMPT = """\
You are fixing a bug in this repository. Read the issue, find the cause, and
edit the source files so the issue is resolved.

Rules:
- Change source files only. Do not add, edit, or delete tests.
- Do not commit. Leave your work as uncommitted changes in the working tree.
- Make the smallest change that actually fixes the issue.

Issue:
{problem}
"""


def claude_solver(with_map: bool, model: str = "", turns: int = 40,
                  binary: str = "claude") -> Solver:
    """Drive the `claude` CLI headless inside the checkout.

    The two arms differ in exactly one thing — whether `codepulse` wired the
    repo first (AC5). Same model, same turn limit, same prompt, or the
    comparison measures something other than the map.
    """
    def solve(instance: Instance, checkout_dir: Path) -> str:
        if with_map:
            subprocess.run(["codepulse", ".", "--no-panel"], cwd=checkout_dir,
                           capture_output=True, timeout=900)
        command = [binary, "-p", _PROMPT.format(problem=instance.problem_statement),
                   "--max-turns", str(turns), "--permission-mode", "acceptEdits"]
        if model:
            command += ["--model", model]
        subprocess.run(command, cwd=checkout_dir, capture_output=True,
                       text=True, timeout=SOLVE_TIMEOUT,
                       env={**os.environ, "CODEPULSE_BENCH": "1"})
        return diff_of(checkout_dir)
    return solve


def diff_of(checkout_dir: Path) -> str:
    """The agent's work, as a patch. Test files are stripped: the harness
    supplies its own tests, and letting a solver edit them is how a resolve
    rate becomes fiction."""
    from .truth import is_test_path

    result = subprocess.run(["git", "diff", "--no-color", "--no-ext-diff"],
                            cwd=checkout_dir, capture_output=True, text=True)
    if result.returncode:
        return ""
    return _strip_test_files(result.stdout, is_test_path)


def _strip_test_files(diff_text: str, is_test) -> str:
    blocks, current, keep = [], [], True
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current and keep:
                blocks.append("".join(current))
            parts = line.split()
            path = parts[-1][2:] if len(parts) >= 4 else ""
            current, keep = [line], not is_test(path)
        else:
            current.append(line)
    if current and keep:
        blocks.append("".join(current))
    return "".join(blocks)


# ── predictions and grading ───────────────────────────────────────────

def write_predictions(predictions: Sequence[Prediction], path: Path) -> Path:
    """Exactly the upstream schema, nothing extra (AC3)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for prediction in predictions:
            handle.write(json.dumps(asdict(prediction), sort_keys=True) + "\n")
    return path


def evaluate(predictions: Path, run_id: str, workers: int = 4,
             dataset: str = "princeton-nlp/SWE-bench_Lite") -> dict:
    """Hand the file to the official harness and read its report (AC4)."""
    command = [
        "python", "-m", "swebench.harness.run_evaluation",
        "--dataset_name", dataset, "--predictions_path", str(predictions),
        "--max_workers", str(workers), "--run_id", run_id,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=None)
    report = _find_report(predictions.parent, run_id)
    if report is None:
        return {"error": "official harness produced no report",
                "returncode": result.returncode, "stderr": result.stderr[-4000:]}
    return json.loads(report.read_text())


def _find_report(near: Path, run_id: str) -> Path | None:
    for base in (Path.cwd(), near):
        matches = sorted(base.glob(f"*{run_id}*.json"))
        if matches:
            return matches[0]
    return None


def run(instances: Sequence[Instance], arms: dict[str, Solver], out_dir: Path,
        cache_dir: Path, dry_run: bool = True, run_id: str = "codepulse") -> dict:
    """Both arms over the same instances. dry_run exercises everything except
    the two things that cost money (AC2)."""
    from . import repos

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not dry_run:
        blockers = preflight()
        if blockers:
            raise RuntimeError("cannot run:\n  - " + "\n  - ".join(blockers))
    summary: dict = {"dry_run": dry_run, "arms": {}, "instances":
                     [i.instance_id for i in instances]}
    for arm, solver in arms.items():
        predictions, timings = [], []
        for instance in instances:
            checkout_dir = repos.checkout(instance.repo, instance.base_commit, cache_dir)
            started = time.perf_counter()
            patch = "" if dry_run else solver(instance.redacted(), checkout_dir)
            timings.append(time.perf_counter() - started)
            predictions.append(Prediction(instance.instance_id, patch, arm))
        path = write_predictions(predictions, out_dir / f"predictions-{arm}.jsonl")
        summary["arms"][arm] = {
            "predictions": str(path),
            "patched": sum(1 for p in predictions if p.model_patch.strip()),
            "total_seconds": round(sum(timings), 1),
        }
        if not dry_run:
            summary["arms"][arm]["report"] = evaluate(path, f"{run_id}-{arm}")
    (out_dir / "resolve.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    return summary
