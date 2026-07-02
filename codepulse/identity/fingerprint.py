"""Unit -> Fingerprint. Language-agnostic: operates on Unit only.

body_hash is AST-normalized: comments and whitespace vanish at parse,
docstrings are removed explicitly, identifier names are kept.
"""
import ast
import hashlib

from .model import Fingerprint, Unit


def fingerprint(unit: Unit) -> Fingerprint:
    return Fingerprint(
        contract_hash=_hash(canonical_contract(unit)),
        body_hash=_hash(canonical_body(unit)),
        neighborhood_hash=_hash(",".join(sorted(unit.calls))),
    )


def canonical_body(unit: Unit) -> str:
    node = ast.parse(unit.body).body[0]
    statements = list(node.body)
    if statements and _is_docstring(statements[0]):
        statements = statements[1:]
    if not statements:
        return "pass"
    return "\n".join(ast.unparse(stmt) for stmt in statements)


def canonical_contract(unit: Unit) -> str:
    sig = unit.signature
    params = ",".join(
        f"{p.name}:{p.annotation or ''}:{'1' if p.default is not None else '0'}"
        for p in sig.params
    )
    return f"{unit.kind}({params})->{sig.returns or ''}"


def _is_docstring(stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]
