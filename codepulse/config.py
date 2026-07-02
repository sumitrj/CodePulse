"""Configurables: the control points, in one file the whole system reads.

Persisted at <root>/.codepulse/config.json. Every knob here is load-bearing -
the scanner reads scope, the Workbench reads gate + judge, the hooks read
injection. Missing file -> built-in defaults (nothing breaks unconfigured).
"""
import json
from pathlib import Path

CONFIG_FILE = ".codepulse/config.json"

# The schema is also the documentation: each field carries its owner, control
# type, and whether it is wired ("live") or surfaced-but-not-yet-enforced
# ("display"). The settings page renders straight from this.
SCHEMA = {
    "scope": {
        "_owner": "repo owner",
        "exclude_dirs": {"type": "chips", "live": True,
                         "help": "directory names never indexed"},
        "languages": {"type": "chips", "live": False,
                      "help": "extractors in scope (Python live; others planned)"},
        "granularity": {"type": "select", "options": ["function", "class", "module"],
                        "live": False, "help": "unit size (function-level today)"},
    },
    "gate": {
        "_owner": "tech lead",
        "max_radius": {"type": "slider", "min": 0, "max": 50, "live": True,
                       "help": "blast radius above which hands-free escalates to a human"},
        "max_fan_in": {"type": "slider", "min": 0, "max": 100, "live": True,
                       "help": "load-bearing threshold - escalate above this many dependents"},
        "min_confidence": {"type": "slider", "min": 0, "max": 50, "live": True,
                           "help": "lowest brief confidence eligible for hands-free"},
        "require_tests": {"type": "switch", "live": True,
                          "help": "refuse hands-free on repos with no proof surface"},
    },
    "judge": {
        "_owner": "tech lead",
        "model": {"type": "select",
                  "options": ["claude-opus-4-8", "claude-sonnet-5", "claude-fable-5"],
                  "live": True, "help": "model that classifies changes"},
        "use_model": {"type": "switch", "live": True,
                      "help": "call the model (off = paranoid heuristic only)"},
        "paranoia": {"type": "select", "options": ["escalate", "resolve"], "live": False,
                     "help": "when unsure, escalate (default) or resolve silently"},
    },
    "hooks": {
        "_owner": "developer",
        "inject_pre": {"type": "switch", "live": True,
                       "help": "inject the file's unit map before each edit"},
        "verify_post": {"type": "switch", "live": True,
                        "help": "verify the edit and flag MAJOR changes after"},
        "injection_budget": {"type": "slider", "min": 1, "max": 40, "live": True,
                             "help": "max units injected per pre-edit hook"},
    },
    "freshness": {
        "_owner": "ops",
        "cadence": {"type": "select", "options": ["manual", "save", "commit"], "live": False,
                    "help": "reindex trigger (post-edit hook refreshes today)"},
    },
}

DEFAULTS = {
    "scope": {
        "exclude_dirs": [".git", ".venv", "venv", "env", "node_modules", "__pycache__",
                         ".pytest_cache", "dist", "build", ".codepulse", ".claude"],
        "languages": ["python"],
        "granularity": "function",
    },
    "gate": {"max_radius": 10, "max_fan_in": 25, "min_confidence": 4, "require_tests": True},
    "judge": {"model": "claude-opus-4-8", "use_model": True, "paranoia": "escalate"},
    "hooks": {"inject_pre": True, "verify_post": True, "injection_budget": 12},
    "freshness": {"cadence": "manual"},
}


class Config:
    def __init__(self, data: dict):
        self.data = data

    def section(self, name: str) -> dict:
        return self.data.get(name, {})

    def get(self, section: str, key: str):
        return self.data.get(section, {}).get(key, DEFAULTS[section][key])

    def exclude_dirs(self) -> set[str]:
        return set(self.get("scope", "exclude_dirs"))

    def gate_config(self):
        from .workbench import GateConfig
        g = self.section("gate")
        return GateConfig(
            max_radius=g.get("max_radius", DEFAULTS["gate"]["max_radius"]),
            max_fan_in=g.get("max_fan_in", DEFAULTS["gate"]["max_fan_in"]),
            min_confidence=g.get("min_confidence", DEFAULTS["gate"]["min_confidence"]),
            require_tests=g.get("require_tests", DEFAULTS["gate"]["require_tests"]),
        )

    def to_dict(self) -> dict:
        return self.data


def _merge(base: dict, over: dict) -> dict:
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in base.items()}
    for section, values in over.items():
        if isinstance(values, dict) and isinstance(out.get(section), dict):
            out[section].update(values)
        else:
            out[section] = values
    return out


def load(root: Path) -> Config:
    path = Path(root) / CONFIG_FILE
    if not path.exists():
        return Config(_merge(DEFAULTS, {}))
    try:
        return Config(_merge(DEFAULTS, json.loads(path.read_text())))
    except (json.JSONDecodeError, OSError):
        return Config(_merge(DEFAULTS, {}))


def save(root: Path, data: dict) -> Path:
    """Persist only the live/known keys, validated against the schema."""
    clean: dict = {}
    for section, fields in SCHEMA.items():
        incoming = data.get(section, {})
        if not isinstance(incoming, dict):
            continue
        for key, spec in fields.items():
            if key.startswith("_") or key not in incoming:
                continue
            clean.setdefault(section, {})[key] = _coerce(spec, incoming[key])
    path = Path(root) / CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = _merge(DEFAULTS, clean)
    path.write_text(json.dumps(merged, indent=1))
    return path


def _coerce(spec: dict, value):
    kind = spec["type"]
    if kind == "switch":
        return bool(value)
    if kind == "slider":
        v = int(value)
        return max(spec.get("min", 0), min(spec.get("max", 10**9), v))
    if kind == "chips":
        return [str(x) for x in value] if isinstance(value, list) else \
            [s.strip() for s in str(value).split(",") if s.strip()]
    if kind == "select":
        return value if value in spec["options"] else spec["options"][0]
    return value
