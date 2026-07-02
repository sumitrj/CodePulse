"""Python source -> Units. The only language-specific module in identity."""
import ast

from .model import Param, Signature, Unit


def extract_units(source: str, path: str) -> list[Unit]:
    tree = ast.parse(source)
    lines = source.splitlines()
    units: list[Unit] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            units.append(_unit(node, lines, path, "function", node.name))
        elif isinstance(node, ast.ClassDef):
            units.append(_unit(node, lines, path, "class", node.name))
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    units.append(
                        _unit(member, lines, path, "method", f"{node.name}.{member.name}")
                    )
    return units


def _unit(node, lines, path, kind, qualname) -> Unit:
    return Unit(
        name=node.name,
        kind=kind,
        path=path,
        qualname=qualname,
        signature=_signature(node) if kind != "class" else Signature(),
        body=_segment(lines, node),
        calls=frozenset(_calls(node)),
    )


def _signature(node) -> Signature:
    args = node.args
    params: list[Param] = []
    positional = list(args.posonlyargs) + list(args.args)
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for arg, default in zip(positional, defaults):
        params.append(_param(arg, default))
    if args.vararg:
        params.append(_param(args.vararg, None, prefix="*"))
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        params.append(_param(arg, default))
    if args.kwarg:
        params.append(_param(args.kwarg, None, prefix="**"))
    returns = ast.unparse(node.returns) if node.returns is not None else None
    return Signature(params=tuple(params), returns=returns)


def _param(arg, default, prefix="") -> Param:
    return Param(
        name=prefix + arg.arg,
        annotation=ast.unparse(arg.annotation) if arg.annotation is not None else None,
        default=ast.unparse(default) if default is not None else None,
    )


def _segment(lines, node) -> str:
    start = node.lineno
    for decorator in getattr(node, "decorator_list", []):
        start = min(start, decorator.lineno)
    segment = lines[start - 1 : node.end_lineno]
    col = node.col_offset
    return "\n".join(
        line[col:] if not line[:col].strip() else line for line in segment
    )


def _calls(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            name = _dotted(n.func)
            if name:
                yield name


def _dotted(expr) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = _dotted(expr.value)
        return f"{base}.{expr.attr}" if base else expr.attr
    return None
