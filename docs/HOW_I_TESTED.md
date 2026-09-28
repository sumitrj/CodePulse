# How I tested CodePulse

For anyone deciding whether to believe the numbers in the README.

CodePulse answers *where* — so the only question worth being graded on is
whether "where" is right. This is the whole method, including the parts that
went against me.

## The short version

| | Result on 500 real bugs (SWE-bench Verified) |
|---|---|
| **Map + keyword search** | Right file ranked first 13% more often than keyword search alone. Established: the 95% interval excludes zero. |
| **Map + keyword search, top 10** | No measurable difference from keyword search. |
| **Map alone** | Worse than keyword search. The map earns its place combined, not as a replacement. |
| **Exact function** | On average, 38% of the functions a fix edited appear in the map's top 20. Keyword search ranks files, not functions, so there is nothing to compare against. |
| **Not measured** | Cross-language edges (the benchmark is all Python) and whether agents fix more bugs. |

Everything below reproduces with two commands and no API key:

```bash
python -m bench.run --dataset verified     # the numbers
python -m bench.validate                   # the oracle, checked against git
```

## The question

SWE-bench gives 500 human-validated real GitHub issues, each with the patch
that actually fixed it. So:

> Given only the issue text, does the tool rank the files and functions the
> gold patch changed?

No LLM in the loop, no containers, no human judgment. Two runs of the same
commit produce the same numbers.

## The oracle, and why it doesn't use CodePulse

Ground truth comes from each gold patch, built with stdlib `ast` and a diff
parser written for the purpose. It deliberately does **not** use CodePulse's
engine, or even `codepulse.delta`, which parses diffs perfectly well. A grader
that shares a parser with the thing it grades cannot catch that parser being
wrong: an entity the engine can't see has to count *against* the engine, not
quietly leave the denominator.

- Attribution reads the **old side** of each hunk, and credits the innermost
  enclosing function or class — so `Class.method` counts and `Class` doesn't.
  Crediting both would inflate recall.
- The new side is reconstructed by applying the hunks, so a function the fix
  *added* is a target under its own name.
- Hunk headers drift in real patches ("Hunk #1 succeeded at 968, offset 4
  lines"). Every hunk is re-anchored by exact-content search before any line
  number is used. This is offset tolerance, not fuzz — the whole context block
  must match exactly, only its position may move.
- Test files are excluded and recorded, never silently dropped.

**The oracle is checked against `git`.** Every post-fix file is rebuilt twice —
once by me, once by `git apply` on a scratch copy — and the two must be
byte-identical.

On SWE-bench Verified the recorded run reports **620 of 621 files agreeing,
with zero disagreements.** The one shortfall was a bug in my validator, not a
discrepancy: it staged only the `.py` files into the scratch tree, and `git`
applies a patch all-or-nothing, so a gold patch that also touched `setup.cfg`
was refused outright. That is fixed, and the instance now agrees — but
the run's raw output (`results.json`, 8 MB, kept out of git and rebuilt by the
command above) still records 620/621, and I have not edited it. Re-running validation would
show 621/621.

A further 15 tests run the same comparison on `difflib`-generated patches with
no network at all, across insertions, deletions, first- and last-line edits,
adjacent and distant hunks, and every context width from 0 to 5.

Those tests are generated rather than hand-written for a reason: my first draft
hand-wrote the diffs, and `git` rejected them, because a hand-written fixture is
a third implementation of the diff format and gets it subtly wrong.

## What is being compared

Six arms, all seeing the identical candidate set (non-test Python files), none
seeing the gold patch — a test asserts each arm scores the same on a redacted
copy, so that isn't a promise, it's checked.

| Arm | What it is |
|---|---|
| `random` | Zero skill. The floor. |
| `bm25` | Lexical retrieval. The baseline, because it's what a grep-driven agent approximates. |
| `pulse_map` | The map: symbols from the issue, resolved, expanded over the graph. |
| `pulse_walk` | The map done greedily: every entity seeded lexically, activation spreading along edges, ranked on how many *distinct* issue terms reached each entity. |
| `hybrid` / `hybrid_walk` | Lexical fused with each map arm by reciprocal rank. |

BM25 uses the standard `k1=1.2, b=0.75` and is deliberately **not** tuned. So is
everything else — see below.

## The three things that make the numbers readable

**A floor.** `recall@10 = 0.797` means nothing on its own; the reader can't tell
skill from an easy candidate set. Random scores **0.040**, so the task is hard.

**A ceiling.** 99.9% of gold files are in the candidate set at all. Anything
below 100% would be a limit of my harness, not of any arm, and it caps every
column.

**Intervals.** Every arm is compared to BM25 with a paired bootstrap, and the
scorecard prints a 95% interval on each difference. When nothing separates, it
says so in those words. This is the part that changed my conclusions: on a
33-instance pilot, "+8% recall@5" looked like a win and was two issues' worth
of luck.

Skipped instances are counted and explained. On Verified: **0 skipped of 500.**

## Tuning, and why it doesn't contaminate the result

SWE-bench Lite was the development set. SWE-bench Verified is the reporting set
and was run **once**. `bench.tune` refuses to run against Verified — an argparse
guard with a test behind it, not a line in a README. Every config tried is
written to `tuning.json`, not just the winner, and `config_hash` appears in
every result row so a reader can confirm the reporting run used the frozen
config rather than one refitted after seeing the answers.

The grid search then found nothing: **+0.009 MRR against a top-5 spread of
0.013**, with `recall@1` unmoved across all 54 cells. So the shipped
configuration is the untuned default, and the reported number carries no tuning
debt at all.

## Results — SWE-bench Verified, n=500, 0 skipped

![Difference from BM25 with 95% intervals](img/proof.svg)

| Arm | recall@1 | recall@5 | recall@10 | MRR | entity_recall@20 | median s |
|---|---|---|---|---|---|---|
| `random` | 0.000 | 0.025 | 0.040 | 0.020 | n/a | 0.02 |
| `bm25` | 0.360 | 0.693 | 0.797 | 0.543 | n/a | 0.50 |
| `pulse_map` | 0.330 | 0.600 | 0.678 | 0.479 | 0.353 | 5.14 |
| `pulse_walk` | 0.239 | 0.569 | 0.727 | 0.423 | 0.378 | 1.34 |
| `hybrid` | **0.406** | 0.693 | 0.803 | **0.579** | 0.353 | 5.63 |
| `hybrid_walk` | 0.347 | 0.704 | 0.814 | 0.542 | 0.378 | 0.51 |

**What is established** (95% interval excludes zero):

| | vs BM25 | 95% CI |
|---|---|---|
| `hybrid` recall@1 | **+0.046** (+13%) | [+0.008, +0.084] |
| `hybrid` MRR | **+0.037** (+7%) | [+0.007, +0.065] |

**What is not.** `hybrid`'s recall@5 (+0.000) and recall@10 (+0.006) do not
separate from BM25. `hybrid_walk` has **no** established win on any metric.

**What went against me.** Used as a *replacement* for lexical search, the map is
significantly **worse**: `pulse_map` loses at recall@5, recall@10 and MRR;
`pulse_walk` loses at all four. The map earns its place fused, not alone.

So the one sentence I can defend:

> Fused with lexical retrieval, the map improves the top of the ranking —
> rank-1 by 13% and MRR by 7% on 500 held-out instances. It does not measurably
> improve recall at depth, and on its own it is worse than BM25.

**Function-level is a capability, not a win.** The map ranks the exact function
or class at `entity_recall@20 = 0.378`. BM25 has no score here at all — not a
zero, an absence: it ranks files and produces no entity ranking to measure.
There is no baseline to beat and I computed no interval, so this belongs in the
sentence "BM25 cannot do this," never in "we beat BM25 by 0.378."

**Speed.** `hybrid_walk` reaches the best recall at 0.51s per instance —
essentially BM25's own 0.50s, and 11× faster than `hybrid`. That costs the
rank-1 and MRR advantage, so `hybrid` is the accuracy configuration and
`hybrid_walk` the fast one.

## What the testing actually caught

More useful than the score:

- **`codepulse .` could not map django at all.** An `href="javascript:(…)"`
  bookmarklet in an admindocs template was treated as a path reference, and
  asking the filesystem about it raised `ENAMETOOLONG`, aborting the whole
  index. Never would have surfaced in a demo.
- **The oracle was blaming the wrong functions.** Trusting drifted hunk headers
  attributed changes to whatever entity happened to sit at the stated line — a
  wrong answer, not a missing one.
- **The map arm was blind to bare lowercase identifiers.** `ccode`, `sinc` — no
  dot, no underscore, no capital — so fenced code blocks, the highest-signal
  part of any issue, were skipped entirely.
- **It killed a claim.** "About 8× fewer tokens" was in our own docs with
  nothing in the repo reproducing it. This harness measures localization
  accuracy and wall time, not tokens, so it can't support that number either.
  It should not be published until something measures it.

## What this does not show

- **Cross-language edges are not measured at all.** SWE-bench is 100% Python
  application code, and Dockerfile→module, `os.environ`→Terraform-variable edges
  are the thing no single-language tool sees. That is a real gap in the
  evidence, not an oversight.
- **Localization is a proxy for resolve rate.** A correctly located bug can
  still be fixed wrongly. `bench/exec_harness.py` scaffolds the container-based
  resolve-rate run, and until someone runs it green this project publishes the
  localization number and claims no resolve rate.
- **BM25 is a floor, not the state of the art.** Published localizers do better
  — Agentless reports roughly 69.7% file-level accuracy on SWE-bench Lite. The
  comparison here is against grep-shaped retrieval, which is what the README
  compares to, not against the leaderboard.
- **Lite is no longer a clean test set for me.** I fixed the seed extractor
  after watching it fail on a sympy instance. That is legitimate development,
  and it is exactly why the headline is reported on Verified.
