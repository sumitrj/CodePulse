"""
Tests for relationship coverage — no relation left behind. Maps 1:1 to
acceptance criteria in specs/coverage/SPEC.md. Red until the engine gains
name-node dedup / attach:following / star imports / self_name, and the
python v7, typescript v3, and tsx v1 recipes land.
"""
from pathlib import Path

import pytest

from codepulse import six
from codepulse.engine import Addr, Engine
from codepulse.recipes import RECIPES_DIR, load_recipe


@pytest.fixture
def python_recipe():
    return load_recipe(RECIPES_DIR / "python.yml")


@pytest.fixture
def ts_recipe():
    return load_recipe(RECIPES_DIR / "typescript.yml")


@pytest.fixture
def build(tmp_path):
    def _build(files, recipes):
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        for rel, src in files.items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(src)
        engine = Engine(tmp_path / "pulse.db", recipes, root=repo)
        engine.apply()
        return engine
    return _build


def dsts(engine, src, kind):
    return {e.dst for e in engine.outgoing(src, kind=kind)}


# === AC1: star imports bind the whole module; bare calls resolve ===

def test_star_import_yields_edge_and_resolves_bare_calls(build, python_recipe):
    engine = build({
        "lib.py": "def base_fn():\n    return 1\n",
        "main.py": "from lib import *\n\ndef use():\n    return base_fn()\n",
    }, [python_recipe])

    assert Addr("lib.py", "<module>") in dsts(engine, Addr("main.py", "<module>"), "imports")
    assert Addr("lib.py", "base_fn") in dsts(engine, Addr("main.py", "use"), "calls")


# === AC2: decorators call from the decorated entity ===

def test_decorators_yield_calls_edges_from_the_decorated_entity(build, python_recipe):
    engine = build({
        "lib.py": "def decorate(f):\n    return f\n\ndef factory(arg):\n    return decorate\n",
        "main.py": (
            "from lib import decorate, factory\n"
            "\n"
            "@decorate\n"
            "def plain():\n"
            "    pass\n"
            "\n"
            "@factory(1)\n"
            "def fancy():\n"
            "    pass\n"
        ),
    }, [python_recipe])

    assert Addr("lib.py", "decorate") in dsts(engine, Addr("main.py", "plain"), "calls")
    assert Addr("lib.py", "factory") in dsts(engine, Addr("main.py", "fancy"), "calls")


# === AC3: attribute-style state reads and writes resolve ===

def test_attribute_state_reads_and_writes_resolve_cross_module(build, python_recipe):
    engine = build({
        "lib.py": "LIMIT = 10\n",
        "main.py": (
            "import lib\n"
            "\n"
            "def peek():\n"
            "    return lib.LIMIT\n"
            "\n"
            "def poke():\n"
            "    lib.LIMIT = 5\n"
            "\n"
            "def bump():\n"
            "    lib.LIMIT += 1\n"
        ),
    }, [python_recipe])

    limit = Addr("lib.py", "LIMIT")
    assert limit in dsts(engine, Addr("main.py", "peek"), "reads")
    assert limit in dsts(engine, Addr("main.py", "poke"), "writes")
    assert limit in dsts(engine, Addr("main.py", "bump"), "writes")


# === AC4: annotations yield types edges ===

def test_annotations_yield_types_edges_to_in_repo_classes(build, python_recipe):
    engine = build({
        "lib.py": "class Base:\n    pass\n",
        "main.py": (
            "from lib import Base\n"
            "\n"
            "def f(a: Base) -> Base:\n"
            "    v: Base = a\n"
            "    return v\n"
            "\n"
            "def g(items: list[Base]):\n"
            "    return items\n"
        ),
    }, [python_recipe])

    assert Addr("lib.py", "Base") in dsts(engine, Addr("main.py", "f"), "types")
    assert Addr("lib.py", "Base") in dsts(engine, Addr("main.py", "g"), "types")


# === AC5: classes in argument position are reads ===

def test_class_passed_as_argument_yields_reads_edge(build, python_recipe):
    engine = build({
        "lib.py": "class Base:\n    pass\n",
        "main.py": (
            "from lib import Base\n"
            "\n"
            "def check(x):\n"
            "    return isinstance(x, Base)\n"
            "\n"
            "def check2(x):\n"
            "    return isinstance(x, (Base, dict))\n"
        ),
    }, [python_recipe])

    assert Addr("lib.py", "Base") in dsts(engine, Addr("main.py", "check"), "reads")
    assert Addr("lib.py", "Base") in dsts(engine, Addr("main.py", "check2"), "reads")


# === AC6: raise and except reference the exception class ===

def test_raise_and_except_yield_reads_edges_to_the_exception(build, python_recipe):
    engine = build({
        "lib.py": "class Boom(Exception):\n    pass\n",
        "main.py": (
            "from lib import Boom\n"
            "\n"
            "def fail():\n"
            "    raise Boom\n"
            "\n"
            "def guard():\n"
            "    try:\n"
            "        return 1\n"
            "    except Boom as e:\n"
            "        return e\n"
            "\n"
            "def guard2():\n"
            "    try:\n"
            "        return 1\n"
            "    except (Boom, ValueError):\n"
            "        return 2\n"
        ),
    }, [python_recipe])

    boom = Addr("lib.py", "Boom")
    assert boom in dsts(engine, Addr("main.py", "fail"), "reads")
    assert boom in dsts(engine, Addr("main.py", "guard"), "reads")
    assert boom in dsts(engine, Addr("main.py", "guard2"), "reads")


# === AC7: class attributes are state; self.attr resolves to them ===

def test_class_attributes_are_state_and_self_access_resolves(build, python_recipe):
    engine = build({"k.py": (
        "class K:\n"
        "    attr = 1\n"
        "\n"
        "    def get(self):\n"
        "        return self.attr\n"
        "\n"
        "    def put(self):\n"
        "        self.attr = 2\n"
    )}, [python_recipe])

    attr = Addr("k.py", "K.attr")
    kinds = {e.addr: e.kind for e in engine.entities("k.py")}
    assert kinds.get(attr) == "state"
    assert attr in dsts(engine, Addr("k.py", "K.get"), "reads")
    assert attr in dsts(engine, Addr("k.py", "K.put"), "writes")


# === AC8: pattern and chained assignments — one state entity per name ===

def test_pattern_and_chained_assignments_yield_one_entity_per_name(build, python_recipe):
    engine = build({"s.py": "a, b = 1, 2\nx = y = 3\n"}, [python_recipe])

    states = {e.addr.name for e in engine.entities("s.py") if e.kind == "state"}

    assert states == {"a", "b", "x", "y"}


# === AC9 (TS): new Foo() is a calls edge ===

def test_new_expression_yields_calls_edge_to_the_class(build, ts_recipe):
    engine = build({
        "util.ts": "export class TBase { }\n",
        "main.ts": (
            'import { TBase } from "./util"\n'
            "export function makes(): TBase { return new TBase() }\n"
        ),
    }, [ts_recipe])

    assert Addr("util.ts", "TBase") in dsts(engine, Addr("main.ts", "makes"), "calls")


# === AC10 (TS): implements edge; interfaces/aliases/enums are entities ===

def test_implements_edge_and_type_entities_exist(build, ts_recipe):
    engine = build({
        "util.ts": (
            "export interface Shape { area(): number }\n"
            "export type Alias = number\n"
            "export enum Color { Red, Green }\n"
        ),
        "main.ts": (
            'import { Shape } from "./util"\n'
            "export class Impl implements Shape { area(): number { return 1 } }\n"
        ),
    }, [ts_recipe])

    kinds = {e.addr.name: e.kind for e in engine.entities("util.ts")}
    assert kinds.get("Shape") == "interface"
    assert kinds.get("Alias") == "type"
    assert kinds.get("Color") == "enum"
    assert kinds.get("Color.Red") == "state"
    assert Addr("util.ts", "Shape") in dsts(engine, Addr("main.ts", "Impl"), "implements")


# === AC11 (TS): abstract classes and generators; methods keep qualnames ===

def test_abstract_class_and_generator_entities_with_qualified_methods(build, ts_recipe):
    engine = build({"probe.ts": (
        "export abstract class Abs { run(): number { return this.step() }\n"
        "  step(): number { return 1 } }\n"
        "export function* gen() { yield 1 }\n"
    )}, [ts_recipe])

    kinds = {e.addr.name: e.kind for e in engine.entities("probe.ts")}

    assert kinds.get("Abs") == "class"
    assert kinds.get("Abs.run") == "method"
    assert kinds.get("Abs.step") == "method"
    assert kinds.get("gen") == "function"


# === AC12 (TS): top-level state entities; identifier and ns.member reads ===

def test_ts_top_level_state_and_reads_resolve(build, ts_recipe):
    engine = build({
        "util.ts": "export const VALUE = 42\nlet local = 1\nvar old = 2\n",
        "main.ts": (
            'import { VALUE } from "./util"\n'
            'import * as ns from "./util"\n'
            "export function readsPlain(): number { return VALUE }\n"
            "export function readsNs(): number { return ns.VALUE }\n"
        ),
    }, [ts_recipe])

    kinds = {e.addr.name: e.kind for e in engine.entities("util.ts")}
    value = Addr("util.ts", "VALUE")
    assert kinds.get("VALUE") == "state"
    assert kinds.get("local") == "state"
    assert kinds.get("old") == "state"
    assert value in dsts(engine, Addr("main.ts", "readsPlain"), "reads")
    assert value in dsts(engine, Addr("main.ts", "readsNs"), "reads")


# === AC13 (TS): namespace and side-effect imports ===

def test_namespace_and_side_effect_imports_load(build, ts_recipe):
    engine = build({
        "util.ts": "export function util(): number { return 1 }\n",
        "side.ts": "export const side = 1\n",
        "main.ts": (
            'import * as ns from "./util"\n'
            'import "./side"\n'
            "export function callNs(): number { return ns.util() }\n"
        ),
    }, [ts_recipe])

    assert Addr("util.ts", "util") in dsts(engine, Addr("main.ts", "callNs"), "calls")
    assert Addr("side.ts", "<module>") in dsts(engine, Addr("main.ts", "<module>"), "imports")


# === AC14 (TS): export-from and export-star chase to the definition ===

def test_reexports_chase_to_the_definition(build, ts_recipe):
    engine = build({
        "util.ts": "export function util(): number { return 1 }\n",
        "more.ts": "export function extra(): number { return 2 }\n",
        "barrel.ts": 'export { util } from "./util"\nexport * from "./more"\n',
        "main.ts": (
            'import { util, extra } from "./barrel"\n'
            "export function a(): number { return util() }\n"
            "export function b(): number { return extra() }\n"
        ),
    }, [ts_recipe])

    assert Addr("util.ts", "util") in dsts(engine, Addr("main.ts", "a"), "calls")
    assert Addr("more.ts", "extra") in dsts(engine, Addr("main.ts", "b"), "calls")


# === AC15 (TS): this.method() resolves within the class ===

def test_this_method_call_resolves_to_class_method(build, ts_recipe):
    engine = build({"probe.ts": (
        "export abstract class Abs { run(): number { return this.step() }\n"
        "  step(): number { return 1 } }\n"
    )}, [ts_recipe])

    assert Addr("probe.ts", "Abs.step") in dsts(engine, Addr("probe.ts", "Abs.run"), "calls")


# === AC16 (TS): type annotations yield types edges ===

def test_ts_type_annotations_yield_types_edges(build, ts_recipe):
    engine = build({
        "util.ts": "export interface Shape { area(): number }\n",
        "main.ts": (
            'import { Shape } from "./util"\n'
            "export function area(s: Shape): number { return 0 }\n"
        ),
    }, [ts_recipe])

    assert Addr("util.ts", "Shape") in dsts(engine, Addr("main.ts", "area"), "types")


# === AC17 (TSX): .tsx loads; JSX elements call their component ===

def test_tsx_files_load_and_jsx_elements_call_the_component(build):
    tsx_recipe = load_recipe(RECIPES_DIR / "tsx.yml")
    engine = build({"app.tsx": (
        "export function Widget() { return <div /> }\n"
        "export function Page() { return <Widget /> }\n"
    )}, [tsx_recipe])

    assert Addr("app.tsx", "Widget") in dsts(engine, Addr("app.tsx", "Page"), "calls")


# === AC18: radius counts writes, implements, and types dependents ===

def test_radius_counts_new_impact_kinds(build, python_recipe, ts_recipe):
    py = build({"s.py": (
        "counter = 0\n"
        "class Base:\n"
        "    pass\n"
        "\n"
        "def bump():\n"
        "    global counter\n"
        "    counter = 1\n"
        "\n"
        "def annotated(x: Base):\n"
        "    return x\n"
    )}, [python_recipe])

    assert Addr("s.py", "bump") in six.compute_radius(py, Addr("s.py", "counter"))
    assert Addr("s.py", "annotated") in six.compute_radius(py, Addr("s.py", "Base"))


def test_radius_counts_implements_dependents(build, ts_recipe):
    ts = build({
        "util.ts": "export interface Shape { area(): number }\n",
        "main.ts": (
            'import { Shape } from "./util"\n'
            "export class Impl implements Shape { area(): number { return 1 } }\n"
        ),
    }, [ts_recipe])

    assert Addr("main.ts", "Impl") in six.compute_radius(ts, Addr("util.ts", "Shape"))


# === AC19: one name node = one entity, no ghosts ===

def test_overlapping_entity_rules_mint_one_entity_per_name_node(build, ts_recipe):
    engine = build({"v.ts": (
        "export const VALUE = 42\n"
        "export const arrow = () => 2\n"
    )}, [ts_recipe])

    entities = [e for e in engine.entities("v.ts") if e.addr.name != "<module>"]
    by_name = {e.addr.name: e.kind for e in entities}

    assert len(entities) == len(by_name)          # no duplicate names
    assert by_name == {"VALUE": "state", "arrow": "function"}


# === Traceability ===
# AC1:  test_star_import_yields_edge_and_resolves_bare_calls
# AC2:  test_decorators_yield_calls_edges_from_the_decorated_entity
# AC3:  test_attribute_state_reads_and_writes_resolve_cross_module
# AC4:  test_annotations_yield_types_edges_to_in_repo_classes
# AC5:  test_class_passed_as_argument_yields_reads_edge
# AC6:  test_raise_and_except_yield_reads_edges_to_the_exception
# AC7:  test_class_attributes_are_state_and_self_access_resolves
# AC8:  test_pattern_and_chained_assignments_yield_one_entity_per_name
# AC9:  test_new_expression_yields_calls_edge_to_the_class
# AC10: test_implements_edge_and_type_entities_exist
# AC11: test_abstract_class_and_generator_entities_with_qualified_methods
# AC12: test_ts_top_level_state_and_reads_resolve
# AC13: test_namespace_and_side_effect_imports_load
# AC14: test_reexports_chase_to_the_definition
# AC15: test_this_method_call_resolves_to_class_method
# AC16: test_ts_type_annotations_yield_types_edges
# AC17: test_tsx_files_load_and_jsx_elements_call_the_component
# AC18: test_radius_counts_new_impact_kinds, test_radius_counts_implements_dependents
# AC19: test_overlapping_entity_rules_mint_one_entity_per_name_node
