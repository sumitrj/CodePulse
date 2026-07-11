"""
Tests for the recipe system. Maps 1:1 to acceptance criteria in specs/recipes/SPEC.md.
Red until the engine and recipes/python.yml exist.
"""
from pathlib import Path

import pytest

from codepulse.recipes import (  # ImportError until implementation — expected
    CheckReport,
    Recipe,
    RecipeError,
    check_recipe,
    load_recipe,
)
from codepulse.engine import (  # ImportError until implementation — expected
    Addr,
    Engine,
    External,
)


ROOT = Path(__file__).resolve().parents[1]
PYTHON_RECIPE_PATH = ROOT / "recipes" / "python.yml"

TOY_RECIPE = """\
name: yaml-keys
version: "1"
language: yaml
matches: ["*.yml", "*.yaml"]
entities:
  - kind: key
    query: "(block_mapping_pair key: (flow_node) @name)"
"""

BILLING = "def total(items):\n    return sum(items)\n"


def single(items):
    items = list(items)
    assert len(items) == 1, f"expected exactly one item, got {items}"
    return items[0]


@pytest.fixture
def python_recipe():
    return load_recipe(PYTHON_RECIPE_PATH)


@pytest.fixture
def build(tmp_path, python_recipe):
    """Write files under a repo root, run a full apply, return the engine."""
    def _build(files, recipes=None):
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        for rel, src in files.items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(src)
        engine = Engine(tmp_path / "pulse.db", recipes or [python_recipe], root=repo)
        engine.apply()
        return engine, repo
    return _build


# === AC1: loading a valid recipe returns a Recipe with name, version, matches ===

def test_load_valid_recipe_returns_name_version_matches(tmp_path):
    path = tmp_path / "toy.yml"
    path.write_text(TOY_RECIPE)

    recipe = load_recipe(path)

    assert (recipe.name, recipe.version) == ("yaml-keys", "1")
    assert "*.yml" in recipe.matches


# === AC2: a recipe missing a required field raises RecipeError naming it ===

def test_load_recipe_missing_name_raises_error_naming_the_field(tmp_path):
    path = tmp_path / "broken.yml"
    path.write_text("version: \"1\"\nlanguage: yaml\nmatches: [\"*.yml\"]\n")

    with pytest.raises(RecipeError, match="name"):
        load_recipe(path)


# === AC3: a malformed tree-sitter query raises RecipeError naming the query ===

def test_load_recipe_with_malformed_query_raises_error_naming_the_query(tmp_path):
    path = tmp_path / "badquery.yml"
    path.write_text(
        "name: bad\nversion: \"1\"\nlanguage: yaml\nmatches: [\"*.yml\"]\n"
        "entities:\n  - kind: key\n    query: \"(((\"\n"
    )

    with pytest.raises(RecipeError, match=r"\(\(\("):
        load_recipe(path)


# === AC4: same-file call yields a calls edge with correct src, dst, line ===

def test_same_file_call_produces_a_calls_edge_with_line(build):
    engine, _ = build({"app.py": (
        "def helper():\n"
        "    return 1\n"
        "\n\n"
        "def main():\n"
        "    return helper()\n"
    )})

    edge = single(engine.outgoing(Addr("app.py", "main"), kind="calls"))

    assert edge.dst == Addr("app.py", "helper")
    assert edge.line == 6


# === AC5: cross-file calls resolve through all four import forms ===

@pytest.mark.parametrize("orders_src", [
    "from billing import total\n\ndef process(items):\n    return total(items)\n",
    "import billing\n\ndef process(items):\n    return billing.total(items)\n",
    "import billing as b\n\ndef process(items):\n    return b.total(items)\n",
    "from billing import total as t\n\ndef process(items):\n    return t(items)\n",
], ids=["from-import", "plain-import", "module-alias", "name-alias"])
def test_cross_file_calls_resolve_through_import_forms(build, orders_src):
    engine, _ = build({"billing.py": BILLING, "orders.py": orders_src})

    edge = single(engine.outgoing(Addr("orders.py", "process"), kind="calls"))

    assert edge.dst == Addr("billing.py", "total")


# === AC6: self.method() resolves to Class.method; instantiation calls the class ===

def test_self_method_call_resolves_to_class_method(build):
    engine, _ = build({"cart.py": (
        "class Cart:\n"
        "    def add(self):\n"
        "        return 1\n"
        "    def checkout(self):\n"
        "        return self.add()\n"
    )})

    edge = single(engine.outgoing(Addr("cart.py", "Cart.checkout"), kind="calls"))

    assert edge.dst == Addr("cart.py", "Cart.add")


def test_instantiating_a_class_yields_a_calls_edge_to_the_class(build):
    engine, _ = build({"cart.py": (
        "class Cart:\n"
        "    pass\n"
        "\n\n"
        "def make():\n"
        "    return Cart()\n"
    )})

    edge = single(engine.outgoing(Addr("cart.py", "make"), kind="calls"))

    assert edge.dst == Addr("cart.py", "Cart")


# === AC7: an unresolvable call target becomes External with the name as written ===

def test_unresolvable_call_is_kept_as_external_with_dotted_name(build):
    engine, _ = build({"app.py": (
        "import json\n"
        "\n\n"
        "def load(raw):\n"
        "    return json.loads(raw)\n"
    )})

    edge = single(engine.outgoing(Addr("app.py", "load"), kind="calls"))

    assert edge.dst == External("json.loads")


# === AC8: imports yield imports edges from module scope ===

def test_in_repo_import_yields_imports_edge_to_resolved_addr(build):
    engine, _ = build({
        "billing.py": BILLING,
        "orders.py": "from billing import total\n",
    })

    edge = single(engine.outgoing(Addr("orders.py", "<module>"), kind="imports"))

    assert edge.dst == Addr("billing.py", "total")


def test_external_import_yields_imports_edge_to_external(build):
    engine, _ = build({"app.py": "import json\n"})

    edge = single(engine.outgoing(Addr("app.py", "<module>"), kind="imports"))

    assert edge.dst == External("json")


# === AC9: module-state reads and global writes yield reads/writes edges ===

def test_module_state_read_and_global_write_yield_edges(build):
    engine, _ = build({"state.py": (
        "counter = 0\n"
        "\n\n"
        "def peek():\n"
        "    return counter\n"
        "\n\n"
        "def bump():\n"
        "    global counter\n"
        "    counter = counter + 1\n"
    )})

    read = single(engine.outgoing(Addr("state.py", "peek"), kind="reads"))
    writes = engine.outgoing(Addr("state.py", "bump"), kind="writes")

    assert read.dst == Addr("state.py", "counter")
    assert Addr("state.py", "counter") in [e.dst for e in writes]


# === AC10: inheritance yields an inherits edge cross-file ===

def test_cross_file_inheritance_yields_inherits_edge(build):
    engine, _ = build({
        "base.py": "class Base:\n    pass\n",
        "impl.py": "from base import Base\n\n\nclass Impl(Base):\n    pass\n",
    })

    edge = single(engine.outgoing(Addr("impl.py", "Impl"), kind="inherits"))

    assert edge.dst == Addr("base.py", "Base")


# === AC11: module contains its top-level units; a class contains its methods ===

def test_containment_edges_for_module_and_class(build):
    engine, _ = build({"cart.py": (
        "class Cart:\n"
        "    def add(self):\n"
        "        return 1\n"
        "\n\n"
        "def make():\n"
        "    return Cart()\n"
    )})

    module_children = [e.dst for e in engine.outgoing(Addr("cart.py", "<module>"), kind="contains")]
    class_children = [e.dst for e in engine.outgoing(Addr("cart.py", "Cart"), kind="contains")]

    assert Addr("cart.py", "Cart") in module_children
    assert Addr("cart.py", "make") in module_children
    assert Addr("cart.py", "Cart.add") in class_children


# === AC12: every entity and edge carries recipe name and version ===

def test_rows_carry_recipe_provenance(build, python_recipe):
    engine, _ = build({"billing.py": BILLING})

    entity = single(e for e in engine.entities("billing.py") if e.addr.name == "total")
    edge = single(engine.outgoing(Addr("billing.py", "<module>"), kind="contains"))

    assert (entity.recipe, entity.recipe_version) == (python_recipe.name, python_recipe.version)
    assert (edge.recipe, edge.recipe_version) == (python_recipe.name, python_recipe.version)


# === AC13: update() re-extracts only changed files and replaces stale edges ===

def test_update_touches_only_changed_files_and_drops_stale_edges(build):
    engine, repo = build({
        "billing.py": BILLING,
        "orders.py": "from billing import total\n\ndef process(items):\n    return total(items)\n",
    })
    (repo / "orders.py").write_text("def process(items):\n    return len(items)\n")

    report = engine.update([repo / "orders.py"])

    assert list(report.extracted) == ["orders.py"]
    assert engine.incoming(Addr("billing.py", "total"), kind="calls") == []
    assert [e.addr.name for e in engine.entities("billing.py")].count("total") == 1


# === AC14: extraction is deterministic across runs ===

def test_two_engines_over_the_same_tree_answer_identically(tmp_path, python_recipe):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "billing.py").write_text(BILLING)
    (repo / "orders.py").write_text(
        "from billing import total\n\ndef process(items):\n    return total(items)\n"
    )
    first = Engine(tmp_path / "a.db", [python_recipe], root=repo)
    second = Engine(tmp_path / "b.db", [python_recipe], root=repo)

    first.apply()
    second.apply()

    assert sorted(first.entities(), key=repr) == sorted(second.entities(), key=repr)
    assert sorted(first.outgoing(Addr("orders.py", "process")), key=repr) == \
           sorted(second.outgoing(Addr("orders.py", "process")), key=repr)


# === AC15: a second-language recipe works with zero engine changes ===

def test_yaml_keys_recipe_extracts_entities_through_the_same_engine(tmp_path):
    recipe_path = tmp_path / "toy.yml"
    recipe_path.write_text(TOY_RECIPE)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.yml").write_text("server:\n  port: 7317\ndebug: false\n")
    engine = Engine(tmp_path / "pulse.db", [load_recipe(recipe_path)], root=repo)

    engine.apply()

    names = [e.addr.name for e in engine.entities("config.yml")]
    assert "server" in names
    assert "debug" in names


# === AC16: check_recipe passes a conforming fixture and names unmet expectations ===

def _write_fixture(root: Path, expected: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "billing.py").write_text(BILLING)
    (root / "orders.py").write_text(
        "from billing import total\n\ndef process(items):\n    return total(items)\n"
    )
    (root / "expected.yml").write_text(expected)
    return root


def test_check_recipe_passes_on_conforming_fixture(tmp_path, python_recipe):
    fixture = _write_fixture(tmp_path / "fix", (
        "- src: orders.py:process\n"
        "  kind: calls\n"
        "  dst: billing.py:total\n"
    ))

    report = check_recipe(python_recipe, fixture)

    assert report.ok
    assert report.failures == ()


def test_check_recipe_fails_and_names_the_missing_edge(tmp_path, python_recipe):
    fixture = _write_fixture(tmp_path / "fix", (
        "- src: orders.py:process\n"
        "  kind: calls\n"
        "  dst: billing.py:refund\n"
    ))

    report = check_recipe(python_recipe, fixture)

    assert not report.ok
    assert any("billing.py:refund" in failure for failure in report.failures)


# === Traceability ===
# AC1:  test_load_valid_recipe_returns_name_version_matches
# AC2:  test_load_recipe_missing_name_raises_error_naming_the_field
# AC3:  test_load_recipe_with_malformed_query_raises_error_naming_the_query
# AC4:  test_same_file_call_produces_a_calls_edge_with_line
# AC5:  test_cross_file_calls_resolve_through_import_forms (x4 forms)
# AC6:  test_self_method_call_resolves_to_class_method,
#       test_instantiating_a_class_yields_a_calls_edge_to_the_class
# AC7:  test_unresolvable_call_is_kept_as_external_with_dotted_name
# AC8:  test_in_repo_import_yields_imports_edge_to_resolved_addr,
#       test_external_import_yields_imports_edge_to_external
# AC9:  test_module_state_read_and_global_write_yield_edges
# AC10: test_cross_file_inheritance_yields_inherits_edge
# AC11: test_containment_edges_for_module_and_class
# AC12: test_rows_carry_recipe_provenance
# AC13: test_update_touches_only_changed_files_and_drops_stale_edges
# AC14: test_two_engines_over_the_same_tree_answer_identically
# AC15: test_yaml_keys_recipe_extracts_entities_through_the_same_engine
# AC16: test_check_recipe_passes_on_conforming_fixture,
#       test_check_recipe_fails_and_names_the_missing_edge
