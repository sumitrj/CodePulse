# SWE-bench resolve rate — the expensive number

## Intent
`specs/bench/SPEC.md` measures localization: does the map point at the right
code? This spec measures the end of the sentence: does an agent holding the
map *fix more bugs*? That is the number people mean by "SWE-bench score", and
it costs what it costs — containers, an LLM in the loop, hours per run, and
nondeterminism in every datapoint. It exists so the cheap localization number
can be checked against the thing it is a proxy for.

## Division of labour — we do not grade ourselves
We generate patches. The **official** `swebench` harness grades them, in its
own containers, with its own FAIL_TO_PASS / PASS_TO_PASS logic. Reimplementing
grading is how a project accidentally publishes a resolve rate nobody else can
reproduce; a number is only worth having if it was produced by the same code
that produced everyone else's. If `swebench` is not installed, the run refuses
to report a resolve rate rather than substituting a homegrown check.

## Interface
```python
# bench/exec_harness.py
@dataclass(frozen=True)
class Prediction:
    instance_id: str
    model_patch: str            # unified diff, applied at base_commit
    model_name_or_path: str     # the arm label, e.g. "claude-with-map"

Solver = Callable[[Instance, Path], str]     # (redacted instance, checkout) -> patch

def claude_solver(with_map: bool, model: str = "", turns: int = 40) -> Solver
    # drives the `claude` CLI headless in the checkout; when with_map is true
    # it runs `codepulse . --no-panel` first so the MCP server and skill are
    # wired, and the agent can ask the map instead of grepping

def preflight() -> list[str]        # [] when runnable; else human-readable blockers
def write_predictions(preds, path: Path) -> Path      # official JSONL format
def evaluate(predictions: Path, run_id: str, workers: int = 4) -> dict
    # shells out to `python -m swebench.harness.run_evaluation`, parses its report
def run(instances, arms: dict[str, Solver], out_dir: Path, dry_run: bool) -> dict
```

## Inputs and outputs
- Input: the same `Instance` list `bench.dataset` produces, and the same
  checkout cache — the two harnesses agree on which instances they discuss.
- Output: `predictions-<arm>.jsonl`, the official harness's report json, and
  `resolve.md` pairing resolve rate with the localization scores for the same
  instance set.
- Errors: `preflight()` names every blocker at once (no container runtime, no
  `swebench` package, no `claude` on PATH) instead of failing on the first.

## Acceptance criteria
1. `preflight()` returns a non-empty list, and `run(..., dry_run=False)`
   refuses to start, when any of: no reachable container runtime, `swebench`
   not importable, no solver executable on PATH.
2. `dry_run=True` performs the full pipeline except container execution and
   LLM calls: it resolves instances, prepares checkouts, and writes a
   predictions file with empty patches, so the plumbing is testable at zero cost.
3. `write_predictions` emits exactly the official schema — one JSON object per
   line with `instance_id`, `model_patch`, `model_name_or_path` — and nothing
   else, so the file can be fed to the upstream harness unmodified.
4. Resolve rate is only ever read out of the official harness's report. No
   code path in this module decides whether an instance passed.
5. Both arms run the same solver with one difference — whether the repo was
   wired with `codepulse` first. Any other divergence (model, turn limit,
   prompt) is a confound and must fail the run.
6. Every LLM call's cost is recorded per instance, and `resolve.md` reports
   total spend. A resolve-rate win that cost 10× the tokens is reported as
   such, next to the localization number for the same instances.

## Status
Scaffolded and unit-tested against fakes; **not yet validated end to end**,
because that needs a container runtime and a paid model run. `preflight()`
is the honest gate — until someone runs it green on real hardware, this
project publishes the localization number and does not claim a resolve rate.
