"""codepulse CLI: index a repo, ask it the six verbs, and serve Claude Code.

Subcommands `hook pre` / `hook post` implement the push surface: Claude Code
pipes its hook JSON on stdin; we answer with additionalContext so the map
arrives before the question is asked.
"""
import argparse
import json
import sys
from pathlib import Path

from . import verbs
from .relationships import UnitRef
from .store import Store, find_root


def _root(args) -> Path:
    if args.root:
        return Path(args.root).resolve()
    found = find_root(Path.cwd())
    return found if found else Path.cwd()


def _load(args) -> Store:
    root = _root(args)
    try:
        return Store.load(root)
    except FileNotFoundError:
        print(f"No index at {root}/.codepulse - run: codepulse index", file=sys.stderr)
        sys.exit(2)


def cmd_index(args):
    root = _root(args)
    previous = None
    try:
        previous = Store.load(root)
    except FileNotFoundError:
        pass
    store, result = Store.build(root, previous=previous)
    target = store.save()
    carried = sum(1 for a in result.assignments if a.kind != "new")
    print(
        f"Indexed {len(store.file_hashes)} files -> {len(store.units)} units, "
        f"{len(store.graph.edges)} edges ({carried} identities carried forward). Store: {target}"
    )


def cmd_verb(args):
    store = _load(args)
    if args.verb == "what":
        print(verbs.what_is(store, args.name))
    elif args.verb == "who":
        print(verbs.who_touches(store, args.name))
    elif args.verb == "radius":
        print(verbs.radius(store, args.name))
    elif args.verb == "locate":
        print(verbs.locate(store, " ".join(args.query)))
    elif args.verb == "map":
        print(verbs.system_map(store))
    elif args.verb == "changed":
        report, new_store = verbs.what_changed(store, use_model=args.llm)
        print(report)
        if args.update:
            new_store.save()
            print("(store updated)")


# -- Claude Code hooks ----------------------------------------------------

def _hook_payload() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return {}


def _hook_reply(event: str, context: str):
    if context.strip():
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": context,
            }
        }))


def cmd_hook_pre(args):
    """PreToolUse on Edit/Write: inject the neighborhood of the file being touched."""
    payload = _hook_payload()
    file_path = (payload.get("tool_input") or {}).get("file_path", "")
    root = find_root(Path(file_path).resolve() if file_path else Path.cwd())
    if not root or not file_path:
        return
    store = Store.load(root)
    rel = str(Path(file_path).resolve().relative_to(root))
    units = [(uid, u) for uid, u in store.units.items() if u.path == rel]
    if not units:
        return
    lines = [f"CodePulse map for {rel} (knowledge injected before your edit):"]
    for uid, u in sorted(units, key=lambda x: x[1].qualname)[:12]:
        impact = verbs.compute_radius(store, UnitRef(u.path, u.qualname))
        callers = sorted({f"{r.path}::{r.qualname}" for r in impact})[:4]
        lines.append(
            f"- {u.qualname} [{u.kind}]: blast radius {len(impact)}"
            + (f" -> {', '.join(callers)}" + (" ..." if len(impact) > 4 else "") if callers else "")
        )
    lines.append("If you remove or relocate behavior from these units, their dependents break silently. Check the list above before editing; use the codepulse MCP tools for deeper queries.")
    _hook_reply("PreToolUse", "\n".join(lines))


def cmd_hook_post(args):
    """PostToolUse on Edit/Write: verify the edit against the map, refresh the store."""
    payload = _hook_payload()
    file_path = (payload.get("tool_input") or {}).get("file_path", "")
    root = find_root(Path(file_path).resolve() if file_path else Path.cwd())
    if not root:
        return
    store = Store.load(root)
    new_store, result = Store.build(root, previous=store)
    rel = str(Path(file_path).resolve().relative_to(root)) if file_path else None
    warnings = []
    from .judge import classify_change
    for a in result.assignments:
        if a.kind in ("exact", "new") or (rel and a.unit.path != rel):
            continue
        old = store.units.get(a.unit_id)
        if old is None:
            continue
        verdict = classify_change(old, a.unit, use_model=False)
        if verdict.classification == "major":
            impacted = verbs.compute_radius(store, UnitRef(old.path, old.qualname))
            dependents = ", ".join(sorted(f"{r.path}::{r.qualname}" for r in impacted)) or "none indexed"
            warnings.append(
                f"MAJOR change to {a.unit.qualname}: {verdict.summary}\n"
                f"  Dependents to verify: {dependents}"
            )
    new_store.save()
    if warnings:
        _hook_reply("PostToolUse", "CodePulse verification:\n" + "\n".join(warnings))


def cmd_hooks_install(args):
    root = _root(args)
    entry = Path(__file__).resolve().parents[1] / "pulse.py"
    def hook(event: str, cmd: str) -> dict:
        return {"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": cmd}]}
    settings = {
        "hooks": {
            "PreToolUse": [hook("pre", f"python3 {entry} hook pre")],
            "PostToolUse": [hook("post", f"python3 {entry} hook post")],
        }
    }
    target = root / ".claude" / "settings.json"
    if target.exists():
        print(f"{target} already exists - merge this manually:\n{json.dumps(settings, indent=2)}")
        return
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"Hooks installed at {target}")


def cmd_serve(args):
    from .mcp_server import serve
    serve(_root(args))


def cmd_app(args):
    from .app import serve_app
    serve_app(_root(args), port=args.port)


def _issue(args):
    from .workbench import Issue, fetch_issue
    if args.file:
        data = json.loads(Path(args.file).read_text())
        return Issue(number=data["number"], title=data["title"],
                     body=data.get("body", ""), labels=tuple(data.get("labels", [])))
    return fetch_issue(args.number, repo=args.repo)


def cmd_brief(args):
    from .workbench import GateConfig, brief_text, gate, make_brief
    store, issue = _load(args), _issue(args)
    brief = make_brief(store, issue)
    print(brief_text(brief))
    eligible, reasons = gate(store, brief, GateConfig(max_radius=args.max_radius))
    print("\nGate:", "ELIGIBLE for hands-free" if eligible
          else "ESCALATE to human:\n  - " + "\n  - ".join(reasons))


def cmd_work(args):
    from .workbench import GateConfig, claude_executor, run_workpiece, save_workpiece
    issue = _issue(args)
    config = GateConfig(max_radius=args.max_radius)
    wp = run_workpiece(_root(args), issue, config, claude_executor(args.executor),
                       dry_run=not args.live, use_model=args.llm)
    path = save_workpiece(_root(args), wp)
    print(f"Workpiece v{wp['version']} [{wp['status']}] -> {path}")
    if wp["status"] == "escalated":
        print("Escalated to human:\n  - " + "\n  - ".join(wp["gate"]["reasons"]))
    elif wp.get("rejection"):
        print("Reason:", wp["rejection"])
    elif wp["status"] == "delivered":
        from .workbench import render_pr_body
        print(f"\n{wp['delivery']['pr_title']}  (branch {wp['delivery']['branch']}, {wp['delivery']['mode']})")
        print(render_pr_body(wp))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="codepulse")
    parser.add_argument("--root", help="repo root (default: walk up to nearest .codepulse)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("index", help="build or refresh the map")

    for verb, help_text in [
        ("what", "verb 1: what is this unit?"),
        ("who", "verb 2: who touches it?"),
        ("radius", "verb 3: what breaks if I change it?"),
    ]:
        p = sub.add_parser(verb, help=help_text)
        p.add_argument("name")
    p = sub.add_parser("locate", help="verb 5: where does X live?")
    p.add_argument("query", nargs="+")
    p = sub.add_parser("changed", help="verb 4: what changed since last index?")
    p.add_argument("--llm", action="store_true", help="use Claude for verdicts")
    p.add_argument("--update", action="store_true", help="write the refreshed store")
    sub.add_parser("map", help="system silhouette + simplicity audit")

    hook = sub.add_parser("hook", help="Claude Code hook endpoints (stdin JSON)")
    hook.add_argument("event", choices=["pre", "post"])
    sub.add_parser("install-hooks", help="write .claude/settings.json hooks for this repo")
    sub.add_parser("serve-mcp", help="MCP server over stdio (six verbs as tools)")
    p = sub.add_parser("app", help="companion app (Material 3 web UI)")
    p.add_argument("--port", type=int, default=7317)
    for name, help_text in [("brief", "issue -> map-scoped brief + gate decision"),
                            ("work", "issue -> hands-free workpiece (brief/gate/execute/prove/deliver)")]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("number", nargs="?", type=int)
        p.add_argument("--file", help="issue JSON file instead of gh")
        p.add_argument("--repo", help="owner/repo for gh")
        p.add_argument("--max-radius", type=int, default=10)
        if name == "work":
            p.add_argument("--executor", help="executor command template (default: headless claude)")
            p.add_argument("--live", action="store_true", help="push + PR via gh (default dry-run)")
            p.add_argument("--llm", action="store_true", help="Claude verdicts in Prove")

    args = parser.parse_args(argv)
    command = args.command
    if command == "index":
        cmd_index(args)
    elif command in ("what", "who", "radius", "locate", "map", "changed"):
        args.verb = command
        cmd_verb(args)
    elif command == "hook":
        try:
            (cmd_hook_pre if args.event == "pre" else cmd_hook_post)(args)
        except Exception:
            return 0  # hooks must never break the edit
    elif command == "install-hooks":
        cmd_hooks_install(args)
    elif command == "serve-mcp":
        cmd_serve(args)
    elif command == "app":
        cmd_app(args)
    elif command == "brief":
        cmd_brief(args)
    elif command == "work":
        cmd_work(args)
    return 0
