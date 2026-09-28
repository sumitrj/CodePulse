"""
The map from any working directory, and a map no single file can take down.
Maps 1:1 to acceptance criteria in specs/anywhere/SPEC.md.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from codepulse import main as cli
from codepulse.engine import Engine
from codepulse.mcp_server import Engines, db_path, dispatch, repo_root
from codepulse.recipes import builtin_recipes

ROOT = Path(__file__).resolve().parents[1]


def git_repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True)
    for rel, src in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(src)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    return path


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path / "cache"


@pytest.fixture
def two_repos(tmp_path):
    a = git_repo(tmp_path / "alpha", {"pkg/a.py": "def alpha_only():\n    return 1\n"})
    b = git_repo(tmp_path / "beta", {"b.py": "def beta_only():\n    return 2\n"})
    return a, b


# === AC1: a call names its repo; the launch directory doesn't matter ===

def test_repo_argument_selects_the_repo_from_any_cwd(two_repos, tmp_path, monkeypatch):
    a, b = two_repos
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    engines = Engines()

    for repo, name in ((a, "alpha_only"), (b, "beta_only")):
        engine = engines.for_call({"repo": str(repo)})
        engine.refresh()
        assert name in dispatch(engine, "pulse_locate", {"query": name})


def test_a_path_inside_the_repo_resolves_to_its_top_level(two_repos):
    a, _ = two_repos
    assert repo_root(a / "pkg" / "a.py") == a.resolve()


# === AC2: without `repo`, the pinned root, else the launch directory ===

def test_no_repo_argument_falls_back_to_pin_then_cwd(two_repos, monkeypatch):
    a, b = two_repos
    assert Engines(default=a).for_call({}).root == a.resolve()
    monkeypatch.chdir(b)
    assert Engines().for_call({}).root == b.resolve()


# === AC3: a non-git folder maps only when named, never by climbing ===

def test_unnamed_non_repo_cwd_is_refused_with_a_hint(tmp_path, monkeypatch):
    loose = tmp_path / "loose"
    loose.mkdir()
    monkeypatch.chdir(loose)
    with pytest.raises(ValueError, match="repo=<path"):
        Engines().for_call({})


def test_named_non_repo_folder_maps_as_itself_not_a_wired_parent(tmp_path):
    (tmp_path / "parent" / ".codepulse").mkdir(parents=True)
    child = tmp_path / "parent" / "child"
    child.mkdir()
    assert Engines().for_call({"repo": str(child)}).root == child.resolve()


def test_home_directory_is_never_mapped_whole(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(ValueError, match="refusing"):
        repo_root(tmp_path, named=True)


# === AC4: asking about an unwired repo never writes into it ===

def test_unwired_repo_map_lives_in_the_user_cache(two_repos, cache):
    a, _ = two_repos
    assert db_path(a).is_relative_to(cache)
    assert not (a / ".codepulse").exists()
    (a / ".codepulse").mkdir()
    assert db_path(a) == a / ".codepulse" / "pulse.db"


# === AC5: every tool advertises the optional `repo` argument ===

def test_every_tool_takes_an_optional_repo():
    from codepulse.mcp_server import _tool_list
    for tool in _tool_list():
        assert "repo" in tool["inputSchema"]["properties"]
        assert "repo" not in tool["inputSchema"]["required"]


# === AC6: the stdio server answers for a repo it wasn't launched in ===

def test_stdio_server_launched_outside_any_repo(two_repos, tmp_path, cache):
    a, _ = two_repos
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "pulse_map", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "pulse_locate", "arguments": {"query": "alpha", "repo": str(a)}}},
    ]
    env = {**os.environ, "XDG_CACHE_HOME": str(cache), "PYTHONPATH": str(ROOT)}
    out = subprocess.run(
        [sys.executable, "-m", "codepulse", "serve-mcp"], cwd=elsewhere, env=env,
        input="\n".join(json.dumps(r) for r in requests) + "\n",
        capture_output=True, text=True, timeout=120,
    )
    replies = {r["id"]: r for r in map(json.loads, out.stdout.splitlines())}
    assert replies[2]["result"]["isError"] is True           # no repo, cwd isn't one
    assert "alpha_only" in replies[3]["result"]["content"][0]["text"]


# === AC7: install wires the user level, not a repo ===

def test_install_writes_skill_and_allowlist_under_home(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    cli.install(home=tmp_path / "home")
    settings = json.loads((tmp_path / "home" / ".claude" / "settings.json").read_text())
    assert "mcp__codepulse__pulse_map" in settings["permissions"]["allow"]
    assert (tmp_path / "home" / ".claude" / "skills" / "codepulse" / "SKILL.md").is_file()


def test_user_level_server_command_pins_no_root():
    assert "--root" not in cli._mcp_command()["args"]


# === AC8: non-file references never reach the filesystem ===

DATA_URI = "data:image/png;base64," + "iVBORw0KGgo" * 20_000     # ~220 KB, one "path"


@pytest.mark.parametrize("ref", [
    DATA_URI, "javascript:(function(){alert(1)})()", "https://cdn.example.com/x.js",
    "mailto:a@b.c", "//cdn.example.com/x.js", "#top",
], ids=["data-uri", "javascript", "https", "mailto", "protocol-relative", "fragment"])
def test_non_file_reference_is_dropped_not_stat_ed(tmp_path, ref):
    repo = git_repo(tmp_path / "site", {
        "index.html": f'<html><body><img src="{ref}"><script src="./app.js"></script></body></html>',
        "app.js": "",
    })
    eng = Engine(tmp_path / "pulse.db", builtin_recipes(), root=repo)
    eng.apply()
    targets = [t for (t,) in eng.db.execute("SELECT target FROM edges WHERE src_path='index.html'")]
    assert targets == ["./app.js"]


def test_query_and_fragment_still_load_the_file(tmp_path):
    repo = git_repo(tmp_path / "site", {
        "index.html": '<script src="./app.js?v=3#main"></script>', "app.js": "",
    })
    eng = Engine(tmp_path / "pulse.db", builtin_recipes(), root=repo)
    eng.apply()
    dst = eng.db.execute("SELECT dst_path FROM edges WHERE src_path='index.html'").fetchone()
    assert dst == ("app.js",)


# === AC9: one file the recipes choke on costs that file, not the map ===

def test_a_failing_file_is_skipped_and_reported(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "r", {"good.py": "def fine():\n    pass\n",
                                     "bad.py": "def boom():\n    pass\n"})
    eng = Engine(tmp_path / "pulse.db", builtin_recipes(), root=repo)
    real = eng._extract_file

    def flaky(rel, source, recipe):
        real(rel, source, recipe)                 # half-written rows must roll back
        if rel == "bad.py":
            raise RuntimeError("recipe choked")
    monkeypatch.setattr(eng, "_extract_file", flaky)

    report = eng.apply()

    assert report.failed == ("bad.py",)
    assert "good.py" in report.extracted
    paths = {p for (p,) in eng.db.execute("SELECT path FROM entities")}
    assert "good.py" in paths and "bad.py" not in paths


# === Traceability ===
# AC1: test_repo_argument_selects_the_repo_from_any_cwd
#      test_a_path_inside_the_repo_resolves_to_its_top_level
# AC2: test_no_repo_argument_falls_back_to_pin_then_cwd
# AC3: test_unnamed_non_repo_cwd_is_refused_with_a_hint
#      test_named_non_repo_folder_maps_as_itself_not_a_wired_parent
#      test_home_directory_is_never_mapped_whole
# AC4: test_unwired_repo_map_lives_in_the_user_cache
# AC5: test_every_tool_takes_an_optional_repo
# AC6: test_stdio_server_launched_outside_any_repo
# AC7: test_install_writes_skill_and_allowlist_under_home
#      test_user_level_server_command_pins_no_root
# AC8: test_non_file_reference_is_dropped_not_stat_ed (x6)
#      test_query_and_fragment_still_load_the_file
# AC9: test_a_failing_file_is_skipped_and_reported
