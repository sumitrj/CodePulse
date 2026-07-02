"""Frozen data model for unit identity. Imports nothing internal."""
from dataclasses import dataclass, field
from typing import Literal

UnitKind = Literal["function", "class", "method"]
MatchKind = Literal["exact", "renamed", "moved", "renamed+moved", "refactored", "new"]


@dataclass(frozen=True)
class Param:
    name: str
    annotation: str | None = None
    default: str | None = None


@dataclass(frozen=True)
class Signature:
    params: tuple[Param, ...] = ()
    returns: str | None = None


@dataclass(frozen=True)
class Unit:
    name: str
    kind: UnitKind
    path: str
    qualname: str
    signature: Signature
    body: str
    calls: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Fingerprint:
    contract_hash: str
    body_hash: str
    neighborhood_hash: str


@dataclass(frozen=True)
class Assignment:
    unit: Unit
    unit_id: str
    kind: MatchKind
    confidence: float


@dataclass(frozen=True)
class IdentityResult:
    assignments: tuple[Assignment, ...]
    removed: frozenset[str]
