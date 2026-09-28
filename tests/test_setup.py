"""
Tests for `codepulse <repo>` wiring. Maps 1:1 to acceptance criteria in
specs/setup/SPEC.md. Runs the real command.
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_setup(repo: Path):
    return subprocess.run(
        [sys.executable, "-m", "codepulse", str(repo), "--no-panel"],
        capture_output=True, text=True, timeout=120, cwd=ROOT,
    )


@pytest.fixture
def repo(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    (target / "app.py").write_text("def handler():\n    return work()\n\ndef work():\n    return 1\n")
    return target


@pytest.fixture
def wired(repo):
    result = run_setup(repo)
    assert result.returncode == 0, result.stderr
    return repo


# === AC1: .mcp.json gains a correct codepulse server ===

def test_mcp_json_has_codepulse_server_with_absolute_paths(wired):
    data = json.loads((wired / ".mcp.json").read_text())

    server = data["mcpServers"]["codepulse"]
    assert Path(server["command"]).is_absolute()
    assert server["args"][-3:] == ["serve-mcp", "--root", str(wired.resolve())]


# === AC2: an existing .mcp.json survives the merge ===

def test_existing_mcp_servers_survive_the_merge(repo):
    (repo / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"other": {"command": "x", "args": []}}}))

    result = run_setup(repo)

    assert result.returncode == 0, result.stderr
    data = json.loads((repo / ".mcp.json").read_text())
    assert data["mcpServers"]["other"] == {"command": "x", "args": []}
    assert "codepulse" in data["mcpServers"]


# === AC3: the skill lands with a description ===

def test_skill_is_installed_with_description(wired):
    skill = wired / ".claude" / "skills" / "codepulse" / "SKILL.md"

    assert skill.is_file()
    assert "description:" in skill.read_text()


# === AC4: the map is warm ===

def test_map_is_warm_after_setup(wired):
    db = wired / ".codepulse" / "pulse.db"

    assert db.is_file()
    names = {row[0] for row in sqlite3.connect(db).execute("SELECT name FROM entities")}
    assert "handler" in names


# === AC5 + AC6: .codepulse ignored locally, setup idempotent ===

def test_codepulse_dir_is_git_excluded_once_and_rerun_is_safe(wired):
    before = (wired / ".mcp.json").read_text()

    result = run_setup(wired)

    assert result.returncode == 0, result.stderr
    exclude = (wired / ".git" / "info" / "exclude").read_text()
    assert exclude.count(".codepulse/") == 1
    assert (wired / ".mcp.json").read_text() == before


# === Traceability ===
# AC1: test_mcp_json_has_codepulse_server_with_absolute_paths
# AC2: test_existing_mcp_servers_survive_the_merge
# AC3: test_skill_is_installed_with_description
# AC4: test_map_is_warm_after_setup
# AC5+AC6: test_codepulse_dir_is_git_excluded_once_and_rerun_is_safe
