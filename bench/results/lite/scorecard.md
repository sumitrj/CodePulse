# SWE-bench localization — scorecard

Dataset: `princeton-nlp/SWE-bench_Lite` · attempted 33 · scored 33 · skipped 0

Question: given only the issue text, does the arm rank the files the gold patch actually changed? Macro-averaged over instances.

| Arm | recall@1 | recall@5 | recall@10 | all_found@10 | mrr | median s/instance |
|---|---|---|---|---|---|---|
| `random` | 0.000 | 0.000 | 0.061 | 0.061 | 0.032 | 0.01 |
| `bm25` | 0.424 | 0.758 | 0.849 | 0.849 | 0.558 | 0.14 |
| `pulse_map` | 0.242 | 0.697 | 0.849 | 0.849 | 0.431 | 1.61 |
| `pulse_walk` | 0.242 | 0.667 | 0.818 | 0.818 | 0.417 | 0.23 |
| `hybrid` | 0.333 | 0.818 | 0.909 | 0.909 | 0.549 | 1.72 |
| `hybrid_walk` | 0.303 | 0.818 | 0.879 | 0.879 | 0.502 | 0.14 |

**Ceiling:** 100.0% of gold files are in the candidate set at all. Anything below 100% is a limit of this harness's file filter, not of any arm, and it caps every column above.

**Verdict**

- `recall@10`: `hybrid` scores highest (not established) at 0.909 vs BM25's 0.849 (+0.061, +7%).
- `recall@5`: `hybrid` scores highest (not established) at 0.818 vs BM25's 0.758 (+0.061, +8%).
- `recall@1`: BM25 scores highest at 0.424; best other arm is `hybrid` at 0.333 — but the gap is within noise.
- `mrr`: BM25 scores highest at 0.558; best other arm is `hybrid` at 0.549 — but the gap is within noise.

## Is the difference real?

Paired bootstrap over the same 33 instances, 95% interval on the difference vs `bm25`. An interval containing 0 means this run does **not** establish which arm is better — with this few instances, one issue is worth about 3 points.

| Arm | Metric | Difference vs bm25 | 95% CI | Established? |
|---|---|---|---|---|
| `random` | recall@1 | -0.424 | [-0.576, -0.242] | **yes** |
| `random` | recall@5 | -0.758 | [-0.879, -0.606] | **yes** |
| `random` | recall@10 | -0.788 | [-0.909, -0.636] | **yes** |
| `random` | mrr | -0.526 | [-0.657, -0.394] | **yes** |
| `pulse_map` | recall@1 | -0.182 | [-0.364, +0.030] | no |
| `pulse_map` | recall@5 | -0.061 | [-0.273, +0.151] | no |
| `pulse_map` | recall@10 | +0.000 | [-0.182, +0.151] | no |
| `pulse_map` | mrr | -0.127 | [-0.284, +0.030] | no |
| `pulse_walk` | recall@1 | -0.182 | [-0.333, -0.030] | **yes** |
| `pulse_walk` | recall@5 | -0.091 | [-0.273, +0.091] | no |
| `pulse_walk` | recall@10 | -0.030 | [-0.151, +0.091] | no |
| `pulse_walk` | mrr | -0.141 | [-0.275, -0.011] | **yes** |
| `hybrid` | recall@1 | -0.091 | [-0.242, +0.061] | no |
| `hybrid` | recall@5 | +0.061 | [-0.091, +0.212] | no |
| `hybrid` | recall@10 | +0.061 | [-0.091, +0.212] | no |
| `hybrid` | mrr | -0.009 | [-0.123, +0.102] | no |
| `hybrid_walk` | recall@1 | -0.121 | [-0.273, +0.030] | no |
| `hybrid_walk` | recall@5 | +0.061 | [-0.091, +0.212] | no |
| `hybrid_walk` | recall@10 | +0.030 | [-0.061, +0.121] | no |
| `hybrid_walk` | mrr | -0.056 | [-0.168, +0.060] | no |

## Function-level

Same question one level down: the exact function or class the patch edited. Only arms that rank entities appear.

Scored against entities that exist in the base checkout. Functions the fix *created* cannot be retrieved from the code before it, so they are counted and excluded rather than charged to every arm — 7 of 61 gold entities were unreachable this run.

| Arm | entity_recall@5 | entity_recall@10 | entity_recall@20 |
|---|---|---|---|
| `pulse_map` | 0.237 | 0.237 | 0.318 |
| `pulse_walk` | 0.187 | 0.280 | 0.391 |
| `hybrid` | 0.237 | 0.237 | 0.318 |
| `hybrid_walk` | 0.187 | 0.280 | 0.391 |

## By repo (recall@10)

| Repo | n | `random` | `bm25` | `pulse_map` | `pulse_walk` | `hybrid` | `hybrid_walk` |
|---|---|---|---|---|---|---|---|
| astropy/astropy | 3 | 0.00 | 0.67 | 1.00 | 0.33 | 1.00 | 0.67 |
| django/django | 3 | 0.00 | 1.00 | 0.67 | 1.00 | 0.67 | 1.00 |
| matplotlib/matplotlib | 3 | 0.00 | 1.00 | 0.67 | 0.67 | 0.67 | 1.00 |
| mwaskom/seaborn | 3 | 0.00 | 0.67 | 1.00 | 1.00 | 1.00 | 1.00 |
| pallets/flask | 3 | 0.33 | 1.00 | 0.67 | 1.00 | 1.00 | 1.00 |
| psf/requests | 3 | 0.33 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| pydata/xarray | 3 | 0.00 | 1.00 | 0.67 | 1.00 | 1.00 | 1.00 |
| pylint-dev/pylint | 3 | 0.00 | 0.67 | 0.67 | 0.67 | 0.67 | 0.67 |
| pytest-dev/pytest | 3 | 0.00 | 0.67 | 1.00 | 0.67 | 1.00 | 0.67 |
| scikit-learn/scikit-learn | 2 | 0.00 | 0.50 | 1.00 | 1.00 | 1.00 | 1.00 |
| sphinx-doc/sphinx | 2 | 0.00 | 1.00 | 1.00 | 0.50 | 1.00 | 0.50 |
| sympy/sympy | 2 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
