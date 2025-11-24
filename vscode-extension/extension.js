const vscode = require("vscode");
const path = require("path");
const { LanguageClient, TransportKind, Executable } = require("vscode-languageclient/node");

let client;
let previewPanel;
let previewArgsState = "";
let lastPreviewDocument = undefined;
let dracPreviewDecoration;

function activate(context) {
  const serverCommand = "avrae-ls";
  /** @type {Executable} */
  const serverOptions = {
    command: serverCommand,
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
      if (message.command === "revealLine" && lastPreviewDocument) {
        const editor = vscode.window.visibleTextEditors.find((e) => e.document.uri.toString() === lastPreviewDocument.uri.toString());
        if (editor) {
          const line = Math.max(0, Number(message.line || 0));
          const pos = new vscode.Position(line, 0);
          editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
          editor.selection = new vscode.Selection(pos, pos);
        }
      }
    });
    renderPreview({});
  }

  function renderPreview(result) {
    if (!previewPanel) return;
    const { stdout = "", result: value, error, validationError } = result;
    const renderedResult = value === undefined
      ? `<div class="empty">No result</div>`
      : `<pre class="result code-block">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
    const renderedStdout = stdout
      ? `<pre class="stdout code-block">${escapeHtml(stdout)}</pre>`
      : `<div class="empty">No stdout</div>`;
    const diagnostics = [
      validationError ? `Embed preview warning: ${validationError}` : null,
      error ? `Runtime error: ${error}` : null,
    ].filter(Boolean);
    const renderedDiagnostics = diagnostics.length
      ? diagnostics.map((d, idx) => `<pre class="diagnostic code-block" data-line="${idx === 0 ? 0 : ""}">${escapeHtml(d)}</pre>`).join("")
      : `<div class="empty">No diagnostics</div>`;
    const renderedCommand = result.command
      ? `<pre class="code-block">${escapeHtml(result.command)}</pre>`
      : `<div class="empty">No command captured</div>`;
    const canPreview =
      result.result && (result.commandName === "echo" || result.commandName === "embed");
    const previewLabel = result.commandName === "embed" ? "Embed" : "Preview";
    const renderedPreview = canPreview
      ? `<pre class="code-block">${escapeHtml(String(result.result))}</pre>`
      : `<div class="empty">Nothing to preview</div>`;

    previewPanel.webview.html = `<!DOCTYPE html>
<html>
<head>
<style>
body { font-family: var(--vscode-editor-font-family, monospace); padding: 12px; }
pre { white-space: pre-wrap; word-break: break-word; }
.stdout { color: var(--vscode-editor-foreground); }
.result { color: var(--vscode-charts-green); }
.diagnostic { color: var(--vscode-errorForeground); }
.error { color: var(--vscode-errorForeground); }
.warning { color: var(--vscode-charts-yellow); }
.command { margin: 8px 0; font-weight: 600; }
.preview { margin: 8px 0; }
.code-block { background: var(--vscode-editor-background); padding: 8px; border-radius: 4px; border: 1px solid var(--vscode-editorWidget-border); }
.controls { margin: 0 0 12px 0; }
.controls label { display: block; margin-bottom: 4px; }
.controls input { width: 100%; box-sizing: border-box; padding: 6px; }
.controls button { margin-top: 6px; }
.tabs { display: flex; gap: 6px; margin-bottom: 8px; }
.tab-btn { padding: 6px 10px; border: 1px solid var(--vscode-editorWidget-border); background: var(--vscode-editorWidget-background); color: var(--vscode-editor-foreground); border-radius: 4px; cursor: pointer; }
.tab-btn.active { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
.tab-panel { display: none; }
.tab-panel.active { display: block; }
.empty { color: var(--vscode-descriptionForeground); padding: 6px 0; }
</style>
</head>
<body>
  <h3>Avrae Alias Preview</h3>
  <div class="controls">
    <label for="argsInput">Args (space-separated, quote to keep spaces):</label>
    <input id="argsInput" type="text" value="${escapeHtml(previewArgsState)}" />
    <button id="runBtn">Run Preview</button>
  </div>
  <div class="tabs">
    <button class="tab-btn active" data-tab="preview">Preview</button>
    <button class="tab-btn" data-tab="result">Result</button>
    <button class="tab-btn" data-tab="stdout">Stdout</button>
    <button class="tab-btn" data-tab="diagnostics">Diagnostics</button>
    <button class="tab-btn" data-tab="command">Command</button>
  </div>
  <div id="preview" class="tab-panel active">${renderedPreview}</div>
  <div id="result" class="tab-panel">${renderedResult}</div>
  <div id="stdout" class="tab-panel">${renderedStdout}</div>
  <div id="diagnostics" class="tab-panel">${renderedDiagnostics}</div>
  <div id="command" class="tab-panel">${renderedCommand}</div>
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
    const tabs = document.querySelectorAll('.tab-btn');
    const panels = document.querySelectorAll('.tab-panel');
    tabs.forEach(btn => {
      btn.addEventListener('click', () => {
        tabs.forEach(b => b.classList.remove('active'));
        panels.forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        const panel = document.getElementById(btn.dataset.tab);
        if (panel) panel.classList.add('active');
      });
    });
    document.querySelectorAll('.diagnostic').forEach(el => {
      el.addEventListener('click', () => {
        const line = Number(el.dataset.line || 0);
        vscode.postMessage({ command: 'revealLine', line });
      });
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
