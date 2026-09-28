"""Scoring. Plain arithmetic, no library, so the numbers can be checked by hand.

recall@k is the headline because localization feeds patching: an arm that puts
one of three needed files in the top 5 has not localized the fix. `all_found@k`
is the strict version and the one to quote when it matters.
"""
from __future__ import annotations

import random
from statistics import median
from typing import Sequence

DEFAULT_KS = (1, 3, 5, 10, 20)


def score(ranking, truth, ks: Sequence[int] = DEFAULT_KS) -> dict:
    files = list(ranking.files)
    gold = set(truth.files)
    row: dict = {"truth_files": len(gold), "retrieved": len(files),
                 "seconds": round(ranking.seconds, 3)}
    for k in ks:
        top = set(files[:k])
        row[f"recall@{k}"] = len(top & gold) / len(gold) if gold else 0.0
        row[f"all_found@{k}"] = float(gold.issubset(top)) if gold else 0.0
    row["precision@1"] = float(bool(files) and files[0] in gold)
    row["mrr"] = next((1.0 / (index + 1) for index, path in enumerate(files)
                       if path in gold), 0.0)
    row["exact_set"] = float(set(files[:len(gold)]) == gold) if gold else 0.0

    # Only entities that exist in the base checkout can be retrieved from it;
    # the ones the fix invented are reported, not scored.
    gold_entities = set(getattr(truth, "reachable_entities", truth.entities))
    row["truth_entities"] = len(getattr(truth, "entities", ()))
    row["unreachable_entities"] = len(getattr(truth, "added_entities", ()))
    if ranking.entities and gold_entities:
        entities = list(ranking.entities)
        for k in ks:
            hit = set(entities[:k]) & gold_entities
            row[f"entity_recall@{k}"] = len(hit) / len(gold_entities)
    return row


def paired_bootstrap(rows: Sequence[dict], arm_a: str, arm_b: str,
                     metric: str, iterations: int = 10000,
                     seed: int = 20260727) -> dict:
    """Is arm_a's lead over arm_b real, or is it two lucky instances?

    A 33-instance run moves ~3 points per instance, so a "+7%" gap is about
    two issues going the other way. Resampling instances *in pairs* (both arms
    saw the same instance, so the comparison is paired) gives an interval that
    says so out loud. A CI straddling zero means the ranking of these two arms
    is not established by this run — which is a finding, not a failure.

    Deterministic: fixed seed, so the published interval reproduces exactly.
    """
    paired = [(row["arms"][arm_a][metric], row["arms"][arm_b][metric])
              for row in rows
              if metric in row.get("arms", {}).get(arm_a, {})
              and metric in row.get("arms", {}).get(arm_b, {})]
    if len(paired) < 2:
        return {"n": len(paired)}
    observed = sum(a - b for a, b in paired) / len(paired)
    rng = random.Random(seed)
    size = len(paired)
    diffs = []
    for _ in range(iterations):
        sample_ = [paired[rng.randrange(size)] for _ in range(size)]
        diffs.append(sum(a - b for a, b in sample_) / size)
    diffs.sort()
    low = diffs[int(0.025 * iterations)]
    high = diffs[min(int(0.975 * iterations), iterations - 1)]
    return {
        "n": size, "metric": metric, "a": arm_a, "b": arm_b,
        "difference": round(observed, 4),
        "ci95": [round(low, 4), round(high, 4)],
        # "significant" here means only: the 95% interval excludes zero.
        "separates": bool(low > 0 or high < 0),
    }


def aggregate(rows: Sequence[dict]) -> dict:
    """Macro-average: every instance counts once, so the repos with many
    instances cannot quietly set the score."""
    if not rows:
        return {"instances": 0}
    out: dict = {"instances": len(rows)}
    keys = {key for row in rows for key in row if isinstance(row.get(key), (int, float))}
    for key in sorted(keys):
        values = [row[key] for row in rows if key in row]
        if not values:
            continue
        if key == "seconds":
            out["median_seconds"] = round(median(values), 3)
            out["total_seconds"] = round(sum(values), 1)
        elif key.startswith(("recall@", "all_found@", "entity_recall@")) or key in (
                "precision@1", "mrr", "exact_set"):
            out[key] = round(sum(values) / len(values), 4)
            out[f"{key}_n"] = len(values)
        else:
            out[key] = round(sum(values) / len(values), 2)
    return out
