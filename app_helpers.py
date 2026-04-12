from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any

_HERE = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(__file__).resolve().parent  # type: ignore[attr-defined]
_FFPROBE_PATH: str | None = None
_FFPROBE_CHECKED = False


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
    print("Launch gui.py to start JingleAllTheDay.")
    raise SystemExit(1)
