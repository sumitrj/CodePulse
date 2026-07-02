"""Relationship extraction: typed, resolved edges between units.

Cross-file references are resolved through import bindings, not recorded as
strings - `from billing import total as t; t(x)` yields an edge to billing.py's
`total` unit. Unresolvable targets are preserved as ExternalRef with the dotted
name as written. `incoming()` is the blast-radius primitive.
"""
import ast
from dataclasses import dataclass
from typing import Literal, Mapping, Union

EdgeKind = Literal["calls", "imports", "reads", "writes", "inherits", "contains"]

MODULE_SCOPE = "<module>"


@dataclass(frozen=True)
class UnitRef:
    path: str
    qualname: str


@dataclass(frozen=True)
class StateRef:
    path: str
    name: str


@dataclass(frozen=True)
class ExternalRef:
    name: str


Target = Union[UnitRef, StateRef, ExternalRef]


@dataclass(frozen=True)
class Edge:
    src: UnitRef
    dst: Target
    kind: EdgeKind
    line: int


@dataclass(frozen=True)
class EdgeGraph:
    edges: frozenset[Edge]

    def outgoing(self, ref: UnitRef, kind: EdgeKind | None = None) -> frozenset[Edge]:
        return frozenset(
            e for e in self.edges if e.src == ref and (kind is None or e.kind == kind)
        )

    def incoming(self, ref: Target, kind: EdgeKind | None = None) -> frozenset[Edge]:
        return frozenset(
            e for e in self.edges if e.dst == ref and (kind is None or e.kind == kind)
        )


def extract_edges(files: Mapping[str, str]) -> EdgeGraph:
    snapshot = _Snapshot(files)
    edges: set[Edge] = set()
    for path in files:
        edges |= _file_edges(path, snapshot)
    return EdgeGraph(frozenset(edges))


def _module_name(path: str) -> str:
    return path.removesuffix(".py").replace("/", ".")


class _Snapshot:
    """Per-file symbol tables for cross-file resolution."""

    def __init__(self, files: Mapping[str, str]):
        self.trees: dict[str, ast.Module] = {}
        self.modules: dict[str, str] = {}  # module name -> path
        self.units: dict[str, set[str]] = {}  # path -> top-level unit names
        self.methods: dict[str, set[tuple[str, str]]] = {}  # path -> (class, method)
        self.state: dict[str, set[str]] = {}  # path -> module-level variable names
        for path, source in files.items():
            try:
                tree = ast.parse(source, filename=path)
            except SyntaxError:
                raise
            self.trees[path] = tree
            self.modules[_module_name(path)] = path
            units, methods, state = set(), set(), set()
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    units.add(node.name)
                elif isinstance(node, ast.ClassDef):
                    units.add(node.name)
                    for member in node.body:
                        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            methods.add((node.name, member.name))
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            state.add(target.id)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    state.add(node.target.id)
            self.units[path] = units
            self.methods[path] = methods
            self.state[path] = state

    def member(self, module: str, name: str) -> Target:
        """Resolve module.name to a snapshot ref, or ExternalRef as written."""
        if module in self.modules:
            path = self.modules[module]
            if name in self.units[path]:
                return UnitRef(path, name)
            if name in self.state[path]:
                return StateRef(path, name)
        return ExternalRef(f"{module}.{name}")


def _file_edges(path: str, snap: _Snapshot) -> set[Edge]:
    tree = snap.trees[path]
    module_ref = UnitRef(path, MODULE_SCOPE)
    edges: set[Edge] = set()

    # bindings: local name -> ("module", module) | ("member", module, name)
    bindings: dict[str, tuple] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                bindings[local] = ("module", alias.name)
                if alias.name in snap.modules:
                    dst: Target = UnitRef(snap.modules[alias.name], MODULE_SCOPE)
                else:
                    dst = ExternalRef(alias.name)
                edges.add(Edge(module_ref, dst, "imports", node.lineno))
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                bindings[local] = ("member", node.module, alias.name)
                edges.add(
                    Edge(module_ref, snap.member(node.module, alias.name), "imports", node.lineno)
                )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ref = UnitRef(path, node.name)
            edges.add(Edge(module_ref, ref, "contains", node.lineno))
            edges |= _unit_edges(ref, node, None, path, snap, bindings)
        elif isinstance(node, ast.ClassDef):
            class_ref = UnitRef(path, node.name)
            edges.add(Edge(module_ref, class_ref, "contains", node.lineno))
            for base in node.bases:
                dst = _resolve_base(base, path, snap, bindings)
                if dst is not None:
                    edges.add(Edge(class_ref, dst, "inherits", base.lineno))
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_ref = UnitRef(path, f"{node.name}.{member.name}")
                    edges.add(Edge(class_ref, method_ref, "contains", member.lineno))
                    edges |= _unit_edges(method_ref, member, node.name, path, snap, bindings)
    return edges


def _unit_edges(ref, fn, owner_class, path, snap, bindings) -> set[Edge]:
    edges: set[Edge] = set()
    globals_ = {
        name
        for node in ast.walk(fn)
        if isinstance(node, ast.Global)
        for name in node.names
    }
    local = _local_names(fn) - globals_

    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            dst = _resolve_call(node.func, owner_class, path, snap, bindings, local)
            if dst is not None:
                edges.add(Edge(ref, dst, "calls", node.lineno))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in local:
                continue
            dst = _resolve_state(node.id, path, snap, bindings)
            if dst is not None:
                edges.add(Edge(ref, dst, "reads", node.lineno))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            if node.id in globals_ and node.id in snap.state[path]:
                edges.add(Edge(ref, StateRef(path, node.id), "writes", node.lineno))
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            if node.target.id in globals_ and node.target.id in snap.state[path]:
                edges.add(Edge(ref, StateRef(path, node.target.id), "reads", node.lineno))
    return edges


def _local_names(fn) -> set[str]:
    args = fn.args
    names = {arg.arg for arg in args.posonlyargs + args.args + args.kwonlyargs}
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node is not fn:
            names.add(node.name)
    return names


def _resolve_call(func, owner_class, path, snap, bindings, local) -> Target | None:
    if isinstance(func, ast.Name):
        name = func.id
        if name in local:
            return None
        if name in snap.units[path]:
            return UnitRef(path, name)
        if name in bindings:
            binding = bindings[name]
            if binding[0] == "member":
                dst = snap.member(binding[1], binding[2])
                if isinstance(dst, StateRef):
                    return ExternalRef(f"{binding[1]}.{binding[2]}")
                return dst
            return ExternalRef(binding[1])
        return ExternalRef(name)
    if isinstance(func, ast.Attribute):
        written = _dotted(func)
        base = func.value
        if isinstance(base, ast.Name):
            if base.id == "self" and owner_class and (owner_class, func.attr) in snap.methods[path]:
                return UnitRef(path, f"{owner_class}.{func.attr}")
            if base.id in bindings and bindings[base.id][0] == "module":
                module = bindings[base.id][1]
                if module in snap.modules:
                    dst = snap.member(module, func.attr)
                    if isinstance(dst, UnitRef):
                        return dst
                return ExternalRef(written or f"{base.id}.{func.attr}")
        return ExternalRef(written) if written else None
    return None


def _resolve_state(name, path, snap, bindings) -> StateRef | None:
    if name in snap.state[path]:
        return StateRef(path, name)
    if name in bindings and bindings[name][0] == "member":
        dst = snap.member(bindings[name][1], bindings[name][2])
        if isinstance(dst, StateRef):
            return dst
    return None


def _resolve_base(base, path, snap, bindings) -> Target | None:
    if isinstance(base, ast.Name):
        if base.id in snap.units[path]:
            return UnitRef(path, base.id)
        if base.id in bindings and bindings[base.id][0] == "member":
            dst = snap.member(bindings[base.id][1], bindings[base.id][2])
            if isinstance(dst, UnitRef):
                return dst
            return ExternalRef(f"{bindings[base.id][1]}.{bindings[base.id][2]}")
        return ExternalRef(base.id)
    if isinstance(base, ast.Attribute):
        written = _dotted(base)
        if isinstance(base.value, ast.Name) and base.value.id in bindings:
            binding = bindings[base.value.id]
            if binding[0] == "module" and binding[1] in snap.modules:
                dst = snap.member(binding[1], base.attr)
                if isinstance(dst, UnitRef):
                    return dst
        return ExternalRef(written) if written else None
    return None


def _dotted(expr) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = _dotted(expr.value)
        return f"{base}.{expr.attr}" if base else expr.attr
    return None
