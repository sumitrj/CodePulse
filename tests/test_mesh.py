"""
Tests for the multi-language mesh. Maps 1:1 to acceptance criteria in
specs/mesh/SPEC.md. Red until the five recipes and the path/name
resolution strategies exist.
"""
from pathlib import Path

import pytest

from codepulse.recipes import builtin_recipes, check_recipe, load_recipe
from codepulse.engine import Addr, Engine
from codepulse import six


ROOT = Path(__file__).resolve().parents[1]

MESH_FILES = {
    "app.py": (
        "import os\n"
        "\n\n"
        "def connect():\n"
        "    return os.environ[\"DB_URL\"]\n"
    ),
    "infra.tf": (
        "variable \"DB_URL\" {\n"
        "  type = string\n"
        "}\n"
    ),
    "Dockerfile": (
        "FROM python:3.11\n"
        "COPY app.py /app/app.py\n"
    ),
    "config.yml": (
        "server:\n"
        "  port: 7317\n"
        "debug: false\n"
    ),
    "web/index.html": (
        "<html><body>\n"
        "<script src=\"./main.ts\"></script>\n"
        "</body></html>\n"
    ),
    "web/main.ts": (
        "import { helper } from \"./util\"\n"
        "\n"
        "export function boot() {\n"
        "  return helper()\n"
        "}\n"
    ),
    "web/util.ts": (
        "export function helper() {\n"
        "  return 1\n"
        "}\n"
    ),
}


@pytest.fixture(scope="module")
def mesh(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mesh")
    repo = tmp / "repo"
    for rel, src in MESH_FILES.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src)
    engine = Engine(tmp / "pulse.db", builtin_recipes(), root=repo)
    engine.apply()
    return engine


# === AC1: every shipped recipe passes its fixture check ===

@pytest.mark.parametrize("recipe_path", sorted((ROOT / "recipes").glob("*.yml")),
                         ids=lambda p: p.stem)
def test_recipe_passes_its_fixture(recipe_path):
    recipe = load_recipe(recipe_path)
    fixture = ROOT / "fixtures" / recipe.name

    assert fixture.is_dir(), f"no fixture repo at fixtures/{recipe.name}"
    report = check_recipe(recipe, fixture)

    assert report.ok, "\n".join(report.failures)


# === AC2: nested YAML keys become dotted entities ===

def test_yaml_nested_keys_get_dotted_names(mesh):
    names = {e.addr.name for e in mesh.entities("config.yml")}

    assert "server.port" in names
    assert "debug" in names


# === AC3: Dockerfile COPY yields a copies edge to the file ===

def test_dockerfile_copy_yields_edge_to_copied_file(mesh):
    dsts = {e.dst for e in mesh.outgoing(Addr("Dockerfile", "<module>"), kind="copies")}

    assert Addr("app.py", "<module>") in dsts


# === AC4: TS cross-file call resolves through a ./ import ===

def test_typescript_cross_file_call_resolves(mesh):
    edges = mesh.outgoing(Addr("web/main.ts", "boot"), kind="calls")

    assert Addr("web/util.ts", "helper") in {e.dst for e in edges}


# === AC5: a Terraform variable is an addressable entity ===

def test_terraform_variable_is_an_entity(mesh):
    names = {e.addr.name for e in mesh.entities("infra.tf")}

    assert "DB_URL" in names


# === AC6: HTML script src yields a loads edge ===

def test_html_script_src_yields_loads_edge(mesh):
    dsts = {e.dst for e in mesh.outgoing(Addr("web/index.html", "<module>"), kind="loads")}

    assert Addr("web/main.ts", "<module>") in dsts


# === AC7: python os.environ read reaches the terraform variable ===

def test_python_env_read_reaches_terraform_variable(mesh):
    edges = mesh.outgoing(Addr("app.py", "connect"), kind="reads")

    assert Addr("infra.tf", "DB_URL") in {e.dst for e in edges}


# === AC8: verbs cross languages ===

def test_radius_crosses_typescript_files(mesh):
    out = six.radius(mesh, "helper")

    assert "web/main.ts :: boot" in out


def test_who_touches_shows_python_reader_of_terraform_variable(mesh):
    out = six.who_touches(mesh, "DB_URL")

    assert "app.py :: connect" in out


# === AC9: a dangling path reference is dropped, never invented ===

def test_dangling_path_reference_is_dropped(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Dockerfile").write_text("FROM alpine\nCOPY missing.py /app/\n")
    engine = Engine(tmp_path / "pulse.db", builtin_recipes(), root=repo)

    engine.apply()

    edges = engine.outgoing(Addr("Dockerfile", "<module>"), kind="copies")
    assert edges == []


# === Traceability ===
# AC1: test_recipe_passes_its_fixture (x6 recipes)
# AC2: test_yaml_nested_keys_get_dotted_names
# AC3: test_dockerfile_copy_yields_edge_to_copied_file
# AC4: test_typescript_cross_file_call_resolves
# AC5: test_terraform_variable_is_an_entity
# AC6: test_html_script_src_yields_loads_edge
# AC7: test_python_env_read_reaches_terraform_variable
# AC8: test_radius_crosses_typescript_files,
#      test_who_touches_shows_python_reader_of_terraform_variable
# AC9: test_dangling_path_reference_is_dropped
