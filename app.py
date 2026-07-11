#!/usr/bin/env python3
"""Application bootstrap entrypoint for JingleAllTheDay."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

from app_helpers import apply_windows_taskbar_icon as _apply_windows_taskbar_icon
from gui import MainWindow
from PyQt6.QtCore import QLockFile, QStandardPaths
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

APP_NAME = "JingleAllTheDay"
APP_VERSION = "1.8.5.070626"
APP_ID = "JingleAllTheDay.App"
_INSTANCE_LOCK_NAME = "single_instance.lock"
_QT_LOCK_NAME = "single_instance.qtlock"

_HERE = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(__file__).resolve().parent  # type: ignore[attr-defined]


class _SingleInstanceGuard:
    def __init__(self, app: QApplication) -> None:
        self._app = app
        lock_root = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.TempLocation
        )
        if lock_root.strip():
            base_dir = Path(lock_root) / APP_NAME
        else:
            # Defensive fallback when Qt cannot resolve temp location.
            base_dir = Path.home() / ".cache" / APP_NAME
        self._lock_path = base_dir / _INSTANCE_LOCK_NAME
        self._qt_lock_path = base_dir / _QT_LOCK_NAME
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._qt_lock = QLockFile(str(self._qt_lock_path))
        # Disable time-based stale detection for strict single-instance behavior.
        self._qt_lock.setStaleLockTime(0)
        self._acquired = False

    def acquire_or_prompt(self) -> bool:
        if not self._qt_lock.tryLock(0):
            return False

        if self._try_acquire_pid_lock():
            return True

        self._qt_lock.unlock()
        return False

    def release(self) -> None:
        if not self._acquired:
            if self._qt_lock.isLocked():
                self._qt_lock.unlock()
            return
        self._remove_stale_lock()
        if self._qt_lock.isLocked():
            self._qt_lock.unlock()
        self._acquired = False

    def _try_acquire_pid_lock(self) -> bool:
        try:
            with self._lock_path.open("x", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
            self._acquired = True
            return True
        except FileExistsError:
            owner_pid = self._read_owner_pid()
            if owner_pid is None or not self._is_pid_alive(owner_pid):
                self._remove_stale_lock()
                try:
                    with self._lock_path.open("x", encoding="utf-8") as handle:
                        handle.write(str(os.getpid()))
                    self._acquired = True
                    return True
                except Exception:
                    return False
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
        if sys.platform == "win32":
            process_query_limited_information = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
            if handle:
                kernel32.CloseHandle(handle)
                return True
            # Access denied can still mean the process exists.
            last_error = ctypes.get_last_error()
            if last_error == 5:
                return True
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

def main() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            # Keep startup resilient if Win32 shell integration is unavailable.
            pass

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
        QMessageBox.information(
            None,
            "JingleAllTheDay Already Running",
            "Another instance of JingleAllTheDay is already running.\n\n"
            "This launch will now exit to prevent concurrent access.",
        )
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
