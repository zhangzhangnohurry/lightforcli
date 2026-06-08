#!/usr/bin/env python3
"""ClaudeLight PySide6 desktop app — frameless traffic-light status client.

Shows a polished borderless desktop widget reflecting the aggregate state
across all active Claude Code sessions. Connects to the local state server
for real-time updates via SSE.

Usage:
    python3 claude_light_app.py              # start app (auto-starts server if needed)
    python3 claude_light_app.py --no-server  # start app only, don't auto-start server
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QMouseEvent, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QBoxLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QSystemTrayIcon,
    QWidget,
)


STATE_SCRIPT = Path(__file__).resolve().parent / "claude_light_state.py"
DEFAULT_HOST = os.environ.get("CLAUDE_LIGHT_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("CLAUDE_LIGHT_PORT", "8765"))
SERVER_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
CONFIG_PATH = Path(os.environ.get("CLAUDE_LIGHT_CONFIG", Path.home() / ".config" / "claude-light" / "hud.json"))
DEFAULT_HUD_SETTINGS = {
    "background": "#0d1117",
    "opacity": 0.78,
    "layout_mode": "compact",
    "density": "compact",
    "rotation_ms": 4200,
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

COLORS = {
    "red": "#ff5a52",
    "yellow": "#ffd966",
    "green": "#48e18a",
    "off_red": "#30373e",
    "off_yellow": "#30373e",
    "off_green": "#30373e",
    "surface": "#111417",
    "surface_2": "#191f24",
    "surface_3": "#222a30",
    "edge": "#35414a",
    "text": "#f5f7f8",
    "muted": "#98a4ad",
}

MODE_ACCENTS = {
    "alarm": COLORS["red"], "error": COLORS["red"],
    "busy": COLORS["yellow"], "thinking": COLORS["yellow"],
    "success": COLORS["green"], "green": COLORS["green"],
    "off": "#5e6971",
}

MODE_PRIORITY = {
    "alarm": 6, "error": 5, "busy": 4, "thinking": 3,
    "green": 2, "success": 1, "off": 0,
}

def rgba_from_hex(hex_color: str, opacity: float) -> str:
    color = QColor(hex_color)
    if not color.isValid():
        color = QColor(COLORS["surface"])
    alpha = max(0, min(255, int(opacity * 255)))
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


def window_style(background: str, opacity: float) -> str:
    return f"""
QMainWindow {{
    background: transparent;
}}
QWidget#Shell {{
    background: {rgba_from_hex(background, opacity)};
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 28px;
}}
QFrame#SessionPill {{
    background: rgba(255, 255, 255, 0.065);
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 17px;
}}
QFrame#SessionPill[selected="true"] {{
    background: rgba(255, 255, 255, 0.11);
    border: 1px solid rgba(255, 255, 255, 0.24);
}}
QLabel {{
    color: {COLORS['text']};
}}
QLabel#SessionLabel {{
    color: {COLORS['muted']};
}}
QToolButton#SettingsButton {{
    color: {COLORS['muted']};
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 10px;
    font-size: 12px;
}}
QToolButton#SettingsButton:hover {{
    color: {COLORS['text']};
    background: rgba(255, 255, 255, 0.09);
}}
"""


def load_hud_settings() -> dict:
    settings = dict(DEFAULT_HUD_SETTINGS)
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            settings.update({k: data[k] for k in settings if k in data})
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    try:
        settings["opacity"] = max(0.35, min(1.0, float(settings["opacity"])))
    except (TypeError, ValueError):
        settings["opacity"] = DEFAULT_HUD_SETTINGS["opacity"]
    try:
        rotation_ms = int(settings["rotation_ms"])
        settings["rotation_ms"] = 0 if rotation_ms <= 0 else max(1500, min(30000, rotation_ms))
    except (TypeError, ValueError):
        settings["rotation_ms"] = DEFAULT_HUD_SETTINGS["rotation_ms"]
    if not QColor(str(settings.get("background", ""))).isValid():
        settings["background"] = DEFAULT_HUD_SETTINGS["background"]
    if settings.get("layout_mode") not in {"compact", "horizontal", "vertical"}:
        settings["layout_mode"] = DEFAULT_HUD_SETTINGS["layout_mode"]
    if settings.get("density") not in {"compact", "diagnostic"}:
        settings["density"] = DEFAULT_HUD_SETTINGS["density"]
    return settings


def save_hud_settings(settings: dict) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


class LampWidget(QFrame):
    """A single circular lamp with a subtle glow."""

    def __init__(self, color_key: str, off_color_key: str, size: int = 24) -> None:
        super().__init__()
        self.color_key = color_key
        self.off_color_key = off_color_key
        self.size_px = size
        self.is_on: bool | None = None
        self.setFixedSize(size, size)
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setOffset(0, 0)
        self._glow.setBlurRadius(0)
        self.setGraphicsEffect(self._glow)
        self.set_on(False)

    def set_on(self, on: bool) -> None:
        if self.is_on == on:
            return
        self.is_on = on
        bg = COLORS[self.color_key] if on else COLORS[self.off_color_key]
        edge = bg if on else "#4a545d"
        self.setStyleSheet(f"""
            LampWidget {{
                background: {bg};
                border: 1px solid {edge};
                border-radius: {self.size_px // 2}px;
            }}
        """)
        self._glow.setBlurRadius(max(6, int(self.size_px * 0.75)) if on else 4)
        self._glow.setColor(QColor(bg if on else "#050607"))


class TrafficLightWidget(QFrame):
    """The original three lamps, kept as the only circular status indicators."""

    def __init__(self, lamp_size: int = 24, spacing: int = 8) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setSpacing(spacing)
        layout.setContentsMargins(0, 0, 0, 0)

        self.red_lamp = LampWidget("red", "off_red", lamp_size)
        self.yellow_lamp = LampWidget("yellow", "off_yellow", lamp_size)
        self.green_lamp = LampWidget("green", "off_green", lamp_size)

        for lamp in (self.red_lamp, self.yellow_lamp, self.green_lamp):
            layout.addWidget(lamp, alignment=Qt.AlignCenter)

        self.setFixedSize(lamp_size * 3 + spacing * 2, lamp_size + 2)

    def set_lamps(self, red: bool, yellow: bool, green: bool) -> None:
        self.red_lamp.set_on(red)
        self.yellow_lamp.set_on(yellow)
        self.green_lamp.set_on(green)


def lamps_for_mode(mode: str, t_ms: int) -> tuple[bool, bool, bool]:
    if mode == "off":
        return False, False, False
    if mode == "green" or mode == "success":
        return False, False, True
    if mode == "error":
        on = (t_ms // 240) % 2 == 0
        return on, False, False
    if mode == "busy":
        on = (t_ms // 650) % 2 == 0
        return False, on, False
    if mode == "alarm":
        on = (t_ms // 260) % 2 == 0
        return on, on, on
    if mode == "thinking":
        phase = ((t_ms % 1050) // 350)
        return phase == 2, phase == 1, phase == 0
    return False, False, False


class SessionPillWidget(QFrame):
    """Glass-style low-interference session row/card."""

    def __init__(self, sid: str, session: dict, text: str, detail: str, selected: bool, diagnostic: bool) -> None:
        super().__init__()
        self.sid = sid
        self.session = session
        self.mode = str(session.get("mode") or "off")
        self.traffic_light = TrafficLightWidget(lamp_size=11, spacing=4)
        self.setObjectName("SessionPill")
        self.setProperty("selected", "true" if selected else "false")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setSpacing(9)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.addWidget(self.traffic_light, alignment=Qt.AlignVCenter)

        labels = QVBoxLayout()
        labels.setSpacing(1)
        title = QLabel(text)
        title.setStyleSheet("font-size: 12px; font-weight: 750; color: #e5edf6;")
        title.setMaximumWidth(250 if diagnostic else 165)
        labels.addWidget(title)
        if diagnostic and detail:
            subtitle = QLabel(detail)
            subtitle.setStyleSheet("font-size: 10px; color: #8ea0b8;")
            subtitle.setMaximumWidth(250)
            labels.addWidget(subtitle)
        layout.addLayout(labels)

        state = QLabel(MODE_LABELS.get(self.mode, self.mode))
        state.setStyleSheet(f"font-size: 12px; font-weight: 800; color: {MODE_ACCENTS.get(self.mode, MODE_ACCENTS['off'])};")
        layout.addWidget(state, alignment=Qt.AlignVCenter)

    def animate(self, t_ms: int) -> None:
        self.traffic_light.set_lamps(*lamps_for_mode(self.mode, t_ms))


class ClaudeLightWindow(QMainWindow):
    """Frameless top HUD with traffic light + active session shortcut."""

    state_updated = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ClaudeLight")
        self.setFixedSize(570, 58)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.hud_settings = load_hud_settings()
        self.setStyleSheet(window_style(self.hud_settings["background"], self.hud_settings["opacity"]))

        self.aggregate_mode = "off"
        self.display_mode = "off"
        self.sessions: dict = {}
        self.reflected_session_id = ""
        self.display_entries: list[tuple[str, dict]] = []
        self.session_widgets: list[SessionPillWidget] = []
        self.display_index = 0
        self.anim_start = time.time()
        self._drag_start: QPoint | None = None

        shell = QWidget()
        shell.setObjectName("Shell")
        shell.installEventFilter(self)
        self.setCentralWidget(shell)
        self.content = QHBoxLayout(shell)
        self.content.setSpacing(14)
        self.content.setContentsMargins(18, 10, 14, 10)

        self.traffic_light = TrafficLightWidget()
        self.traffic_light.installEventFilter(self)
        self.content.addWidget(self.traffic_light, alignment=Qt.AlignVCenter)

        self.title_label = QLabel("Off")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 800; line-height: 1;")
        self.title_label.installEventFilter(self)
        self.content.addWidget(self.title_label, alignment=Qt.AlignVCenter)

        self.session_label = QLabel("No active session")
        self.session_label.setObjectName("SessionLabel")
        self.session_label.setMinimumWidth(300)
        self.session_label.setStyleSheet("font-size: 13px;")
        self.session_label.installEventFilter(self)
        self.content.addWidget(self.session_label, stretch=1, alignment=Qt.AlignVCenter)

        self.sessions_panel = QWidget()
        self.sessions_panel.installEventFilter(self)
        self.sessions_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.sessions_panel)
        self.sessions_layout.setSpacing(8)
        self.sessions_layout.setContentsMargins(0, 0, 0, 0)
        self.content.addWidget(self.sessions_panel, stretch=1, alignment=Qt.AlignVCenter)

        self.settings_button = QToolButton()
        self.settings_button.setObjectName("SettingsButton")
        self.settings_button.setText("⚙")
        self.settings_button.setFixedSize(24, 24)
        self.settings_button.setToolTip("HUD settings")
        self.settings_button.installEventFilter(self)
        self.settings_button.clicked.connect(self.show_settings_menu)
        self.content.addWidget(self.settings_button, alignment=Qt.AlignVCenter)
        self.apply_layout_mode()

        self.anim_timer = QTimer()
        self.anim_timer.timeout.connect(self.animate)
        self.anim_timer.start(16)

        self.poll_timer = QTimer()
        self.poll_timer.timeout.connect(self.poll_state)
        self.poll_timer.start(2000)

        self.rotation_timer = QTimer()
        self.rotation_timer.timeout.connect(self.rotate_display_entry)
        if int(self.hud_settings["rotation_ms"]) > 0:
            self.rotation_timer.start(int(self.hud_settings["rotation_ms"]))

        self.sse_thread = threading.Thread(target=self._sse_reader, daemon=True)
        self.sse_thread.start()
        self.state_updated.connect(self._on_state_updated)

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setToolTip("ClaudeLight")
        tray_menu = QMenu()
        tray_menu.addAction("Show", self.show)
        tray_menu.addAction("Quit", QApplication.quit)
        self.tray_icon.activated.connect(
            lambda reason: tray_menu.exec() if reason == QSystemTrayIcon.ActivationReason.Context else None
        )
        self._update_tray_icon("off")
        self.tray_icon.show()

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self.poll_state()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_start)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_start = None
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if not self.handle_wheel(event):
            super().wheelEvent(event)

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and self.event_targets_hud(watched, event):
            return self.handle_wheel(event)  # type: ignore[arg-type]
        return super().eventFilter(watched, event)

    def event_targets_hud(self, watched: object, event: QEvent) -> bool:
        if isinstance(watched, QWidget) and (watched is self or self.isAncestorOf(watched)):
            return True
        global_position = getattr(event, "globalPosition", None)
        if callable(global_position):
            return self.frameGeometry().contains(global_position().toPoint())
        return False

    def handle_wheel(self, event: QWheelEvent) -> bool:
        if len(self.display_entries) <= 1:
            return False
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta == 0:
            return False
        self.rotate_display_entry(-1 if delta > 0 else 1)
        rotation_ms = int(self.hud_settings["rotation_ms"])
        if rotation_ms > 0:
            self.rotation_timer.start(rotation_ms)
        event.accept()
        return True

    def show_settings_menu(self) -> None:
        menu = QMenu(self)
        layout_menu = menu.addMenu("布局")
        for label, value in (("单条 HUD", "compact"), ("横向多 Session", "horizontal"), ("纵向多 Session", "vertical")):
            action = layout_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.hud_settings["layout_mode"] == value)
            action.triggered.connect(lambda _checked=False, mode=value: self.set_layout_mode(mode))
        density_menu = layout_menu.addMenu("信息密度")
        for label, value in (("低干扰", "compact"), ("诊断信息", "diagnostic")):
            action = density_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.hud_settings["density"] == value)
            action.triggered.connect(lambda _checked=False, density=value: self.set_density(density))

        background_menu = menu.addMenu("背景 / 透明度")
        for label, color in (
            ("玻璃黑", "#0d1117"),
            ("雾灰", "#17202a"),
            ("蓝黑", "#101827"),
            ("纯黑", "#050607"),
        ):
            action = background_menu.addAction(label)
            action.triggered.connect(lambda _checked=False, c=color: self.set_background_color(c))
        custom_action = background_menu.addAction("自定义颜色…")
        custom_action.triggered.connect(self.choose_background_color)
        opacity_menu = background_menu.addMenu("透明度")
        for label, opacity in (("100%", 1.0), ("94%", 0.94), ("85%", 0.85), ("70%", 0.70), ("55%", 0.55)):
            action = opacity_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(abs(float(self.hud_settings["opacity"]) - opacity) < 0.01)
            action.triggered.connect(lambda _checked=False, o=opacity: self.set_opacity(o))

        rotation_menu = menu.addMenu("轮播周期")
        for label, ms in (("关闭自动轮播", 0), ("2 秒", 2000), ("4.2 秒", 4200), ("8 秒", 8000), ("15 秒", 15000)):
            action = rotation_menu.addAction(label)
            action.setCheckable(True)
            current = int(self.hud_settings["rotation_ms"])
            action.setChecked((ms == 0 and not self.rotation_timer.isActive()) or (ms != 0 and current == ms and self.rotation_timer.isActive()))
            action.triggered.connect(lambda _checked=False, value=ms: self.set_rotation_period(value))

        menu.exec(self.settings_button.mapToGlobal(self.settings_button.rect().bottomLeft()))

    def set_layout_mode(self, mode: str) -> None:
        if mode not in {"compact", "horizontal", "vertical"}:
            return
        self.hud_settings["layout_mode"] = mode
        self.apply_layout_mode()
        save_hud_settings(self.hud_settings)

    def set_density(self, density: str) -> None:
        if density not in {"compact", "diagnostic"}:
            return
        self.hud_settings["density"] = density
        self.update_display_entry()
        save_hud_settings(self.hud_settings)

    def set_background_color(self, color: str) -> None:
        self.hud_settings["background"] = color
        self.apply_hud_settings()

    def choose_background_color(self) -> None:
        color = QColorDialog.getColor(QColor(str(self.hud_settings["background"])), self, "选择 HUD 背景色")
        if color.isValid():
            self.set_background_color(color.name())

    def set_opacity(self, opacity: float) -> None:
        self.hud_settings["opacity"] = opacity
        self.apply_hud_settings()

    def set_rotation_period(self, ms: int) -> None:
        if ms <= 0:
            self.hud_settings["rotation_ms"] = 0
            self.rotation_timer.stop()
        else:
            self.hud_settings["rotation_ms"] = ms
            self.rotation_timer.start(ms)
        save_hud_settings(self.hud_settings)

    def apply_hud_settings(self) -> None:
        self.setStyleSheet(window_style(str(self.hud_settings["background"]), float(self.hud_settings["opacity"])))
        save_hud_settings(self.hud_settings)

    def apply_layout_mode(self) -> None:
        mode = str(self.hud_settings.get("layout_mode") or "compact")
        is_compact = mode == "compact"
        self.traffic_light.setVisible(is_compact)
        self.title_label.setVisible(is_compact)
        self.session_label.setVisible(is_compact)
        self.sessions_panel.setVisible(not is_compact)
        if mode == "vertical":
            self.sessions_layout.setDirection(QBoxLayout.Direction.TopToBottom)
        else:
            self.sessions_layout.setDirection(QBoxLayout.Direction.LeftToRight)
        self.update_display_entry()

    def poll_state(self) -> None:
        try:
            with urllib.request.urlopen(f"{SERVER_URL}/api/state", timeout=1) as r:
                data = json.loads(r.read())
                self.state_updated.emit(data)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            pass

    def _sse_reader(self) -> None:
        while True:
            try:
                req = urllib.request.Request(f"{SERVER_URL}/events")
                with urllib.request.urlopen(req, timeout=30) as r:
                    buffer = ""
                    while True:
                        chunk = r.read(4096).decode("utf-8", errors="replace")
                        if not chunk:
                            break
                        buffer += chunk
                        while "\n\n" in buffer:
                            event_text, buffer = buffer.split("\n\n", 1)
                            for line in event_text.split("\n"):
                                if line.startswith("data: "):
                                    try:
                                        data = json.loads(line[6:])
                                        self.state_updated.emit(data)
                                    except json.JSONDecodeError:
                                        pass
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
                time.sleep(3)

    def _on_state_updated(self, data: dict) -> None:
        self.aggregate_mode = data.get("aggregate_mode", "off")
        self.sessions = data.get("sessions", {})
        self.anim_start = time.time()
        previous_sid = self.reflected_session_id

        self.display_entries = self.sorted_display_entries()
        ids = [sid for sid, _ in self.display_entries]
        if previous_sid in ids:
            self.display_index = ids.index(previous_sid)
        else:
            self.display_index = 0
        self.update_display_entry()

    def sorted_display_entries(self) -> list[tuple[str, dict]]:
        if not self.sessions:
            return []
        entries = list(self.sessions.items())
        if any(str(info.get("mode", "off")) != "off" for _, info in entries):
            entries = [(sid, info) for sid, info in entries if str(info.get("mode", "off")) != "off"]
        return sorted(
            entries,
            key=lambda item: (
                -MODE_PRIORITY.get(str(item[1].get("mode", "off")), 0),
                -float(item[1].get("updated_at", 0)),
                str(item[0]),
            ),
        )

    def reflected_session(self) -> tuple[str, dict]:
        if not self.display_entries:
            return "", {}
        self.display_index %= len(self.display_entries)
        return self.display_entries[self.display_index]

    def rotate_display_entry(self, step: int = 1) -> None:
        if len(self.display_entries) <= 1:
            return
        self.display_index = (self.display_index + step) % len(self.display_entries)
        self.update_display_entry()

    def update_display_entry(self) -> None:
        sid, session = self.reflected_session()
        self.reflected_session_id = sid
        self.display_mode = str(session.get("mode") or "off") if sid else "off"
        label = MODE_LABELS.get(self.display_mode, self.display_mode)
        accent = MODE_ACCENTS.get(self.display_mode, MODE_ACCENTS["off"])
        self.title_label.setText(label)
        self.title_label.setStyleSheet(
            f"font-size: 18px; font-weight: 800; line-height: 1; color: {accent};"
        )
        text = self.display_text_for_session(sid, session)
        self.session_label.setText(text)
        self.session_label.setToolTip(self.tooltip_for_session(sid, session))
        self._update_tray_icon(self.display_mode)
        self.setWindowTitle(f"ClaudeLight — {label}")
        self.render_session_panel()
        self.resize_for_layout()

    def clear_sessions_panel(self) -> None:
        self.session_widgets = []
        while self.sessions_layout.count():
            item = self.sessions_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def render_session_panel(self) -> None:
        if str(self.hud_settings.get("layout_mode") or "compact") == "compact":
            self.clear_sessions_panel()
            return
        self.clear_sessions_panel()
        diagnostic = self.hud_settings.get("density") == "diagnostic"
        if not self.display_entries:
            pill = SessionPillWidget("", {"mode": "off"}, "No active workspace", "", True, False)
            self.session_widgets.append(pill)
            self.sessions_layout.addWidget(pill)
            return
        for sid, session in self.visible_multi_entries():
            name = self.short_session_name(sid, session)
            detail = self.detail_text_for_session(sid, session)
            pill = SessionPillWidget(sid, session, name, detail, sid == self.reflected_session_id, diagnostic)
            pill.setToolTip(self.tooltip_for_session(sid, session))
            pill.installEventFilter(self)
            self.session_widgets.append(pill)
            self.sessions_layout.addWidget(pill)
        self.sessions_layout.addStretch(1)

    def visible_multi_entries(self) -> list[tuple[str, dict]]:
        """Adaptive fill guard: show all sessions until the HUD would become impractically large."""
        entries = self.display_entries
        screen = self.screen() or QApplication.primaryScreen()
        if not screen or str(self.hud_settings.get("layout_mode")) == "vertical":
            return entries[:10]
        available_width = max(700, screen.availableGeometry().width() - 80)
        diagnostic = self.hud_settings.get("density") == "diagnostic"
        approx_width = 268 if diagnostic else 205
        max_items = max(1, (available_width - 80) // approx_width)
        return entries[:max_items]

    def short_session_name(self, sid: str, session: dict) -> str:
        if not sid:
            return "No active workspace"
        workspace = str(
            session.get("workspace")
            or session.get("workspace_name")
            or session.get("project_name")
            or session.get("name")
            or ""
        )
        source = str(session.get("source") or "manual")
        short = str(session.get("session_short") or self.compact_session_id(sid))
        if workspace:
            return f"{workspace} · {source}"
        return f"{source} · {short}"

    def detail_text_for_session(self, sid: str, session: dict) -> str:
        source = str(session.get("source") or "manual")
        directory = str(session.get("directory") or session.get("cwd") or "")
        active = str(session.get("active_editor") or session.get("summary") or "")
        short = str(session.get("session_short") or self.compact_session_id(sid))
        parts = [short]
        if directory:
            parts.append(directory)
        if active and source == "vscode":
            parts.append(active)
        return " · ".join(parts[:3])

    def resize_for_layout(self) -> None:
        mode = str(self.hud_settings.get("layout_mode") or "compact")
        if mode == "compact":
            width, height = 570, 58
        elif mode == "vertical":
            diagnostic = self.hud_settings.get("density") == "diagnostic"
            count = max(1, len(self.visible_multi_entries()))
            width = 430 if diagnostic else 360
            height = min(720, 34 + count * (52 if diagnostic else 43))
        else:
            diagnostic = self.hud_settings.get("density") == "diagnostic"
            count = max(1, len(self.visible_multi_entries()))
            width = min(1500, 74 + count * (270 if diagnostic else 208))
            height = 62 if not diagnostic else 78
        self.setFixedSize(width, height)
        self.recenter_top()

    def display_text_for_session(self, sid: str, session: dict) -> str:
        if not sid:
            return "No active workspace"
        source = str(session.get("source") or "manual")
        source_label = {"claude": "Claude", "vscode": "VSCode", "manual": "Manual"}.get(source, source.title())
        workspace = str(
            session.get("workspace")
            or session.get("workspace_name")
            or session.get("project_name")
            or session.get("name")
            or "workspace"
        )
        directory = str(session.get("directory") or "")
        short = str(session.get("session_short") or self.compact_session_id(sid))
        active = str(session.get("active_editor") or session.get("summary") or "")
        parts = [source_label, workspace]
        if directory and directory != workspace:
            parts.append(directory)
        parts.append(short)
        if active and source == "vscode":
            parts.append(active)
        return " · ".join(parts[:4])

    def compact_session_id(self, sid: str) -> str:
        if not sid:
            return ""
        if sid == "default" or len(sid) <= 10:
            return sid
        return f"{sid[:4]}…{sid[-4:]}"

    def tooltip_for_session(self, sid: str, session: dict) -> str:
        if not sid:
            return "No active workspace"
        lines = [f"session: {sid}"]
        lines.append(f"short: {self.compact_session_id(sid)}")
        for key in ("source", "workspace", "cwd", "directory", "active_editor", "summary", "uri"):
            value = str(session.get(key) or "")
            if value:
                lines.append(f"{key}: {value}")
        if len(self.display_entries) > 1:
            lines.append("scroll to switch workspace/session")
        return "\n".join(lines)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.recenter_top()

    def recenter_top(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if not screen:
            return
        available = screen.availableGeometry()
        x = available.x() + (available.width() - self.width()) // 2
        y = available.y() + 8
        self.move(x, y)

    def _update_tray_icon(self, mode: str) -> None:
        if not hasattr(self, "tray_icon"):
            return
        color = QColor(MODE_ACCENTS.get(mode, MODE_ACCENTS["off"]))
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(3, 3, 26, 26)
        painter.end()
        self.tray_icon.setIcon(QIcon(pixmap))

    def animate(self) -> None:
        t_ms = int((time.time() - self.anim_start) * 1000)
        mode = self.display_mode
        self.traffic_light.set_lamps(*lamps_for_mode(mode, t_ms))
        for widget in self.session_widgets:
            widget.animate(t_ms)


def start_server_if_needed() -> subprocess.Popen | None:
    """Start the state server if it's not already running."""
    try:
        with urllib.request.urlopen(f"{SERVER_URL}/api/state", timeout=1):
            return None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        pass

    proc = subprocess.Popen(
        [sys.executable, str(STATE_SCRIPT), "serve", "--no-open"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(20):
        try:
            with urllib.request.urlopen(f"{SERVER_URL}/api/state", timeout=0.5):
                return proc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            time.sleep(0.5)
    return proc


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-server", action="store_true", help="don't auto-start state server")
    args = parser.parse_args()

    server_proc = None
    if not args.no_server:
        server_proc = start_server_if_needed()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    window = ClaudeLightWindow()
    window.show()

    result = app.exec()

    if server_proc:
        server_proc.terminate()

    return result


if __name__ == "__main__":
    raise SystemExit(main())
