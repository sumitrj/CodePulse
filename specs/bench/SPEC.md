# SWE-bench localization — a number that isn't on the honor system

## Intent
`demo/benchmark.md` asks a human to run two Claude sessions and grade them.
That proves something to the person who runs it and nothing to anyone else:
no fixed task set, no ground truth, no rerun. This spec replaces it with a
benchmark that answers one falsifiable question on a public dataset:

> Given a real bug report, does the map point at the code that actually had
> to change?

That is the *localization* subtask of SWE-bench. It is the honest thing to
measure here, because localization is what CodePulse claims to do — it does
not write patches. Ground truth comes from each instance's gold patch, so
scoring needs no LLM, no containers, and no human judgment, and two runs of
the same commit produce byte-identical scores.

Resolve rate — does a generated patch make the tests pass — is a different
claim, needs containers and an agent in the loop, and lives in
`specs/bench-exec/SPEC.md`. This spec is the fast inner loop that one wraps.

## Interface
```python
# bench/dataset.py — SWE-bench Lite, stdlib only
SWEBENCH_LITE = "princeton-nlp/SWE-bench_Lite"

@dataclass(frozen=True)
class Instance:
    instance_id: str        # "astropy__astropy-12907"
    repo: str               # "astropy/astropy"
    base_commit: str        # sha to check out — the pre-fix state
    problem_statement: str  # the issue text; the only input an arm may read
    patch: str              # gold fix diff; ORACLE ONLY, never shown to an arm

def load(cache_dir: Path, limit: int | None = None,
         repos: Sequence[str] = (), instances: Sequence[str] = ()) -> list[Instance]
def sample(pool: list[Instance], limit: int) -> list[Instance]
    # deterministic stratified pick: round-robin over repos, instance_id order

# bench/repos.py — one checkout per (repo, base_commit), cached
def checkout(repo: str, commit: str, cache_dir: Path) -> Path

# bench/truth.py — the oracle, independent of CodePulse by construction
@dataclass(frozen=True)
class Truth:
    files: frozenset[str]            # non-test files the gold patch edits
    entities: frozenset[tuple[str, str]]   # (path, dotted name) that must change
    skipped_files: frozenset[str]    # test files filtered out, reported not hidden

def truth_for(instance: Instance, checkout_dir: Path) -> Truth

# bench/arms.py — the things being compared. Each sees problem_statement
# and the repo; none may see `patch`.
@dataclass(frozen=True)
class Ranking:
    files: tuple[str, ...]                  # best first, deduped
    entities: tuple[tuple[str, str], ...]   # best first; empty for file-only arms
    seconds: float
    detail: dict                            # arm-specific, for the audit trail

def random_arm(instance, checkout_dir, engine=None) -> Ranking  # the zero-skill floor
def bm25(instance, checkout_dir, engine=None) -> Ranking    # lexical baseline
def pulse_map(instance, checkout_dir, engine) -> Ranking    # seeds + graph expansion
def pulse_walk(instance, checkout_dir, engine, config=None) -> Ranking
    # greedy spreading activation: seed EVERY entity lexically, travel along
    # edges best-first, rank on distinct terms reached rather than total mass
def hybrid(instance, checkout_dir, engine) -> Ranking       # bm25 fused with pulse_map
def hybrid_walk(instance, checkout_dir, engine) -> Ranking  # bm25 fused with pulse_walk
def config_hash(cfg: dict) -> str        # tuned parameters, recorded per result row
WALK_DEFAULTS: dict
ARMS: dict[str, Callable]

# bench/tune.py — parameter fitting, development set only
GRID: dict
def configs(grid: dict) -> list[dict]
def evaluate(instances, cache_dir, grid, ks) -> list[dict]   # every config, every row

# bench/validate.py — the oracle checked against an independent implementation
def git_reconstruct(patch_text, old_files: dict[str, str], scratch: Path) -> dict
def compare_instance(instance, checkout_dir) -> dict

# bench/metrics.py
def score(ranking: Ranking, truth: Truth, ks: Sequence[int]) -> dict
    # recall@k, precision@1, mrr, all_found@k, exact_set, entity_recall@k
def aggregate(rows: Sequence[dict]) -> dict          # macro-average over instances

# bench/run.py — CLI
# python -m bench.run --limit 30 --out bench/results
```

## Inputs and outputs
- Input: SWE-bench Lite (300 instances, all Python) fetched over the
  HuggingFace datasets-server rows API and cached as JSONL. No new
  dependency — `urllib` and `json` only, in keeping with the engine.
- Output: `results.json` (per-instance rows plus aggregates, the audit trail)
  and `scorecard.md` (the table a reader can check).
- Errors: an instance whose repo cannot be fetched, or whose gold patch
  touches no non-test Python file, is **skipped and counted** — never
  silently dropped. The run reports `attempted`, `scored`, and `skipped`
  with reasons, because a benchmark that hides its denominator is a lie.

## Acceptance criteria
1. `Instance` never exposes `patch` to an arm: `arms.*` take
   `(instance, checkout_dir, engine)` and the run harness passes a copy with
   `patch=""`. A test asserts every arm scores identically on a redacted copy.
2. `truth_for` uses stdlib `ast` on the base-commit file contents — never the
   CodePulse engine. The oracle must not share a failure mode with the thing
   it grades: an entity the engine cannot see must still count against it.
3. Changed-line attribution reads the **old side** of each hunk, so an entity
   is "must change" when the fix deleted or replaced its lines; a pure
   insertion is attributed to the entity enclosing the hunk's old-side anchor,
   which is the line *preceding* the insertion point (appending inside a body
   is commoner than inserting between two definitions).
3a. Anchoring cannot name something that did not exist yet, so the new side is
   reconstructed by applying the hunks to the base content, and any
   function or class present only there is a target under its own name.
3b. Hunk headers drift, so every hunk is re-anchored by searching for its old
   side near the declared line before any line number is used. This is offset
   tolerance, not fuzz — the whole context/removed block must match
   **exactly**, only its position may differ. Trusting a drifted header would
   attribute a change to whichever entity happens to occupy the stated line,
   which is a wrong answer rather than a missing one. A hunk that matches
   nowhere in the window raises, and the instance is skipped.
4. A hunk that lands outside any function or class is attributed to
   `<module>`, matching the engine's `MODULE_SCOPE` name.
5. Entity names are dotted and match the engine's convention
   (`Class.method`, `Outer.Inner.method`), so a scorer can compare oracle and
   arm output without a translation table.
6. Test files are excluded from `Truth.files` and recorded in
   `Truth.skipped_files`: `test_*.py`, `*_test.py`, `conftest.py`, and any
   path with a `tests/` or `test/` component.
7. `sample` is deterministic and repo-stratified: same pool and limit yield
   the same ids in the same order, with repos represented round-robin rather
   than the first repo alphabetically filling the budget.
8. `metrics.score` computes recall@k over **files** as
   `|retrieved@k ∩ truth| / |truth|`, `all_found@k` as the stricter
   "every truth file is in the top k", and `mrr` over the first truth hit.
9. Every arm is timed, and the scorecard reports median seconds per instance
   alongside accuracy: a retrieval win paid for by a 60× slowdown is not a win.
10. `run.py` writes results even when interrupted mid-run, and re-running with
    the same arguments and caches reproduces `results.json` byte-for-byte
    apart from timing fields.
11. At least three arms are always reported — `bm25`, `pulse_map`, `hybrid` —
    so the map is measured against a real baseline and against itself fused
    with one. Reporting the map alone is not a permitted output.
12. `pulse_walk` ranks on **co-activation**, not accumulated mass: each of the
    top-`max_terms` issue terms by IDF carries a bit, the bitmask travels with
    activation along edges, and the final score scales with the count of
    *distinct* terms that reached an entity. Two different issue terms
    converging on one function is strong evidence; one term shouting ten times
    is not, and summation cannot tell those apart. Activation is divided by
    out-degree, or load-bearing types absorb every walk and rank first for
    every issue in the repo.
13. **Unusual direct mention:** a term the issue names whose document frequency
    is at or below `unusual_df` is treated as a fingerprint, boosting matching
    entities linearly per rare term (not exponentially — a long tail must not
    run away). This is also the only path by which an entity connected to the
    issue by no edge at all can be retrieved.
14. Parameter fitting happens on the development set and nowhere else.
    `bench.tune` **refuses to run against SWE-bench Verified** and says why.
    Every config tried is written to `tuning.json`, not just the winner, and
    the top-5 spread is reported so a flat objective is visible as flat.
    `config_hash` appears in every result row, so a reader can confirm the
    reporting run used the config frozen on the development set rather than
    one refitted after the fact.
15. Caches for the walk (adjacency, entity documents) hold **one repo at a
    time**. A 500-instance run visits 500 repos and the largest carries ~48k
    entities; accumulating them would make the benchmark the memory hog the
    tool claims not to be.
16. Entities the fix *created* stay in `Truth.entities` and are also recorded
    in `Truth.added_entities`. Nothing reading the base checkout can retrieve a
    function that does not exist there, so `entity_recall@k` scores against
    `reachable_entities` only, and the count of unreachable targets is
    reported. Scoring them would charge every arm for an impossibility and
    silently cap the metric below 1.0.

## Non-goals
- Resolve rate, patch generation, container execution (see `bench-exec`).
- Beating a published state-of-the-art localizer. The baseline here is BM25
  because BM25 is what a grep-driven agent approximates, and that is the
  comparison the README makes.
