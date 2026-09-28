# PR delta — diff-grained blast radius

## Intent
`pulse_changed` answers at file grain: touch one line of a file and every
entity in it is reported "changed". The delta verb reads the diff itself:
only entities whose lines the diff actually touches are reported, each
classified added / modified / deleted, each carrying its own blast radius,
cross-language dependents, and a load-bearing warning. A PR review starts
from "these 3 functions changed, 9 entities at risk, one deleted function
is still referenced" instead of a file list.

## Interface
```python
# codepulse/delta.py — stdlib only, answers from the Engine graph
LOAD_BEARING_FAN_IN = 3        # direct impact fan-in at or above this flags a warning

@dataclass(frozen=True)
class FileDiff:
    path: str                              # new-side path; old-side when the file was deleted
    status: str                            # "added" | "modified" | "deleted"
    added: tuple[tuple[int, int], ...]     # inclusive runs of + lines, new-file coordinates
    removed: tuple[tuple[int, int], ...]   # inclusive runs of - lines, old-file coordinates

@dataclass(frozen=True)
class Touched:
    addr: Addr
    kind: str                              # entity kind from the graph
    change: str                            # "added" | "modified" | "deleted"
    radius: dict[Addr, int]                # dependent -> hops; for deleted: entities still
                                           # referencing the gone name, at hop 1
    load_bearing: bool

@dataclass(frozen=True)
class DeltaReport:
    files: tuple[FileDiff, ...]
    touched: tuple[Touched, ...]
    at_risk: dict[Addr, int]               # union of touched radii, min hops, touched excluded
    cross_language: dict[str, tuple[str, ...]]   # changed path -> labels of dependents
                                                 # whose file runs under a different recipe

def parse_diff(text: str) -> list[FileDiff]      # unified diff; git and difflib headers alike
def compute_delta(engine: Engine, diff_text: str,
                  base_files: dict[str, str] | None = None) -> DeltaReport
def git_delta(engine: Engine, base: str = "HEAD") -> DeltaReport
    # runs `git diff <base>` at engine.root, fetches base contents via `git show <base>:<path>`
def delta_text(engine: Engine, diff_text: str | None = None, base: str = "HEAD",
               base_files: dict[str, str] | None = None) -> str   # plain-sentence rendering

# codepulse/mcp_server.py — one new tool
# pulse_delta {base?: revision (default HEAD), diff?: raw unified diff text}
```

## Inputs and outputs
- Input: unified diff text (git `diff --git` blocks or bare `---`/`+++`/`@@`
  form, `a/`/`b/` prefixes and `/dev/null` understood), or a git revision for
  `git_delta`; optional `base_files` mapping changed paths to their base-side
  contents (enables deleted/added entity classification without git).
- Output: `DeltaReport` dataclasses; `delta_text` returns plain sentences.
- Errors: none raised to callers — an empty or unparseable diff answers with a
  plain sentence; MCP wraps exceptions as tool errors (unchanged).

## Acceptance criteria
1. `parse_diff` returns per-file added/removed line runs — added in new-file
   coordinates, removed in old-file coordinates — and never counts context lines.
2. `parse_diff` classifies each file added / modified / deleted from its
   `/dev/null` sides; a deleted file keeps its old-side path.
3. Only entities whose span intersects a changed line are touched: a diff
   confined to one function's body reports that function and not its
   untouched file-siblings.
4. An entity present only in the head state is `change="added"` with an empty radius.
5. An entity present in the base state and gone from head is `change="deleted"`,
   and head entities still referencing its name appear in its radius at hop 1.
6. A modified entity's radius equals `six.compute_radius` for its addr:
   transitive dependents by hops.
7. `load_bearing` is true exactly when direct impact fan-in (calls/reads/inherits)
   is at least `LOAD_BEARING_FAN_IN`.
8. `cross_language` maps a changed path to the labels of dependents extracted by a
   different recipe (a Dockerfile `copies` edge onto a changed .py file appears).
9. `at_risk` is the union of all touched radii at minimum hops, with touched
   entities themselves excluded.
10. `delta_text` renders plain sentences naming each touched entity with its
    change and risk; an empty diff answers
    "The diff touches nothing on the map."
11. `git_delta` on a real git repo detects both a modified and a deleted entity
    from `git diff` alone — base contents fetched via git, not caller-supplied.
12. `pulse_delta` is listed by the MCP server and dispatches a `diff` argument
    to the delta answer.

## Out of scope
- Renames: git rename headers are treated as delete + add (no identity carry-over).
- Working-tree-vs-index subtleties (`--staged`); `git_delta` reads `git diff <base>`.
- Panel/board rendering of the delta; CLI surface; V1 store/judge integration.
- Semantic "risk scoring" beyond hop counts and the fan-in threshold.

## Open questions / assumptions
- Entity spans are not persisted (only start lines); the implementation may
  re-parse changed files through their recipes to get spans. Contract is
  behavioral (AC3), not representational.
- Module/package scope entities are never listed in `touched` — file-level
  impact is what `cross_language` and `files` are for.
- Without `base_files` and without git, deletions are undetectable; the report
  then classifies from head spans only. Stated, not tested.
- Deleted-entity dependents are found via edges still targeting the gone name
  (resolved-to-external or matching target text) — an honest "still referenced".
