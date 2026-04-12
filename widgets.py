from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QTableWidget


class DeselectableTableWidget(QTableWidget):
    """Clears selection when clicking blank table whitespace."""

    def __init__(self, rows: int, columns: int) -> None:
        super().__init__(rows, columns)
        self._preserve_selection_callback: Callable[[], bool] | None = None

    def set_preserve_selection_callback(self, callback: Callable[[], bool]) -> None:
        self._preserve_selection_callback = callback

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        # Let arrow keys propagate to the parent window for global shortcuts.
        if event is not None and event.key() in (
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        ):
            event.ignore()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            pt = event.position().toPoint()
            row = self.rowAt(pt.y())
            col = self.columnAt(pt.x())
            if row < 0 or col < 0:
                if self._preserve_selection_callback is not None and self._preserve_selection_callback():
                    event.accept()
                    return
                self.clearSelection()
                self.setCurrentCell(-1, -1)
                event.accept()
                return
        super().mousePressEvent(event)


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch gui.py to start JingleAllTheDay.")
    raise SystemExit(1)
