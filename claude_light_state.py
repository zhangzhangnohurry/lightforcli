#!/usr/bin/env python3
"""ClaudeLight state backend — multi-session status light server.

Runs a localhost HTTP server that tracks per-session Claude Code states,
computes an aggregate mode (most urgent across all sessions), and serves
real-time updates via SSE. UI clients (PySide6 app, VSCode extension)
connect here.

Usage:
    python3 claude_light_state.py serve          # start server
    python3 claude_light_state.py send thinking  # send manual CLI mode (session_id = manual:cli)
    python3 claude_light_state.py status         # print current state
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
from datetime import datetime
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


APP_NAME = "ClaudeLight"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
STATE_PATH = STATE_DIR / "claude-light" / "state.json"
SESSION_EXPIRE_SECONDS = 30 * 60  # 30 minutes

VALID_MODES = {
    "off", "thinking", "busy", "green", "success", "error", "alarm",
}

MODE_LABELS = {
    "off": "Off",
    "thinking": "Thinking",
    "busy": "Busy",
    "green": "Green",
    "success": "Success",
    "error": "Error",
    "alarm": "Alarm",
}

# Priority ranking: higher number = more urgent
MODE_PRIORITY = {
    "alarm": 6, "error": 5, "busy": 4, "thinking": 3,
    "green": 2, "success": 1, "off": 0,
}

SESSION_METADATA_FIELDS = {
    "source", "workspace", "workspace_name", "project_name", "cwd",
    "directory", "path", "summary", "active_editor", "uri", "session_short",
}

ACTIVE_MODES = {"thinking", "busy", "alarm"}
INTERRUPT_MARKER = "[Request interrupted by user]"


def parse_iso_timestamp(value: object) -> float:
    text = str(value or "")
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def tail_text(path: Path, max_bytes: int = 256 * 1024) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as fp:
            if size > max_bytes:
                fp.seek(size - max_bytes)
                fp.readline()
            return fp.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def transcript_has_interrupt_after(path_value: object, since: float) -> bool:
    path_text = str(path_value or "")
    if not path_text or path_text.startswith("file:"):
        return False
    path = Path(path_text).expanduser()
    if not path.exists():
        return False
    for line in tail_text(path).splitlines():
        if INTERRUPT_MARKER not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            return True
        if parse_iso_timestamp(entry.get("timestamp")) >= since:
            return True
    return False


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ClaudeLight</title>
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
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  min-height: 100%;
  display: grid;
  place-items: center;
  background: var(--bg);
  color: var(--text);
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
main {
  width: min(92vw, 560px);
  display: grid;
  grid-template-columns: minmax(150px, 190px) 1fr;
  gap: 28px;
  align-items: center;
}
.tower {
  width: 100%;
  aspect-ratio: 0.48;
  border: 1px solid var(--edge);
  background: #08090a;
  border-radius: 8px;
  padding: 15%;
  display: grid;
  gap: 8%;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.35);
}
.lamp {
  border-radius: 50%;
  background: #24282b;
  border: 1px solid #3b4248;
  box-shadow: inset 0 0 20px rgba(0, 0, 0, 0.7);
  transition: background 120ms linear, box-shadow 120ms linear, opacity 120ms linear;
}
.lamp.red.on { background: var(--red); box-shadow: 0 0 36px rgba(255,59,48,.7), inset 0 0 12px rgba(255,255,255,.35); }
.lamp.yellow.on { background: var(--yellow); box-shadow: 0 0 36px rgba(255,210,63,.72), inset 0 0 12px rgba(255,255,255,.35); }
.lamp.green.on { background: var(--green); box-shadow: 0 0 36px rgba(51,209,122,.7), inset 0 0 12px rgba(255,255,255,.35); }
.meta { min-width: 0; display: grid; gap: 18px; }
h1 { margin: 0; font-size: clamp(28px, 6vw, 54px); line-height: 1; letter-spacing: 0; }
.mode { margin: 0; color: var(--muted); font-size: 18px; }
.sessions { margin: 0; color: var(--muted); font-size: 14px; }
.controls { display: flex; flex-wrap: wrap; gap: 8px; }
button {
  border: 1px solid var(--edge); border-radius: 6px; padding: 8px 10px;
  color: var(--text); background: var(--panel); cursor: pointer; font: inherit;
}
button:hover { border-color: #68737b; }
.log {
  margin: 0; padding: 12px; min-height: 116px; max-height: 160px;
  overflow: auto; border: 1px solid var(--edge); border-radius: 8px;
  background: var(--panel); color: var(--muted);
  font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
@media (max-width: 620px) {
  body { place-items: start center; padding: 22px 0; }
  main { grid-template-columns: 1fr; width: min(88vw, 360px); }
  .tower { width: min(58vw, 210px); justify-self: center; }
}
</style>
</head>
<body>
<main>
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
    <pre id="log" class="log"></pre>
  </section>
</main>
<script>
const validModes = ["thinking","busy","green","success","error","alarm","off"];
const labels = {
  green:"Green",busy:"Busy",error:"Error",
  thinking:"Thinking",success:"Success",alarm:"Alarm",off:"Off"
};
const lamps = { red: document.getElementById("red"), yellow: document.getElementById("yellow"), green: document.getElementById("green") };
const logEl = document.getElementById("log");
const sessionsEl = document.getElementById("sessions");
let mode = "off";
let started = Date.now();
let events = [];

function pushLog(next) {
  const stamp = new Date().toLocaleTimeString();
  events.unshift(stamp + "  " + next);
  events = events.slice(0, 8);
  logEl.textContent = events.join("\n");
}

function setLamp(name, on) { lamps[name].classList.toggle("on", Boolean(on)); }

function renderStatic(r, y, g) { setLamp("red", r); setLamp("yellow", y); setLamp("green", g); }

function setModeLabel(next) {
  document.body.dataset.mode = next;
  document.title = (labels[next] || next) + " - ClaudeLight";
  document.getElementById("title").textContent = labels[next] || next;
  document.getElementById("mode").textContent = "Mode: " + next;
}

function applyMode(next) {
  if (!validModes.includes(next)) next = "off";
  if (mode !== next) { started = Date.now(); pushLog(next); }
  mode = next;
  setModeLabel(mode);
}

function renderSessions(sessionData) {
  if (!sessionData || Object.keys(sessionData).length === 0) {
    sessionsEl.textContent = "";
    return;
  }
  const count = Object.keys(sessionData).length;
  const parts = Object.entries(sessionData).map(([id, m]) => id.slice(0,8) + ": " + (labels[m] || m));
  sessionsEl.textContent = count + " session(s) — " + parts.join(", ");
}

function tick() {
  const t = Date.now() - started;
  if (mode === "off") renderStatic(false, false, false);
  else if (mode === "error") renderStatic(Math.floor(t/240)%2===0, false, false);
  else if (mode === "busy") renderStatic(false, Math.floor(t/650)%2===0, false);
  else if (mode === "green" || mode === "success") renderStatic(false, false, true);
  else if (mode === "alarm") {
    const on = Math.floor(t/260)%2===0;
    renderStatic(on, on, on);
  }
  else if (mode === "thinking") {
    const phase = Math.floor((t%1050)/350);
    renderStatic(phase===2, phase===1, phase===0);
  }
  requestAnimationFrame(tick);
}

fetch("/api/state")
  .then(r => r.json())
  .then(data => {
    applyMode(data.aggregate_mode || "off");
    renderSessions(data.sessions || {});
  })
  .catch(() => applyMode("off"));

const stream = new EventSource("/events");
stream.onmessage = (event) => {
  try {
    const data = JSON.parse(event.data);
    applyMode(data.aggregate_mode || "off");
    renderSessions(data.sessions || {});
  } catch (_) {}
};

tick();
</script>
</body>
</html>
"""


class SessionState:
    """Per-session tracking with expiry."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, object]] = {}
        self.lock = threading.RLock()
        self.subscribers: list[queue.Queue[dict[str, object]]] = []
        self.aggregate_mode: str = "off"
        self.updated_at: float = time.time()

    def compute_aggregate(self) -> str:
        active = {}
        now = time.time()
        with self.lock:
            for sid, info in list(self.sessions.items()):
                if now - float(info.get("updated_at", 0)) < SESSION_EXPIRE_SECONDS:
                    active[sid] = info
                else:
                    del self.sessions[sid]
        if not active:
            return "off"
        best = max(active.values(), key=lambda s: MODE_PRIORITY.get(str(s.get("mode", "off")), 0))
        return str(best.get("mode", "off"))

    def set_session_mode(
        self,
        session_id: str,
        mode: str,
        name: str = "",
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        mode = mode.strip().lower()
        if mode not in VALID_MODES:
            raise ValueError(f"unknown mode: {mode}")
        now = time.time()
        with self.lock:
            previous = self.sessions.get(session_id, {})
            next_info = dict(previous)
            display_name = name.strip() or str(previous.get("name", "")) or session_id[:12]
            next_info.update({
                "mode": mode,
                "name": display_name,
                "updated_at": now,
            })
            for key, value in (metadata or {}).items():
                if key in SESSION_METADATA_FIELDS and value not in (None, ""):
                    next_info[key] = value
            self.sessions[session_id] = next_info
            self.updated_at = now
            self.aggregate_mode = self.compute_aggregate()
            snapshot = self.snapshot()
            subscribers = list(self.subscribers)
        for sub in subscribers:
            try:
                sub.put_nowait(snapshot)
            except queue.Full:
                pass
        return snapshot

    def end_session(self, session_id: str) -> dict[str, object]:
        with self.lock:
            self.sessions.pop(session_id, None)
            self.updated_at = time.time()
            self.aggregate_mode = self.compute_aggregate()
            snapshot = self.snapshot()
            subscribers = list(self.subscribers)
        for sub in subscribers:
            try:
                sub.put_nowait(snapshot)
            except queue.Full:
                pass
        return snapshot

    def reconcile_interrupted_sessions(self) -> dict[str, object] | None:
        changed = False
        with self.lock:
            for sid, info in list(self.sessions.items()):
                if str(info.get("source", "")) != "claude":
                    continue
                if str(info.get("mode", "off")) not in ACTIVE_MODES:
                    continue
                updated_at = float(info.get("updated_at", 0))
                if not transcript_has_interrupt_after(info.get("uri"), updated_at):
                    continue
                next_info = dict(info)
                next_info["mode"] = "off"
                next_info["interrupted"] = True
                next_info["interrupt_reason"] = "user escaped request"
                next_info["updated_at"] = time.time()
                self.sessions[sid] = next_info
                changed = True
            if not changed:
                return None
            self.updated_at = time.time()
            self.aggregate_mode = self.compute_aggregate()
            snapshot = self.snapshot()
            subscribers = list(self.subscribers)
        for sub in subscribers:
            try:
                sub.put_nowait(snapshot)
            except queue.Full:
                pass
        return snapshot

    def snapshot(self) -> dict[str, object]:
        aggregate = self.compute_aggregate()
        with self.lock:
            sessions = dict(self.sessions)
        return {
            "aggregate_mode": aggregate,
            "sessions": sessions,
            "updated_at": self.updated_at,
        }

    def subscribe(self) -> queue.Queue[dict[str, object]]:
        sub: queue.Queue[dict[str, object]] = queue.Queue(maxsize=8)
        with self.lock:
            self.subscribers.append(sub)
            sub.put_nowait(self.snapshot())
        return sub

    def unsubscribe(self, sub: queue.Queue[dict[str, object]]) -> None:
        with self.lock:
            if sub in self.subscribers:
                self.subscribers.remove(sub)


class PersistentState:
    """Loads/saves aggregate state to disk for persistence across restarts."""

    def __init__(self, state_path: Path, session_state: SessionState) -> None:
        self.state_path = state_path
        self.session_state = session_state

    def load(self) -> None:
        try:
            data = json.loads(self.state_path.read_text())
            sessions = data.get("sessions", {})
            for sid, info in sessions.items():
                mode = str(info.get("mode", "off")).lower()
                if mode in VALID_MODES:
                    self.session_state.sessions[sid] = info
            self.session_state.aggregate_mode = self.session_state.compute_aggregate()
        except FileNotFoundError:
            self.save()
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot = self.session_state.snapshot()
        self.state_path.write_text(
            json.dumps(snapshot, ensure_ascii=False), encoding="utf-8"
        )


class ClaudeLightHandler(BaseHTTPRequestHandler):
    server_version = "ClaudeLight/1.0"

    @property
    def app_state(self) -> SessionState:
        return self.server.app_state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        if getattr(self.server, "quiet", False):  # type: ignore[attr-defined]
            return
        super().log_message(fmt, *args)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.write_bytes(HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/state":
            self.write_json(self.app_state.snapshot())
            return
        if parsed.path == "/api/sessions":
            with self.app_state.lock:
                sessions = dict(self.app_state.sessions)
            self.write_json({"sessions": sessions})
            return
        if parsed.path == "/api/mode":
            params = urllib.parse.parse_qs(parsed.query)
            mode = params.get("mode", [""])[0]
            session_id = params.get("session_id", [""])[0]
            if not session_id:
                self.send_error(HTTPStatus.BAD_REQUEST, "session_id is required")
                return
            name = params.get("name", [""])[0]
            metadata = {
                key: values[0]
                for key, values in params.items()
                if key in SESSION_METADATA_FIELDS and values
            }
            self.handle_mode_update(session_id, mode, name, metadata)
            return
        if parsed.path == "/events":
            self.handle_events()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in ("/api/mode", "/api/session/end"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(min(length, 8192)).decode("utf-8", errors="replace")

        if parsed.path == "/api/session/end":
            try:
                data = json.loads(body) if body.strip() else {}
            except json.JSONDecodeError:
                data = {}
            session_id = str(data.get("session_id", ""))
            if not session_id:
                self.send_error(HTTPStatus.BAD_REQUEST, "session_id is required")
                return
            snapshot = self.app_state.end_session(session_id)
            self.write_json(snapshot)
            return

        # /api/mode
        try:
            data = json.loads(body) if body.strip() else {}
        except json.JSONDecodeError:
            data = {}
        # Support both JSON body {"session_id":..., "mode":...} and plain mode string
        if data and "mode" in data:
            session_id = str(data.get("session_id", ""))
            if not session_id:
                self.send_error(HTTPStatus.BAD_REQUEST, "session_id is required")
                return
            mode = str(data.get("mode", ""))
            name = str(data.get("name", ""))
            metadata = self.extract_metadata(data)
        else:
            self.send_error(HTTPStatus.BAD_REQUEST, "JSON body with session_id and mode is required")
            return
        self.handle_mode_update(session_id, mode, name, metadata)

    def extract_metadata(self, data: dict[str, object]) -> dict[str, object]:
        metadata: dict[str, object] = {}
        nested = data.get("metadata")
        if isinstance(nested, dict):
            metadata.update({
                str(key): value
                for key, value in nested.items()
                if str(key) in SESSION_METADATA_FIELDS
            })
        metadata.update({
            key: data[key]
            for key in SESSION_METADATA_FIELDS
            if key in data
        })
        return metadata

    def handle_mode_update(
        self,
        session_id: str,
        mode: str,
        name: str = "",
        metadata: dict[str, object] | None = None,
    ) -> None:
        try:
            snapshot = self.app_state.set_session_mode(session_id, mode, name, metadata)
        except ValueError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self.write_json(snapshot)

    def handle_events(self) -> None:
        subscriber = self.app_state.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                snapshot = subscriber.get(timeout=25)
                payload = json.dumps(snapshot)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError, queue.Empty):
            pass
        finally:
            self.app_state.unsubscribe(subscriber)

    def write_json(self, payload: dict[str, object]) -> None:
        self.write_bytes(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def write_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def normalize_mode(mode: str) -> str:
    mode = mode.strip().lower()
    if mode not in VALID_MODES:
        choices = ", ".join(sorted(VALID_MODES))
        raise SystemExit(f"Unknown mode: {mode}\nAvailable modes: {choices}")
    return mode


def server_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def send_mode(mode: str, host: str, port: int, session_id: str = "manual:cli") -> None:
    mode = normalize_mode(mode)
    url = f"{server_url(host, port)}/api/mode"
    payload = json.dumps({
        "session_id": session_id,
        "mode": mode,
        "name": "Manual CLI",
        "metadata": {"source": "manual", "workspace": "CLI", "session_short": "manual"},
    }).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=0.6) as response:
            if response.status != HTTPStatus.OK:
                raise SystemExit(f"Server returned HTTP {response.status}")
        print(f"sent {mode} (session: {session_id})")
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        # Server not running — save to state file for next startup
        state_path = STATE_PATH
        state_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "aggregate_mode": mode,
            "sessions": {session_id: {
                "mode": mode,
                "name": "Manual CLI",
                "updated_at": time.time(),
                "source": "manual",
                "workspace": "CLI",
                "session_short": "manual",
            }},
            "updated_at": time.time(),
        }
        state_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        print(f"saved {mode} (session: {session_id}) — server not running")


def open_browser(url: str) -> None:
    if sys.platform.startswith("linux"):
        cmd = ["xdg-open", url]
    elif sys.platform == "darwin":
        cmd = ["open", url]
    elif os.name == "nt":
        cmd = ["cmd", "/c", "start", "", url]
    else:
        print(f"Open {url}")
        return
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        print(f"Open {url}")


def serve(host: str, port: int, state_path: Path, open_ui: bool, quiet: bool) -> None:
    session_state = SessionState()
    persistent = PersistentState(state_path, session_state)
    persistent.load()

    class ReuseAddrHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = True

    httpd = ReuseAddrHTTPServer((host, port), ClaudeLightHandler)
    httpd.app_state = session_state  # type: ignore[attr-defined]
    httpd.quiet = quiet  # type: ignore[attr-defined]
    httpd.persistent = persistent  # type: ignore[attr-defined]

    # Periodic save + session expiry
    def maintenance() -> None:
        while True:
            time.sleep(10)
            session_state.reconcile_interrupted_sessions()
            session_state.compute_aggregate()  # expires stale sessions
            persistent.save()

    maint_thread = threading.Thread(target=maintenance, daemon=True)
    maint_thread.start()

    url = server_url(host, port)
    print(f"{APP_NAME} serving at {url}")
    print(f"State file: {state_path}")
    if open_ui:
        open_browser(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        persistent.save()
        httpd.server_close()


def print_status(state_path: Path) -> None:
    session_state = SessionState()
    persistent = PersistentState(state_path, session_state)
    persistent.load()
    snapshot = session_state.snapshot()
    agg = snapshot.get("aggregate_mode", "off")
    sessions = snapshot.get("sessions", {})
    updated = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.localtime(float(snapshot.get("updated_at", 0)))
    )
    print(f"aggregate={agg} sessions={len(sessions)} updated_at={updated} state={state_path}")
    for sid, info in sessions.items():
        print(f"  {sid[:12]}: {info.get('mode', '?')}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    parser.add_argument("--state", default=str(STATE_PATH), help="state JSON path")
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="run the state server + web UI")
    serve_parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    serve_parser.add_argument("--quiet", action="store_true", help="suppress HTTP access logs")

    send_parser = subparsers.add_parser("send", help="send one mode to the running server")
    send_parser.add_argument("mode")
    send_parser.add_argument("--session-id", default="manual:cli", help="session identifier")

    status_parser = subparsers.add_parser("status", help="print current state")
    status_parser.set_defaults(command="status")

    end_parser = subparsers.add_parser("end", help="end a session")
    end_parser.add_argument("--session-id", required=True, help="session identifier")
    end_parser.set_defaults(command="end")

    return parser


def end_session(host: str, port: int, session_id: str) -> None:
    url = f"{server_url(host, port)}/api/session/end"
    payload = json.dumps({"session_id": session_id}).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=0.6):
            print(f"ended session {session_id}")
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        print(f"could not reach server to end session {session_id}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    state_path = Path(args.state).expanduser()

    if args.command == "serve":
        serve(args.host, args.port, state_path, open_ui=not args.no_open, quiet=args.quiet)
        return 0
    if args.command == "send":
        send_mode(args.mode, args.host, args.port, session_id=args.session_id)
        return 0
    if args.command == "status":
        print_status(state_path)
        return 0
    if args.command == "end":
        end_session(args.host, args.port, args.session_id)
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
