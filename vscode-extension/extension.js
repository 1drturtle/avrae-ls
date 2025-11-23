const vscode = require("vscode");
const path = require("path");
const { LanguageClient, TransportKind, Executable } = require("vscode-languageclient/node");

let client;
let previewPanel;
let previewArgsState = "";
let lastPreviewDocument = undefined;

function activate(context) {
  const serverCommand = "avrae-ls";
  // const serverArgs = ["tool", "run", "avrae-ls"];

  // Ensure the server runs from the repo root even if the extension host is launched elsewhere.

  /** @type {Executable} */
  const serverOptions = {
    command: serverCommand,
    // args: serverArgs,
    transport: TransportKind.stdio,
  };

  const clientOptions = {
    documentSelector: [{ scheme: "file", language: "avrae" }, { scheme: "untitled", language: "avrae" }],
    outputChannelName: "Avrae LS",
  };

  client = new LanguageClient(
    "avraeLS",
    "Avrae Draconic Alias Language Server",
    serverOptions,
    clientOptions
  );

  context.subscriptions.push(client.start());

  context.subscriptions.push(
    vscode.commands.registerCommand("avrae-ls.runAlias", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showWarningMessage("Open an Avrae alias file to run.");
        return;
      }
      const selection = editor.selection;
      const text = selection && !selection.isEmpty ? editor.document.getText(selection) : editor.document.getText();
      const profile = await vscode.window.showInputBox({
        prompt: "Context profile (optional)",
        placeHolder: "default",
      });
      const args = [
        {
          uri: editor.document.uri.toString(),
          text,
          profile: profile || undefined,
        },
      ];
      const result = await client.sendRequest("workspace/executeCommand", {
        command: "avrae.runAlias",
        arguments: args,
      });
      if (result && result.error) {
        vscode.window.showErrorMessage(`Avrae alias error: ${result.error}`);
      } else if (result) {
        const output = result.stdout ? `${result.stdout}\n` : "";
        const value = result.result !== undefined ? `Result: ${JSON.stringify(result.result)}` : "";
        vscode.window.showInformationMessage(`${output}${value}`.trim() || "Alias executed.");
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("avrae-ls.reloadConfig", async () => {
      try {
        await client.sendRequest("workspace/executeCommand", { command: "avrae.reloadConfig", arguments: [] });
        vscode.window.showInformationMessage("Avrae LS config reloaded.");
      } catch (err) {
        vscode.window.showErrorMessage(`Failed to reload config: ${err}`);
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("avrae-ls.showPreview", async () => {
      ensurePreviewPanel(context);
      await runAndRenderActive();
    })
  );

  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument(async (doc) => {
      if (doc.languageId !== "avrae") return;
      if (!previewPanel) return;
      await runAndRenderDocument(doc);
    })
  );

  async function runAndRenderActive() {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      vscode.window.showWarningMessage("Open an Avrae alias file to preview.");
      return;
    }
    await runAndRenderDocument(editor.document);
  }

  async function runAndRenderLastPreview() {
    if (lastPreviewDocument) {
      await runAndRenderDocument(lastPreviewDocument);
      return;
    }
    await runAndRenderActive();
  }

  async function runAndRenderDocument(document) {
    lastPreviewDocument = document;
    const args = [
      {
        uri: document.uri.toString(),
        text: document.getText(),
        args: parseArgs(previewArgsState),
      },
    ];
    let result;
    try {
      result = await client.sendRequest("workspace/executeCommand", {
        command: "avrae.runAlias",
        arguments: args,
      });
    } catch (err) {
      renderPreview({
        stdout: "",
        result: "",
        error: `Failed to run alias: ${err}`,
      });
      return;
    }
    renderPreview(result || {});
  }

  function ensurePreviewPanel(ctx) {
    if (previewPanel) {
      previewPanel.reveal(vscode.ViewColumn.Beside);
      return;
    }
    previewPanel = vscode.window.createWebviewPanel(
      "avraeAliasPreview",
      "Avrae Alias Preview",
      vscode.ViewColumn.Beside,
      { enableScripts: true }
    );
    previewPanel.onDidDispose(() => {
      previewPanel = undefined;
    }, null, ctx.subscriptions);
    previewPanel.webview.onDidReceiveMessage(async (message) => {
      if (message.command === "setArgs") {
        previewArgsState = message.args || "";
        await runAndRenderLastPreview();
      }
    });
    renderPreview({});
  }

  function renderPreview(result) {
    if (!previewPanel) return;
    const { stdout = "", result: value, error, validationError } = result;
    const validation = validationError
      ? `<pre class="warning">Embed preview warning: ${escapeHtml(validationError)}</pre>`
      : "";
    const renderedResult =
      value === undefined ? "" : `<pre class="result">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
    const renderedStdout = stdout ? `<pre class="stdout">${escapeHtml(stdout)}</pre>` : "";
    const renderedError = error ? `<pre class="error">${escapeHtml(error)}</pre>` : "";
    const renderedCommand = result.command
      ? `<div class="command"><strong>Command:</strong><pre class="block">${escapeHtml(result.command)}</pre></div>`
      : "";
    const canPreview =
      result.result && (result.commandName === "echo" || result.commandName === "embed");
    const previewLabel = result.commandName === "embed" ? "Embed" : "Preview";
    const renderedPreview = canPreview
      ? `<div class="preview"><strong>${previewLabel}:</strong><pre class="block">${escapeHtml(
        String(result.result)
      )}</pre></div>`
      : "";

    previewPanel.webview.html = `<!DOCTYPE html>
<html>
<head>
<style>
body { font-family: var(--vscode-editor-font-family, monospace); padding: 12px; }
pre { white-space: pre-wrap; word-break: break-word; }
.stdout { color: var(--vscode-editor-foreground); }
.result { color: var(--vscode-charts-green); }
.error { color: var(--vscode-errorForeground); }
.warning { color: var(--vscode-charts-yellow); }
.command { margin: 8px 0; font-weight: 600; }
.preview { margin: 8px 0; }
.block { white-space: pre-wrap; margin: 4px 0 0 0; }
.controls { margin: 0 0 12px 0; }
.controls label { display: block; margin-bottom: 4px; }
.controls input { width: 100%; box-sizing: border-box; padding: 6px; }
.controls button { margin-top: 6px; }
</style>
</head>
<body>
  <h3>Avrae Alias Preview</h3>
  <div class="controls">
    <label for="argsInput">Args (space-separated, quote to keep spaces):</label>
    <input id="argsInput" type="text" value="${escapeHtml(previewArgsState)}" />
    <button id="runBtn">Run Preview</button>
  </div>
  ${renderedCommand || ""}
  ${renderedPreview || ""}
  ${validation || ""}
  ${renderedError || ""}
  ${renderedStdout || ""}
  ${renderedResult || ""}
  <script>
    const vscode = acquireVsCodeApi();
    const input = document.getElementById('argsInput');
    const btn = document.getElementById('runBtn');
    btn.addEventListener('click', () => {
      vscode.postMessage({ command: 'setArgs', args: input.value || '' });
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        vscode.postMessage({ command: 'setArgs', args: input.value || '' });
      }
    });
  </script>
</body>
</html>`;
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function parseArgs(input) {
    if (!input) return [];
    const result = [];
    let current = "";
    let quote = null;
    for (let i = 0; i < input.length; i++) {
      const ch = input[i];
      if (quote) {
        if (ch === "\\" && i + 1 < input.length) {
          current += input[i + 1];
          i += 1;
          continue;
        }
        if (ch === quote) {
          quote = null;
          continue;
        }
        current += ch;
      } else {
        if (ch === '"' || ch === "'") {
          quote = ch;
        } else if (/\s/.test(ch)) {
          if (current) {
            result.push(current);
            current = "";
          }
        } else {
          current += ch;
        }
      }
    }
    if (current) result.push(current);
    return result;
  }
}

function deactivate() {
  if (!client) {
    return undefined;
  }
  return client.stop();
}

module.exports = {
  activate,
  deactivate,
};
