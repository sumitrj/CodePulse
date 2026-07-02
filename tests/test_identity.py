"""
Tests for the CodePulse unit identity fingerprint.
Maps 1:1 to acceptance criteria in specs/identity/SPEC.md.
"""
import pytest

from codepulse.identity import extract_units, fingerprint, resolve  # ImportError until Phase 3 — expected


BILLING_V1 = (
    "def total(items):\n"
    '    """Sum of item prices."""\n'
    "    return sum(item.price for item in items)\n"
)


def units_of(files):
    return [u for path, src in sorted(files.items()) for u in extract_units(src, path)]


def initial(files):
    """Bootstrap a first snapshot: every unit gets a fresh id."""
    result = resolve({}, units_of(files))
    return {a.unit_id: a.unit for a in result.assignments}


def single(items):
    items = list(items)
    assert len(items) == 1
    return items[0]


# === AC1: extract_units yields one Unit per function/class/method with correct
#          name, kind, path, and signature ===

def test_extract_units_yields_functions_classes_and_methods_with_signatures():
    source = (
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "\n\n"
        "class Cart:\n"
        '    def checkout(self, coupon: str = "") -> float:\n'
        "        return 0.0\n"
    )

    units = extract_units(source, "shop.py")

    by_name = {u.name: u for u in units}
    assert set(by_name) == {"add", "Cart", "checkout"}
    assert by_name["add"].kind == "function"
    assert by_name["Cart"].kind == "class"
    assert by_name["checkout"].kind == "method"
    assert by_name["checkout"].qualname == "Cart.checkout"
    assert tuple(p.name for p in by_name["add"].signature.params) == ("a", "b")
    assert by_name["add"].signature.returns == "int"
    assert all(u.path == "shop.py" for u in units)


# === AC2: fingerprints are deterministic and formatting-insensitive ===

@pytest.mark.parametrize("variant", [
    (
        "def total(items):\n"
        '    """Sum of item prices."""\n'
        "    return sum(item.price for item in items)\n"
    ),
    (
        "def total(items):\n"
        "    # add up every line item\n"
        "    return sum(item.price for item in items)\n"
    ),
    (
        "def total(items):\n"
        "\n"
        '    """Adds the prices together."""\n'
        "    return sum(item.price for item in items)\n"
    ),
], ids=["identical-source", "comment-swapped-for-docstring", "docstring-reworded-blank-line"])
def test_formatting_and_comment_changes_leave_fingerprint_unchanged(variant):
    base_unit = single(extract_units(BILLING_V1, "billing.py"))
    variant_unit = single(extract_units(variant, "billing.py"))

    assert fingerprint(variant_unit) == fingerprint(base_unit)


# === AC3: an unchanged unit keeps its id as an exact match ===

def test_unchanged_unit_keeps_its_id_as_an_exact_match():
    old = initial({"billing.py": BILLING_V1})

    result = resolve(old, units_of({"billing.py": BILLING_V1}))

    a = single(result.assignments)
    assert a.unit_id == single(old)
    assert a.kind == "exact"
    assert a.confidence == 1.0
    assert result.removed == frozenset()


# === AC4: rename, move, or both with intact body keeps identity ===

_BODY = (
    '    """Sum of item prices."""\n'
    "    return sum(item.price for item in items)\n"
)

@pytest.mark.parametrize("new_files, expected_kind", [
    ({"billing.py": "def compute_total(items):\n" + _BODY}, "renamed"),
    ({"pricing.py": "def total(items):\n" + _BODY}, "moved"),
    ({"pricing.py": "def compute_total(items):\n" + _BODY}, "renamed+moved"),
], ids=["rename", "move", "rename-and-move"])
def test_rename_or_move_with_intact_body_keeps_identity(new_files, expected_kind):
    old = initial({"billing.py": BILLING_V1})

    result = resolve(old, units_of(new_files))

    a = single(result.assignments)
    assert a.unit_id == single(old)
    assert a.kind == expected_kind


# === AC5: moderate body refactor with intact contract keeps identity ===

def test_moderate_body_refactor_with_intact_contract_keeps_identity():
    v1 = (
        "def total(items):\n"
        "    result = 0\n"
        "    for item in items:\n"
        "        result += item.price\n"
        "    return result\n"
    )
    v2 = (
        "def total(items):\n"
        "    prices = [item.price for item in items]\n"
        "    return sum(prices)\n"
    )
    old = initial({"billing.py": v1})

    result = resolve(old, units_of({"billing.py": v2}))

    a = single(result.assignments)
    assert a.unit_id == single(old)
    assert a.kind == "refactored"
    assert 0.0 < a.confidence < 1.0


# === AC6: in-place contract evolution keeps identity ===

def test_contract_evolution_in_place_keeps_identity():
    v2 = (
        "def total(items, discount=0.0):\n"
        '    """Sum of item prices, minus a flat discount."""\n'
        "    return sum(item.price for item in items) - discount\n"
    )
    old = initial({"billing.py": BILLING_V1})

    result = resolve(old, units_of({"billing.py": v2}))

    a = single(result.assignments)
    assert a.unit_id == single(old)
    assert result.removed == frozenset()


# === AC7: a split keeps identity on the continuing unit; the extracted part is new ===

def test_split_keeps_identity_on_continuing_unit_and_mints_a_new_one():
    v1 = (
        "def process(order):\n"
        "    if not order.items:\n"
        '        raise ValueError("empty order")\n'
        '    order.status = "done"\n'
        "    db.write(order)\n"
        "    audit.log(order)\n"
    )
    v2 = (
        "def process(order):\n"
        "    if not order.items:\n"
        '        raise ValueError("empty order")\n'
        '    order.status = "done"\n'
        "    persist(order)\n"
        "\n\n"
        "def persist(order):\n"
        "    db.write(order)\n"
        "    audit.log(order)\n"
    )
    old = initial({"orders.py": v1})

    result = resolve(old, units_of({"orders.py": v2}))

    by_name = {a.unit.name: a for a in result.assignments}
    assert by_name["process"].unit_id == single(old)
    assert by_name["persist"].kind == "new"
    assert by_name["persist"].unit_id not in old


# === AC8: same-named units in different files follow their own bodies ===

def test_same_named_units_in_different_files_follow_their_own_bodies():
    v1 = {
        "alpha.py": "def run():\n    return 'alpha result'\n",
        "beta.py": "def run():\n    return 'beta result'\n",
    }
    v2 = {
        "jobs/alpha.py": "def run():\n    return 'alpha result'\n",
        "jobs/beta.py": "def run():\n    return 'beta result'\n",
    }
    old = initial(v1)
    alpha_id = single(uid for uid, u in old.items() if "alpha result" in u.body)

    result = resolve(old, units_of(v2))

    alpha_match = single(a for a in result.assignments if "alpha result" in a.unit.body)
    assert alpha_match.unit_id == alpha_id


# === AC9a: a brand-new unit gets a fresh id ===

def test_brand_new_unit_gets_a_fresh_id():
    v2 = {
        "billing.py": BILLING_V1 + "\n\ndef tax(amount, rate):\n    return amount * rate\n"
    }
    old = initial({"billing.py": BILLING_V1})

    result = resolve(old, units_of(v2))

    tax = single(a for a in result.assignments if a.unit.name == "tax")
    assert tax.kind == "new"
    assert tax.unit_id not in old
    assert result.removed == frozenset()


# === AC9b: a deleted unit's id is reported removed ===

def test_deleted_unit_is_reported_removed():
    old = initial({"billing.py": BILLING_V1})

    result = resolve(old, units_of({"billing.py": "TAX_RATE = 0.2\n"}))

    assert result.assignments == ()
    assert result.removed == frozenset(old)


# === AC10: resolution is a function — one assignment per unit, no id reused,
#           old ids conserved across matched and removed ===

def test_resolution_assigns_each_unit_once_and_conserves_old_ids():
    v1 = {
        "tasks.py": (
            "def ping():\n    return 'pong'\n"
            "\n\n"
            "def echo():\n    return 'pong'\n"
        )
    }
    v2 = {
        "tasks.py": (
            "def ping_v2():\n    return 'pong'\n"
            "\n\n"
            "def echo_v2():\n    return 'pong'\n"
        )
    }
    old = initial(v1)

    result = resolve(old, units_of(v2))

    assert len(result.assignments) == 2
    assigned = [a.unit_id for a in result.assignments]
    assert len(set(assigned)) == len(assigned)
    matched_old = {uid for uid in assigned if uid in old}
    assert matched_old | result.removed == set(old)
    assert matched_old & result.removed == set()


# === Traceability ===
# AC1:  test_extract_units_yields_functions_classes_and_methods_with_signatures
# AC2:  test_formatting_and_comment_changes_leave_fingerprint_unchanged (x3 variants)
# AC3:  test_unchanged_unit_keeps_its_id_as_an_exact_match
# AC4:  test_rename_or_move_with_intact_body_keeps_identity (x3 variants)
# AC5:  test_moderate_body_refactor_with_intact_contract_keeps_identity
# AC6:  test_contract_evolution_in_place_keeps_identity
# AC7:  test_split_keeps_identity_on_continuing_unit_and_mints_a_new_one
# AC8:  test_same_named_units_in_different_files_follow_their_own_bodies
# AC9:  test_brand_new_unit_gets_a_fresh_id, test_deleted_unit_is_reported_removed
# AC10: test_resolution_assigns_each_unit_once_and_conserves_old_ids
