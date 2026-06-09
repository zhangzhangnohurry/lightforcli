# ClaudeLight

A lightweight desktop HUD for **Claude Code / VSCode Claude** sessions. It shows
small traffic-light status indicators for one or many AI coding sessions, using a
local state server plus Claude Code hooks and an optional VSCode extension.

The current desktop HUD is a frameless **glass-style** always-on-top widget. From
its gear menu you can switch between:

- **Single HUD**: one reflected session at a time.
- **Horizontal multi-session**: several sessions in one top strip.
- **Vertical multi-session**: compact stacked session pills.
- **Low-interference / diagnostic density**: choose how much metadata each pill shows.

## Architecture

```text
Claude Code CLI hooks ─┐
VSCode extension ──────┼──► claude_light_state.py (127.0.0.1:8765)
Manual CLI/API ────────┘              │
                                      │ HTTP API + SSE
                                      ▼
                         claude_light_app.py desktop HUD
                         browser fallback at http://127.0.0.1:8765
```

## Components

| Path | Purpose |
|---|---|
| `claude_light_state.py` | Local multi-session state server, HTTP API, SSE stream, persistent state. |
| `claude_light_hook.py` | Claude Code hook adapter; maps hook JSON events to HUD modes. |
| `claude_light_app.py` | PySide6 frameless desktop HUD with glass UI, tray icon, settings menu. |
| `claude-light-vscode/` | VSCode extension that reports editor/workspace Claude activity. |
| `install-claude-light.sh` | Installs Claude Code hook entries into `~/.claude/settings.json`. |

## Dependencies

### Runtime

- Python 3.10+
- PySide6, for the desktop HUD
- Claude Code CLI, if you want automatic Claude hook updates
- VSCode, if you want VSCode/editor workspace reporting

### Development / VSCode extension

- Node.js + npm
- TypeScript dependencies from `claude-light-vscode/package.json`
- Optional: `@vscode/vsce` for packaging a local `.vsix`

Recommended Python environment:

```bash
cd /home/ttelab/Documents/code/gry/greenredyellow
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install PySide6
```

If you use `uv`:

```bash
uv venv .venv
. .venv/bin/activate
uv pip install PySide6
```

## Quick Start

### Linux / macOS

#### 1. Install Claude Code hooks

```bash
cd /path/to/greenredyellow
bash install-claude-light.sh
```

### Windows

#### 1. Install Claude Code hooks

Double-click `install-claude-light.bat`, or run in PowerShell:

```powershell
cd C:\path\to\greenredyellow
powershell -ExecutionPolicy Bypass -File install-claude-light.ps1
```

This updates `~/.claude/settings.json` while preserving existing hooks. The hook
adapter sends events to the local server at `127.0.0.1:8765` by default.

### 2. Start the desktop HUD

Most of the time, this is enough:

```bash
cd /home/ttelab/Documents/code/gry/greenredyellow
. .venv/bin/activate
python claude_light_app.py
```

`claude_light_app.py` auto-starts `claude_light_state.py serve --no-open` when the
server is not already running.

If you want explicit separate processes:

```bash
# Terminal 1: state server only, no browser window
. .venv/bin/activate
python claude_light_state.py serve --no-open

# Terminal 2: desktop HUD only
. .venv/bin/activate
python claude_light_app.py --no-server
```

For a detached Linux/X11 session:

```bash
setsid .venv/bin/python claude_light_state.py serve --no-open >/tmp/claude-light-state.log 2>&1 < /dev/null &
setsid .venv/bin/python claude_light_app.py --no-server >/tmp/claude-light-app.log 2>&1 < /dev/null &
```

### 3. Optional browser fallback

```bash
python claude_light_state.py serve
```

This opens the fallback page at:

```text
http://127.0.0.1:8765
```

Use `--no-open` if you do not want a browser tab.

## VSCode Extension

Compile the extension:

```bash
cd /home/ttelab/Documents/code/gry/greenredyellow/claude-light-vscode
npm install
npm run compile
```

### Run in Extension Development Host

For development/testing, open this folder in VSCode and press `F5`, or launch an
Extension Development Host with this folder as the extension development path.

### Install locally as `.vsix`

`code --install-extension .` does **not** install an unpacked extension directory;
VSCode expects an extension id or a `.vsix` path. Package then install:

```bash
cd /home/ttelab/Documents/code/gry/greenredyellow/claude-light-vscode
npm install
npm run compile
npx @vscode/vsce package
code --install-extension claude-light-0.1.0.vsix
```

After installing/updating, reload VSCode:

```text
Developer: Reload Window
```

The extension contributes:

- Activity bar view: `ClaudeLight`
- Status bar indicator
- Command: `Set ClaudeLight Mode`
- Workspace publisher that reports Claude/Codex-like active tabs to the server

VSCode settings:

| Setting | Default | Description |
|---|---:|---|
| `claude-light.host` | `127.0.0.1` | State server host. |
| `claude-light.port` | `8765` | State server port. |

## HUD Settings

Click the small gear button on the desktop HUD.

Available settings:

| Menu | Options |
|---|---|
| Layout | Single HUD, horizontal multi-session, vertical multi-session |
| Density | Low-interference, diagnostic information |
| Background / opacity | Glass black, fog gray, blue-black, pure black, custom color; 100/94/85/70/55% opacity |
| Rotation period | Off, 2s, 4.2s, 8s, 15s |

Settings are saved to:

```text
~/.config/claude-light/hud.json
```

Example:

```json
{
  "background": "#0d1117",
  "opacity": 0.78,
  "rotation_ms": 2000,
  "layout_mode": "compact",
  "density": "compact"
}
```

Valid `layout_mode` values: `compact`, `horizontal`, `vertical`.
Valid `density` values: `compact`, `diagnostic`.

## Light Modes and Effects

Only these modes are valid server states:

| Mode | HUD effect | Meaning |
|---|---|---|
| `thinking` | Green → Yellow → Red chase | Claude/editor session is reasoning or active. |
| `busy` | Yellow blink | A tool/command is running. |
| `success` | Green solid | Session completed successfully. |
| `green` | Green solid | Manual/compat success-like state. |
| `error` | Red fast blink | Tool or session failed. |
| `alarm` | Red + Yellow + Green flash together | Needs user attention/approval/input. |
| `off` | All lamps off | No active tracked work for that session. |

Priority across sessions:

```text
alarm > error > busy > thinking > green > success > off
```

The HUD reflects the selected/highest-priority active session. Mouse wheel and
auto-rotation move the selected session; the lamps always reflect that selected
session, not unrelated carousel text.

## Claude Hook Mapping

| Claude Code hook event | Condition | Mode |
|---|---|---|
| `UserPromptSubmit` | New user prompt submitted | `thinking` |
| `PreToolUse` | Normal tool starts | `busy` |
| `PreToolUse` | User-input/approval tool | `alarm` |
| `PostToolUse` | Tool error | `error` |
| `PostToolUse` | Tool success | `thinking` |
| `Stop` | Completed/success | `success` |
| `Stop` | Error/failure/cancelled/aborted | `error` |
| `Stop` | Blocked/needs input | `alarm` |
| `Notification` | Explicit approval/user-attention text only | `alarm` |
| `Notification` | Non-attention notification | ignored |

The state server also scans Claude transcript tails for the marker
`[Request interrupted by user]`; if a user presses Esc during an active Claude
request, the session is reconciled to `off` without using a fixed timeout. This
avoids breaking legitimate long-running tasks.

## CLI and API

### Server commands

```bash
# Start server and open fallback web UI
python claude_light_state.py serve

# Start server without opening browser
python claude_light_state.py serve --no-open

# Quiet HTTP access logs
python claude_light_state.py serve --no-open --quiet

# Print persisted state
python claude_light_state.py status

# Send a manual mode
python claude_light_state.py send thinking --session-id manual:test

# End a session
python claude_light_state.py end --session-id manual:test
```

Global options:

```bash
python claude_light_state.py --host 127.0.0.1 --port 8765 --state ~/.local/state/claude-light/state.json serve --no-open
```

### HTTP API

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/state` | GET | Full aggregate snapshot. |
| `/api/sessions` | GET | Session map. |
| `/events` | GET | Server-Sent Events stream. |
| `/api/mode?session_id=...&mode=...` | GET | Update one session. |
| `/api/mode` | POST JSON | Update one session. Requires `session_id` and `mode`. |
| `/api/session/end` | POST JSON | Remove one session. Requires `session_id`. |

POST example:

```bash
curl -sS http://127.0.0.1:8765/api/mode \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "manual:test",
    "mode": "alarm",
    "name": "Manual Test",
    "metadata": {
      "source": "manual",
      "workspace": "greenredyellow",
      "session_short": "test"
    }
  }'
```

Anonymous/plain `/api/mode` writes are rejected to avoid dirty `default` session
data.

## Manual Testing / Verification

```bash
# Python syntax checks
python3 -m py_compile claude_light_app.py claude_light_state.py claude_light_hook.py

# Compile VSCode extension
cd claude-light-vscode && npm run compile

# Start server + HUD
cd /home/ttelab/Documents/code/gry/greenredyellow
. .venv/bin/activate
python claude_light_state.py serve --no-open &
python claude_light_app.py --no-server &

# Send modes
python claude_light_state.py send thinking --session-id s1
python claude_light_state.py send busy --session-id s2
python claude_light_state.py send alarm --session-id s3

# Check state
python claude_light_state.py status
curl -sS http://127.0.0.1:8765/api/state | python3 -m json.tool

# End sessions
python claude_light_state.py end --session-id s1
python claude_light_state.py end --session-id s2
python claude_light_state.py end --session-id s3

# Test hook mapping
printf '{"session_id":"test","hook_event_name":"PreToolUse","tool_name":"Bash","cwd":"/tmp/demo"}' | python3 claude_light_hook.py
printf '{"session_id":"test","hook_event_name":"Notification","message":"requires approval: Do you want to proceed?"}' | python3 claude_light_hook.py
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_LIGHT_HOST` | `127.0.0.1` | Host used by app/hook to contact the state server. |
| `CLAUDE_LIGHT_PORT` | `8765` | Port used by app/hook to contact the state server. |
| `CLAUDE_LIGHT_LOG` | Linux: `~/.local/state/claude-light/hook.log`; Win: `%LOCALAPPDATA%\claude-light\hook.log` | Hook debug log path. |
| `CLAUDE_LIGHT_CONFIG` | Linux: `~/.config/claude-light/hud.json`; Win: `%APPDATA%\claude-light\hud.json` | Desktop HUD settings path. |
| `XDG_STATE_HOME` | `~/.local/state` (Linux only) | Base path for persisted server state. Windows uses `%LOCALAPPDATA%` instead. |

## State Files

| Linux/macOS | Windows | Purpose |
|---|---|---|
| `~/.local/state/claude-light/state.json` | `%LOCALAPPDATA%\claude-light\state.json` | Persisted sessions and aggregate state. |
| `~/.local/state/claude-light/hook.log` | `%LOCALAPPDATA%\claude-light\hook.log` | Hook adapter log. |
| `~/.config/claude-light/hud.json` | `%APPDATA%\claude-light\hud.json` | Desktop HUD preferences. |

Sessions expire after 30 minutes of inactivity. Expired sessions are removed by
the server maintenance loop.

## Cross-Platform Notes

- Linux/X11: tested with PySide6 frameless always-on-top HUD and system tray.
- Windows 11: tested with PySide6; tray and always-on-top work; install via `install-claude-light.ps1`.
- macOS: PySide6 should run, but tray/always-on-top behavior may vary.
- Browser fallback works anywhere the local Python server can run.
- On Windows, use `python` instead of `python3`; state/config paths use `%LOCALAPPDATA%` / `%APPDATA%`.

## Troubleshooting

### `code --install-extension .` says extension not found

Use a `.vsix` package instead:

```bash
cd claude-light-vscode
npx @vscode/vsce package
code --install-extension claude-light-0.1.0.vsix
```

### Desktop HUD does not show

Check server and app processes:

```bash
pgrep -af 'claude_light_app.py|claude_light_state.py'
cat /tmp/claude-light-app.log
cat /tmp/claude-light-state.log
```

Start explicitly:

```bash
. .venv/bin/activate
python claude_light_state.py serve --no-open
python claude_light_app.py --no-server
```

### Claude/VScode state does not update

- Confirm the server is reachable: `curl http://127.0.0.1:8765/api/state`.
- Re-run `bash install-claude-light.sh` after moving this repository.
- Reload VSCode after installing/updating the extension.
- Check `~/.local/state/claude-light/hook.log` for hook errors.

## Uninstall

1. Stop `claude_light_app.py` and `claude_light_state.py`.
2. Remove Claude hook entries pointing to `claude_light_hook.py` from
   `~/.claude/settings.json`.
3. Uninstall the VSCode extension if installed.
4. Optionally remove state/config files:

```bash
rm -rf ~/.local/state/claude-light ~/.config/claude-light
```

## License

MIT
