"""Minimal MCP server (stdio, JSON-RPC 2.0) exposing the six verbs to agents.

Answers come from the recipe-built engine graph; every call refreshes first,
so agents never read a stale map. Stdlib only. Register with Claude Code:
  claude mcp add codepulse -- python3 /path/to/codepulse/pulse.py serve-mcp --root /path/to/repo
"""
import json
import subprocess
import sys
from pathlib import Path

from . import six
from .engine import Engine
from .recipes import builtin_recipes

_TOOLS = [
    ("pulse_what", "What is this entity? Card: kind, recipe provenance, calls, dependents.",
     {"name": "entity name, or path::name"}, ["name"]),
    ("pulse_who", "Who touches this entity? All incoming edges.",
     {"name": "entity name, or path::name"}, ["name"]),
    ("pulse_radius", "What breaks if I change it? Transitive blast radius.",
     {"name": "entity name, or path::name"}, ["name"]),
    ("pulse_changed", "What did a change touch? Touched entities plus dependents at risk.",
     {"paths": "space-separated changed file paths (optional; defaults to git-modified files)"}, []),
    ("pulse_locate", "Where does X live? Rank entities by name and path tokens.",
     {"query": "words describing the thing you are looking for"}, ["query"]),
    ("pulse_map", "System silhouette: counts and load-bearing entities.",
     {}, []),
]


def _tool_list():
    return [
        {
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": {k: {"type": "string", "description": v} for k, v in params.items()},
                "required": required,
            },
        }
        for name, description, params, required in _TOOLS
    ]


def _git_modified(root: Path) -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [root / line for line in out.splitlines() if line.strip()]


def dispatch(engine: Engine, name: str, arguments: dict) -> str:
    if name == "pulse_what":
        return six.what_is(engine, arguments["name"])
    if name == "pulse_who":
        return six.who_touches(engine, arguments["name"])
    if name == "pulse_radius":
        return six.radius(engine, arguments["name"])
    if name == "pulse_locate":
        return six.locate(engine, arguments["query"])
    if name == "pulse_map":
        return six.system_map(engine)
    if name == "pulse_changed":
        raw = arguments.get("paths", "").split()
        paths = [engine.root / p for p in raw] if raw else _git_modified(engine.root)
        if not paths:
            return "No changed paths given and nothing modified per git."
        return six.what_changed(engine, paths)
    return f"Unknown tool {name}"


def serve(root: Path):
    db_dir = root / ".codepulse"
    db_dir.mkdir(exist_ok=True)
    engine = Engine(db_dir / "pulse.db", builtin_recipes(), root=root)
    engine.refresh()

    def reply(request_id, result=None, error=None):
        message = {"jsonrpc": "2.0", "id": request_id}
        message["error" if error else "result"] = error or result
        print(json.dumps(message), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, request_id = request.get("method"), request.get("id")
        if method == "initialize":
            reply(request_id, {
                "protocolVersion": request.get("params", {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "codepulse", "version": "0.2.0"},
            })
        elif method == "tools/list":
            reply(request_id, {"tools": _tool_list()})
        elif method == "tools/call":
            params = request.get("params", {})
            try:
                engine.refresh()  # never answer from a stale map
                text = dispatch(engine, params.get("name", ""), params.get("arguments", {}))
                reply(request_id, {"content": [{"type": "text", "text": text}]})
            except Exception as exc:
                reply(request_id, {"content": [{"type": "text", "text": f"codepulse error: {exc}"}],
                                   "isError": True})
        elif method == "ping":
            reply(request_id, {})
        elif request_id is not None:
            reply(request_id, error={"code": -32601, "message": f"method not found: {method}"})
