"""Minimal MCP server (stdio, JSON-RPC 2.0) exposing the six verbs to agents.

Stdlib only. Register with Claude Code:
  claude mcp add codepulse -- python3 /path/to/codepulse/pulse.py serve-mcp --root /path/to/repo
"""
import json
import sys
from pathlib import Path

from . import verbs
from .store import Store

_TOOLS = [
    ("pulse_what", "What is this unit? Card: contract, promise, calls, dependents.",
     {"name": "unit name or qualname"}),
    ("pulse_who", "Who touches this unit or state? All incoming edges.",
     {"name": "unit or module-level variable name"}),
    ("pulse_radius", "What breaks if I change it? Transitive blast radius.",
     {"name": "unit or state name"}),
    ("pulse_changed", "What changed since the last index? Unit-level verdicts.",
     {}),
    ("pulse_locate", "Where does X live? Locate units by meaning, not string match.",
     {"query": "natural-language description of the behavior"}),
    ("pulse_map", "System silhouette: regions, load-bearing units, duplicate implementations.",
     {}),
]


def _tool_list():
    tools = []
    for name, description, params in _TOOLS:
        tools.append({
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": {k: {"type": "string", "description": v} for k, v in params.items()},
                "required": list(params),
            },
        })
    return tools


def _dispatch(store: Store, name: str, arguments: dict) -> str:
    if name == "pulse_what":
        return verbs.what_is(store, arguments["name"])
    if name == "pulse_who":
        return verbs.who_touches(store, arguments["name"])
    if name == "pulse_radius":
        return verbs.radius(store, arguments["name"])
    if name == "pulse_locate":
        return verbs.locate(store, arguments["query"])
    if name == "pulse_map":
        return verbs.system_map(store)
    if name == "pulse_changed":
        report, _ = verbs.what_changed(store, use_model=False)
        return report
    return f"Unknown tool {name}"


def serve(root: Path):
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
                "serverInfo": {"name": "codepulse", "version": "0.1.0"},
            })
        elif method == "tools/list":
            reply(request_id, {"tools": _tool_list()})
        elif method == "tools/call":
            params = request.get("params", {})
            try:
                store = Store.load(root)  # reload per call: always fresh
                text = _dispatch(store, params.get("name", ""), params.get("arguments", {}))
                reply(request_id, {"content": [{"type": "text", "text": text}]})
            except Exception as exc:
                reply(request_id, {"content": [{"type": "text", "text": f"codepulse error: {exc}"}],
                                   "isError": True})
        elif method == "ping":
            reply(request_id, {})
        elif request_id is not None:
            reply(request_id, error={"code": -32601, "message": f"method not found: {method}"})
