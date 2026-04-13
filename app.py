#!/usr/bin/env python3
"""Application bootstrap entrypoint for JingleAllTheDay."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from app_helpers import apply_windows_taskbar_icon as _apply_windows_taskbar_icon
from gui import MainWindow
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

APP_NAME = "JingleAllTheDay"
APP_VERSION = "1.2.1.041226"
APP_ID = "JingleAllTheDay.App"

_HERE = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(__file__).resolve().parent  # type: ignore[attr-defined]


def main() -> None:
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)

    app = QApplication(sys.argv)
    # Keep historical storage path stable for QStandardPaths.AppDataLocation.
    # Setting organization name here would redirect users to a new empty data folder.
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    icon_path = _HERE / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow(app_name=APP_NAME, app_version=APP_VERSION)
    _apply_windows_taskbar_icon(window)
    window.show()

    try:
        exit_code = app.exec()
    except KeyboardInterrupt:
        # Gracefully handle Ctrl+C / debugger stop without noisy tracebacks.
        exit_code = 130

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
