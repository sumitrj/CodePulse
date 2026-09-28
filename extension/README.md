# CodePulse for VS Code

For developers who want the CodePulse panel in the VS Code or Cursor sidebar
instead of a browser tab.

The panel shows a live map of the folder you have open, and answers six
questions about anything in it: what is this, who touches it, what breaks if
I change it, what did my change touch, where does X live, and where does it
start. Your AI agent gets the same answers as tools; see the
[main README](../README.md).

## Install

1. Install CodePulse: `uv tool install git+https://github.com/sumitrj/CodePulse`
2. In VS Code or Cursor: **Extensions → … → Install from VSIX**, and pick
   `codepulse-0.4.0.vsix` from this folder.

It finds the Python that `uv tool install` created on its own. If you
installed CodePulse another way, set **`codepulse.python`** to that Python.

## Use

Click the pulse icon in the activity bar, or run **CodePulse: Open Panel**.
The extension starts a local server for the open folder, and nothing leaves
your machine.
