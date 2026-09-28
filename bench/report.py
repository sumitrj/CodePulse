"""The scorecard — the table a sceptic reads first."""
from __future__ import annotations

_HEADLINE = ("recall@1", "recall@5", "recall@10", "all_found@10", "mrr")


def scorecard(payload: dict) -> str:
    arms = payload.get("arms", [])
    aggregates = payload.get("aggregates", {})
    lines = [
        "# SWE-bench localization — scorecard",
        "",
        f"Dataset: `{payload.get('dataset')}` · attempted "
        f"{payload.get('attempted', 0)} · scored {payload.get('scored', 0)} · "
        f"skipped {len(payload.get('skipped', []))}",
        "",
        "Question: given only the issue text, does the arm rank the files the "
        "gold patch actually changed? Macro-averaged over instances.",
        "",
        "| Arm | " + " | ".join(_HEADLINE) + " | median s/instance |",
        "|---|" + "---|" * (len(_HEADLINE) + 1),
    ]
    for arm in arms:
        agg = aggregates.get(arm, {})
        cells = [f"{agg.get(key, 0):.3f}" if isinstance(agg.get(key), (int, float))
                 else "—" for key in _HEADLINE]
        lines.append(f"| `{arm}` | " + " | ".join(cells) + " | "
                     f"{agg.get('median_seconds', 0):.2f} |")
    ceiling = payload.get("retrievable")
    if isinstance(ceiling, (int, float)):
        lines += [
            "",
            f"**Ceiling:** {ceiling * 100:.1f}% of gold files are in the candidate "
            "set at all. Anything below 100% is a limit of this harness's file "
            "filter, not of any arm, and it caps every column above.",
        ]
    lines += ["", _verdict(arms, aggregates, payload), "", _significance(payload), ""]

    entity_arms = [a for a in arms if "entity_recall@10" in aggregates.get(a, {})]
    if entity_arms:
        lines += [
            "## Function-level",
            "",
            "Same question one level down: the exact function or class the patch "
            "edited. Only arms that rank entities appear.",
            "",
            "Scored against entities that exist in the base checkout. Functions the "
            "fix *created* cannot be retrieved from the code before it, so they are "
            "counted and excluded rather than charged to every arm — "
            f"{_unreachable(payload)} of "
            f"{_total_entities(payload)} gold entities were unreachable this run.",
            "",
            "| Arm | entity_recall@5 | entity_recall@10 | entity_recall@20 |",
            "|---|---|---|---|",
        ]
        for arm in entity_arms:
            agg = aggregates[arm]
            lines.append(
                f"| `{arm}` | " + " | ".join(
                    f"{agg.get(f'entity_recall@{k}', 0):.3f}" for k in (5, 10, 20)) + " |")
        lines.append("")

    skipped = payload.get("skipped", [])
    if skipped:
        lines += ["## Skipped instances", "",
                  "Counted, not hidden — these are out of the denominator.", ""]
        lines += [f"- `{s['instance_id']}` — {s['reason']}" for s in skipped[:25]]
        if len(skipped) > 25:
            lines.append(f"- …and {len(skipped) - 25} more (see results.json)")
        lines.append("")

    by_repo = _by_repo(payload)
    if by_repo:
        lines += ["## By repo (recall@10)", "",
                  "| Repo | n | " + " | ".join(f"`{a}`" for a in arms) + " |",
                  "|---|---|" + "---|" * len(arms)]
        for repo, (count, per_arm) in sorted(by_repo.items()):
            cells = " | ".join(f"{per_arm.get(a, 0):.2f}" for a in arms)
            lines.append(f"| {repo} | {count} | {cells} |")
        lines.append("")
    return "\n".join(lines)


def _significance(payload: dict) -> str:
    """Report the intervals, and say plainly when a lead is not established."""
    comparisons = payload.get("comparisons") or []
    if not comparisons:
        return ""
    n = comparisons[0].get("n", 0)
    lines = [
        "## Is the difference real?",
        "",
        f"Paired bootstrap over the same {n} instances, 95% interval on the "
        "difference vs `bm25`. An interval containing 0 means this run does "
        "**not** establish which arm is better"
        + (f" — at this sample size one instance moves a metric by about "
           f"{100 / n:.1f} points, so small gaps are noise."
           if n < 100 else
           f". One instance is worth {100 / n:.1f} points here, so the "
           "intervals are tight enough to trust."),
        "",
        "| Arm | Metric | Difference vs bm25 | 95% CI | Established? |",
        "|---|---|---|---|---|",
    ]
    for c in comparisons:
        low, high = c["ci95"]
        lines.append(
            f"| `{c['a']}` | {c['metric']} | {c['difference']:+.3f} | "
            f"[{low:+.3f}, {high:+.3f}] | "
            f"{'**yes**' if c['separates'] else 'no'} |")
    if not any(c["separates"] for c in comparisons):
        lines += ["", "**No comparison on this page is established by this run.** "
                      "Treat the table above as a direction to investigate, not a "
                      "result to quote."]
    return "\n".join(lines)


def _unreachable(payload: dict) -> int:
    return sum(len(row.get("unreachable_entities", ()))
               for row in payload.get("instances", []))


def _total_entities(payload: dict) -> int:
    return sum(len(row.get("truth_entities", ()))
               for row in payload.get("instances", []))


def _established(payload: dict, arm: str, metric: str) -> bool | None:
    for c in payload.get("comparisons") or []:
        if c.get("a") == arm and c.get("metric") == metric:
            return bool(c.get("separates"))
    return None


def _verdict(arms, aggregates, payload: dict | None = None) -> str:
    """State what the numbers say, including when they say the map lost.

    Reported on whichever headline metric still discriminates. Most SWE-bench
    Lite instances have a single gold file that every arm finds within ten, so
    recall@10 pins to 1.000 for everybody and a verdict quoting it would be
    true and useless. Where the top of the ranking is what separates the arms,
    say so on recall@1 or MRR instead.
    """
    if "bm25" not in aggregates:
        return ""
    lines = []
    for metric in ("recall@10", "recall@5", "recall@1", "mrr"):
        values = {a: aggregates.get(a, {}).get(metric) for a in arms}
        values = {a: v for a, v in values.items() if isinstance(v, (int, float))}
        if len(values) < 2:
            continue
        if max(values.values()) - min(values.values()) < 1e-9:
            lines.append(f"- `{metric}` does not separate the arms "
                         f"(all {max(values.values()):.3f}) — saturated, ignore it.")
            continue
        baseline = values["bm25"]
        best_arm, best = max(values.items(), key=lambda kv: kv[1])
        if best_arm == "bm25":
            runner = max((kv for kv in values.items() if kv[0] != "bm25"),
                         key=lambda kv: kv[1])
            solid = _established(payload or {}, runner[0], metric)
            lines.append(f"- `{metric}`: BM25 scores highest at {baseline:.3f}; best "
                         f"other arm is `{runner[0]}` at {runner[1]:.3f}"
                         + ("." if solid else " — but the gap is within noise."))
        else:
            delta = best - baseline
            solid = _established(payload or {}, best_arm, metric)
            # Never call an unestablished gap a lead: this line is what gets
            # screenshotted, and it must not contradict the interval below it.
            verb = "**leads**" if solid else "scores highest (not established)"
            lines.append(f"- `{metric}`: `{best_arm}` {verb} at {best:.3f} vs "
                         f"BM25's {baseline:.3f} (+{delta:.3f}"
                         + (f", +{delta / baseline * 100:.0f}%)." if baseline else ")."))
    return "**Verdict**\n\n" + "\n".join(lines) if lines else ""


def _by_repo(payload: dict) -> dict:
    buckets: dict[str, list] = {}
    for row in payload.get("instances", []):
        buckets.setdefault(row["repo"], []).append(row)
    out = {}
    for repo, rows in buckets.items():
        per_arm = {}
        for arm in payload.get("arms", []):
            values = [r["arms"][arm]["recall@10"] for r in rows
                      if "recall@10" in r["arms"].get(arm, {})]
            if values:
                per_arm[arm] = sum(values) / len(values)
        out[repo] = (len(rows), per_arm)
    return out
