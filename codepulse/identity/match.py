"""resolve(): three matching tiers, cheap to expensive.

Tier 1 (exact) clears the bulk; Tier 2 (anchor: same path+qualname) handles
refactors and contract evolution, and its ordering before Tier 3 is what makes
splits fall out correctly - the continuing unit claims its old id by anchor,
so an extracted sibling has nothing left to steal. Tier 3 scores the remaining
old x new pairs globally with deterministic greedy assignment.
"""
import hashlib
from difflib import SequenceMatcher

from .fingerprint import canonical_body, canonical_contract, fingerprint
from .model import Assignment, Fingerprint, IdentityResult, Unit

# The anchor is evidence, not proof: below this, a same-named occupant is an
# impostor and falls through to global matching.
_ANCHOR_GUARD = 0.3
_MATCH_THRESHOLD = 0.6


def resolve(old, new) -> IdentityResult:
    old = dict(old)
    new = list(new)
    old_fp = {uid: fingerprint(u) for uid, u in old.items()}
    new_fp = [fingerprint(u) for u in new]
    assignments: dict[int, Assignment] = {}
    claimed: set[str] = set()

    # Tier 1 - exact: full fingerprint and location match
    for i, unit in enumerate(new):
        for uid, old_unit in old.items():
            if uid in claimed:
                continue
            if (
                old_fp[uid] == new_fp[i]
                and old_unit.path == unit.path
                and old_unit.qualname == unit.qualname
            ):
                assignments[i] = Assignment(unit, uid, "exact", 1.0)
                claimed.add(uid)
                break

    # Tier 2 - anchor: same path+qualname, content changed
    for i, unit in enumerate(new):
        if i in assignments:
            continue
        for uid, old_unit in old.items():
            if uid in claimed:
                continue
            if (
                old_unit.path == unit.path
                and old_unit.qualname == unit.qualname
                and old_unit.kind == unit.kind
            ):
                score = _score(old_unit, unit, old_fp[uid], new_fp[i])
                if score >= _ANCHOR_GUARD:
                    assignments[i] = Assignment(
                        unit, uid, "refactored", round(min(score, 0.99), 4)
                    )
                    claimed.add(uid)
                break

    # Tier 3 - content: global scoring over the remainder, greedy by score
    candidates = []
    for i, unit in enumerate(new):
        if i in assignments:
            continue
        for uid, old_unit in old.items():
            if uid in claimed or old_unit.kind != unit.kind:
                continue
            score = _score(old_unit, unit, old_fp[uid], new_fp[i])
            if score >= _MATCH_THRESHOLD:
                candidates.append((score, unit.qualname, old_unit.qualname, i, uid))
    for score, _, _, i, uid in sorted(candidates, key=lambda c: (-c[0], c[1], c[2])):
        if i in assignments or uid in claimed:
            continue
        unit, old_unit = new[i], old[uid]
        kind, confidence = _kind(old_unit, unit, old_fp[uid], new_fp[i])
        if confidence is None:
            confidence = round(min(score, 0.99), 4)
        assignments[i] = Assignment(unit, uid, kind, confidence)
        claimed.add(uid)

    # Leftovers: unmatched new units get fresh deterministic ids
    for i, unit in enumerate(new):
        if i not in assignments:
            assignments[i] = Assignment(unit, _mint(unit, new_fp[i]), "new", 1.0)

    removed = frozenset(uid for uid in old if uid not in claimed)
    return IdentityResult(
        assignments=tuple(assignments[i] for i in range(len(new))),
        removed=removed,
    )


def _score(old_unit: Unit, unit: Unit, old_fp: Fingerprint, new_fp: Fingerprint) -> float:
    if old_fp.body_hash == new_fp.body_hash:
        # An intact body always outranks any similarity-based match
        return (
            0.97
            + (0.01 if old_unit.name == unit.name else 0.0)
            + (0.01 if old_unit.path == unit.path else 0.0)
        )
    contract_sim = (
        1.0
        if old_fp.contract_hash == new_fp.contract_hash
        else _ratio(canonical_contract(old_unit), canonical_contract(unit))
    )
    body_sim = _ratio(canonical_body(old_unit), canonical_body(unit))
    return min(0.9, 0.4 * contract_sim + 0.6 * body_sim)


def _kind(old_unit: Unit, unit: Unit, old_fp: Fingerprint, new_fp: Fingerprint):
    if old_fp.body_hash == new_fp.body_hash:
        renamed = old_unit.qualname != unit.qualname
        moved = old_unit.path != unit.path
        if renamed and moved:
            return "renamed+moved", 0.95
        if renamed:
            return "renamed", 0.95
        if moved:
            return "moved", 0.95
    return "refactored", None


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _mint(unit: Unit, fp: Fingerprint) -> str:
    raw = f"{unit.path}|{unit.qualname}|{fp.contract_hash}|{fp.body_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
