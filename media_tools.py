"""Locating and running the ffmpeg/ffprobe command-line tools.

Kept free of Qt imports so the audio engine can use it.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
from pathlib import Path

# Stops a console window flashing up when the windowed Windows build runs a tool.
NO_WINDOW_FLAGS: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@functools.cache
def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


@functools.cache
def ffprobe_path() -> str | None:
    return shutil.which("ffprobe")


def probe_duration_seconds(path: str | Path, timeout: float = 2.0) -> float | None:
    """Return the container duration reported by ffprobe, or None if unavailable."""
    ffprobe = ffprobe_path()
    if not ffprobe:
        return None
    args = [
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
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW_FLAGS,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = (result.stdout or b"").decode("utf-8", errors="ignore").strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
