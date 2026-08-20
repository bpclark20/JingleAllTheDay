from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

_HERE = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(__file__).resolve().parent  # type: ignore[attr-defined]
_FFPROBE_PATH: str | None = None
_FFPROBE_CHECKED = False
RESERVED_INTERNAL_TAG_RECENT = "Recent"
RESERVED_INTERNAL_TAG_RECORDING = "JADR"
_RESERVED_INTERNAL_TAGS = {
    RESERVED_INTERNAL_TAG_RECENT.casefold(),
    RESERVED_INTERNAL_TAG_RECORDING.casefold(),
}
APPEARANCE_MODE_SYSTEM = "system"
APPEARANCE_MODE_LIGHT = "light"
APPEARANCE_MODE_DARK = "dark"
APPEARANCE_MODES = (
    APPEARANCE_MODE_SYSTEM,
    APPEARANCE_MODE_LIGHT,
    APPEARANCE_MODE_DARK,
)


def coerce_appearance_mode(value: Any) -> str:
    text = str(value).strip().lower()
    if text in APPEARANCE_MODES:
        return text
    return APPEARANCE_MODE_SYSTEM


def _palette_luma(color: QColor) -> float:
    red = color.redF()
    green = color.greenF()
    blue = color.blueF()
    return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue)


def is_system_dark_mode(app: QApplication) -> bool:
    style_hints = app.styleHints()
    color_scheme_attr = getattr(style_hints, "colorScheme", None)
    if callable(color_scheme_attr):
        try:
            color_scheme = color_scheme_attr()
            dark_member = getattr(Qt.ColorScheme, "Dark", None)
            light_member = getattr(Qt.ColorScheme, "Light", None)
            if dark_member is not None and color_scheme == dark_member:
                return True
            if light_member is not None and color_scheme == light_member:
                return False
        except Exception:
            pass

    # Fallback for environments where style hints are unavailable or ambiguous.
    window_color = app.palette().color(QPalette.ColorRole.Window)
    return _palette_luma(window_color) < 0.5


def resolved_appearance_mode(app: QApplication, configured_mode: str) -> str:
    mode = coerce_appearance_mode(configured_mode)
    if mode != APPEARANCE_MODE_SYSTEM:
        return mode
    return APPEARANCE_MODE_DARK if is_system_dark_mode(app) else APPEARANCE_MODE_LIGHT


def _build_dark_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(43, 43, 43))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(232, 232, 232))
    palette.setColor(QPalette.ColorRole.Base, QColor(29, 29, 29))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(47, 47, 47))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(60, 60, 60))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(232, 232, 232))
    palette.setColor(QPalette.ColorRole.Text, QColor(232, 232, 232))
    palette.setColor(QPalette.ColorRole.Button, QColor(56, 56, 56))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(232, 232, 232))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(160, 160, 160))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 86, 86))
    palette.setColor(QPalette.ColorRole.Link, QColor(110, 170, 255))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(70, 120, 220))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(140, 140, 140))
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        QColor(140, 140, 140),
    )
    return palette


def _build_light_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(246, 246, 246))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(25, 25, 25))
    palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 220))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(25, 25, 25))
    palette.setColor(QPalette.ColorRole.Text, QColor(25, 25, 25))
    palette.setColor(QPalette.ColorRole.Button, QColor(239, 239, 239))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(25, 25, 25))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(120, 120, 120))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(180, 0, 0))
    palette.setColor(QPalette.ColorRole.Link, QColor(30, 90, 190))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(43, 121, 223))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(130, 130, 130))
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        QColor(130, 130, 130),
    )
    return palette


def _light_menu_stylesheet() -> str:
    return (
        "QMenuBar { background-color: #f0f0f0; color: #1a1a1a; }"
        "QMenuBar::item { background: transparent; color: #1a1a1a; }"
        "QMenuBar::item:selected { background-color: #dcdcdc; }"
        "QMenu { background-color: #ffffff; color: #1a1a1a; border: 1px solid #b9b9b9; }"
        "QMenu::item:selected { background-color: #2b79df; color: #ffffff; }"
        "QPushButton, QToolButton {"
        " background-color: #efefef; color: #1a1a1a; border: 1px solid #b9b9b9;"
        "}"
        "QPushButton:hover, QToolButton:hover { background-color: #e3e3e3; }"
        "QPushButton:pressed, QToolButton:pressed { background-color: #d7d7d7; }"
        "QPushButton:disabled, QToolButton:disabled {"
        " background-color: #f1f1f1; color: #8d8d8d; border: 1px solid #d2d2d2;"
        "}"
        "QLineEdit, QComboBox, QAbstractSpinBox {"
        " background-color: #ffffff; color: #1a1a1a; border: 1px solid #b9b9b9;"
        " selection-background-color: #2b79df; selection-color: #ffffff;"
        "}"
        "QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {"
        " background-color: #efefef; color: #8a8a8a; border: 1px solid #c8c8c8;"
        "}"
        "QLineEdit[placeholderText]:not(:focus) { color: #6f6f6f; }"
        "QToolTip { background-color: #ffffdd; color: #1a1a1a; border: 1px solid #b9b9b9; }"
    )


def _dark_menu_stylesheet() -> str:
    return (
        "QMenuBar { background-color: #2b2b2b; color: #f2f2f2; }"
        "QMenuBar::item { background: transparent; color: #f2f2f2; }"
        "QMenuBar::item:selected { background-color: #3a3a3a; }"
        "QMenu { background-color: #2f2f2f; color: #f2f2f2; border: 1px solid #555; }"
        "QMenu::item:selected { background-color: #466fb8; color: #ffffff; }"
        "QPushButton, QToolButton {"
        " background-color: #3a3a3a; color: #f2f2f2; border: 1px solid #5a5a5a;"
        "}"
        "QPushButton:hover, QToolButton:hover { background-color: #454545; }"
        "QPushButton:pressed, QToolButton:pressed { background-color: #505050; }"
        "QPushButton:disabled, QToolButton:disabled {"
        " background-color: #343434; color: #8f8f8f; border: 1px solid #4f4f4f;"
        "}"
        "QLineEdit, QComboBox, QAbstractSpinBox {"
        " background-color: #2e2e2e; color: #f2f2f2; border: 1px solid #5a5a5a;"
        " selection-background-color: #466fb8; selection-color: #ffffff;"
        "}"
        "QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {"
        " background-color: #353535; color: #8f8f8f; border: 1px solid #4f4f4f;"
        "}"
        "QLineEdit[placeholderText]:not(:focus) { color: #9c9c9c; }"
        "QToolTip { background-color: #2f2f2f; color: #f2f2f2; border: 1px solid #555; }"
    )


def apply_windows_titlebar_theme(window: Any, use_dark_titlebar: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
    except Exception:
        return

    try:
        dwmapi = ctypes.windll.dwmapi
    except Exception:
        return

    # Windows 10/11: 20 is current immersive dark mode attribute, 19 is legacy fallback.
    attrs = (20, 19)
    value = ctypes.c_int(1 if use_dark_titlebar else 0)
    size = ctypes.sizeof(value)
    applied = False
    for attr in attrs:
        try:
            result = int(dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), size))
        except Exception:
            continue
        if result == 0:
            applied = True
            break

    if not applied:
        return

    # Force non-client redraw so title bar updates immediately.
    try:
        user32 = ctypes.windll.user32
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOZORDER = 0x0004
        SWP_NOACTIVATE = 0x0010
        SWP_FRAMECHANGED = 0x0020
        user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
    except Exception:
        pass


def apply_app_appearance_mode(app: QApplication, configured_mode: str) -> str:
    effective_mode = resolved_appearance_mode(app, configured_mode)
    if effective_mode == APPEARANCE_MODE_DARK:
        app.setPalette(_build_dark_palette())
        app.setStyleSheet(_dark_menu_stylesheet())
        return APPEARANCE_MODE_DARK
    app.setPalette(_build_light_palette())
    app.setStyleSheet(_light_menu_stylesheet())
    return APPEARANCE_MODE_LIGHT


def ensure_qt_logging_rules() -> None:
    """Suppress noisy Qt internal warnings from multimedia backend teardown."""
    suppress_rules = [
        "qt.core.qobject.connect.warning=false",
        "qt.multimedia.ffmpeg.warning=false",
    ]
    current = os.environ.get("QT_LOGGING_RULES", "").strip()
    existing = [part.strip() for part in current.split(";") if part.strip()]
    merged = list(existing)
    for rule in suppress_rules:
        if rule not in merged:
            merged.append(rule)
    os.environ["QT_LOGGING_RULES"] = ";".join(merged)


def apply_windows_taskbar_icon(window: Any) -> None:
    if sys.platform != "win32":
        return
    try:
        icon_source = Path(sys.executable) if getattr(sys, "frozen", False) else (_HERE / "icon.ico")
        if not icon_source.exists():
            return
        hwnd = int(window.winId())
        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32
        large = ctypes.c_void_p()
        small = ctypes.c_void_p()
        extracted = shell32.ExtractIconExW(str(icon_source), 0, ctypes.byref(large), ctypes.byref(small), 1)
        if extracted <= 0:
            return
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        GCLP_HICON = -14
        GCLP_HICONSM = -34
        if small.value:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small.value)
            user32.SetClassLongPtrW(hwnd, GCLP_HICONSM, small.value)
        if large.value:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, large.value)
            user32.SetClassLongPtrW(hwnd, GCLP_HICON, large.value)
    except Exception:
        # Taskbar icon integration is optional; never block app startup on it.
        return


def runtime_app_dir() -> Path:
    """Return the folder where the running app is located."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def normalize_tags(raw: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if raw is None:
        return []

    if isinstance(raw, str):
        pieces = raw.replace(";", ",").split(",")
    else:
        pieces = [str(item) for item in raw]

    out: list[str] = []
    seen: set[str] = set()
    for piece in pieces:
        tag = piece.strip()
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
    return out


def tags_to_text(tags: list[str]) -> str:
    return ", ".join(tags)


def merge_tags(existing: list[str], incoming: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for tag in [*existing, *incoming]:
        clean = tag.strip()
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


def remove_tags(existing: list[str], incoming: list[str]) -> list[str]:
    remove_keys = {tag.casefold() for tag in incoming}
    if not remove_keys:
        return list(existing)
    return [tag for tag in existing if tag.casefold() not in remove_keys]


def is_reserved_internal_tag(tag: str) -> bool:
    return tag.strip().casefold() in _RESERVED_INTERNAL_TAGS


def sanitize_user_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    clean: list[str] = []
    rejected: list[str] = []
    for tag in normalize_tags(tags):
        if is_reserved_internal_tag(tag):
            rejected.append(tag)
            continue
        clean.append(tag)
    return clean, rejected


def coerce_recent_window_days(value: Any, default: int = 14) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(1, min(3650, parsed))


def now_epoch_seconds() -> int:
    return max(0, int(time.time()))


def coerce_volume_percent(value: Any, default: int = 100) -> int:
    try:
        percent = int(round(float(value)))
    except (TypeError, ValueError):
        percent = default
    return max(0, min(100, percent))


def chip_palette_for_tag_seed(tag_seed: str) -> tuple[str, str, str]:
    palettes = [
        ("#3b2f6b", "#5a4a96", "#4a3a82"),
        ("#204d4f", "#2f7073", "#296063"),
        ("#5a3e2b", "#7f5a3d", "#6d4f36"),
        ("#2f4f2f", "#4c7a4c", "#3f683f"),
        ("#5c2f4f", "#844571", "#6f3d60"),
        ("#2f3f6a", "#455d99", "#3a5185"),
        ("#4f4a2a", "#7a7340", "#686234"),
        ("#4a2f2f", "#734646", "#613b3b"),
    ]
    digest = hashlib.sha1(tag_seed.casefold().encode("utf-8")).digest()
    idx = digest[0] % len(palettes)
    return palettes[idx]


def format_size_label(total_bytes: int) -> str:
    size = float(max(0, total_bytes))
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_idx = 0
    while size >= 1000.0 and unit_idx < len(units) - 1:
        size /= 1000.0
        unit_idx += 1

    if unit_idx == 0:
        return f"{int(size)}{units[unit_idx]}"
    return f"{size:.2f}{units[unit_idx]}"


def format_duration_hms(total_seconds: float) -> str:
    sec = max(0, int(round(total_seconds)))
    hours, rem = divmod(sec, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def get_ffprobe_path() -> str | None:
    global _FFPROBE_PATH, _FFPROBE_CHECKED
    if _FFPROBE_CHECKED:
        return _FFPROBE_PATH
    _FFPROBE_CHECKED = True
    _FFPROBE_PATH = shutil.which("ffprobe")
    return _FFPROBE_PATH


def probe_duration_seconds(path: Path) -> float:
    suffix = path.suffix.lower()
    if suffix in {".wav", ".wave"}:
        try:
            with wave.open(str(path), "rb") as w:
                frames = w.getnframes()
                rate = w.getframerate()
                if rate > 0:
                    return float(frames) / float(rate)
        except (wave.Error, OSError):
            pass

    ffprobe = get_ffprobe_path()
    if not ffprobe:
        return 0.0

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        run_kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": 2.0,
            "check": False,
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(cmd, **run_kwargs)
        if result.returncode != 0:
            return 0.0
        value = (result.stdout or "").strip()
        if not value:
            return 0.0
        return max(0.0, float(value))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
