"""Tests for the companion app's JSON views of the verbs."""
import pytest

from codepulse.app import radius_data, search_data, summary_data, unit_data
from codepulse.store import Store
from tests.test_pulse import REPO


@pytest.fixture
def store(tmp_path):
    for name, src in REPO.items():
        (tmp_path / name).write_text(src)
    built, _ = Store.build(tmp_path)
    built.save()
    return built


def test_summary_reports_counts_regions_and_load_bearing(store):
    data = summary_data(store)

    assert data["files"] == 3 and data["units"] == 2
    assert any(l["qualname"] == "total" for l in data["load_bearing"])


def test_search_ranks_the_priced_unit_first(store):
    data = search_data(store, "sum of prices")

    assert data["results"][0]["qualname"] == "total"


def test_unit_card_carries_contract_dependents_and_radius(store):
    data = unit_data(store, "billing.py", "total")

    assert data["params"] == ["items"]
    assert data["dependents"] == ["orders.py::process"]
    assert data["radius"] == 1


def test_radius_groups_units_by_hop(store):
    data = radius_data(store, "RATE")

    assert data["total"] == 2
    assert [h["depth"] for h in data["hops"]] == [1, 2]
