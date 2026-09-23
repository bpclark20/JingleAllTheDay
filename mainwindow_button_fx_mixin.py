from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation
from PyQt6.QtWidgets import QGraphicsOpacityEffect


class MainWindowButtonFxMixin:
    def _set_loop_breathing(self, enabled: bool) -> None:
        if not enabled:
            if self._loop_breath_anim is not None:
                self._loop_breath_anim.stop()
            if self._loop_breath_effect is not None:
                try:
                    self._loop_breath_effect.setOpacity(1.0)
                except RuntimeError:
                    pass
            self._loop_btn.setGraphicsEffect(None)
            self._loop_breath_anim = None
            self._loop_breath_effect = None
            return

        if self._loop_breath_effect is None:
            self._loop_breath_effect = QGraphicsOpacityEffect(self._loop_btn)
        try:
            self._loop_btn.setGraphicsEffect(self._loop_breath_effect)
        except RuntimeError:
            self._loop_breath_effect = QGraphicsOpacityEffect(self._loop_btn)
            self._loop_btn.setGraphicsEffect(self._loop_breath_effect)
            self._loop_breath_anim = None

        if self._loop_breath_anim is None:
            self._loop_breath_anim = QPropertyAnimation(
                self._loop_breath_effect,
                b"opacity",
                self,
            )
            self._loop_breath_anim.setDuration(1100)
            self._loop_breath_anim.setStartValue(1.0)
            self._loop_breath_anim.setKeyValueAt(0.5, 0.72)
            self._loop_breath_anim.setEndValue(1.0)
            self._loop_breath_anim.setLoopCount(-1)
            self._loop_breath_anim.setEasingCurve(QEasingCurve.Type.InOutSine)

        self._loop_breath_anim.start()

    def _set_play_stop_breathing(self, enabled: bool) -> None:
        if not enabled:
            if self._play_stop_breath_anim is not None:
                self._play_stop_breath_anim.stop()
            if self._play_stop_breath_effect is not None:
                try:
                    self._play_stop_breath_effect.setOpacity(1.0)
                except RuntimeError:
                    pass
            self._play_btn.setGraphicsEffect(None)
            self._play_stop_breath_anim = None
            self._play_stop_breath_effect = None
            return

        if self._play_stop_breath_effect is None:
            self._play_stop_breath_effect = QGraphicsOpacityEffect(self._play_btn)
        try:
            self._play_btn.setGraphicsEffect(self._play_stop_breath_effect)
        except RuntimeError:
            self._play_stop_breath_effect = QGraphicsOpacityEffect(self._play_btn)
            self._play_btn.setGraphicsEffect(self._play_stop_breath_effect)
            self._play_stop_breath_anim = None

        if self._play_stop_breath_anim is None:
            self._play_stop_breath_anim = QPropertyAnimation(
                self._play_stop_breath_effect,
                b"opacity",
                self,
            )
            self._play_stop_breath_anim.setDuration(1000)
            self._play_stop_breath_anim.setStartValue(1.0)
            self._play_stop_breath_anim.setKeyValueAt(0.5, 0.68)
            self._play_stop_breath_anim.setEndValue(1.0)
            self._play_stop_breath_anim.setLoopCount(-1)
            self._play_stop_breath_anim.setEasingCurve(QEasingCurve.Type.InOutSine)

        self._play_stop_breath_anim.start()

    def _set_stop_button_breathing(self, enabled: bool) -> None:
        if not enabled:
            if self._stop_btn_breath_anim is not None:
                self._stop_btn_breath_anim.stop()
            if self._stop_btn_breath_effect is not None:
                try:
                    self._stop_btn_breath_effect.setOpacity(1.0)
                except RuntimeError:
                    pass
            self._stop_btn.setGraphicsEffect(None)
            self._stop_btn_breath_anim = None
            self._stop_btn_breath_effect = None
            return

        if self._stop_btn_breath_effect is None:
            self._stop_btn_breath_effect = QGraphicsOpacityEffect(self._stop_btn)
        try:
            self._stop_btn.setGraphicsEffect(self._stop_btn_breath_effect)
        except RuntimeError:
            self._stop_btn_breath_effect = QGraphicsOpacityEffect(self._stop_btn)
            self._stop_btn.setGraphicsEffect(self._stop_btn_breath_effect)
            self._stop_btn_breath_anim = None

        if self._stop_btn_breath_anim is None:
            self._stop_btn_breath_anim = QPropertyAnimation(
                self._stop_btn_breath_effect,
                b"opacity",
                self,
            )
            self._stop_btn_breath_anim.setDuration(1000)
            self._stop_btn_breath_anim.setStartValue(1.0)
            self._stop_btn_breath_anim.setKeyValueAt(0.5, 0.68)
            self._stop_btn_breath_anim.setEndValue(1.0)
            self._stop_btn_breath_anim.setLoopCount(-1)
            self._stop_btn_breath_anim.setEasingCurve(QEasingCurve.Type.InOutSine)

        self._stop_btn_breath_anim.start()

    def _set_mode_live_breathing(self, enabled: bool) -> None:
        if not enabled:
            if self._mode_live_breath_anim is not None:
                self._mode_live_breath_anim.stop()
            if self._mode_live_breath_effect is not None:
                try:
                    self._mode_live_breath_effect.setOpacity(1.0)
                except RuntimeError:
                    pass
            self._mode_btn.setGraphicsEffect(None)
            # Qt may delete the installed effect when detached; reset cached refs.
            self._mode_live_breath_anim = None
            self._mode_live_breath_effect = None
            return

        if self._mode_live_breath_effect is None:
            self._mode_live_breath_effect = QGraphicsOpacityEffect(self._mode_btn)
        try:
            self._mode_btn.setGraphicsEffect(self._mode_live_breath_effect)
        except RuntimeError:
            self._mode_live_breath_effect = QGraphicsOpacityEffect(self._mode_btn)
            self._mode_btn.setGraphicsEffect(self._mode_live_breath_effect)
            self._mode_live_breath_anim = None

        if self._mode_live_breath_anim is None:
            self._mode_live_breath_anim = QPropertyAnimation(
                self._mode_live_breath_effect,
                b"opacity",
                self,
            )
            self._mode_live_breath_anim.setDuration(1300)
            self._mode_live_breath_anim.setStartValue(1.0)
            self._mode_live_breath_anim.setKeyValueAt(0.5, 0.72)
            self._mode_live_breath_anim.setEndValue(1.0)
            self._mode_live_breath_anim.setLoopCount(-1)
            self._mode_live_breath_anim.setEasingCurve(QEasingCurve.Type.InOutSine)

        self._mode_live_breath_anim.start()


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
