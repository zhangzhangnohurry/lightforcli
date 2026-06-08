import * as vscode from 'vscode';
import * as http from 'http';
import { ClaudeLightPanel } from './panel';
import { StatusBarItem } from './statusBarItem';

let panel: ClaudeLightPanel | undefined;
let statusBarItem: StatusBarItem;
let publishTimer: ReturnType<typeof setTimeout> | undefined;

export function activate(context: vscode.ExtensionContext) {
  statusBarItem = new StatusBarItem(context);
  statusBarItem.start();
  registerWorkspacePublisher(context);

  context.subscriptions.push(
    vscode.commands.registerCommand('claude-light.openPanel', () => {
      if (!panel) {
        panel = new ClaudeLightPanel(context.extensionUri, statusBarItem);
      }
      panel.reveal();
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('claude-light.setMode', async () => {
      const modes = [
        'thinking', 'busy', 'green', 'success', 'error', 'alarm', 'off'
      ];
      const mode = await vscode.window.showQuickPick(modes, {
        placeHolder: 'Select ClaudeLight mode'
      });
      if (mode) {
        const config = vscode.workspace.getConfiguration('claude-light');
        const host = config.get<string>('host', '127.0.0.1');
            const port = config.get<number>('port', 8765);
            try {
          const body = JSON.stringify({
            session_id: vscodeSessionId(),
            mode,
            name: workspaceName(),
            metadata: workspaceMetadata(),
          });
          const url = `http://${host}:${port}/api/mode`;
          const req = http.request(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            timeout: 1000,
          }, (res: any) => {
            res.resume();
          });
          req.on('error', () => {
            vscode.window.showWarningMessage('ClaudeLight server not reachable');
          });
          req.write(body);
          req.end();
        } catch {
          vscode.window.showWarningMessage('ClaudeLight server not reachable');
        }
      }
    })
  );
}

export function deactivate() {
  panel?.dispose();
  statusBarItem.dispose();
}

function registerWorkspacePublisher(context: vscode.ExtensionContext) {
  const schedule = () => scheduleWorkspacePublish();
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(schedule),
    vscode.workspace.onDidChangeWorkspaceFolders(schedule),
    vscode.window.onDidChangeWindowState(schedule),
    vscode.window.onDidOpenTerminal(schedule),
    vscode.window.onDidCloseTerminal(schedule),
    vscode.window.tabGroups.onDidChangeTabs(schedule),
    vscode.window.tabGroups.onDidChangeTabGroups(schedule)
  );
  scheduleWorkspacePublish(50);
}

function scheduleWorkspacePublish(delay = 250) {
  if (publishTimer) {
    clearTimeout(publishTimer);
  }
  publishTimer = setTimeout(() => {
    publishWorkspaceState().catch(() => undefined);
  }, delay);
}

async function publishWorkspaceState(): Promise<void> {
  const config = vscode.workspace.getConfiguration('claude-light');
  const host = config.get<string>('host', '127.0.0.1');
  const port = config.get<number>('port', 8765);
  const mode = workspaceMode();
  const body = JSON.stringify({
    session_id: vscodeSessionId(),
    mode,
    name: workspaceName(),
    metadata: workspaceMetadata(mode),
  });
  await httpPost(`http://${host}:${port}/api/mode`, body);
}

function workspaceFolder(): vscode.WorkspaceFolder | undefined {
  const editorUri = vscode.window.activeTextEditor?.document.uri;
  if (editorUri) {
    const folder = vscode.workspace.getWorkspaceFolder(editorUri);
    if (folder) return folder;
  }
  return vscode.workspace.workspaceFolders?.[0];
}

function workspaceName(): string {
  return workspaceFolder()?.name || vscode.workspace.name || 'VSCode';
}

function vscodeSessionId(): string {
  const folder = workspaceFolder();
  const key = folder?.uri.toString() || vscode.workspace.name || 'no-workspace';
  return `vscode:${key}`;
}

function workspaceMode(): string {
  return activeAssistantTabLabel() ? 'thinking' : 'off';
}

function activeAssistantTabLabel(): string {
  const active = vscode.window.tabGroups.activeTabGroup?.activeTab;
  const activeLabel = active?.label || '';
  if (isAssistantTabLabel(activeLabel)) return activeLabel;
  for (const group of vscode.window.tabGroups.all) {
    for (const tab of group.tabs) {
      if (isAssistantTabLabel(tab.label)) return tab.label;
    }
  }
  return '';
}

function isAssistantTabLabel(label: string): boolean {
  const lower = label.toLowerCase();
  return lower.includes('claude code') || lower === 'claude' || lower.includes('codex');
}

function workspaceMetadata(mode: string = workspaceMode()): Record<string, string> {
  const folder = workspaceFolder();
  const editor = vscode.window.activeTextEditor?.document;
  const activeEditor = editor ? basename(editor.uri.fsPath || editor.fileName) : '';
  const assistantTab = activeAssistantTabLabel();
  const cwd = folder?.uri.fsPath || '';
  const terminals = vscode.window.terminals.length.toString();
  const activity = assistantTab ? `${assistantTab} tab active` : (activeEditor || 'workspace idle');
  return {
    source: 'vscode',
    workspace: workspaceName(),
    workspace_name: workspaceName(),
    project_name: workspaceName(),
    cwd,
    directory: basename(cwd),
    path: cwd,
    uri: folder?.uri.toString() || '',
    active_editor: activeEditor || assistantTab,
    summary: `${activity} · ${terminals} terminal(s) · ${mode}`,
  };
}

function basename(value: string): string {
  if (!value) return '';
  const normalized = value.replace(/\\/g, '/');
  return normalized.slice(normalized.lastIndexOf('/') + 1);
}

function httpPost(url: string, body: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const req = http.request({
      hostname: parsed.hostname,
      port: parsed.port,
      path: parsed.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body).toString(),
      },
      timeout: 1000,
    }, (res) => {
      res.resume();
      res.on('end', () => resolve());
    });
    req.on('error', reject);
    req.on('timeout', () => {
      req.destroy();
      reject(new Error('ClaudeLight server request timed out'));
    });
    req.write(body);
    req.end();
  });
}
