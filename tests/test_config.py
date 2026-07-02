"""Tests for the configurables: persistence, coercion, and that knobs are load-bearing."""
import pytest

from codepulse import config as cfg
from codepulse.store import Store


def test_defaults_load_when_no_file(tmp_path):
    c = cfg.load(tmp_path)

    assert c.get("gate", "max_radius") == 10
    assert c.exclude_dirs() >= {".git", "__pycache__"}


def test_save_coerces_and_clamps_values(tmp_path):
    cfg.save(tmp_path, {"gate": {"max_radius": "999", "require_tests": 0},
                        "hooks": {"injection_budget": 5}})
    c = cfg.load(tmp_path)

    assert c.get("gate", "max_radius") == 50          # clamped to slider max
    assert c.get("gate", "require_tests") is False    # coerced to bool
    assert c.get("hooks", "injection_budget") == 5


def test_save_ignores_unknown_keys(tmp_path):
    cfg.save(tmp_path, {"gate": {"max_radius": 7, "bogus": 1}, "nonsense": {"x": 1}})
    data = cfg.load(tmp_path).to_dict()

    assert data["gate"]["max_radius"] == 7
    assert "bogus" not in data["gate"]
    assert "nonsense" not in data


def test_gate_config_reflects_saved_values(tmp_path):
    cfg.save(tmp_path, {"gate": {"max_radius": 3, "min_confidence": 9}})

    gc = cfg.load(tmp_path).gate_config()

    assert gc.max_radius == 3 and gc.min_confidence == 9


def test_exclude_dirs_is_load_bearing_for_the_scanner(tmp_path):
    (tmp_path / "keep.py").write_text("def a():\n    return 1\n")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "lib.py").write_text("def b():\n    return 2\n")

    cfg.save(tmp_path, {"scope": {"exclude_dirs": ["vendor"]}})
    store, _ = Store.build(tmp_path)

    paths = {u.path for u in store.units.values()}
    assert "keep.py" in paths
    assert not any(p.startswith("vendor") for p in paths)
