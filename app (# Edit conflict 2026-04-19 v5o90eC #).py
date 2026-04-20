#!/usr/bin/env python3
"""Application bootstrap entrypoint for JingleAllTheDay."""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from app_helpers import apply_windows_taskbar_icon as _apply_windows_taskbar_icon
from gui import MainWindow
from PyQt6.QtCore import QStandardPaths
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

APP_NAME = "JingleAllTheDay"
APP_VERSION = "1.4.0.041926"
APP_ID = "JingleAllTheDay.App"
_INSTANCE_LOCK_NAME = "single_instance.lock"

_HERE = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(__file__).resolve().parent  # type: ignore[attr-defined]


class _SingleInstanceGuard:
    def __init__(self, app: QApplication) -> None:
        self._app = app
        app_data_location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        self._lock_path = Path(app_data_location) / _INSTANCE_LOCK_NAME
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._acquired = False

    def acquire_or_prompt(self) -> bool:
        if self._try_acquire():
            return True

        owner_pid = self._read_owner_pid()
        if owner_pid is None:
            self._remove_stale_lock()
            return self._try_acquire()

        if not self._is_pid_alive(owner_pid):
            self._remove_stale_lock()
            return self._try_acquire()

        response = QMessageBox.question(
            None,
            "JingleAllTheDay Already Running",
            (
                "Another instance of JingleAllTheDay is already running.\n\n"
                "Would you like this instance to terminate the first one and continue?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return False

        if not self._terminate_pid(owner_pid):
            QMessageBox.warning(
                None,
                "Could Not Terminate First Instance",
                "The existing instance could not be terminated. This launch will now exit.",
            )
            return False

        if not self._wait_for_pid_exit(owner_pid, timeout_seconds=4.0):
            QMessageBox.warning(
                None,
                "Existing Instance Still Running",
                "The existing instance did not exit in time. This launch will now exit.",
            )
            return False

        self._remove_stale_lock()
        if self._try_acquire():
            return True

        QMessageBox.warning(
            None,
            "Could Not Acquire Instance Lock",
            "Could not acquire the single-instance lock after terminating the first instance.",
        )
        return False

    def release(self) -> None:
        if not self._acquired:
            return
        self._remove_stale_lock()
        self._acquired = False

    def _try_acquire(self) -> bool:
        try:
            with self._lock_path.open("x", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
            self._acquired = True
            return True
        except FileExistsError:
            return False
        except Exception:
            return False

    def _remove_stale_lock(self) -> None:
        try:
            self._lock_path.unlink(missing_ok=True)
        except Exception:
            pass

    def _read_owner_pid(self) -> int | None:
        try:
            raw = self._lock_path.read_text(encoding="utf-8").strip()
            pid = int(raw)
            return pid if pid > 0 else None
        except Exception:
            return None

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    @staticmethod
    def _terminate_pid(pid: int) -> bool:
        if pid <= 0:
            return False
        if sys.platform == "win32":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
                check=False,
            )
            return result.returncode == 0

        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            return False
        return True

    def _wait_for_pid_exit(self, pid: int, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + max(0.2, timeout_seconds)
        while time.monotonic() < deadline:
            if not self._is_pid_alive(pid):
                return True
            self._app.processEvents()
            time.sleep(0.05)
        return not self._is_pid_alive(pid)


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

    instance_guard = _SingleInstanceGuard(app)
    if not instance_guard.acquire_or_prompt():
        sys.exit(0)

    window = MainWindow(app_name=APP_NAME, app_version=APP_VERSION)
    _apply_windows_taskbar_icon(window)
    window.show()

    try:
        exit_code = app.exec()
    except KeyboardInterrupt:
        # Gracefully handle Ctrl+C / debugger stop without noisy tracebacks.
        exit_code = 130
    finally:
        instance_guard.release()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
