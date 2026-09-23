from __future__ import annotations

import sys
from typing import Any

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from app_helpers import (
    APPEARANCE_MODE_DARK,
    APPEARANCE_MODE_LIGHT,
    APPEARANCE_MODE_SYSTEM,
    apply_app_appearance_mode as _apply_app_appearance_mode,
    apply_windows_titlebar_theme as _apply_windows_titlebar_theme,
    coerce_appearance_mode as _coerce_appearance_mode,
)


class MainWindowAppearanceMixin:
    def appearance_mode(self) -> str:
        return self._appearance_mode

    def _set_appearance_mode(self, mode: str, *, announce: bool = True) -> None:
        normalized_mode = _coerce_appearance_mode(mode)
        if normalized_mode != self._appearance_mode:
            self._appearance_mode = normalized_mode
            self._settings.setValue("options/appearanceMode", self._appearance_mode)
        self._apply_current_appearance_mode()
        self._start_titlebar_reassertion()
        self._refresh_appearance_action_checks()
        if announce:
            label = {
                APPEARANCE_MODE_SYSTEM: "Follow System",
                APPEARANCE_MODE_LIGHT: "Light",
                APPEARANCE_MODE_DARK: "Dark",
            }.get(self._appearance_mode, "Follow System")
            self._status.showMessage(f"Appearance mode: {label}")

    def _apply_current_appearance_mode(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        self._appearance_effective_mode = _apply_app_appearance_mode(app, self._appearance_mode)
        use_dark_titlebar = self._appearance_effective_mode == APPEARANCE_MODE_DARK
        for widget in app.topLevelWidgets():
            try:
                _apply_windows_titlebar_theme(widget, use_dark_titlebar)
                widget.update()
            except RuntimeError:
                continue

        # Windows can occasionally lag one paint cycle on non-client updates;
        # re-apply titlebar mode on the next event loop turn for consistency.
        QTimer.singleShot(0, self._apply_deferred_titlebar_theme)

    def _apply_deferred_titlebar_theme(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        use_dark_titlebar = self._appearance_effective_mode == APPEARANCE_MODE_DARK
        for widget in app.topLevelWidgets():
            try:
                _apply_windows_titlebar_theme(widget, use_dark_titlebar)
            except RuntimeError:
                continue

    def _start_titlebar_reassertion(self) -> None:
        if sys.platform != "win32":
            return
        # Re-assert for ~1.2s to ride out transient non-client repaints.
        self._titlebar_reassert_remaining = 8
        if not self._titlebar_reassert_timer.isActive():
            self._titlebar_reassert_timer.start()

    def _on_titlebar_reassert_tick(self) -> None:
        if self._titlebar_reassert_remaining <= 0:
            self._titlebar_reassert_timer.stop()
            return
        self._titlebar_reassert_remaining -= 1
        app = QApplication.instance()
        if app is None:
            return
        use_dark_titlebar = self._appearance_effective_mode == APPEARANCE_MODE_DARK
        for widget in app.topLevelWidgets():
            try:
                _apply_windows_titlebar_theme(widget, use_dark_titlebar)
            except RuntimeError:
                continue

    def _refresh_appearance_action_checks(self) -> None:
        if self._appearance_action_system is not None:
            self._appearance_action_system.setChecked(self._appearance_mode == APPEARANCE_MODE_SYSTEM)
        if self._appearance_action_light is not None:
            self._appearance_action_light.setChecked(self._appearance_mode == APPEARANCE_MODE_LIGHT)
        if self._appearance_action_dark is not None:
            self._appearance_action_dark.setChecked(self._appearance_mode == APPEARANCE_MODE_DARK)

    def _on_appearance_follow_system_triggered(self) -> None:
        self._set_appearance_mode(APPEARANCE_MODE_SYSTEM)

    def _on_appearance_light_triggered(self) -> None:
        self._set_appearance_mode(APPEARANCE_MODE_LIGHT)

    def _on_appearance_dark_triggered(self) -> None:
        self._set_appearance_mode(APPEARANCE_MODE_DARK)

    def _on_system_color_scheme_changed(self, *_: Any) -> None:
        if self._appearance_mode != APPEARANCE_MODE_SYSTEM:
            return
        self._apply_current_appearance_mode()

    def _refresh_system_appearance_fallback(self) -> None:
        if self._appearance_mode != APPEARANCE_MODE_SYSTEM:
            return
        self._apply_current_appearance_mode()


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
