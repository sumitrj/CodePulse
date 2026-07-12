#!/usr/bin/env bash
# CodePulse setup — one command from clone to working map.
#   ./setup.sh /path/to/your/repo
# Wires the target repo: MCP server (.mcp.json), Claude skill, warm index,
# local git-ignore for .codepulse/. Idempotent. See specs/setup/SPEC.md.
set -euo pipefail

PULSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${1:?usage: setup.sh /path/to/repo}"
REPO="$(cd "$REPO" && pwd)"
PY="$PULSE_DIR/.venv/bin/python"

echo "codepulse setup -> $REPO"

# 1. interpreter: CodePulse's own venv
if [ ! -x "$PY" ]; then
  echo "  venv  : creating (uv sync)"
  (cd "$PULSE_DIR" && uv sync --quiet)
fi

# 2. .mcp.json — merge, never clobber
"$PY" - "$REPO" "$PULSE_DIR" <<'PYEOF'
import json, sys
from pathlib import Path
repo, pulse = Path(sys.argv[1]), Path(sys.argv[2])
target = repo / ".mcp.json"
data = json.loads(target.read_text()) if target.exists() else {}
data.setdefault("mcpServers", {})["codepulse"] = {
    "command": str(pulse / ".venv" / "bin" / "python"),
    "args": [str(pulse / "pulse.py"), "--root", str(repo), "serve-mcp"],
}
target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
print(f"  mcp   : {target}")
PYEOF

# 3. the Claude skill
mkdir -p "$REPO/.claude/skills/codepulse"
cp "$PULSE_DIR/skill/SKILL.md" "$REPO/.claude/skills/codepulse/SKILL.md"
echo "  skill : $REPO/.claude/skills/codepulse/SKILL.md"

# 4. warm the map so the first question is instant
"$PY" - "$REPO" <<'PYEOF'
import sys, time
from pathlib import Path
from codepulse.engine import Engine
from codepulse.recipes import builtin_recipes
repo = Path(sys.argv[1])
(repo / ".codepulse").mkdir(exist_ok=True)
started = time.time()
engine = Engine(repo / ".codepulse" / "pulse.db", builtin_recipes(), root=repo)
report = engine.refresh()
entities = engine.db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
print(f"  map   : {len(report.extracted)} files indexed, {entities} entities ({time.time() - started:.1f}s)")
PYEOF

# 5. keep .codepulse/ out of git without touching tracked files
if [ -d "$REPO/.git" ]; then
  EXCLUDE="$REPO/.git/info/exclude"
  mkdir -p "$(dirname "$EXCLUDE")"
  touch "$EXCLUDE"
  grep -qx '.codepulse/' "$EXCLUDE" || echo '.codepulse/' >> "$EXCLUDE"
  echo "  git   : .codepulse/ locally ignored"
fi

echo "done. Open Claude Code in $REPO and approve the 'codepulse' MCP server when asked."
