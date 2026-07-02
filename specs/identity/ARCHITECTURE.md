# Identity — architecture (Phase 2)

Design that makes `tests/test_identity.py` pass. No implementation here; this is the
shape Phase 3 fills in. Companion to SPEC.md.

## Placement

Identity is a **pure library**: no IO, no state, no daemon awareness. The V1 daemon
(Watch→Cut) will call it per changed file and hand `IdentityResult` to the store.
Purity is what makes it testable, replayable, and portable.

## Layout

```
codepulse/                      # project root
  pyproject.toml                # package "codepulse"; runtime deps: NONE (stdlib only); dev: pytest
  codepulse/
    __init__.py
    identity/
      __init__.py               # public surface only: extract_units, fingerprint, resolve + types
      model.py                  # frozen dataclasses: Param, Signature, Unit, Fingerprint, Assignment, IdentityResult
      extract.py                # Python source -> list[Unit]   (the only language-specific module)
      normalize.py              # AST canonicalization feeding the hashes
      fingerprint.py            # Unit -> Fingerprint
      match.py                  # resolve(): tiers, scoring, assignment
  specs/identity/               # SPEC.md, ARCHITECTURE.md (this file)
  tests/                        # test_identity.py, RED_BASELINE.txt
```

Rules: `model.py` imports nothing internal. `extract.py` and `normalize.py` know about
Python's `ast`; `fingerprint.py` and `match.py` know only `Unit`/`Fingerprint` — they are
language-agnostic by construction. That line is the future multi-language seam: a
TypeScript adapter later implements only `extract_units(source, path) -> list[Unit]`
(a `LanguageAdapter` protocol), and everything downstream works untouched.

## Normalization (normalize.py)

`body_hash` input = `ast.parse` the body → drop docstring expressions → `ast.unparse`
→ canonical text. Comments and whitespace vanish at parse; docstrings are removed
explicitly (AC2). Identifier names are KEPT — internal renames make a unit
"refactored", not "exact", and names feed similarity scoring.

- `contract_hash` input: canonical string of param names + annotations + default-presence + return annotation.
- `neighborhood_hash` input: sorted, deduplicated callee names.
- All hashes: sha256 over our own canonical strings (never over `ast.unparse` version quirks — pin Python ≥3.11 and hash only strings we format ourselves where feasible).

## Extraction (extract.py)

One `Unit` per module-level function, class, and first-level method. `qualname` is
`Class.method` for methods. `calls` collects `Call` targets (leaf name of
`Name`/`Attribute`). `body` via `ast.get_source_segment`. Nested defs/lambdas are not
units (SPEC out-of-scope). Invalid source: let `SyntaxError` propagate.

## Matching (match.py) — three tiers, cheap to expensive, each old id claimable once

**Tier 1 — Exact.** Full fingerprint equal AND same path+qualname → `"exact"`,
confidence 1.0. Removes the bulk instantly.

**Tier 2 — Anchor.** Same path+qualname, fingerprint differs → candidate for
`"refactored"` / contract evolution (AC5, AC6). Score = body similarity (difflib ratio
over normalized token text) blended with contract similarity; accept above threshold.
Guard: if the same-named occupant is wholly dissimilar (an impostor took the name),
reject and fall through to Tier 3 — the anchor is evidence, not proof.
Ordering note: Tier 2 before Tier 3 is what makes the split (AC7) fall out for free —
`process` claims its old id by anchor first, so the extracted `persist` (whose body is
a subset of old `process`) has nothing left to steal and is minted `"new"`.

**Tier 3 — Content (global).** Remaining old × new: score = weighted(body-hash
equality or similarity, contract similarity, neighborhood overlap, name similarity,
path proximity). Deterministic greedy assignment on descending score with a stable
tie-break (score, then qualname, then path); accept above threshold (~0.6, internal).
Handles rename/move/both (AC4) and same-name disambiguation (AC8 — body dominates name).

**Leftovers.** Unmatched new → `"new"` with fresh id; unmatched old → `removed`.
Conservation (AC10) holds structurally: every old id is claimed at most once across
tiers, every new unit exits exactly one stage.

**Kind derivation** (from the matched pair, not the tier):
body_hash equal → `renamed` / `moved` / `renamed+moved` by what differs (name, path,
both); everything equal → `exact`; body differs → `refactored`. Confidence: 1.0 iff
exact; body-intact renames/moves ~0.95; refactored = the similarity score.

**Id minting:** deterministic — sha256 over (path, qualname, contract_hash, body_hash)
at first sight, truncated. Replayable across machines; no randomness in the library.

## Tradeoffs accepted for V1

- **Greedy over Hungarian** assignment: deterministic and fast; optimal matching is a
  drop-in upgrade inside Tier 3 if refactor-history benchmarks demand it.
- **O(old × new) Tier 3**: fine because the daemon diffs touched files, not whole repos;
  a fingerprint-bucketed index is the scaling path.
- **difflib for similarity**: stdlib, deterministic, good enough to rank; swappable
  behind `score(old, new) -> float` without touching tiers.

## Phase 3 order

model → normalize → extract (AC1, AC2 green) → fingerprint → match tiers 1/leftovers
(AC3, AC9) → tier 2 (AC5, AC6, AC7) → tier 3 (AC4, AC8, AC10). Run the suite after
each step; RED_BASELINE.txt flips line by line.
