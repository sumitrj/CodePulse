"""The Judge: classify a unit's change as refactor / patch / minor / major.

Semantic effort is concentrated at change events, never run as a mirror.
Mechanical fast paths handle the provable cases without a model call; the
model (Claude) judges the rest; a paranoid heuristic covers offline runs.
A promise leaving a unit is MAJOR even when the signature is unchanged.
"""
from dataclasses import dataclass
from typing import Literal

from .identity import Unit
from .identity.fingerprint import fingerprint

Classification = Literal["refactor", "patch", "minor", "major"]

_SYSTEM = """You are the Judge in CodePulse, a system of record for code as capabilities.
Classify how a code unit changed between two snapshots:

- refactor: provably no semantic change (formatting, rename of internals, equivalent restructure).
- patch: internal change; every behavioral promise the old unit made still holds.
- minor: behavior extended; all existing promises still hold.
- major: a promise the old unit made no longer holds - behavior was removed, weakened,
  or relocated out of the unit. This includes "silent updates": logic extracted or dropped
  while the signature stays identical. Callers relying on the old behavior would break.

Be paranoid: when genuinely unsure between two classes, choose the more severe one.
The behavioral_summary is one or two plain sentences a teammate would read in a changelog:
what the unit promises now, and what changed."""


@dataclass(frozen=True)
class Verdict:
    classification: Classification
    summary: str
    source: Literal["mechanical", "model", "heuristic"]


def classify_change(old: Unit, new: Unit, model: str = "claude-opus-4-8") -> Verdict:
    old_fp, new_fp = fingerprint(old), fingerprint(new)
    if old_fp.body_hash == new_fp.body_hash and old_fp.contract_hash == new_fp.contract_hash:
        detail = (
            "formatting or comments only"
            if old.qualname == new.qualname and old.path == new.path
            else "unit was renamed or moved intact"
        )
        return Verdict("refactor", f"No semantic change: {detail}.", "mechanical")
    try:
        return _model_verdict(old, new, model)
    except Exception:
        return _heuristic(old, new, old_fp, new_fp)


def _model_verdict(old: Unit, new: Unit, model: str) -> Verdict:
    import anthropic
    from pydantic import BaseModel

    class ChangeVerdict(BaseModel):
        classification: Classification
        behavioral_summary: str

    client = anthropic.Anthropic()
    prompt = (
        f"OLD ({old.path} :: {old.qualname}):\n```python\n{old.body}\n```\n\n"
        f"NEW ({new.path} :: {new.qualname}):\n```python\n{new.body}\n```\n\n"
        "Classify this change and summarize the behavioral difference."
    )
    response = client.messages.parse(
        model=model,
        max_tokens=2048,
        thinking={"type": "adaptive"},
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=ChangeVerdict,
    )
    verdict = response.parsed_output
    if verdict is None:
        raise ValueError("model returned no parseable verdict")
    return Verdict(verdict.classification, verdict.behavioral_summary, "model")


def _heuristic(old: Unit, new: Unit, old_fp, new_fp) -> Verdict:
    """Offline fallback, biased paranoid: lost calls or a shrunken body escalate."""
    lost_calls = old.calls - new.calls
    if lost_calls:
        return Verdict(
            "major",
            f"Heuristic: unit no longer invokes {', '.join(sorted(lost_calls))} - "
            "a behavioral promise may have left the unit.",
            "heuristic",
        )
    if old_fp.contract_hash != new_fp.contract_hash:
        return Verdict("minor", "Heuristic: contract changed; behavior likely extended.", "heuristic")
    if len(new.body) < 0.7 * len(old.body):
        return Verdict("major", "Heuristic: body shrank substantially; promises may be gone.", "heuristic")
    return Verdict("patch", "Heuristic: internal change; contract and callees intact.", "heuristic")
