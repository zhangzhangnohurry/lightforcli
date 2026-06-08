import * as vscode from 'vscode';
import * as http from 'http';
import { StatusBarItem } from './statusBarItem';

const MODE_LABELS: Record<string, string> = {
  green: 'Green', busy: 'Busy', error: 'Error', thinking: 'Thinking',
  success: 'Success', alarm: 'Alarm', off: 'Off',
};

export class ClaudeLightPanel {
  public static currentPanel?: ClaudeLightPanel;
  private readonly _panel: vscode.WebviewPanel;
  private _disposables: vscode.Disposable[] = [];

  constructor(private extensionUri: vscode.Uri, private statusBarItem: StatusBarItem) {
    ClaudeLightPanel.currentPanel = this;

    this._panel = vscode.window.createWebviewPanel(
      'claude-light-panel',
      'ClaudeLight',
      vscode.ViewColumn.One,
      {
        enableScripts: true,
        localResourceRoots: [this.extensionUri],
      }
    );

    this._panel.webview.html = this._getHtmlForWebview();

    this._panel.webview.onDidReceiveMessage(
      async (msg) => {
        if (msg.type === 'setMode') {
          await this.sendMode(msg.mode);
        }
      },
      null,
      this._disposables
    );

    this._panel.onDidDispose(() => this.dispose(), null, this._disposables);

    // Start SSE polling
    this.startPolling();
  }

  public reveal() {
    this._panel.reveal();
  }

  private async sendMode(mode: string) {
    const config = vscode.workspace.getConfiguration('claude-light');
    const host = config.get<string>('host', '127.0.0.1');
    const port = config.get<number>('port', 8765);
    const url = `http://${host}:${port}/api/mode`;
    const body = JSON.stringify({ session_id: 'vscode', mode });

    try {
      await this.httpPost(url, body);
    } catch {
      vscode.window.showWarningMessage('ClaudeLight server not reachable');
    }
  }

  private startPolling() {
    const interval = setInterval(async () => {
      const config = vscode.workspace.getConfiguration('claude-light');
      const host = config.get<string>('host', '127.0.0.1');
      const port = config.get<number>('port', 8765);
      try {
        const data = await this.httpGet(`http://${host}:${port}/api/state`);
        const state = JSON.parse(data);
        this._panel.webview.postMessage({
          type: 'stateUpdate',
          aggregate_mode: state.aggregate_mode,
          sessions: state.sessions,
        });
        this.statusBarItem.updateMode(state.aggregate_mode || 'off');
      } catch {
        // server not reachable — ignore
      }
    }, 2000);
    this._disposables.push(new vscode.Disposable(() => clearInterval(interval)));
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

  private httpPost(url: string, body: string): Promise<string> {
    return new Promise((resolve, reject) => {
      const parsed = new URL(url);
      const options = {
        hostname: parsed.hostname,
        port: parsed.port,
        path: parsed.pathname,
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': body.length },
        timeout: 1000,
      };
      const req = http.request(options, (res) => {
        let data = '';
        res.on('data', (chunk) => data += chunk);
        res.on('end', () => resolve(data));
      });
      req.on('error', reject);
      req.write(body);
      req.end();
    });
  }

  private _getHtmlForWebview(): string {
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {
  color-scheme: dark;
  --bg: #111315;
  --panel: #191d20;
  --edge: #32383d;
  --text: #f3f5f6;
  --muted: #9aa4ab;
  --red: #ff3b30;
  --yellow: #ffd23f;
  --green: #33d17a;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--vscode-font-family, ui-sans-serif, system-ui, sans-serif);
  display: grid;
  grid-template-columns: 160px 1fr;
  gap: 24px;
  padding: 16px;
  min-height: 100vh;
  align-items: center;
}
.tower {
  width: 140px;
  aspect-ratio: 0.48;
  border: 1px solid var(--edge);
  background: #08090a;
  border-radius: 8px;
  padding: 15%;
  display: grid;
  gap: 8%;
  box-shadow: 0 24px 60px rgba(0,0,0,0.35);
}
.lamp {
  border-radius: 50%;
  background: #24282b;
  border: 1px solid #3b4248;
  box-shadow: inset 0 0 20px rgba(0,0,0,0.7);
  transition: background 120ms linear, box-shadow 120ms linear;
}
.lamp.red.on { background: var(--red); box-shadow: 0 0 36px rgba(255,59,48,.7), inset 0 0 12px rgba(255,255,255,.35); }
.lamp.yellow.on { background: var(--yellow); box-shadow: 0 0 36px rgba(255,210,63,.72), inset 0 0 12px rgba(255,255,255,.35); }
.lamp.green.on { background: var(--green); box-shadow: 0 0 36px rgba(51,209,122,.7), inset 0 0 12px rgba(255,255,255,.35); }
.meta { display: grid; gap: 16px; }
h1 { font-size: 32px; line-height: 1; }
.mode { color: var(--muted); font-size: 16px; }
.sessions { color: var(--muted); font-size: 13px; }
.controls { display: flex; flex-wrap: wrap; gap: 6px; }
button {
  border: 1px solid var(--edge); border-radius: 6px; padding: 6px 8px;
  color: var(--text); background: var(--panel); cursor: pointer;
  font: inherit; font-size: 12px;
}
button:hover { border-color: #68737b; }
</style>
</head>
<body>
<section class="tower" aria-label="status light">
  <div id="red" class="lamp red"></div>
  <div id="yellow" class="lamp yellow"></div>
  <div id="green" class="lamp green"></div>
</section>
<section class="meta">
  <div>
    <h1 id="title">ClaudeLight</h1>
    <p id="mode" class="mode">Mode: off</p>
    <p id="sessions" class="sessions"></p>
  </div>
  <div class="controls" id="controls"></div>
</section>
<script>
const vscode = acquireVsCodeApi();
const validModes = ["thinking","busy","green","success","error","alarm","off"];
const labels = {green:"Green",busy:"Busy",error:"Error",thinking:"Thinking",success:"Success",alarm:"Alarm",off:"Off"};
const lamps = {red:document.getElementById("red"),yellow:document.getElementById("yellow"),green:document.getElementById("green")};
let mode = "off";
let started = Date.now();

function setLamp(n,o){lamps[n].classList.toggle("on",Boolean(o))}
function renderStatic(r,y,g){setLamp("red",r);setLamp("yellow",y);setLamp("green",g)}
function setModeLabel(n){document.getElementById("title").textContent=labels[n]||n;document.getElementById("mode").textContent="Mode: "+n}
function applyMode(n){if(!validModes.includes(n))n="off";if(mode!==n){started=Date.now()}mode=n;setModeLabel(n)}
function renderSessions(s){if(!s||Object.keys(s).length===0){document.getElementById("sessions").textContent="";return}const c=Object.keys(s).length;const p=Object.entries(s).map(([id,m])=>id.slice(0,8)+": "+(labels[m]||m));document.getElementById("sessions").textContent=c+" session(s) — "+p.join(", ")}

function tick(){
  const t=Date.now()-started;
  if(mode==="off")renderStatic(false,false,false);
  else if(mode==="error")renderStatic(Math.floor(t/240)%2===0,false,false);
  else if(mode==="busy")renderStatic(false,Math.floor(t/650)%2===0,false);
  else if(mode==="green"||mode==="success")renderStatic(false,false,true);
  else if(mode==="alarm"){const on=Math.floor(t/260)%2===0;renderStatic(on,on,on)}
  else if(mode==="thinking"){const p=Math.floor((t%1050)/350);renderStatic(p===2,p===1,p===0)}
  requestAnimationFrame(tick);
}

for(const item of validModes){
  const btn=document.createElement("button");
  btn.textContent=labels[item]||item;
  btn.onclick=()=>vscode.postMessage({type:"setMode",mode:item});
  controls.appendChild(btn);
}

window.addEventListener("message",event=>{
  const msg=event.data;
  if(msg.type==="stateUpdate"){
    applyMode(msg.aggregate_mode||"off");
    renderSessions(msg.sessions||{});
  }
});

applyMode("off");
tick();
</script>
</body>
</html>`;
  }

  public dispose() {
    ClaudeLightPanel.currentPanel = undefined;
    this._panel.dispose();
    while (this._disposables.length) {
      const x = this._disposables.pop();
      if (x) x.dispose();
    }
  }
}