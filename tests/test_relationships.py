"""
Tests for the CodePulse relationship extraction pipeline.
Maps 1:1 to acceptance criteria in specs/relationships/SPEC.md.
"""
import pytest

from codepulse.relationships import (  # ImportError until Phase 3 — expected
    ExternalRef,
    StateRef,
    UnitRef,
    extract_edges,
)


BILLING = "def total(items):\n    return sum(items)\n"


def single(items):
    items = list(items)
    assert len(items) == 1
    return items[0]


# === AC1: a same-file call yields a calls edge with correct src, dst, and line ===

def test_same_file_call_produces_a_call_edge_with_line():
    files = {"app.py": (
        "def helper():\n"
        "    return 1\n"
        "\n\n"
        "def main():\n"
        "    return helper()\n"
    )}

    graph = extract_edges(files)

    edge = single(graph.outgoing(UnitRef("app.py", "main"), kind="calls"))
    assert edge.dst == UnitRef("app.py", "helper")
    assert edge.line == 6


# === AC2: cross-file calls resolve through all four import forms ===

@pytest.mark.parametrize("orders_src", [
    "from billing import total\n\ndef process(items):\n    return total(items)\n",
    "import billing\n\ndef process(items):\n    return billing.total(items)\n",
    "import billing as b\n\ndef process(items):\n    return b.total(items)\n",
    "from billing import total as t\n\ndef process(items):\n    return t(items)\n",
], ids=["from-import", "plain-import", "module-alias", "name-alias"])
def test_cross_file_calls_resolve_through_import_forms(orders_src):
    files = {"billing.py": BILLING, "orders.py": orders_src}

    graph = extract_edges(files)

    edge = single(graph.outgoing(UnitRef("orders.py", "process"), kind="calls"))
    assert edge.dst == UnitRef("billing.py", "total")


# === AC3: self.method() resolves to Class.method; instantiation calls the class ===

def test_self_method_call_and_instantiation_resolve_to_class_units():
    files = {"cart.py": (
        "class Cart:\n"
        "    def subtotal(self):\n"
        "        return 10\n"
        "\n"
        "    def checkout(self):\n"
        "        return self.subtotal()\n"
        "\n\n"
        "def new_cart():\n"
        "    return Cart()\n"
    )}

    graph = extract_edges(files)

    method_call = single(graph.outgoing(UnitRef("cart.py", "Cart.checkout"), kind="calls"))
    assert method_call.dst == UnitRef("cart.py", "Cart.subtotal")
    ctor_call = single(graph.outgoing(UnitRef("cart.py", "new_cart"), kind="calls"))
    assert ctor_call.dst == UnitRef("cart.py", "Cart")


# === AC4: unresolvable call targets are kept as ExternalRef, dotted name as written ===

def test_unresolved_call_targets_are_kept_as_external_refs():
    files = {"app.py": (
        "import json\n"
        "\n\n"
        "def load(raw):\n"
        "    return json.loads(raw)\n"
    )}

    graph = extract_edges(files)

    edge = single(graph.outgoing(UnitRef("app.py", "load"), kind="calls"))
    assert edge.dst == ExternalRef("json.loads")


# === AC5: import statements yield imports edges from module scope ===

def test_import_statements_yield_import_edges_from_module_scope():
    files = {
        "billing.py": BILLING,
        "orders.py": "from billing import total\nimport json\n",
    }

    graph = extract_edges(files)

    dsts = {e.dst for e in graph.outgoing(UnitRef("orders.py", "<module>"), kind="imports")}
    assert dsts == {UnitRef("billing.py", "total"), ExternalRef("json")}


# === AC6: reading module-level state yields reads edges, same-file and cross-file ===

def test_reading_module_level_state_yields_reads_edges():
    files = {
        "config.py": "TAX_RATE = 0.2\n",
        "billing.py": (
            "from config import TAX_RATE\n"
            "\n"
            "MARGIN = 0.1\n"
            "\n\n"
            "def price(base):\n"
            "    return base * (1 + TAX_RATE + MARGIN)\n"
        ),
    }

    graph = extract_edges(files)

    reads = {e.dst for e in graph.outgoing(UnitRef("billing.py", "price"), kind="reads")}
    assert reads == {StateRef("config.py", "TAX_RATE"), StateRef("billing.py", "MARGIN")}


# === AC7: writing module-level state via global yields a writes edge ===

def test_writing_module_level_state_via_global_yields_a_writes_edge():
    files = {"metrics.py": (
        "counter = 0\n"
        "\n\n"
        "def bump():\n"
        "    global counter\n"
        "    counter += 1\n"
    )}

    graph = extract_edges(files)

    edge = single(graph.outgoing(UnitRef("metrics.py", "bump"), kind="writes"))
    assert edge.dst == StateRef("metrics.py", "counter")


# === AC8: inheritance yields inherits edges, same-file and cross-file ===

@pytest.mark.parametrize("files, expected_base", [
    (
        {"shapes.py": "class Shape:\n    pass\n\n\nclass Circle(Shape):\n    pass\n"},
        UnitRef("shapes.py", "Shape"),
    ),
    (
        {
            "base.py": "class Shape:\n    pass\n",
            "shapes.py": "from base import Shape\n\n\nclass Circle(Shape):\n    pass\n",
        },
        UnitRef("base.py", "Shape"),
    ),
], ids=["same-file", "cross-file"])
def test_inheritance_yields_inherits_edges(files, expected_base):
    graph = extract_edges(files)

    edge = single(graph.outgoing(UnitRef("shapes.py", "Circle"), kind="inherits"))
    assert edge.dst == expected_base


# === AC9: module scope contains top-level units; a class contains its methods ===

def test_containment_edges_link_module_to_units_and_class_to_methods():
    files = {"cart.py": (
        "class Cart:\n"
        "    def checkout(self):\n"
        "        return 0\n"
        "\n\n"
        "def new_cart():\n"
        "    return Cart()\n"
    )}

    graph = extract_edges(files)

    module_children = {e.dst for e in graph.outgoing(UnitRef("cart.py", "<module>"), kind="contains")}
    assert module_children == {UnitRef("cart.py", "Cart"), UnitRef("cart.py", "new_cart")}
    class_children = {e.dst for e in graph.outgoing(UnitRef("cart.py", "Cart"), kind="contains")}
    assert class_children == {UnitRef("cart.py", "Cart.checkout")}


# === AC10: incoming/outgoing return exactly the touching edges, filterable by kind ===

def test_incoming_returns_exactly_the_edges_targeting_a_ref():
    files = {
        "billing.py": BILLING,
        "a.py": "from billing import total\n\n\ndef pa(x):\n    return total(x)\n",
        "b.py": "from billing import total\n\n\ndef pb(x):\n    return total(x)\n",
    }

    graph = extract_edges(files)

    callers = {e.src for e in graph.incoming(UnitRef("billing.py", "total"), kind="calls")}
    assert callers == {UnitRef("a.py", "pa"), UnitRef("b.py", "pb")}
    all_kinds = {e.kind for e in graph.incoming(UnitRef("billing.py", "total"))}
    assert all_kinds == {"calls", "imports", "contains"}


# === AC11: extraction is deterministic ===

def test_extraction_is_deterministic_across_runs():
    files = {
        "billing.py": BILLING,
        "orders.py": "from billing import total\n\n\ndef process(items):\n    return total(items)\n",
    }

    first = extract_edges(files)
    second = extract_edges(files)

    assert first == second


# === Traceability ===
# AC1:  test_same_file_call_produces_a_call_edge_with_line
# AC2:  test_cross_file_calls_resolve_through_import_forms (x4 variants)
# AC3:  test_self_method_call_and_instantiation_resolve_to_class_units
# AC4:  test_unresolved_call_targets_are_kept_as_external_refs
# AC5:  test_import_statements_yield_import_edges_from_module_scope
# AC6:  test_reading_module_level_state_yields_reads_edges
# AC7:  test_writing_module_level_state_via_global_yields_a_writes_edge
# AC8:  test_inheritance_yields_inherits_edges (x2 variants)
# AC9:  test_containment_edges_link_module_to_units_and_class_to_methods
# AC10: test_incoming_returns_exactly_the_edges_targeting_a_ref
# AC11: test_extraction_is_deterministic_across_runs
