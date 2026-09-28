"""Minimal MCP server (stdio, JSON-RPC 2.0) exposing the verbs to agents.

Answers come from the recipe-built engine graph; every call refreshes first,
so agents never read a stale map. Stdlib only.

One server serves every repo. Each call names its repo with the optional
`repo` argument (any path inside it); without one, the call falls back to
`--root`, else the directory the client launched us in. So a single
user-level registration works from any working directory:
  claude mcp add --scope user codepulse -- codepulse serve-mcp
"""
import hashlib
import json
import os
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
    ("pulse_delta", "PR delta: which entities the diff itself touches, each added/modified/"
     "deleted with its own blast radius.",
     {"base": "git revision to diff against (optional; defaults to HEAD)",
      "diff": "raw unified diff text (optional; read from git when absent)"}, []),
    ("pulse_locate", "Where does X live? Rank entities by name and path tokens.",
     {"query": "words describing the thing you are looking for"}, ["query"]),
    ("pulse_map", "System silhouette: counts and load-bearing entities.",
     {}, []),
    ("pulse_handlers", "Board: estimated handler-level functions (uncalled roots with downstream reach).",
     {}, []),
]

_REPO_PARAM = ("absolute path to the repo to ask about, or any file/dir inside it "
               "(optional; defaults to the session's working repo)")

# Every verb is a question, never an instruction: nothing here writes to the
# repo, and re-asking costs nothing. The one side effect is the sidecar map —
# .codepulse/pulse.db in a wired repo, else a per-user cache — which each call
# refreshes, invisible to the caller. Declaring that lets clients stop asking
# permission to read.
_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False,
              "idempotentHint": True, "openWorldHint": False}


def _tool_list():
    return [
        {
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": {k: {"type": "string", "description": v}
                               for k, v in {**params, "repo": _REPO_PARAM}.items()},
                "required": required,
            },
            "annotations": {"title": name, **_READ_ONLY},
        }
        for name, description, params, required in _TOOLS
    ]


def tool_names() -> list[str]:
    """Every verb, for installers writing a permission allowlist."""
    return [name for name, *_ in _TOOLS]


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
    if name == "pulse_handlers":
        from . import boards
        return boards.handlers_text(engine)
    if name == "pulse_delta":
        from . import delta
        return delta.delta_text(engine, arguments.get("diff"),
                                base=arguments.get("base") or "HEAD")
    if name == "pulse_changed":
        raw = arguments.get("paths", "").split()
        paths = [engine.root / p for p in raw] if raw else _git_modified(engine.root)
        if not paths:
            return "No changed paths given and nothing modified per git."
        return six.what_changed(engine, paths)
    return f"Unknown tool {name}"


def _cache_home() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "codepulse"


def repo_root(path: Path, named: bool = False) -> Path:
    """The folder a question is about: the git top level when there is one;
    else a folder already wired with .codepulse/; else, only when the caller
    named it, the folder itself. Never the home directory or / — mapping
    either by accident is minutes of CPU, not an answer."""
    path = path.expanduser().resolve()
    start = path if path.is_dir() else path.parent
    if not start.is_dir():
        raise ValueError(f"no such directory: {path}")
    try:
        res = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
        if res.returncode == 0 and res.stdout.strip():
            return Path(res.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    if start in (Path.home().resolve(), Path(start.anchor)):
        raise ValueError(f"refusing to map {start} whole; pass repo=<path to a repo>")
    if (start / ".codepulse").is_dir() or named:
        return start
    raise ValueError(f"{start} is not inside a git repo or a codepulse-wired folder; "
                     "pass repo=<path to a repo>")


def db_path(root: Path) -> Path:
    """A wired repo keeps its map beside it; any other repo gets one in the
    user cache, so asking about a repo never writes into it."""
    if (root / ".codepulse").is_dir():
        return root / ".codepulse" / "pulse.db"
    key = hashlib.sha256(str(root).encode()).hexdigest()[:12]
    target = _cache_home() / f"{root.name}-{key}"
    target.mkdir(parents=True, exist_ok=True)
    return target / "pulse.db"


class Engines:
    """One engine per repo, opened on first use and kept for the session."""

    def __init__(self, default: Path | None = None):
        self.default = default
        self._open: dict[Path, Engine] = {}

    def for_call(self, arguments: dict) -> Engine:
        base = self.default or Path.cwd()
        raw = (arguments.get("repo") or "").strip()
        target = (base / Path(raw).expanduser()) if raw else base
        root = repo_root(target, named=bool(raw))
        if root not in self._open:
            self._open[root] = Engine(db_path(root), builtin_recipes(), root=root)
        return self._open[root]


def serve(root: Path | None = None):
    engines = Engines(root)
    if root is not None:
        try:
            engines.for_call({}).refresh()   # warm the pinned repo before the first question
        except ValueError:
            pass

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
                arguments = params.get("arguments") or {}
                engine = engines.for_call(arguments)
                engine.refresh()  # never answer from a stale map
                text = dispatch(engine, params.get("name", ""), arguments)
                reply(request_id, {"content": [{"type": "text", "text": text}]})
            except Exception as exc:
                reply(request_id, {"content": [{"type": "text", "text": f"codepulse error: {exc}"}],
                                   "isError": True})
        elif method == "ping":
            reply(request_id, {})
        elif request_id is not None:
            reply(request_id, error={"code": -32601, "message": f"method not found: {method}"})
