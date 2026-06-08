#!/usr/bin/env bash
# Install ClaudeLight hooks into ~/.claude/settings.json
# Preserves existing hooks (e.g., oh-my-claudecode).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="${ROOT}/claude_light_hook.py"
SETTINGS_DIR="${HOME}/.claude"
SETTINGS_JSON="${SETTINGS_DIR}/settings.json"

mkdir -p "$SETTINGS_DIR"
chmod +x "$HOOK" "${ROOT}/claude_light_state.py" "${ROOT}/claude_light_app.py"

python3 - "$SETTINGS_JSON" "$HOOK" <<'PY'
import json
import shutil
import sys
import time
from pathlib import Path

settings_path = Path(sys.argv[1]).expanduser()
hook_path = Path(sys.argv[2]).resolve()
command = f"python3 {hook_path}"
events = [
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "Notification",
]

if settings_path.exists():
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    backup = settings_path.with_suffix(f".json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(settings_path, backup)
else:
    data = {}
    backup = None

hooks = data.setdefault("hooks", {})
for event in events:
    entries = hooks.setdefault(event, [])
    found = False
    for entry in entries:
        for item in entry.get("hooks", []):
            if item.get("command") == command:
                found = True
                break
        if found:
            break
    if not found:
        entries.append({
            "matcher": "",
            "hooks": [{"type": "command", "command": command, "timeout": 2}]
        })

settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Installed ClaudeLight hook: {command}")
if backup:
    print(f"Backup: {backup}")
print(f"Updated: {settings_path}")
PY

echo
echo "ClaudeLight installed! Next steps:"
echo
echo "1. Start the state server:"
echo "   python3 ${ROOT}/claude_light_state.py serve"
echo
echo "2. Or start the desktop app (auto-starts server):"
echo "   python3 ${ROOT}/claude_light_app.py"
echo
echo "3. For VSCode, install the extension:"
echo "   cd ${ROOT}/claude-light-vscode && npm install && npm run compile"
echo "   Then: code --install-extension ${ROOT}/claude-light-vscode"
echo
echo "4. Verify hook:"
echo "   printf '{\"session_id\":\"test\",\"hook_event_name\":\"PreToolUse\",\"tool_name\":\"Bash\"}' | python3 ${HOOK}"
echo
echo "Log: ~/.local/state/claude-light/hook.log"