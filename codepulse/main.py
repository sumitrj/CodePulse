"""The codepulse command — one noun, one motion.

    codepulse install      once per machine: the map in every repo, any working dir
    codepulse .            wire this repo, build the map, start the panel
    codepulse <path>       same, for any repo
    codepulse serve-mcp [--root <path>]   what Claude Code invokes (agents)

Wiring is idempotent: .mcp.json merged (never clobbered), the Claude skill
installed, .codepulse/ kept out of git. See specs/setup/SPEC.md.
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .engine import MODULE_SCOPE, SCOPES, Engine
from .mcp_server import tool_names
from .recipes import builtin_recipes


def _skill_source() -> Path | None:
    from .recipes import SKILL_PATH
    return SKILL_PATH if SKILL_PATH.is_file() else None


def _mcp_command(root: Path | None = None) -> dict:
    """Prefer the codepulse executable if it's on PATH; else this interpreter.
    No root means the server follows each call's `repo`, else its launch dir."""
    pin = ["--root", str(root)] if root else []
    exe = shutil.which("codepulse")
    if exe:
        return {"command": exe, "args": ["serve-mcp", *pin]}
    return {"command": sys.executable, "args": ["-m", "codepulse", "serve-mcp", *pin]}


# Read-only shell the skill's documented fallbacks need. Deliberately short:
# everything that writes, and every other command, still stops for a human.
_SAFE_BASH = ("Bash(git diff:*)", "Bash(git status:*)")


def _permission_rules() -> list[str]:
    return [f"mcp__codepulse__{name}" for name in tool_names()] + list(_SAFE_BASH)


def _wire_permissions(target: Path) -> None:
    """Pre-approve the read-only verbs.

    A map you must approve once per verb per task costs more attention than the
    greps it replaced. Merge, never clobber: we only ever add entries the file
    is missing, so a hand-tuned allowlist survives re-wiring untouched.
    """
    if target.exists():
        try:
            data = json.loads(target.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  perms : skipped — {target} is unreadable ({exc}); "
                  f"add these yourself: {', '.join(_permission_rules())}")
            return
    else:
        data = {}

    allow = data.setdefault("permissions", {}).setdefault("allow", [])
    if not isinstance(allow, list):
        print(f"  perms : skipped — permissions.allow in {target} is not a list")
        return
    added = [rule for rule in _permission_rules() if rule not in allow]
    if not added:
        print(f"  perms : already allowed ({len(tool_names())} read-only verbs)")
        return
    allow.extend(added)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"  perms : {len(added)} rule(s) pre-approved in {target}")


def wire(root: Path) -> None:
    target = root / ".mcp.json"
    data = json.loads(target.read_text()) if target.exists() else {}
    data.setdefault("mcpServers", {})["codepulse"] = _mcp_command(root)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"  mcp   : {target}")

    _wire_permissions(root / ".claude" / "settings.json")

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


def install(home: Path | None = None) -> None:
    """User-level wiring: one MCP registration, skill, and allowlist in
    ~/.claude, so every Claude Code session has the map whatever its working
    directory. The server picks the repo per call; nothing is written into
    any repo until you run `codepulse <repo>` for its panel."""
    home = home or Path.home()
    command = _mcp_command()
    claude = shutil.which("claude")
    add = ["mcp", "add", "--scope", "user", "codepulse", "--", command["command"], *command["args"]]
    if claude:
        subprocess.run([claude, "mcp", "remove", "--scope", "user", "codepulse"],
                       capture_output=True)            # re-install replaces, never duplicates
        res = subprocess.run([claude, *add], capture_output=True, text=True)
        if res.returncode == 0:
            print("  mcp   : registered at user scope (all repos)")
        else:
            print(f"  mcp   : `claude mcp add` failed: {res.stderr.strip() or res.stdout.strip()}")
    else:
        print("  mcp   : claude CLI not on PATH — run this yourself:")
        print("          claude " + " ".join(add))

    _wire_permissions(home / ".claude" / "settings.json")

    skill = _skill_source()
    if skill:
        dest = home / ".claude" / "skills" / "codepulse" / "SKILL.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(skill.read_text())
        print(f"  skill : {dest}")
    print("done. Start Claude Code anywhere; pass repo=<path> to ask about another repo.")


_IGNORE_SCAFFOLD = """\
# codepulse ignore — one directory name per line, matched anywhere in the tree.
# codepulse already respects your .gitignore and skips the usual noise
# (.git, .venv, node_modules, __pycache__, dist, build, site-packages, …).
# Add directory names below to scope the map further — e.g. data, notebooks,
# fixtures — then re-run `codepulse .`. Lines starting with # are ignored.
"""


def _first_run_scaffold(root: Path, engine: Engine) -> None:
    """Surface the ignore decision at the moment it matters — first run —
    by writing an editable .codepulse/ignore and naming what was skipped."""
    ignore = root / ".codepulse" / "ignore"
    if not ignore.exists():
        ignore.write_text(_IGNORE_SCAFFOLD)
        print(f"  scope : respecting .gitignore · edit {ignore} to narrow further")
    nested = engine.nested_repos()
    if nested:
        shown = ", ".join(nested[:4]) + (f" (+{len(nested) - 4} more)" if len(nested) > 4 else "")
        print(f"  nested: skipped {len(nested)} vendored repo(s): {shown}")
        print("          to map one, run  codepulse <that-dir>")


def build_engine(root: Path, announce: bool = True) -> Engine:
    (root / ".codepulse").mkdir(exist_ok=True)
    engine = Engine(root / ".codepulse" / "pulse.db", builtin_recipes(), root=root)
    if announce:
        _first_run_scaffold(root, engine)
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
        entities = engine.db.execute(
            "SELECT COUNT(*) FROM entities WHERE name NOT IN (?, ?)", SCOPES).fetchone()[0]
        files = engine.db.execute(
            "SELECT COUNT(*) FROM entities WHERE name=?", (MODULE_SCOPE,)).fetchone()[0]
        print(f"  map   : {files} files, {entities} entities "
              f"({len(report.extracted)} read just now, {time.time() - started:.1f}s)")
        if report.failed:
            shown = ", ".join(report.failed[:4]) + (" …" if len(report.failed) > 4 else "")
            print(f"  skip  : {len(report.failed)} file(s) the recipes couldn't parse: {shown}")
        hint = root / ".codepulse" / "ignore"
        if len(report.extracted) > 5000:
            print(f"  hint  : large repo — add directory names to {hint} (one per line) to scope the map")
    return engine


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] == "serve-mcp":   # the agent surface, invoked by .mcp.json
        mcp = argparse.ArgumentParser(prog="codepulse serve-mcp")
        mcp.add_argument("--root", default=None,
                         help="default repo when a call names none "
                              "(default: the directory the client launched us in)")
        opts = mcp.parse_args(argv[1:])
        from .mcp_server import serve
        serve(Path(opts.root).resolve() if opts.root else None)
        return

    if argv == ["install"]:               # a folder named install: `codepulse ./install`
        install()
        return

    parser = argparse.ArgumentParser(
        prog="codepulse",
        description="One live map of your repo, for you and your agents. "
                    "codepulse install registers the map for every repo (once per machine); "
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
