"""Differential validation: does our oracle agree with git?

`bench/truth.py` is the single point of failure in this benchmark. If its diff
handling is wrong, every score downstream is confidently wrong in a direction
nobody would notice — and two real bugs have already been found in it. So we
check it the only way worth trusting: against an independent implementation
that predates us and that everyone already relies on.

For each instance we rebuild the post-fix file two ways — ours via
`truth.apply_hunks`, git's via `git apply` on a scratch copy — and require them
to be byte-identical. `git apply` is used as a standalone patch tool in a temp
directory, so the cached checkouts are never modified.

    python -m bench.validate --limit 50
    python -m bench.validate                 # all 300

Writes oracle-validation.json: agreements, disagreements, and every mismatch in
full. A disagreement is a bug in us until proven otherwise.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import dataset, repos
from .truth import PatchError, apply_hunks, parse_patch


def git_reconstruct(patch_text: str, old_files: dict[str, str],
                    scratch: Path) -> dict[str, str]:
    """Apply `patch_text` to `old_files` using git, in an isolated directory."""
    scratch.mkdir(parents=True, exist_ok=True)
    for rel, text in old_files.items():
        target = scratch / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    patch_path = scratch / "__oracle__.patch"
    patch_path.write_text(patch_text if patch_text.endswith("\n") else patch_text + "\n")
    result = subprocess.run(
        # --unidiff-zero: git refuses zero-context hunks by default as
        # ambiguous. --recount: tolerate a miscounted header, which real gold
        # patches occasionally carry. Neither loosens content matching.
        ["git", "apply", "--whitespace=nowarn", "--recount", "--unidiff-zero",
         str(patch_path)],
        cwd=scratch, capture_output=True, text=True)
    if result.returncode:
        raise PatchError(f"git refused the patch: {result.stderr.strip()[:300]}")
    out: dict[str, str] = {}
    for rel in old_files:
        produced = scratch / rel
        out[rel] = produced.read_text() if produced.is_file() else ""
    return out


def compare_instance(instance, checkout_dir: Path) -> dict:
    """Reconstruct every modified Python file both ways and diff the results."""
    every = parse_patch(instance.patch)
    diffs = [d for d in every if d.status == "modified" and d.path.endswith(".py")]
    row = {"instance_id": instance.instance_id, "files": len(diffs),
           "agreed": 0, "disagreed": [], "error": ""}
    if not diffs:
        return row
    # git applies a patch all-or-nothing, so the scratch tree needs *every*
    # file the patch touches — including the setup.cfg and .rst files we do
    # not compare. Staging only the Python ones makes git refuse the whole
    # patch and reads as an oracle failure when nothing is actually wrong.
    old_files: dict[str, str] = {}
    for diff in every:
        if diff.status == "added":
            continue                      # git creates these itself
        source = checkout_dir / diff.path
        if not source.is_file():
            if diff in diffs:
                row["error"] = f"{diff.path} missing from checkout"
                return row
            continue
        old_files[diff.path] = source.read_text(errors="replace")
    scratch = Path(tempfile.mkdtemp(prefix="oracle-"))
    try:
        theirs = git_reconstruct(instance.patch, old_files, scratch)
        for diff in diffs:
            try:
                ours = apply_hunks(old_files[diff.path], diff)
            except PatchError as exc:
                row["disagreed"].append({"path": diff.path, "why": f"we raised: {exc}"})
                continue
            if ours == theirs.get(diff.path):
                row["agreed"] += 1
            else:
                row["disagreed"].append({
                    "path": diff.path, "why": "reconstruction differs",
                    "first_divergence": _first_divergence(ours, theirs.get(diff.path, "")),
                })
    except PatchError as exc:
        row["error"] = str(exc)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return row


def _first_divergence(ours: str, theirs: str) -> dict:
    a, b = ours.splitlines(), theirs.splitlines()
    for index in range(max(len(a), len(b))):
        line_a = a[index] if index < len(a) else "<end of file>"
        line_b = b[index] if index < len(b) else "<end of file>"
        if line_a != line_b:
            return {"line": index + 1, "ours": line_a[:200], "git": line_b[:200]}
    return {}


def validate(instances, cache_dir: Path, verbose: bool = True) -> dict:
    rows, unusable = [], []
    for index, instance in enumerate(instances, start=1):
        try:
            checkout_dir = repos.checkout(instance.repo, instance.base_commit, cache_dir)
        except repos.CheckoutError as exc:
            unusable.append({"instance_id": instance.instance_id, "reason": str(exc)})
            continue
        row = compare_instance(instance, checkout_dir)
        rows.append(row)
        if verbose:
            mark = ("OK  " if not row["disagreed"] and not row["error"]
                    else "DIFF" if row["disagreed"] else "ERR ")
            print(f"[{index}/{len(instances)}] {mark} {instance.instance_id} "
                  f"({row['agreed']}/{row['files']} files agree)"
                  + (f"  {row['error']}" if row["error"] else ""), flush=True)
    files_checked = sum(r["files"] for r in rows)
    files_agreed = sum(r["agreed"] for r in rows)
    return {
        "instances_checked": len(rows),
        "instances_fully_agreed": sum(1 for r in rows
                                      if not r["disagreed"] and not r["error"]),
        "files_checked": files_checked,
        "files_agreed": files_agreed,
        "agreement": round(files_agreed / files_checked, 6) if files_checked else 0.0,
        "disagreements": [r for r in rows if r["disagreed"]],
        "errors": [r for r in rows if r["error"]],
        "unusable": unusable,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench.validate",
        description="Check bench/truth.py's patch handling against git apply")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--cache-dir", type=Path,
                        default=Path.home() / ".cache" / "codepulse-bench")
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parent / "results")
    args = parser.parse_args(argv)

    instances = dataset.load(args.cache_dir / "data", limit=args.limit)
    report = validate(instances, args.cache_dir / "repos")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "oracle-validation.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    print()
    print(f"agreement: {report['files_agreed']}/{report['files_checked']} files "
          f"({report['agreement'] * 100:.2f}%) across "
          f"{report['instances_checked']} instances")
    if report["disagreements"]:
        print(f"DISAGREEMENTS: {len(report['disagreements'])} — "
              "these are bugs in bench/truth.py until proven otherwise")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
