# The Workbench

## Intent
An issue enters; a map-scoped Brief is derived; a Gate decides hands-free eligibility from
blast radius and confidence; an executor works the issue on an isolated branch; a Prove step
runs the radius-selected tests and enforces the promise-test invariant; what exits is a
versioned **workpiece** - brief, change, proof, verdict, trace - deliverable as a PR + issue
comment. The Workbench consumes the six verbs; it adds none.

## Interface
```python
# codepulse/workbench.py
@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    labels: tuple[str, ...] = ()

@dataclass
class GateConfig:
    max_radius: int = 10
    max_fan_in: int = 25
    min_confidence: int = 4
    require_tests: bool = True

Executor = Callable[[Path, str], str]   # (worktree, brief_text) -> trace

def make_brief(store, issue) -> dict            # candidates, radius, confidence
def gate(store, brief, config) -> tuple[bool, list[str]]   # eligible, reasons
def run_workpiece(root, issue, config, executor, dry_run=True) -> dict  # the workpiece
def render_pr_body(workpiece) -> str
def fetch_issue(number, repo=None) -> Issue     # via `gh` CLI
def save_workpiece(root, wp) -> Path            # versioned, append-only
def load_workpieces(root) -> list[dict]
```

## Inputs and outputs
- Input: an `Issue` (from `gh issue view --json` or a JSON file) + an indexed repo (git).
- Output: workpiece dict persisted at `.codepulse/workpieces/<number>/v<N>.json`; in dry-run,
  delivery is returned as `{branch, pr_title, pr_body, comment}` without shelling to `gh`.
- Errors: repo not indexed or not a git repo -> raise; executor/test failures are recorded
  in the workpiece (`status: failed`), not raised.

## Acceptance criteria
1. `make_brief` derives candidate units from issue text (locate semantics) with scores,
   and attaches each top candidate's blast radius.
2. `gate` approves when radius, fan-in, and confidence are within `GateConfig` bounds.
3. `gate` rejects with human-readable reasons naming each exceeded bound.
4. `run_workpiece` executes the executor in an isolated git branch/worktree; the main
   working tree is untouched.
5. Prove selects only tests inside the changed units' blast radius and records pass/fail.
6. Promise-test invariant: a MAJOR verdict with no test change in the diff sets
   `status: "rejected"` and the workpiece says why.
7. Verdict lists a judge classification + summary per changed unit.
8. Workpieces persist versioned: a second run for the same issue becomes `v2` alongside `v1`.
9. `render_pr_body` contains all five artifact sections (Brief/Change/Proof/Verdict/Trace).
10. In dry-run, delivery returns the PR payload and calls no external command.

## Out of scope
- Webhook intake (poll/`gh` only), auto-merge, iteration-on-red loops beyond one pass,
  Workbench write actions from the web app (read-only view), non-GitHub forges.

## Open questions / assumptions
- Default executor is headless Claude Code (`claude -p <brief>`); tests use a fake executor.
- Test selection = units in the changed units' radius whose path contains "test";
  falls back to the whole suite when selection is empty and tests exist.
- Branch naming `pulse/<issue-number>`; worktree under `.codepulse/work/`.
