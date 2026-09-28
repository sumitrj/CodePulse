"""
The README's example, run for real. Every answer the README shows for
examples/shop is asserted here, so the docs cannot drift from the product.
"""
from pathlib import Path

import pytest

from codepulse import six
from codepulse.engine import Engine
from codepulse.recipes import builtin_recipes

SHOP = Path(__file__).resolve().parents[1] / "examples" / "shop"


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    engine = Engine(tmp_path_factory.mktemp("map") / "pulse.db", builtin_recipes(), root=SHOP)
    engine.refresh()
    return engine


def test_the_map_counts_files_and_entities(shop):
    assert six.system_map(shop).startswith("System: 6 files, 8 entities")


def test_terraform_variable_blast_radius_reaches_python(shop):
    out = six.radius(shop, "DB_URL")
    assert out.startswith("Blast radius of infra.tf :: DB_URL: 3 entities at risk.")
    for hop, name in ((1, "app.py :: save_order"), (2, "app.py :: checkout"), (3, "app.py :: main")):
        assert f"[{hop} hop{'s' if hop > 1 else ''}] {name}" in out


def test_who_touches_the_env_var(shop):
    assert "app.py :: save_order" in six.who_touches(shop, "DB_URL")


def test_dockerfile_copy_and_script_tag_are_edges(shop):
    assert "Dockerfile :: <module>" in six.who_touches(shop, "app.py::<module>")
    assert "web/index.html :: <module>" in six.who_touches(shop, "web/cart.ts::<module>")


def test_entry_point_is_main(shop):
    from codepulse import boards
    assert "app.py :: main" in boards.handlers_text(shop)


def test_ambiguous_name_names_the_other_matches(tmp_path):
    (tmp_path / "a.py").write_text("def total():\n    return 1\n")
    (tmp_path / "b.py").write_text("def total():\n    return 2\n")
    engine = Engine(tmp_path / "pulse.db", builtin_recipes(), root=tmp_path)
    engine.refresh()
    for answer in (six.who_touches(engine, "total"), six.radius(engine, "total")):
        assert "Also named 'total': b.py::total" in answer
