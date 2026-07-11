"""Panel server — the human rendering of the six verbs.

A stdlib HTTP server over the engine graph. The webview (VS Code / Cursor /
Antigravity, or any browser) is a thin fetch client; every answer routes
through codepulse.six, the same code that answers agents over MCP.
See specs/panel/SPEC.md.
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import six
from .engine import Engine
from .recipes import builtin_recipes

_WEBVIEW = Path(__file__).parent / "webview" / "panel.html"


def answer(engine: Engine, verb: str, arg: str) -> str:
    arg = (arg or "").strip()
    if verb == "what":
        return six.what_is(engine, arg)
    if verb == "who":
        return six.who_touches(engine, arg)
    if verb == "radius":
        return six.radius(engine, arg)
    if verb == "locate":
        return six.locate(engine, arg)
    if verb == "map":
        return six.system_map(engine)
    if verb == "changed":
        if arg:
            paths = [engine.root / p for p in arg.split()]
        else:
            from .mcp_server import _git_modified
            paths = _git_modified(engine.root)
        if not paths:
            return "No changed paths given and nothing modified per git."
        return six.what_changed(engine, paths)
    return f"No such verb '{verb}'. The six: what, who, radius, changed, locate, map."


def tree(engine: Engine) -> dict:
    files: dict[str, list] = {}
    for entity in engine.entities():
        files.setdefault(entity.addr.path, [])
        if entity.addr.name != "<module>":
            files[entity.addr.path].append(
                {"name": entity.addr.name, "kind": entity.kind, "line": entity.line}
            )
    edges = engine.db.execute(
        "SELECT COUNT(*) FROM edges WHERE status!='dropped'").fetchone()[0]
    return {
        "stats": {
            "files": len(files),
            "entities": sum(len(v) for v in files.values()),
            "edges": edges,
        },
        "files": [
            {"path": path, "entities": sorted(entities, key=lambda e: e["line"])}
            for path, entities in sorted(files.items())
        ],
    }


def make_server(engine: Engine, port: int = 7317) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep stdio quiet; this runs under an editor
            pass

        def do_GET(self):
            url = urlparse(self.path)
            content_type = "application/json"
            try:
                if url.path == "/":
                    body = _WEBVIEW.read_bytes()
                    content_type = "text/html; charset=utf-8"
                elif url.path == "/api/tree":
                    engine.refresh()
                    body = json.dumps(tree(engine)).encode()
                elif url.path == "/api/verb":
                    query = parse_qs(url.query)
                    engine.refresh()
                    text = answer(engine, query.get("v", [""])[0], query.get("arg", [""])[0])
                    body = json.dumps({"text": text}).encode()
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
            except Exception as exc:
                body = json.dumps({"text": f"codepulse error: {exc}"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")  # editor webviews
            self.end_headers()
            self.wfile.write(body)

    return HTTPServer(("127.0.0.1", port), Handler)


def serve(root: Path, port: int = 7317) -> None:
    root = Path(root)
    (root / ".codepulse").mkdir(exist_ok=True)
    engine = Engine(root / ".codepulse" / "pulse.db", builtin_recipes(), root=root)
    engine.refresh()
    server = make_server(engine, port)
    print(f"codepulse panel on http://127.0.0.1:{server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    import argparse

    cli = argparse.ArgumentParser(description="CodePulse panel server")
    cli.add_argument("--root", default=".", help="repo root to map")
    cli.add_argument("--port", type=int, default=7317)
    args = cli.parse_args()
    serve(Path(args.root).resolve(), args.port)
