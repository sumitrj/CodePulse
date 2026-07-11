# CodePulse

One map, two readers, six questions.

CodePulse keeps a live entity-relation graph of your repo — Python, TypeScript, YAML, Dockerfile, Terraform, HTML — fresh within seconds of a save. You read it in this panel; your agents read the identical answers over MCP. Same map, same six questions:

**What is this · Who touches it · What breaks · What changed · Where does X live · How far apart**

## Requirements

A Python (3.11+) with the `codepulse` package installed. Point the
`codepulse.python` setting at it (e.g. the CodePulse repo's `.venv/bin/python`).

## Use

Click the pulse icon in the activity bar, or run **CodePulse: Open Panel**.
The extension starts a local server for the open folder; nothing leaves your machine.

Languages are recipes — YAML files of tree-sitter queries. Add one and the
engine speaks a new language without a code change.
