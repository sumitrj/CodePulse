// CodePulse extension shell — ~100 lines, zero dependencies.
// Spawns the panel server (python -m codepulse.panel) for the open workspace
// and hosts the brutalist webview. Works unchanged in VS Code, Cursor, and
// Antigravity: they are the same editor underneath.
const vscode = require("vscode");
const { spawn } = require("child_process");
const net = require("net");
const http = require("http");
const fs = require("fs");
const os = require("os");
const path = require("path");

let server;
let port;

function freePort() {
  return new Promise((resolve) => {
    const probe = net.createServer();
    probe.listen(0, "127.0.0.1", () => {
      const p = probe.address().port;
      probe.close(() => resolve(p));
    });
  });
}

function waitFor(url, tries = 40) {
  return new Promise((resolve, reject) => {
    const ping = (left) => {
      http.get(url, () => resolve()).on("error", () => {
        if (left <= 0) reject(new Error("CodePulse panel server did not start"));
        else setTimeout(() => ping(left - 1), 250);
      });
    };
    ping(tries);
  });
}

// The user's setting wins; otherwise the Python that `uv tool install` gave
// CodePulse, so installing the tool is the only setup; else plain python3.
function pythonFor(cfg) {
  const set = cfg.inspect("python") || {};
  const chosen = set.workspaceFolderValue || set.workspaceValue || set.globalValue;
  if (chosen) return chosen;
  const data = process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share");
  const uv = path.join(data, "uv", "tools", "codepulse", "bin", "python");
  return fs.existsSync(uv) ? uv : "python3";
}

async function ensureServer(context) {
  if (server) return port;
  const cfg = vscode.workspace.getConfiguration("codepulse");
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || !folders.length) {
    throw new Error("Open a folder first — CodePulse maps a repo.");
  }
  const root = folders[0].uri.fsPath;
  port = cfg.get("port") || (await freePort());
  server = spawn(pythonFor(cfg), ["-m", "codepulse.panel", "--root", root, "--port", String(port)], {
    cwd: root,
  });
  server.on("exit", () => { server = undefined; });
  context.subscriptions.push({ dispose: () => { if (server) server.kill(); } });
  await waitFor(`http://127.0.0.1:${port}/api/tree`);
  return port;
}

function frame(p) {
  return [
    "<!doctype html><html><head>",
    `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; frame-src http://127.0.0.1:${p}; style-src 'unsafe-inline';">`,
    "<style>html,body,iframe{margin:0;padding:0;width:100%;height:100vh;border:0;background:#fff}</style>",
    "</head><body>",
    `<iframe src="http://127.0.0.1:${p}/"></iframe>`,
    "</body></html>",
  ].join("");
}

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("codepulse.open", async () => {
      try {
        const p = await ensureServer(context);
        const panel = vscode.window.createWebviewPanel(
          "codepulse", "CodePulse", vscode.ViewColumn.Active,
          { enableScripts: true, retainContextWhenHidden: true }
        );
        panel.webview.html = frame(p);
      } catch (err) {
        vscode.window.showErrorMessage(String(err.message || err));
      }
    })
  );

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("codepulse.panel", {
      async resolveWebviewView(view) {
        try {
          const p = await ensureServer(context);
          view.webview.options = { enableScripts: true };
          view.webview.html = frame(p);
        } catch (err) {
          view.webview.html = `<body style="font-family:sans-serif;color:#666">${String(err.message || err)}</body>`;
        }
      },
    })
  );
}

function deactivate() {
  if (server) server.kill();
}

module.exports = { activate, deactivate };
