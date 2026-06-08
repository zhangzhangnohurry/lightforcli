import * as vscode from 'vscode';
import * as http from 'http';

const MODE_COLORS: Record<string, string> = {
  alarm: '#ff3b30', error: '#ff3b30',
  busy: '#ffd23f', thinking: '#ffd23f',
  success: '#33d17a', green: '#33d17a',
  off: '#24282b',
};

const MODE_LABELS: Record<string, string> = {
  green: 'Green', busy: 'Busy', error: 'Error', thinking: 'Thinking',
  success: 'Success', alarm: 'Alarm', off: 'Off',
};

export class StatusBarItem {
  private item: vscode.StatusBarItem;
  private pollTimer: ReturnType<typeof setInterval> | undefined;
  private currentMode = 'off';

  constructor(private context: vscode.ExtensionContext) {
    this.item = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Left, 100
    );
    this.item.command = 'claude-light.openPanel';
    this.item.text = '$(lightbulb) Off';
    this.item.tooltip = 'ClaudeLight: No active sessions';
    this.item.show();
    context.subscriptions.push(this.item);
  }

  start() {
    // Poll every 2 seconds
    const interval = setInterval(() => this.poll(), 2000);
    this.context.subscriptions.push(new vscode.Disposable(() => clearInterval(interval)));
    this.poll();
  }

  private async poll() {
    const config = vscode.workspace.getConfiguration('claude-light');
    const host = config.get<string>('host', '127.0.0.1');
    const port = config.get<number>('port', 8765);
    const url = `http://${host}:${port}/api/state`;

    try {
      const data = await this.httpGet(url);
      const state = JSON.parse(data);
      this.currentMode = state.aggregate_mode || 'off';
      const count = Object.keys(state.sessions || {}).length;
      const label = MODE_LABELS[this.currentMode] || this.currentMode;
      this.item.text = `$(lightbulb) ${label}`;
      this.item.tooltip = count > 0
        ? `ClaudeLight: ${label} — ${count} session(s)`
        : `ClaudeLight: ${label}`;
    } catch {
      this.item.text = '$(lightbulb) ●';
      this.item.tooltip = 'ClaudeLight: server not reachable';
    }
  }

  updateMode(mode: string) {
    this.currentMode = mode;
    const label = MODE_LABELS[mode] || mode;
    this.item.text = `$(lightbulb) ${label}`;
  }

  private httpGet(url: string): Promise<string> {
    return new Promise((resolve, reject) => {
      http.get(url, { timeout: 1000 }, (res) => {
        let body = '';
        res.on('data', (chunk) => body += chunk);
        res.on('end', () => resolve(body));
      }).on('error', reject);
    });
  }

  dispose() {
    this.item.dispose();
  }
}