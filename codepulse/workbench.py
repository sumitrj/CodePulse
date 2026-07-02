"""The Workbench: issue in, versioned workpiece out.

Intake -> Brief (verbs 5+3) -> Gate (verb 3 + policy) -> Execute (isolated
branch) -> Prove (radius-selected tests + the promise-test invariant) ->
Deliver (PR whose body IS the workpiece). Consumes the six verbs; adds none.
"""
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import verbs
from .app import search_data
from .judge import classify_change
from .relationships import UnitRef
from .store import Store

Executor = Callable[[Path, str], str]

WORK_DIR = ".codepulse/work"
PIECES_DIR = ".codepulse/workpieces"


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    labels: tuple[str, ...] = ()


@dataclass
class GateConfig:
    max_radius: int = 10
    max_fan_in: int = 25
    min_confidence: int = 4
    require_tests: bool = True


def _is_test_path(path: str) -> bool:
    return "test" in path.lower()


# -- Brief -------------------------------------------------------------------

def make_brief(store: Store, issue: Issue) -> dict:
    query = f"{issue.title} {issue.body}"
    results = [
        r for r in search_data(store, query)["results"] if not _is_test_path(r["path"])
    ][:5]
    for r in results[:3]:
        impact = verbs.compute_radius(store, UnitRef(r["path"], r["qualname"]))
        r["radius"] = len(impact)
        r["at_risk"] = sorted(f"{u.path}::{u.qualname}" for u in impact)[:8]
    return {
        "issue": {"number": issue.number, "title": issue.title},
        "query": query,
        "candidates": results,
        "confidence": results[0]["score"] if results else 0,
    }


def brief_text(brief: dict) -> str:
    lines = [
        f"Issue #{brief['issue']['number']}: {brief['issue']['title']}",
        "CodePulse brief - where this lives and what it endangers:",
    ]
    for c in brief["candidates"]:
        lines.append(
            f"- {c['path']} :: {c['qualname']} (score {c['score']}"
            + (f", blast radius {c['radius']}" if "radius" in c else "") + ")"
        )
        for dep in c.get("at_risk", [])[:4]:
            lines.append(f"    at risk: {dep}")
    lines.append("Change only what the issue requires. Promises that move need tests that move.")
    return "\n".join(lines)


# -- Gate --------------------------------------------------------------------

def gate(store: Store, brief: dict, config: GateConfig) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not brief["candidates"]:
        return False, ["no candidate units located for this issue"]
    top = brief["candidates"][0]
    if brief["confidence"] < config.min_confidence:
        reasons.append(
            f"confidence {brief['confidence']} below minimum {config.min_confidence}"
        )
    for c in brief["candidates"][:3]:
        if c.get("radius", 0) > config.max_radius:
            reasons.append(
                f"blast radius of {c['qualname']} is {c['radius']} (> {config.max_radius})"
            )
    ref = UnitRef(top["path"], top["qualname"])
    fan_in = len({e.src for k in ("calls", "reads", "inherits")
                  for e in store.graph.incoming(ref, kind=k)})
    if fan_in > config.max_fan_in:
        reasons.append(f"{top['qualname']} fan-in {fan_in} (> {config.max_fan_in}) - load-bearing")
    if config.require_tests and not any(_is_test_path(p) for p in store.file_hashes):
        reasons.append("repo has no tests - hands-free requires a proof surface")
    return (not reasons), reasons


# -- Execute / Prove / Deliver -------------------------------------------------

def _git(root: Path, *args, check=True):
    return subprocess.run(["git", *args], cwd=root, check=check,
                          capture_output=True, text=True)


def run_workpiece(root: Path, issue: Issue, config: GateConfig,
                  executor: Executor, dry_run: bool = True,
                  use_model: bool = False) -> dict:
    root = Path(root).resolve()
    store = Store.load(root)
    brief = make_brief(store, issue)
    wp: dict = {"issue": {"number": issue.number, "title": issue.title},
                "brief": brief, "status": "briefed", "version": None}

    eligible, reasons = gate(store, brief, config)
    wp["gate"] = {"eligible": eligible, "reasons": reasons}
    if not eligible:
        wp["status"] = "escalated"
        return wp

    branch = f"pulse/{issue.number}"
    worktree = root / WORK_DIR / str(issue.number)
    _git(root, "worktree", "remove", "--force", str(worktree), check=False)
    _git(root, "branch", "-qD", branch, check=False)
    _git(root, "worktree", "add", "-q", "-B", branch, str(worktree))
    try:
        wp["trace"] = executor(worktree, brief_text(brief))
        _git(worktree, "add", "-A")
        diff = _git(worktree, "diff", "--cached", "--name-only").stdout.split()
        if not diff:
            wp["status"] = "failed"
            wp["rejection"] = "executor produced no change"
            return wp
        _git(worktree, "commit", "-qm", f"pulse: {issue.title} (#{issue.number})")

        new_store, result = Store.build(worktree, previous=store)
        changed, verdicts = [], []
        for a in result.assignments:
            if a.kind == "exact":
                continue
            old = store.units.get(a.unit_id)
            changed.append({"qualname": a.unit.qualname, "path": a.unit.path, "match": a.kind})
            if old is not None:
                v = classify_change(old, a.unit, use_model=use_model)
                verdicts.append({"qualname": a.unit.qualname, "path": a.unit.path,
                                 "classification": v.classification, "summary": v.summary})
        wp["change"] = {"branch": branch, "files": sorted(diff), "events": changed}
        wp["verdict"] = verdicts

        selected = sorted({
            ref.path
            for c in changed if not _is_test_path(c["path"])
            for ref in verbs.compute_radius(new_store, UnitRef(c["path"], c["qualname"]))
            if _is_test_path(ref.path)
        })
        if not selected:
            selected = sorted(p for p in new_store.file_hashes if _is_test_path(p))
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *selected],
            cwd=worktree, capture_output=True, text=True, timeout=600,
        )
        if proc.returncode and "No module named pytest" in proc.stdout + proc.stderr:
            proc = subprocess.run(
                ["uv", "run", "--no-project", "--with", "pytest", "python", "-m", "pytest", "-q", *selected],
                cwd=worktree, capture_output=True, text=True, timeout=600,
            )
        wp["proof"] = {"selected": selected, "passed": proc.returncode == 0,
                       "output": (proc.stdout + proc.stderr)[-1500:]}

        majors = [v for v in wp["verdict"] if v["classification"] == "major"]
        test_moved = any(_is_test_path(f) for f in diff)
        if majors and not test_moved:
            wp["status"] = "rejected"
            wp["rejection"] = (
                "promise-test invariant: MAJOR change to "
                + ", ".join(v["qualname"] for v in majors)
                + " with no test change - when a promise moves, a test must move"
            )
            return wp
        if not wp["proof"]["passed"]:
            wp["status"] = "failed"
            wp["rejection"] = "radius-selected tests failed"
            return wp

        wp["delivery"] = _deliver(root, issue, branch, wp, dry_run)
        wp["status"] = "delivered"
        return wp
    finally:
        if dry_run:
            _git(root, "worktree", "remove", "--force", str(worktree), check=False)
            _git(root, "branch", "-qD", branch, check=False)


def _deliver(root: Path, issue: Issue, branch: str, wp: dict, dry_run: bool) -> dict:
    payload = {
        "mode": "dry-run" if dry_run else "live",
        "branch": branch,
        "pr_title": f"pulse: {issue.title} (#{issue.number})",
        "pr_body": render_pr_body(wp),
        "comment": _comment(wp),
    }
    if not dry_run:
        subprocess.run(["git", "push", "-u", "origin", branch], cwd=root, check=True)
        subprocess.run(["gh", "pr", "create", "--head", branch,
                        "--title", payload["pr_title"], "--body", payload["pr_body"]],
                       cwd=root, check=True)
        subprocess.run(["gh", "issue", "comment", str(issue.number),
                        "--body", payload["comment"]], cwd=root, check=True)
    return payload


def _comment(wp: dict) -> str:
    verdicts = "; ".join(f"{v['qualname']}: {v['classification']}" for v in wp.get("verdict", []))
    proof = wp.get("proof", {})
    return (f"CodePulse workpiece ready on `{wp['change']['branch']}` - "
            f"tests {'passed' if proof.get('passed') else 'FAILED'} "
            f"({', '.join(proof.get('selected', [])) or 'none'}). Verdicts: {verdicts or 'none'}.")


def render_pr_body(wp: dict) -> str:
    b = wp["brief"]
    sections = [
        f"## Brief\nIssue #{b['issue']['number']}: {b['issue']['title']}  (confidence {b['confidence']})",
        "\n".join(f"- `{c['path']}::{c['qualname']}` score {c['score']}"
                  + (f", radius {c['radius']}" if "radius" in c else "")
                  for c in b["candidates"]),
        "## Change\nBranch `" + wp["change"]["branch"] + "`; files: "
        + ", ".join(f"`{f}`" for f in wp["change"]["files"]),
        "\n".join(f"- {e['qualname']} ({e['match']})" for e in wp["change"]["events"]),
        "## Proof\nSelected by blast radius: "
        + (", ".join(f"`{t}`" for t in wp["proof"]["selected"]) or "whole suite")
        + f" - **{'passed' if wp['proof']['passed'] else 'failed'}**",
        "## Verdict\n" + ("\n".join(
            f"- **{v['classification'].upper()}** `{v['qualname']}` - {v['summary']}"
            for v in wp["verdict"]) or "no surviving units changed"),
        "## Trace\n" + wp.get("trace", ""),
    ]
    return "\n\n".join(sections)


# -- persistence & GitHub intake ----------------------------------------------

def save_workpiece(root: Path, wp: dict) -> Path:
    folder = Path(root) / PIECES_DIR / str(wp["issue"]["number"])
    folder.mkdir(parents=True, exist_ok=True)
    version = len(list(folder.glob("v*.json"))) + 1
    wp["version"] = version
    target = folder / f"v{version}.json"
    target.write_text(json.dumps(wp, indent=1))
    return target


def load_workpieces(root: Path) -> list[dict]:
    base = Path(root) / PIECES_DIR
    if not base.exists():
        return []
    latest = []
    for folder in sorted(base.iterdir()):
        versions = sorted(folder.glob("v*.json"), key=lambda p: int(p.stem[1:]))
        if versions:
            latest.append(json.loads(versions[-1].read_text()))
    return latest


def fetch_issue(number: int, repo: str | None = None) -> Issue:
    cmd = ["gh", "issue", "view", str(number), "--json", "number,title,body,labels"]
    if repo:
        cmd += ["-R", repo]
    data = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)
    return Issue(
        number=data["number"], title=data["title"], body=data.get("body") or "",
        labels=tuple(l["name"] for l in data.get("labels", [])),
    )


def claude_executor(command_template: str | None = None) -> Executor:
    """The default hands-free executor: headless Claude Code, briefed by the map."""
    def run(worktree: Path, brief: str) -> str:
        cmd = command_template or "claude -p --permission-mode acceptEdits"
        proc = subprocess.run(
            [*cmd.split(), brief], cwd=worktree, capture_output=True, text=True, timeout=3600,
        )
        return proc.stdout[-3000:]
    return run
