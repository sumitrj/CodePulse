"""Recipes: languages as data.

A recipe is a YAML file declaring, for one language: which tree-sitter query
patterns mark entities, which mark import-style bindings, and which mark
references — plus how references resolve. The engine that runs recipes
(codepulse.engine) knows no language; everything language-shaped lives here,
as data. See specs/recipes/SPEC.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

from tree_sitter import Query

try:  # tree-sitter >= 0.23
    from tree_sitter import QueryCursor
except ImportError:  # pragma: no cover - older bindings
    QueryCursor = None

from tree_sitter_language_pack import get_language, get_parser


class RecipeError(Exception):
    """The recipe file is invalid; the message names the offending field or query."""


@dataclass(frozen=True)
class EntityRule:
    kind: str
    query: str


@dataclass(frozen=True)
class BindingRule:
    query: str
    style: str = "module"            # "module": dotted name maps to a path; "path": text is a relative path


@dataclass(frozen=True)
class DocRule:
    query: str                       # @doc capture; attaches to the innermost enclosing entity


@dataclass(frozen=True)
class ReferenceRule:
    kind: str
    query: str
    resolve: str = "lexical"
    only: tuple[str, ...] = ()       # resolved target must be one of these entity kinds
    unresolved: str = "external"     # "external" keeps the name as written; "drop" discards


@dataclass(frozen=True)
class Recipe:
    name: str
    version: str
    language: str                    # tree-sitter grammar key
    matches: tuple[str, ...]         # file globs
    module_suffix: str = ""          # "billing" -> "billing" + suffix for module targets
    entities: tuple[EntityRule, ...] = ()
    bindings: tuple[BindingRule, ...] = ()
    references: tuple[ReferenceRule, ...] = ()
    docs: tuple[DocRule, ...] = ()


def compile_query(language_name: str, source: str) -> Query:
    lang = get_language(language_name)
    try:
        return Query(lang, source)
    except TypeError:  # pragma: no cover - older bindings expose Language.query
        return lang.query(source)


def run_matches(query: Query, node):
    """Yield one {capture_name: [Node, ...]} dict per match, across binding versions."""
    if QueryCursor is not None:
        raw = QueryCursor(query).matches(node)
    else:  # pragma: no cover - older bindings
        raw = query.matches(node)
    for _pattern, captures in raw:
        yield {
            key: value if isinstance(value, list) else [value]
            for key, value in captures.items()
        }


def parser_for(language_name: str):
    return get_parser(language_name)


_REQUIRED = ("name", "version", "language", "matches")


def load_recipe(path: Path) -> Recipe:
    path = Path(path)
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, Mapping):
        raise RecipeError(f"{path}: recipe must be a YAML mapping")
    for field_name in _REQUIRED:
        if field_name not in data:
            raise RecipeError(f"{path}: missing required field: {field_name}")

    recipe = Recipe(
        name=str(data["name"]),
        version=str(data["version"]),
        language=str(data["language"]),
        matches=tuple(data["matches"]),
        module_suffix=str(data.get("module_suffix", "")),
        entities=tuple(
            EntityRule(kind=e["kind"], query=e["query"])
            for e in data.get("entities") or ()
        ),
        bindings=tuple(
            BindingRule(query=b["query"], style=b.get("style", "module"))
            for b in data.get("bindings") or ()
        ),
        references=tuple(
            ReferenceRule(
                kind=r["kind"],
                query=r["query"],
                resolve=r.get("resolve", "lexical"),
                only=tuple(r.get("only") or ()),
                unresolved=r.get("unresolved", "external"),
            )
            for r in data.get("references") or ()
        ),
        docs=tuple(DocRule(query=d["query"]) for d in data.get("docs") or ()),
    )

    for rule in (*recipe.entities, *recipe.bindings, *recipe.references, *recipe.docs):
        try:
            compile_query(recipe.language, rule.query)
        except Exception as exc:
            raise RecipeError(f"{path}: bad query {rule.query!r}: {exc}") from exc
    return recipe


def builtin_recipes() -> list[Recipe]:
    """Every recipe shipped with codepulse (the repo's recipes/ directory)."""
    recipes_dir = Path(__file__).resolve().parent.parent / "recipes"
    return [load_recipe(p) for p in sorted(recipes_dir.glob("*.yml"))]


@dataclass(frozen=True)
class CheckReport:
    ok: bool
    failures: tuple[str, ...]


def check_recipe(recipe: Recipe, fixture_dir: Path) -> CheckReport:
    """The factory's validate step: run the recipe over a fixture repo and
    verify every edge listed in the fixture's expected.yml exists."""
    import tempfile

    from .engine import Addr, Engine

    fixture_dir = Path(fixture_dir)
    expected = yaml.safe_load((fixture_dir / "expected.yml").read_text()) or []

    def addr(text: str) -> Addr:
        path, _, name = text.rpartition(":")
        return Addr(path, name)

    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        engine = Engine(Path(tmp) / "check.db", [recipe], root=fixture_dir)
        engine.apply()
        for item in expected:
            hits = [
                e for e in engine.outgoing(addr(item["src"]), kind=item["kind"])
                if e.dst == addr(item["dst"])
            ]
            if not hits:
                failures.append(
                    f"expected {item['kind']} edge {item['src']} -> {item['dst']}, not found"
                )
    return CheckReport(ok=not failures, failures=tuple(failures))
