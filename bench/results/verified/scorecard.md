# SWE-bench localization — scorecard

Dataset: `princeton-nlp/SWE-bench_Verified` · attempted 500 · scored 500 · skipped 0

Question: given only the issue text, does the arm rank the files the gold patch actually changed? Macro-averaged over instances.

| Arm | recall@1 | recall@5 | recall@10 | all_found@10 | mrr | median s/instance |
|---|---|---|---|---|---|---|
| `random` | 0.000 | 0.025 | 0.040 | 0.036 | 0.020 | 0.02 |
| `bm25` | 0.360 | 0.693 | 0.797 | 0.766 | 0.543 | 0.50 |
| `pulse_map` | 0.330 | 0.600 | 0.678 | 0.644 | 0.479 | 5.14 |
| `pulse_walk` | 0.239 | 0.569 | 0.727 | 0.688 | 0.423 | 1.34 |
| `hybrid` | 0.406 | 0.693 | 0.803 | 0.768 | 0.579 | 5.63 |
| `hybrid_walk` | 0.347 | 0.704 | 0.814 | 0.778 | 0.542 | 0.51 |

**Ceiling:** 99.9% of gold files are in the candidate set at all. Anything below 100% is a limit of this harness's file filter, not of any arm, and it caps every column above.

**Verdict**

- `recall@10`: `hybrid_walk` scores highest (not established) at 0.814 vs BM25's 0.797 (+0.016, +2%).
- `recall@5`: `hybrid_walk` scores highest (not established) at 0.704 vs BM25's 0.693 (+0.012, +2%).
- `recall@1`: `hybrid` **leads** at 0.406 vs BM25's 0.360 (+0.046, +13%).
- `mrr`: `hybrid` **leads** at 0.579 vs BM25's 0.543 (+0.037, +7%).

## Is the difference real?

Paired bootstrap over the same 500 instances, 95% interval on the difference vs `bm25`. An interval containing 0 means this run does **not** establish which arm is better — with this few instances, one issue is worth about 0 points.

| Arm | Metric | Difference vs bm25 | 95% CI | Established? |
|---|---|---|---|---|
| `random` | recall@1 | -0.360 | [-0.401, -0.320] | **yes** |
| `random` | recall@5 | -0.667 | [-0.708, -0.626] | **yes** |
| `random` | recall@10 | -0.757 | [-0.794, -0.720] | **yes** |
| `random` | mrr | -0.523 | [-0.558, -0.488] | **yes** |
| `pulse_map` | recall@1 | -0.030 | [-0.077, +0.017] | no |
| `pulse_map` | recall@5 | -0.093 | [-0.139, -0.046] | **yes** |
| `pulse_map` | recall@10 | -0.119 | [-0.163, -0.076] | **yes** |
| `pulse_map` | mrr | -0.064 | [-0.104, -0.025] | **yes** |
| `pulse_walk` | recall@1 | -0.121 | [-0.167, -0.076] | **yes** |
| `pulse_walk` | recall@5 | -0.123 | [-0.165, -0.081] | **yes** |
| `pulse_walk` | recall@10 | -0.070 | [-0.105, -0.034] | **yes** |
| `pulse_walk` | mrr | -0.119 | [-0.156, -0.083] | **yes** |
| `hybrid` | recall@1 | +0.046 | [+0.008, +0.084] | **yes** |
| `hybrid` | recall@5 | +0.000 | [-0.038, +0.038] | no |
| `hybrid` | recall@10 | +0.006 | [-0.029, +0.041] | no |
| `hybrid` | mrr | +0.037 | [+0.007, +0.065] | **yes** |
| `hybrid_walk` | recall@1 | -0.013 | [-0.052, +0.026] | no |
| `hybrid_walk` | recall@5 | +0.012 | [-0.020, +0.045] | no |
| `hybrid_walk` | recall@10 | +0.017 | [-0.008, +0.042] | no |
| `hybrid_walk` | mrr | -0.000 | [-0.027, +0.026] | no |

## Function-level

Same question one level down: the exact function or class the patch edited. Only arms that rank entities appear.

Scored against entities that exist in the base checkout. Functions the fix *created* cannot be retrieved from the code before it, so they are counted and excluded rather than charged to every arm — 131 of 1199 gold entities were unreachable this run.

| Arm | entity_recall@5 | entity_recall@10 | entity_recall@20 |
|---|---|---|---|
| `pulse_map` | 0.225 | 0.290 | 0.353 |
| `pulse_walk` | 0.214 | 0.279 | 0.378 |
| `hybrid` | 0.225 | 0.290 | 0.353 |
| `hybrid_walk` | 0.214 | 0.279 | 0.378 |

## By repo (recall@10)

| Repo | n | `random` | `bm25` | `pulse_map` | `pulse_walk` | `hybrid` | `hybrid_walk` |
|---|---|---|---|---|---|---|---|
| astropy/astropy | 22 | 0.05 | 0.74 | 0.86 | 0.58 | 0.90 | 0.67 |
| django/django | 231 | 0.03 | 0.86 | 0.66 | 0.79 | 0.81 | 0.85 |
| matplotlib/matplotlib | 34 | 0.00 | 0.68 | 0.65 | 0.64 | 0.76 | 0.82 |
| mwaskom/seaborn | 2 | 0.25 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| pallets/flask | 1 | 1.00 | 1.00 | 0.00 | 1.00 | 0.00 | 1.00 |
| psf/requests | 8 | 0.25 | 1.00 | 0.88 | 1.00 | 0.88 | 1.00 |
| pydata/xarray | 22 | 0.07 | 0.82 | 0.82 | 0.82 | 0.89 | 0.89 |
| pylint-dev/pylint | 10 | 0.03 | 0.61 | 0.36 | 0.38 | 0.51 | 0.48 |
| pytest-dev/pytest | 19 | 0.16 | 0.89 | 0.82 | 0.87 | 0.84 | 0.87 |
| scikit-learn/scikit-learn | 32 | 0.00 | 0.92 | 0.92 | 0.81 | 0.97 | 0.97 |
| sphinx-doc/sphinx | 44 | 0.09 | 0.66 | 0.48 | 0.55 | 0.69 | 0.68 |
| sympy/sympy | 75 | 0.00 | 0.67 | 0.63 | 0.63 | 0.76 | 0.72 |
