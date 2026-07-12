"""The codepulse command — one noun, one motion.

    codepulse .            wire this repo, build the map, start the panel
    codepulse <path>       same, for any repo
    codepulse serve-mcp --root <path>   what .mcp.json invokes (agents)

Wiring is idempotent: .mcp.json merged (never clobbered), the Claude skill
installed, .codepulse/ kept out of git. See specs/setup/SPEC.md.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from .engine import Engine
from .recipes import builtin_recipes


def _skill_source() -> Path | None:
    for candidate in (
        Path(__file__).resolve().parent.parent / "skill" / "SKILL.md",   # repo checkout
        Path(__file__).resolve().parent / "_skill" / "SKILL.md",         # installed wheel
    ):
        if candidate.is_file():
            return candidate
    return None


def _mcp_command(root: Path) -> dict:
    """Prefer the codepulse executable if it's on PATH; else this interpreter."""
    exe = shutil.which("codepulse")
    if exe:
        return {"command": exe, "args": ["serve-mcp", "--root", str(root)]}
    return {"command": sys.executable,
            "args": ["-m", "codepulse", "serve-mcp", "--root", str(root)]}


def wire(root: Path) -> None:
    target = root / ".mcp.json"
    data = json.loads(target.read_text()) if target.exists() else {}
    data.setdefault("mcpServers", {})["codepulse"] = _mcp_command(root)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"  mcp   : {target}")

    skill = _skill_source()
    if skill:
        dest = root / ".claude" / "skills" / "codepulse" / "SKILL.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(skill.read_text())
        print(f"  skill : {dest}")

    if (root / ".git").is_dir():
        exclude = root / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        lines = exclude.read_text().splitlines() if exclude.exists() else []
        if ".codepulse/" not in lines:
            exclude.write_text("\n".join([*lines, ".codepulse/"]) + "\n")
        print("  git   : .codepulse/ locally ignored")


def build_engine(root: Path, announce: bool = True) -> Engine:
    (root / ".codepulse").mkdir(exist_ok=True)
    engine = Engine(root / ".codepulse" / "pulse.db", builtin_recipes(), root=root)
    if announce:
        last = {"t": time.time()}

        def progress(done: int, total: int) -> None:
            if time.time() - last["t"] > 1.5:
                last["t"] = time.time()
                print(f"  map   : {done}/{total} files…", flush=True)
        engine.progress = progress
        print("  map   : checking freshness…", flush=True)
    started = time.time()
    report = engine.refresh()
    if announce:
        entities = engine.db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        print(f"  map   : {len(report.extracted)} files indexed, "
              f"{entities} entities ({time.time() - started:.1f}s)")
        hint = root / ".codepulse" / "ignore"
        if len(report.extracted) > 5000:
            print(f"  hint  : large repo — add directory names to {hint} (one per line) to scope the map")
    return engine


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] == "serve-mcp":   # the agent surface, invoked by .mcp.json
        mcp = argparse.ArgumentParser(prog="codepulse serve-mcp")
        mcp.add_argument("--root", default=".", help="repo root")
        opts = mcp.parse_args(argv[1:])
        from .mcp_server import serve
        serve(Path(opts.root).resolve())
        return

    parser = argparse.ArgumentParser(
        prog="codepulse",
        description="One live map of your repo, for you and your agents. "
                    "codepulse <path> wires the repo, builds the map, starts the panel; "
                    "codepulse serve-mcp --root <path> is what .mcp.json runs.")
    parser.add_argument("path", nargs="?", default=".",
                        help="repo to map (default: current directory)")
    parser.add_argument("--port", type=int, default=7317, help="panel port")
    parser.add_argument("--no-panel", action="store_true",
                        help="wire and index only; don't start the panel")
    args = parser.parse_args(argv)

    root = Path(args.path).resolve()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")
    print(f"codepulse -> {root}")
    wire(root)
    engine = build_engine(root)
    if args.no_panel:
        print("done. Open Claude Code here and approve the 'codepulse' server when asked.")
        return
    from .panel import make_server
    server = make_server(engine, args.port)
    print(f"  panel : http://127.0.0.1:{server.server_address[1]}  <- open this in a browser")
    print()
    print("this command keeps running (it is the panel). In ANOTHER terminal:")
    print(f"  cd {root} && claude     # approve 'codepulse' when asked, then ask it things")
    print("ctrl-c here only stops the panel — Claude Code runs its own map server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
