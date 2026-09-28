> **The original vision (July 2026), kept for history.** The shipped product is
> narrower: the map and its questions. Identity, the change judge, and fleet
> inventory below are not built. For what exists, read the [README](../README.md).

# CodePulse — PRD v1

**One-liner:** A system of record for code as capabilities — every piece of logic gets an identity, a promise, a version, and a map of dependents, so agents and humans stop re-deriving structure and nothing changes silently.

---

## 1. Problem

Two problems, one root cause: the unit of storage (files, text) is not the unit of meaning (capabilities).

**P1 — Agents rebuild the map every session.** Coding agents spend a large share of tokens and turns grepping and reading to reconstruct structure that was known yesterday and thrown away. Their worst failures are knowledge failures, not reasoning failures: the missed caller reached through a re-export, the third reimplementation of a helper, the function that quietly stopped doing half its job, the wrong test subset.

**P2 — Accelerator inventory is untrackable.** Delivery companies fork a base codebase per client, customize per client, and batch development across the fleet. Today nobody can answer: what does client X actually run, how far has it drifted from base, which forks need this patch, which client customizations converge and should be upstreamed.

## 2. Product thesis

- Code is stored as **units** — function/class/config-level capabilities with stable identity, an explicit contract, declared promises, and a version.
- Semantics are **versioned, not mirrored**. Meaning is asserted at discrete change events in an append-only ledger; the system never claims a continuously-true semantic mirror, so it is never stale about meaning.
- The interface is **six verbs shared by humans and agents** — one language, two renderings. Every answer must survive being spoken as one plain sentence plus one picture.
- Knowledge arrives **before the question.** Hooks inject context into agent turns; the query API is the secondary surface, not the primary one.

## 3. Users and value

| User | Gets |
|---|---|
| Coding agent | Neighborhood injected pre-edit, blast radius verified post-edit, locate-by-meaning, exact test set. Fewer searches, tokens, and wrong edits. |
| Developer | "What breaks if I touch this" — including silent behavior changes that compilers and diffs don't catch. |
| Tech lead | System view: duplication, complexity hotspots, load-bearing units, churn. |
| Delivery org | Per-client per-unit inventory, drift measurement, patch-port lists, upstream-merge candidates. |

## 4. Core concepts

**Unit** — a bounded piece of behavior with:
- **Identity** — a contract-anchored semantic fingerprint (behavioral signal secondary) that survives rename, move, and moderate refactor. This is the moat and the hardest engineering problem.
- **Contract** — inputs, outputs, effects. Tracked mechanically and continuously; always fresh.
- **Promises** — declared behavioral claims: pinned tests, invariants, a short behavioral summary written at version time. Asserted at events, not inferred continuously.
- **Edges** — calls, data flow, ownership, provenance.

**Version event** — on every commit, each touched unit's change is classified: *refactor / patch / minor / major*. A promise leaving a unit is **major even when the signature is unchanged** — this is the silent-update killer.

**Movement** — split, merge, and migrate are typed events with provenance edges ("responsibility Y moved from F to G"), not diff artifacts.

**Lineage** — call edges depend on versions (`F@^3`, package-manager-style, turned inward). A fork is a per-unit lineage; drift between base and a client is lineage divergence, unit by unit.

## 5. Functional scope

**Pipeline (the always-on loop):**
Watch (save / commit / merge) → Cut (syntactic differ maps the diff to touched units — dumb, fast, always-on) → Judge (change classifier: agent-driven, paranoid by default, pinned-test mechanical backstop) → Record (append-only graph of record, snapshot per commit) → Serve.

**The six verbs (the entire query surface, for humans and agents alike):**
1. **What is this?** — the card: identity, contract, promises, version.
2. **Who touches it?** — callers, dependents, owners.
3. **What breaks if I change it?** — blast radius, transitive, configurable depth.
4. **What changed?** — the version ledger over any span.
5. **Where does X live?** — locate by meaning, not string.
6. **How far apart are these two?** — drift between forks, branches, or versions.

Every capability discussed (silent-break alerts, fork inventory, upstream suggestions, test selection) is one of these verbs with a specific subject. A feature requiring a seventh verb triggers a design review, not an API addition.

**Delivery surfaces:**
- **S1 — MCP server** exposing the six verbs (Claude Code, VS Code agents, Antigravity).
- **S2 — Hooks:** pre-edit injection (unit card + dependents into the agent's turn, within token budget) and post-edit verification (radius check, promise check). Push, not pull — this is the primary agent surface.
- **S3 — Companion app:** the same six verbs rendered visually — system silhouette, live edit ripple, drift board, simplicity audit. Read-only in v1.

## 6. Phasing

**V1 — The map** (validates P1). Single repo; TypeScript + Python. Function/class units, identity with rename/move tracking. Verbs 1, 2, 3, 5. MCP server + Claude Code hooks. Local-first daemon.
*Gate:* ≥50% reduction in agent search calls per task; freshness <2s on a 500-file repo; identity survives real refactor histories.

**V2 — The ledger.** Promises, the Judge, version events, verb 4, post-edit verification.
*Gate:* a real silent promise break caught in live usage, with a false-alarm rate low enough that nobody mutes the tool.

**V3 — The fleet** (validates P2). Multi-repo lineage, drift (verb 6), patch-port lists, upstream candidates, companion-app drift board.

## 7. Non-functional requirements

- **Freshness:** a save is reflected in the map in <2s. Past that, agents act on lies; this is a kill-gate, not a nice-to-have.
- **Trust:** missed majors ≈ 0 (paranoid classifier + pinned-test backstop) *and* false-alarm rate low enough to stay unmuted. Either alone is easy; both is the product.
- **Cost:** pre-edit injection within a configurable token budget; verb queries <100ms.
- **Privacy:** local-first; nothing leaves the machine in v1. Cloud/team sync is a later, explicit opt-in.

## 8. Configurables (control points)

| Owner | Knobs |
|---|---|
| Repo owner | Scope: repos, paths, languages, exclusions (vendored/generated/secrets). Granularity: function / class / module. |
| Tech lead | Judge paranoia (resolve silently vs escalate); promise sources (tests / +docstrings / +AI summaries); major-bump rules and override rights. |
| Team | Radius depth; noise thresholds (what interrupts vs what logs). |
| Developer/agent | Push vs pull; injection token budget. |
| Delivery org | Tracked bases, per-client drift tolerance, upstream suggestions auto vs advisory. |
| Ops | Freshness cadence: save / commit / merge. |

## 9. Success metrics

1. **Search saved** — agent search calls and tokens per completed task, before vs after. If this doesn't drop, the delivery layer failed.
2. **Judge accuracy** — escaped majors (target ≈0) and false-alarm rate (low). The trust metric.
3. **Freshness lag** — change→map, in seconds.
4. **Acted-on rate** — share of warnings and suggestions humans actually act on. The honesty gauge.
5. **Time-to-first-useful-answer** — a person or agent who has never seen the tool gets value in <60s, uninstructed.

Meta-health: verb count stays ≤6; knob direction (users widening scope and raising budgets = winning; muting and excluding = defaults are wrong, and the knobs show exactly where).

## 10. Risks

| Risk | Mitigation |
|---|---|
| Unit identity breaks under refactoring → graph becomes confetti | Contract-anchored fingerprint with behavioral signal secondary; majority of early engineering here; measured against real refactor histories before anything else ships. |
| Stale graph → agents confidently act on lies | Incremental-only extraction; freshness as hard NFR with kill-gate. |
| Judge calls a major a patch → trust chain poisoned | Paranoid default; failing pinned test mechanically forces ≥minor; human overrides audited. |
| Alert noise → tool gets muted | Noise budget as first-class config; acted-on rate as a core metric from day one. |
| Agents ignore an offered tool | Hooks (push) are the primary surface; the query API is secondary by design. |

## 11. Open decisions

- Default granularity (function vs class) — resolve with V1 usage data.
- Judge model choice and cost envelope per commit.
- Companion app platform (web vs desktop) — decide at V2 exit.

## 12. Out of scope (v1–v3)

Code generation, refactoring execution, CI replacement, runtime observability, non-code assets (docs, designs), automatic merging of forks.
