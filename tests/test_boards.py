"""
Tests for boards. Maps 1:1 to acceptance criteria in specs/boards/SPEC.md.
Red until codepulse/boards.py exists.
"""
import json
import threading
import urllib.request
from pathlib import Path

import pytest

from codepulse.recipes import load_recipe, RECIPES_DIR
from codepulse.engine import Engine
from codepulse import boards, panel  # ImportError until implementation — expected


ROOT = Path(__file__).resolve().parents[1]

FILES = {
    # handler -> service -> db ; helper is called by service (not a root);
    # nothing calls handler.
    "api.py": (
        "from service import run\n"
        "\n\n"
        "def handler(request):\n"
        "    return run(request)\n"
    ),
    "service.py": (
        "from db import query\n"
        "\n\n"
        "def run(request):\n"
        "    return query(request)\n"
    ),
    "db.py": (
        "def query(request):\n"
        "    return request\n"
    ),
    # decorator-registered endpoint: only module-scope touches it
    "web.py": (
        "from service import run\n"
        "\n\n"
        "def route(path):\n"
        "    def deco(fn):\n"
        "        return fn\n"
        "    return deco\n"
        "\n\n"
        "@route(\"/x\")\n"
        "def endpoint(request):\n"
        "    return run(request)\n"
    ),
}


@pytest.fixture
def engine(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for rel, src in FILES.items():
        (repo / rel).write_text(src)
    eng = Engine(tmp_path / "pulse.db", [load_recipe(RECIPES_DIR / "python.yml")], root=repo)
    eng.apply()
    return eng


# === AC1: an uncalled function with downstream calls is a handler; its callees are not ===

def test_root_with_reach_is_a_handler_and_callees_are_not(engine):
    board = boards.handlers(engine)

    names = {(h["path"], h["name"]) for h in board}
    handler = next(h for h in board if h["name"] == "handler")

    assert ("api.py", "handler") in names
    assert ("service.py", "run") not in names
    assert ("db.py", "query") not in names
    assert handler["reach"] == 2      # run, query
    assert handler["direct"] == 1     # run


# === AC2: a decorator-registered function still counts as a handler ===

def test_decorated_endpoint_is_still_a_handler(engine):
    board = boards.handlers(engine)

    assert ("web.py", "endpoint") in {(h["path"], h["name"]) for h in board}


# === AC3: handlers_text speaks the estimate plainly ===

def test_handlers_text_speaks_the_estimate(engine):
    out = boards.handlers_text(engine)

    assert "handler-level" in out
    assert "api.py :: handler" in out


# === AC4: radius_graph returns hops and connecting edges ===

def test_radius_graph_returns_hops_and_edges(engine):
    data = boards.radius_graph(engine, "query")

    hops = {(n["path"], n["name"]): n["hop"] for n in data["nodes"]}
    assert data["target"] == {"path": "db.py", "name": "query"}
    assert hops[("service.py", "run")] == 1
    assert hops[("api.py", "handler")] == 2
    assert {"src": "service.py::run", "dst": "db.py::query", "kind": "calls"} in data["edges"]


# === AC4 addendum (found live): handlers need the downstream direction ===

def test_radius_graph_direction_out_walks_the_call_tree(engine):
    data = boards.radius_graph(engine, "handler", direction="out")

    hops = {(n["path"], n["name"]): n["hop"] for n in data["nodes"]}
    assert data["direction"] == "out"
    assert hops[("service.py", "run")] == 1
    assert hops[("db.py", "query")] == 2


# === AC5: file_graph aggregates cross-file edges with depths ===

def test_file_graph_aggregates_cross_file_edges(engine):
    data = boards.file_graph(engine)

    ids = {n["id"] for n in data["nodes"]}
    edge = next(e for e in data["edges"] if e["src"] == "api.py" and e["dst"] == "service.py")

    assert data["mode"] == "file"
    assert {"api.py", "service.py", "db.py", "web.py"} <= ids
    assert edge["n"] >= 1
    assert all(e["src"] != e["dst"] for e in data["edges"])
    depth = {n["id"]: n["depth"] for n in data["nodes"]}
    assert depth["api.py"] < depth["service.py"] < depth["db.py"]


# === AC6: API routes and MCP dispatch answer the boards ===

@pytest.fixture
def live(engine):
    server = panel.make_server(engine, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode())


def test_api_routes_answer_the_boards(live):
    handlers = _get(live + "/api/handlers")
    radius = _get(live + "/api/radius?name=query")
    graph = _get(live + "/api/graph")

    assert any(h["name"] == "handler" for h in handlers)
    assert radius["target"]["name"] == "query"
    assert graph["mode"] == "file"


def test_mcp_dispatch_answers_pulse_handlers(engine):
    from codepulse.mcp_server import dispatch

    out = dispatch(engine, "pulse_handlers", {})

    assert "api.py :: handler" in out


# === Traceability ===
# AC1: test_root_with_reach_is_a_handler_and_callees_are_not
# AC2: test_decorated_endpoint_is_still_a_handler
# AC3: test_handlers_text_speaks_the_estimate
# AC4: test_radius_graph_returns_hops_and_edges
# AC5: test_file_graph_aggregates_cross_file_edges
# AC6: test_api_routes_answer_the_boards, test_mcp_dispatch_answers_pulse_handlers
