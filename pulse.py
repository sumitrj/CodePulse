#!/usr/bin/env python3
"""Self-contained entry point for the codepulse CLI (hooks, MCP, verbs)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from codepulse.cli import main

if __name__ == "__main__":
    sys.exit(main())
