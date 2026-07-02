"""CodePulse demo: two snapshots of a toy shop codebase, the six verbs in action.

Run from the codepulse project root:  python demo/demo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codepulse.identity import extract_units, resolve
from codepulse.judge import classify_change
from codepulse.relationships import StateRef, UnitRef, extract_edges


def load(snapshot: str) -> dict[str, str]:
    root = Path(__file__).parent / snapshot
    return {p.name: p.read_text() for p in sorted(root.glob("*.py"))}


def units_of(files):
    return [u for path, src in sorted(files.items()) for u in extract_units(src, path)]


def blast_radius(graph, ref):
    """Transitive closure over incoming calls/reads/inherits - verb 3."""
    seen, frontier = set(), [ref]
    while frontier:
        current = frontier.pop()
        for kind in ("calls", "reads", "inherits"):
            for edge in graph.incoming(current, kind=kind):
                if edge.src not in seen and edge.src.qualname != "<module>":
                    seen.add(edge.src)
                    frontier.append(edge.src)
    return seen


def header(title):
    print(f"\n{'=' * 64}\n  {title}\n{'=' * 64}")


v1_files, v2_files = load("shop_v1"), load("shop_v2")
v1_units, v2_units = units_of(v1_files), units_of(v2_files)
v1_graph = extract_edges(v1_files)

header("VERB 1 - What is this?  (billing.total)")
total = next(u for u in v1_units if u.qualname == "total")
print(f"  unit     : {total.path} :: {total.qualname}  [{total.kind}]")
print(f"  contract : ({', '.join(p.name for p in total.signature.params)})")
print(f"  calls    : {', '.join(sorted(total.calls))}")

header("VERB 2 - Who touches it?  (billing.total)")
for edge in sorted(v1_graph.incoming(UnitRef("billing.py", "total")), key=str):
    print(f"  {edge.src.path} :: {edge.src.qualname:<15} --{edge.kind}--> total  (line {edge.line})")

header("VERB 3 - What breaks if I change it?  (config.TAX_RATE)")
for ref in sorted(blast_radius(v1_graph, StateRef("config.py", "TAX_RATE")), key=str):
    print(f"  at risk: {ref.path} :: {ref.qualname}")

header("IDENTITY - resolving shop_v1 -> shop_v2")
old_snapshot = {a.unit_id: a.unit for a in resolve({}, v1_units).assignments}
result = resolve(old_snapshot, v2_units)
for a in sorted(result.assignments, key=lambda a: (a.kind, a.unit.qualname)):
    origin = ""
    if a.kind not in ("new",) and a.unit_id in old_snapshot:
        old = old_snapshot[a.unit_id]
        if (old.path, old.qualname) != (a.unit.path, a.unit.qualname):
            origin = f"   (was {old.path} :: {old.qualname})"
    print(f"  [{a.kind:>13}] {a.unit.path} :: {a.unit.qualname:<18} conf={a.confidence:.2f}{origin}")
for uid in result.removed:
    old = old_snapshot[uid]
    print(f"  [      removed] {old.path} :: {old.qualname}")

header("THE JUDGE - classifying every surviving change")
for a in result.assignments:
    if a.kind == "new" or a.unit_id not in old_snapshot:
        continue
    verdict = classify_change(old_snapshot[a.unit_id], a.unit)
    flag = "  <-- SILENT PROMISE BREAK CAUGHT" if verdict.classification == "major" else ""
    print(f"\n  {a.unit.path} :: {a.unit.qualname}  [{a.kind}]")
    print(f"    verdict : {verdict.classification.upper()}  (via {verdict.source}){flag}")
    print(f"    summary : {verdict.summary}")

header("SUMMARY")
kinds = {}
for a in result.assignments:
    kinds[a.kind] = kinds.get(a.kind, 0) + 1
print(f"  v1 units: {len(v1_units)}   v2 units: {len(v2_units)}   edges(v1): {len(v1_graph.edges)}")
print("  " + "   ".join(f"{k}: {n}" for k, n in sorted(kinds.items())) + f"   removed: {len(result.removed)}")
print("\n  The point: process() kept its signature but stopped auditing orders.")
print("  No compiler, diff review, or grep flags that. CodePulse does.")
