"""Fit `pulse_walk`'s parameters — on the development set, and only there.

    python -m bench.tune --limit 33 --objective mrr

Tuning is legitimate; tuning and then quoting the set you tuned on is not.
This module refuses to touch SWE-bench Verified for exactly that reason: Lite
is the development set, Verified is reported once with the frozen config, and
`arms.config_hash` in each result row is what lets a reader check that the two
were not the same fitting exercise.

Every config tried is written to tuning.json, not just the winner. A search
that reports only its best cell is indistinguishable from one that got lucky,
and with a few dozen instances the objective is flat enough that the top
several configs are usually the same config wearing different hats.
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

from . import arms, dataset, metrics, repos
from .truth import PatchError, truth_for

# Deliberately small. A 200-cell grid over 33 instances selects noise: the
# winner would be whichever config happened to suit two or three issues.
GRID = {
    "damping": (0.15, 0.35, 0.55),
    "coactivation": (0.0, 0.3, 0.6),
    "unusual_boost": (0.0, 1.0, 2.0),
    "seed_top": (100, 200),
}
OBJECTIVES = ("mrr", "recall@1", "recall@5", "recall@10", "entity_recall@10")


def configs(grid: dict = GRID) -> list[dict]:
    keys = sorted(grid)
    return [dict(zip(keys, values))
            for values in itertools.product(*(grid[k] for k in keys))]


def evaluate(instances, cache_dir: Path, grid: dict, ks, verbose: bool = True) -> list[dict]:
    """Score every config on every instance, building each engine once."""
    from codepulse.main import build_engine

    trials = configs(grid)
    rows: dict[str, list[dict]] = {arms.config_hash({**arms.WALK_DEFAULTS, **c}): []
                                   for c in trials}
    for index, instance in enumerate(instances, start=1):
        try:
            checkout = repos.checkout(instance.repo, instance.base_commit, cache_dir)
            truth = truth_for(instance, checkout)
            engine = build_engine(checkout, announce=False)
        except (PatchError, repos.CheckoutError, Exception) as exc:
            if verbose:
                print(f"[{index}/{len(instances)}] SKIP {instance.instance_id}: {exc}",
                      flush=True)
            continue
        redacted = instance.redacted()
        for trial in trials:
            cfg = {**arms.WALK_DEFAULTS, **trial}
            ranking = arms.pulse_walk(redacted, checkout, engine, cfg)
            rows[arms.config_hash(cfg)].append(metrics.score(ranking, truth, ks))
        if verbose:
            print(f"[{index}/{len(instances)}] {instance.instance_id} "
                  f"x{len(trials)} configs", flush=True)
    out = []
    for trial in trials:
        cfg = {**arms.WALK_DEFAULTS, **trial}
        digest = arms.config_hash(cfg)
        if rows[digest]:
            out.append({"params": trial, "config_hash": digest,
                        **metrics.aggregate(rows[digest])})
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench.tune", description="Grid-search pulse_walk on the dev set")
    parser.add_argument("--limit", type=int, default=33)
    parser.add_argument("--objective", default="mrr", choices=OBJECTIVES)
    parser.add_argument("--dataset", default="lite")
    parser.add_argument("--cache-dir", type=Path,
                        default=Path.home() / ".cache" / "codepulse-bench")
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parent / "results")
    args = parser.parse_args(argv)

    name = dataset.resolve(args.dataset)
    if name == dataset.SWEBENCH_VERIFIED:
        parser.error(
            "refusing to tune on SWE-bench Verified — it is the reporting set. "
            "Tune on `lite`, freeze the config, then run Verified once.")

    instances = dataset.load(args.cache_dir / "data", limit=args.limit, dataset=name)
    results = evaluate(instances, args.cache_dir / "repos", GRID, metrics.DEFAULT_KS)
    if not results:
        print("no instances scored")
        return 2
    results.sort(key=lambda r: -r.get(args.objective, 0))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "tuning.json").write_text(json.dumps({
        "dataset": name, "objective": args.objective,
        "instances": len(instances), "grid": {k: list(v) for k, v in GRID.items()},
        "baseline": arms.WALK_DEFAULTS,
        "results": results,
    }, indent=2, sort_keys=True) + "\n")

    best = results[0]
    spread = best[args.objective] - results[min(4, len(results) - 1)][args.objective]
    print(f"\n{len(results)} configs on {len(instances)} instances, "
          f"objective {args.objective}\n")
    print(f"{'rank':<5}{args.objective:<10}{'r@1':<8}{'r@10':<8}{'ent@10':<9}params")
    for rank, row in enumerate(results[:8], start=1):
        print(f"{rank:<5}{row.get(args.objective, 0):<10.3f}"
              f"{row.get('recall@1', 0):<8.3f}{row.get('recall@10', 0):<8.3f}"
              f"{row.get('entity_recall@10', 0):<9.3f}{row['params']}")
    print(f"\nbest config_hash: {best['config_hash']}")
    print(f"top-5 spread on {args.objective}: {spread:.3f}"
          + ("  — flat; prefer the simplest config, the ranking is noise"
             if spread < 0.05 else ""))
    print("\nFreeze this into arms.WALK_DEFAULTS, then run Verified ONCE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
