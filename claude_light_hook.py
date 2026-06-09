#!/usr/bin/env python3
"""Claude Code native-hook adapter for ClaudeLight.

Reads Claude Code hook JSON from stdin, maps lifecycle events to light
modes, and sends them to the local state server with session_id for
multi-session tracking. Emits no stdout to avoid interfering with
Claude Code's hook JSON contracts.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
STATE_SCRIPT = ROOT / "claude_light_state.py"
if os.name == "nt":
    _default_log = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "claude-light" / "hook.log"
else:
    _default_log = Path.home() / ".local" / "state" / "claude-light" / "hook.log"
LOG_PATH = Path(os.environ.get("CLAUDE_LIGHT_LOG", str(_default_log)))
DEFAULT_HOST = os.environ.get("CLAUDE_LIGHT_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("CLAUDE_LIGHT_PORT", "8765"))


def log(message: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fp:
            fp.write(message.rstrip() + "\n")
    except OSError:
        pass


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        log("invalid json payload")
        return {}
    return value if isinstance(value, dict) else {}


def text_value(value: Any) -> str:
    return str(value or "").strip()


def hook_event(payload: dict[str, Any]) -> str:
    return text_value(
        payload.get("hook_event_name")
        or payload.get("event")
        or payload.get("hook")
        or payload.get("type")
    )


def session_id(payload: dict[str, Any]) -> str:
    sid = text_value(payload.get("session_id"))
    if sid:
        return sid
    transcript = text_value(payload.get("transcript_path") or payload.get("transcript"))
    if transcript:
        return f"claude:{Path(transcript).expanduser().stem}"
    cwd = text_value(
        payload.get("cwd")
        or payload.get("workspace")
        or payload.get("workspace_dir")
        or payload.get("project_dir")
        or payload.get("root")
    )
    if cwd:
        return f"claude:{basename_for_path(cwd)}"
    return "claude:unknown"


def compact_session_id(value: str) -> str:
    value = value.strip()
    if not value:
        return "default"
    if value == "default":
        return "default"
    if len(value) <= 10:
        return value
    return f"{value[:4]}…{value[-4:]}"


def session_name(payload: dict[str, Any]) -> str:
    """Return a human-friendly name for the session when hook metadata allows it."""
    for key in ("session_name", "workspace_name", "project_name"):
        value = text_value(payload.get(key))
        if value:
            return value

    for key in ("cwd", "workspace", "workspace_dir", "project_dir", "root"):
        value = text_value(payload.get(key))
        if value:
            return Path(value).expanduser().resolve().name or value

    transcript = text_value(payload.get("transcript_path") or payload.get("transcript"))
    if transcript:
        parent = Path(transcript).expanduser().parent
        if parent.name:
            return parent.name

    return session_id(payload)[:12]


def basename_for_path(value: str) -> str:
    if not value:
        return ""
    return Path(value).expanduser().resolve().name or value


def session_metadata(payload: dict[str, Any]) -> dict[str, str]:
    """Extract descriptive metadata from Claude hook payloads without inventing it."""
    cwd = text_value(
        payload.get("cwd")
        or payload.get("workspace")
        or payload.get("workspace_dir")
        or payload.get("project_dir")
        or payload.get("root")
    )
    workspace = text_value(
        payload.get("workspace_name")
        or payload.get("project_name")
        or basename_for_path(cwd)
    )
    transcript = text_value(payload.get("transcript_path") or payload.get("transcript"))
    summary = text_value(payload.get("summary") or payload.get("title"))
    sid = session_id(payload)
    metadata = {
        "source": "claude",
        "workspace": workspace,
        "workspace_name": workspace,
        "project_name": text_value(payload.get("project_name")),
        "cwd": cwd,
        "directory": basename_for_path(cwd),
        "path": cwd,
        "summary": summary,
        "uri": transcript,
        "session_short": compact_session_id(sid),
    }
    return {key: value for key, value in metadata.items() if value}


def tool_name(payload: dict[str, Any]) -> str:
    return text_value(payload.get("tool_name") or payload.get("tool") or payload.get("name"))


def stop_reason(payload: dict[str, Any]) -> str:
    for key in ("stop_reason", "reason", "status", "stop_status"):
        value = text_value(payload.get(key)).lower()
        if value:
            return value
    return ""


def has_tool_error(payload: dict[str, Any]) -> bool:
    if payload.get("is_error") is True or payload.get("error") is not None:
        return True
    output = payload.get("tool_output")
    if isinstance(output, dict):
        return output.get("is_error") is True or output.get("error") is not None
    if isinstance(output, str):
        # Claude Code PostToolUse may put error text in tool_output
        return False
    return False


def is_user_input_tool(name: str) -> bool:
    name_lower = name.lower()
    return name_lower in {
        "askuserquestion", "ask_question", "request_user_input",
        "ask_user", "user_input",
    }


PERMISSION_TOOLS = {"bash", "edit", "write", "proxy_bash", "proxy_edit", "proxy_write"}
ATTENTION_TERMS = (
    "requires approval", "requires permission", "needs approval", "needs permission",
    "do you want to proceed", "awaiting approval", "waiting for approval",
    "awaiting user", "waiting for user", "needs input", "request_user_input",
    "askuserquestion", "ask_user_question",
)


def payload_mentions_user_attention(value: Any) -> bool:
    """Detect explicit user-attention text in hook payloads."""
    if isinstance(value, dict):
        return any(
            payload_mentions_user_attention(key) or payload_mentions_user_attention(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(payload_mentions_user_attention(child) for child in value)
    if isinstance(value, str):
        text = value.lower()
        return any(term in text for term in ATTENTION_TERMS)
    return False


def parse_timestamp(value: Any) -> float:
    text = text_value(value)
    if not text:
        return time.time()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


def transcript_path(payload: dict[str, Any]) -> Path | None:
    value = text_value(payload.get("transcript_path") or payload.get("transcript"))
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.exists() else None


def tail_text(path: Path, max_bytes: int = 512 * 1024) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as fp:
            if size > max_bytes:
                fp.seek(size - max_bytes)
                fp.readline()
            return fp.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def iter_content_blocks(entry: dict[str, Any]) -> list[dict[str, Any]]:
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    return []


def transcript_has_pending_permission(payload: dict[str, Any], min_age_seconds: float = 2.5) -> bool:
    """Return true when the Claude transcript shows an approval-gated tool awaiting a result.

    Claude Code does not emit a dedicated "approval prompt opened" hook in all
    versions. The transcript records the pending tool_use before the interactive
    approval UI, and the matching tool_result only appears after the user chooses.
    We use that gap to turn Notification into alarm instead of downgrading it to
    thinking while the terminal is waiting for input.
    """
    path = transcript_path(payload)
    if path is None:
        return False
    pending: dict[str, tuple[str, float]] = {}
    for line in tail_text(path).splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        ts = parse_timestamp(entry.get("timestamp"))
        for block in iter_content_blocks(entry):
            block_type = text_value(block.get("type"))
            if block_type == "tool_use":
                tool = text_value(block.get("name"))
                tool_id = text_value(block.get("id"))
                if tool_id and tool.lower() in PERMISSION_TOOLS:
                    pending[tool_id] = (tool, ts)
            elif block_type == "tool_result":
                tool_id = text_value(block.get("tool_use_id"))
                if tool_id:
                    pending.pop(tool_id, None)
    now = time.time()
    return any(now - ts >= min_age_seconds for _, ts in pending.values())


def mode_for_payload(payload: dict[str, Any]) -> str | None:
    event = hook_event(payload)
    event_lower = event.lower()

    # UserPromptSubmit: a newly active Claude turn/session should appear immediately.
    if event == "UserPromptSubmit" or event_lower == "user_prompt_submit":
        return "thinking"

    # PreToolUse: agent is about to use a tool
    if event == "PreToolUse" or event_lower == "pre_tool_use":
        name = tool_name(payload)
        if is_user_input_tool(name):
            return "alarm"
        return "busy"

    # PostToolUse: a tool finished, but Claude may still be generating.
    # Do not mark the session green here; green/success belongs to final Stop.
    if event == "PostToolUse" or event_lower == "post_tool_use":
        if has_tool_error(payload):
            return "error"
        return "thinking"

    # Stop: session ended
    if event == "Stop" or event_lower == "stop":
        reason = stop_reason(payload)
        if reason in {
            "error", "failed", "failure", "aborted", "abort",
            "cancelled", "canceled", "timeout", "tool_error",
        }:
            return "error"
        if reason in {"blocked", "needs_input", "awaiting_user", "needs_approval"}:
            return "alarm"
        return "success"

    # Notification is not a reliable lifecycle state: Claude can emit it after
    # Stop, which would incorrectly resurrect completed sessions as thinking.
    # Use it only for explicit user-attention/approval alarms.
    if event == "Notification" or event_lower == "notification":
        if payload_mentions_user_attention(payload) or transcript_has_pending_permission(payload):
            return "alarm"
        return None

    return None


def send_to_server(
    session_id: str,
    mode: str,
    name: str = "",
    metadata: dict[str, str] | None = None,
) -> None:
    host = DEFAULT_HOST
    port = DEFAULT_PORT
    url = f"http://{host}:{port}/api/mode"
    payload = json.dumps(
        {"session_id": session_id, "mode": mode, "name": name, "metadata": metadata or {}}
    ).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=1.2):
            pass
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        # Server not running — try to save via state script CLI
        try:
            import subprocess
            subprocess.run(
                [sys.executable or "python3", str(STATE_SCRIPT), "send", mode,
                 "--session-id", session_id],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=2, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            log(f"fallback save failed session={session_id} mode={mode}")


def end_session_on_server(session_id: str) -> None:
    host = DEFAULT_HOST
    port = DEFAULT_PORT
    url = f"http://{host}:{port}/api/session/end"
    payload = json.dumps({"session_id": session_id}).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=1.2):
            pass
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        pass


def main() -> int:
    payload = read_payload()
    sid = session_id(payload)
    name = session_name(payload)
    metadata = session_metadata(payload)
    event = hook_event(payload) or "?"
    mode = mode_for_payload(payload)

    if mode:
        send_to_server(sid, mode, name, metadata)
        log(
            f"event={event} session={sid[:12]} name={name} "
            f"dir={metadata.get('directory', '-')} tool={tool_name(payload) or '-'} mode={mode}"
        )

        # Keep Stop states visible/persistent. Completed sessions expire by TTL
        # in the state server; deleting here made startup lose completed sessions.
    else:
        log(f"event={event} session={sid[:12]} ignored")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
