"""
Tests for the codepulse command. Maps to specs/setup/SPEC.md (the CLI is the
one-noun form of setup.sh) plus the .codepulse/ignore scoping rule.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from codepulse import main as cli
from codepulse.engine import Engine
from codepulse.recipes import builtin_recipes, load_recipe, RECIPES_DIR

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo(tmp_path):
    target = tmp_path / "repo"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    (target / "app.py").write_text("def handler():\n    return 1\n")
    return target


# === codepulse <path> wires mcp, skill, git exclude, and builds the map ===

def test_wire_and_build_produce_working_repo(repo, capsys):
    cli.wire(repo)
    engine = cli.build_engine(repo, announce=False)

    data = json.loads((repo / ".mcp.json").read_text())
    server = data["mcpServers"]["codepulse"]
    assert "serve-mcp" in server["args"] or "serve-mcp" in " ".join(server["args"])
    assert (repo / ".claude" / "skills" / "codepulse" / "SKILL.md").is_file()
    assert ".codepulse/" in (repo / ".git" / "info" / "exclude").read_text()
    assert any(e.addr.name == "handler" for e in engine.entities("app.py"))


# === the console script exists and prints usage ===

def test_console_script_answers_help():
    result = subprocess.run(
        [sys.executable, "-m", "codepulse", "--help"],
        capture_output=True, text=True, timeout=30, cwd=ROOT)

    assert result.returncode == 0
    assert "One live map" in result.stdout


# === .codepulse/ignore scopes the scan ===

def test_codepulse_ignore_file_excludes_directories(tmp_path):
    repo = tmp_path / "repo"
    (repo / "vendor-clone").mkdir(parents=True)
    (repo / "vendor-clone" / "big.py").write_text("def hidden():\n    pass\n")
    (repo / "app.py").write_text("def visible():\n    pass\n")
    (repo / ".codepulse").mkdir()
    (repo / ".codepulse" / "ignore").write_text("# scoped out\nvendor-clone\n")
    engine = Engine(tmp_path / "pulse.db",
                    [load_recipe(RECIPES_DIR / "python.yml")], root=repo)

    engine.apply()

    assert {e.addr.path for e in engine.entities()} == {"app.py"}


# === git scan respects .gitignore and skips nested repos ===

def test_git_scan_respects_gitignore_and_skips_nested_repos(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "app.py").write_text("def keep():\n    return 1\n")
    (repo / "data").mkdir()
    (repo / "data" / "junk.py").write_text("def ignored():\n    pass\n")
    (repo / ".gitignore").write_text("data/\n")
    clone = repo / "vendor-clone"
    clone.mkdir()
    subprocess.run(["git", "init", "-q", str(clone)], check=True)
    (clone / "big.py").write_text("def vendored():\n    pass\n")
    engine = Engine(tmp_path / "pulse.db",
                    [load_recipe(RECIPES_DIR / "python.yml")], root=repo)

    engine.apply()

    paths = {e.addr.path for e in engine.entities()}
    assert paths == {"app.py"}                       # gitignored data/ + nested clone both gone
    assert "vendor-clone" in engine.nested_repos()   # but the clone is surfaced


# === recipes resolve in both layouts ===

def test_builtin_recipes_load_from_repo_layout():
    names = {r.name for r in builtin_recipes()}

    assert {"python", "typescript", "yaml", "dockerfile", "terraform", "html"} <= names
