#!/usr/bin/env python3
"""Build ClaudeLight into standalone executables using PyInstaller.

Produces two exe files:
  - claude-light-app.exe  (desktop HUD + embedded state server, ~100MB+)
  - claude-light-hook.exe (stdin→HTTP relay, ~15-20MB, no PySide6)

Usage:
    pip install pyinstaller
    python build_exe.py

Note: exe must be built on the target platform (Windows exe on Windows, etc).
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build_app() -> None:
    """Build claude-light-app with PySide6 (windowed, no console)."""
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", "claude-light-app",
        "--hidden-import", "PySide6.QtCore",
        "--hidden-import", "PySide6.QtGui",
        "--hidden-import", "PySide6.QtWidgets",
        "--hidden-import", "claude_light_state",
        "--hidden-import", "claude_light_hook",
        str(ROOT / "claude_light_app.py"),
    ]
    print(f"Building claude-light-app...")
    subprocess.run(cmd, check=True)
    print(f"Done: dist/claude-light-app*")


def build_hook() -> None:
    """Build claude-light-hook (console app, no PySide6, stdlib only)."""
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--console",
        "--name", "claude-light-hook",
        "--exclude-module", "PySide6",
        "--exclude-module", "PySide6.QtCore",
        "--exclude-module", "PySide6.QtGui",
        "--exclude-module", "PySide6.QtWidgets",
        str(ROOT / "claude_light_hook.py"),
    ]
    print(f"Building claude-light-hook...")
    subprocess.run(cmd, check=True)
    print(f"Done: dist/claude-light-hook*")


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "all"

    if target in ("app", "all"):
        build_app()
    if target in ("hook", "all"):
        build_hook()

    print()
    print("Build complete. Output in dist/")
    print("Distribute: claude-light-app.exe + claude-light-hook.exe + claude-light-0.1.0.vsix")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())