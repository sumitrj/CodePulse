"""Run the localization benchmark.

    python -m bench.run --limit 30
    python -m bench.run --repos django/django --k 1,3,5,10 --out bench/results

Writes results.json (the audit trail: every instance, every arm, every rank)
and scorecard.md (the table). Skips are counted and explained, never hidden —
an accuracy number without its denominator is not a result.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import asdict
from pathlib import Path

from . import arms as arms_module
from . import dataset, metrics, repos, report
from .truth import PatchError, truth_for

DEFAULT_CACHE = Path.home() / ".cache" / "codepulse-bench"


def _oracle_agreement(rows) -> dict:
    """How often our patch handling matched git, over the scored set."""
    checks = [r["oracle_check"] for r in rows if r.get("oracle_check")]
    if not checks:
        return {}
    files = sum(c["files"] for c in checks)
    agreed = sum(c["agreed"] for c in checks)
    return {"instances": len(checks), "files": files, "agreed": agreed,
            "agreement": round(agreed / files, 6) if files else 0.0,
            "disagreements": [c for c in checks if c["disagreed"]]}


def _comparisons(rows, arm_names, baseline: str = "bm25") -> list[dict]:
    """Every arm against the baseline, with an interval — so a lead of two
    instances cannot be read as a result."""
    if baseline not in arm_names or len(rows) < 2:
        return []
    out = []
    for arm in arm_names:
        if arm == baseline:
            continue
        for metric in ("recall@1", "recall@5", "recall@10", "mrr"):
            verdict = metrics.paired_bootstrap(rows, arm, baseline, metric)
            if verdict.get("n"):
                out.append(verdict)
    return out


def _engine_for(checkout_dir: Path):
    """Index the checkout once and share it across arms.

    The index lives inside the cached checkout, so a second run of the same
    instance pays nothing — which is also what makes the timing column mean
    "retrieval", not "retrieval plus a cold index".
    """
    from codepulse.main import build_engine
    return build_engine(checkout_dir, announce=False)


def run(instances, cache_dir: Path, arm_names, ks, out_dir: Path,
        verbose: bool = True, dataset_name: str = dataset.SWEBENCH_LITE,
        prune_after: bool = False, validate_oracle: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    skipped: list[dict] = []
    results_path = out_dir / "results.json"

    def flush() -> dict:
        payload = {
            "dataset": dataset_name,
            "arms": list(arm_names),
            "ks": list(ks),
            "attempted": len(rows) + len(skipped),
            "scored": len(rows),
            "skipped": skipped,
            "aggregates": {
                arm: metrics.aggregate([r["arms"][arm] for r in rows if arm in r["arms"]])
                for arm in arm_names
            },
            "retrievable": (sum(r.get("retrievable", 0) for r in rows) / len(rows)
                            if rows else 0.0),
            "comparisons": _comparisons(rows, arm_names),
            "oracle_agreement": _oracle_agreement(rows),
            "instances": rows,
        }
        results_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return payload

    for index, instance in enumerate(instances, start=1):
        label = f"[{index}/{len(instances)}] {instance.instance_id}"
        try:
            checkout_dir = repos.checkout(instance.repo, instance.base_commit, cache_dir)
            truth = truth_for(instance, checkout_dir)
        except (PatchError, repos.CheckoutError) as exc:
            skipped.append({"instance_id": instance.instance_id, "reason": str(exc)})
            if verbose:
                print(f"{label}  SKIP  {exc}", flush=True)
            continue
        except Exception as exc:                        # never let one repo kill a run
            skipped.append({"instance_id": instance.instance_id,
                            "reason": f"unexpected: {exc.__class__.__name__}: {exc}"})
            if verbose:
                print(f"{label}  SKIP  unexpected: {exc}", flush=True)
                traceback.print_exc(limit=2)
            continue
        try:
            engine = _engine_for(checkout_dir)
        except Exception as exc:
            skipped.append({"instance_id": instance.instance_id,
                            "reason": f"index failed: {exc.__class__.__name__}: {exc}"})
            if verbose:
                print(f"{label}  SKIP  index failed: {exc}", flush=True)
            continue
        # AC1: the arms see a copy with the gold patch removed.
        redacted = instance.redacted()
        # The ceiling: gold files our own candidate filter can even offer. A
        # value below 1.0 is a bug in the harness, not a limit of any arm, and
        # it caps every score on the page — so it is computed once, per
        # instance, independently of who is being measured.
        pool = set(arms_module.candidates(checkout_dir))
        missing = sorted(truth.files - pool)
        # Validate the oracle on the very instances being scored, in the same
        # pass — the checkout is already here, and a separate validation run
        # would mean cloning all 500 repos a second time.
        oracle = None
        if validate_oracle:
            from .validate import compare_instance
            check = compare_instance(instance, checkout_dir)
            oracle = {"agreed": check["agreed"], "files": check["files"],
                      "disagreed": check["disagreed"], "error": check["error"]}
        row = {
            "instance_id": instance.instance_id,
            "repo": instance.repo,
            "truth_files": sorted(truth.files),
            "truth_entities": sorted(f"{p}::{n}" for p, n in truth.entities),
            "unreachable_entities": sorted(f"{p}::{n}" for p, n in truth.added_entities),
            "skipped_files": sorted(truth.skipped_files),
            "retrievable": (len(truth.files & pool) / len(truth.files)
                            if truth.files else 0.0),
            "unretrievable_files": missing,
            "candidate_files": len(pool),
            "oracle_check": oracle,
            "arms": {},
            "rankings": {},
            "entity_rankings": {},
        }
        for arm in arm_names:
            try:
                ranking = arms_module.ARMS[arm](redacted, checkout_dir, engine)
            except Exception as exc:
                row["arms"][arm] = {"error": f"{exc.__class__.__name__}: {exc}"}
                continue
            row["arms"][arm] = {**metrics.score(ranking, truth, ks), **ranking.detail}
            row["rankings"][arm] = list(ranking.files[:20])
            if ranking.entities:
                row["entity_rankings"][arm] = [f"{p}::{n}"
                                               for p, n in ranking.entities[:20]]
        rows.append(row)
        if verbose:
            summary = "  ".join(
                f"{arm}:r@10={row['arms'][arm].get('recall@10', 0):.2f}"
                for arm in arm_names if "recall@10" in row["arms"].get(arm, {}))
            print(f"{label}  {len(truth.files)} gold file(s)  {summary}", flush=True)
        flush()                    # AC10: an interrupted run still leaves results
        if prune_after:
            # Everything we needed from this checkout is now in `row`.
            del engine
            repos.discard(instance.repo, instance.base_commit, cache_dir)
    payload = flush()
    (out_dir / "scorecard.md").write_text(report.scorecard(payload))
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench.run", description="SWE-bench localization benchmark for CodePulse")
    parser.add_argument("--limit", type=int, default=None,
                        help="how many instances (repo-stratified, deterministic)")
    parser.add_argument("--repos", default="", help="comma-separated repo filter")
    parser.add_argument("--instances", default="", help="comma-separated instance ids")
    parser.add_argument("--arms", default=",".join(arms_module.ARMS),
                        help="comma-separated arm names")
    parser.add_argument("--k", default=",".join(str(k) for k in metrics.DEFAULT_KS))
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parent / "results")
    parser.add_argument("--dataset", default="lite",
                        help="lite | verified | full, or a HuggingFace dataset id")
    parser.add_argument("--prune-after", action="store_true",
                        help="delete each checkout once scored — keeps peak disk "
                             "at about one repo instead of tens of GB, at the "
                             "cost of re-cloning on a later run")
    parser.add_argument("--validate-oracle", action="store_true",
                        help="also check each gold patch against git apply in the "
                             "same pass, so validation needs no second clone")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    dataset_name = dataset.resolve(args.dataset)

    arm_names = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = [a for a in arm_names if a not in arms_module.ARMS]
    if unknown:
        parser.error(f"unknown arm(s): {', '.join(unknown)}; "
                     f"choose from {', '.join(arms_module.ARMS)}")
    if len(arm_names) < 2:
        parser.error("at least two arms are required — a lone arm has no baseline "
                     "to be measured against (specs/bench/SPEC.md AC11)")
    ks = tuple(int(k) for k in args.k.split(",") if k.strip())

    instances = dataset.load(
        args.cache_dir / "data",
        limit=args.limit,
        repos=[r.strip() for r in args.repos.split(",") if r.strip()],
        instances=[i.strip() for i in args.instances.split(",") if i.strip()],
        dataset=dataset_name,
    )
    if not instances:
        print("no instances matched those filters", file=sys.stderr)
        return 2
    if not args.quiet:
        print(f"{dataset_name}: {len(instances)} instance(s) across "
              f"{len({i.repo for i in instances})} repo(s); arms: {', '.join(arm_names)}")
    payload = run(instances, args.cache_dir / "repos", arm_names, ks, args.out,
                  verbose=not args.quiet, dataset_name=dataset_name,
                  prune_after=args.prune_after,
                  validate_oracle=args.validate_oracle)
    print()
    print(report.scorecard(payload))
    print(f"wrote {args.out / 'results.json'} and {args.out / 'scorecard.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
