from __future__ import annotations

import json
from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDrag, QKeyEvent, QMouseEvent
from PyQt6.QtCore import QMimeData
from PyQt6.QtWidgets import QTableWidget


class DeselectableTableWidget(QTableWidget):
    """Clears selection when clicking blank table whitespace."""

    def __init__(self, rows: int, columns: int) -> None:
        super().__init__(rows, columns)
        self._preserve_selection_callback: Callable[[], bool] | None = None
        self._drag_payload_callback: Callable[[], list[dict[str, str]]] | None = None
        self._drag_mime_type = ""

    def set_preserve_selection_callback(self, callback: Callable[[], bool]) -> None:
        self._preserve_selection_callback = callback

    def set_drag_payload_callback(
        self,
        callback: Callable[[], list[dict[str, str]]],
        mime_type: str,
    ) -> None:
        self._drag_payload_callback = callback
        self._drag_mime_type = str(mime_type).strip()

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

    def startDrag(self, supportedActions: Qt.DropAction) -> None:
        if self._drag_payload_callback is None or not self._drag_mime_type:
            super().startDrag(supportedActions)
            return

        payload = self._drag_payload_callback()
        if not payload:
            super().startDrag(supportedActions)
            return

        mime = QMimeData()
        try:
            mime.setData(
                self._drag_mime_type,
                json.dumps(payload, ensure_ascii=True).encode("utf-8"),
            )
        except Exception:
            super().startDrag(supportedActions)
            return

        line_text = []
        for item in payload:
            path_text = str(item.get("path", "")).strip()
            if path_text:
                line_text.append(path_text)
        if line_text:
            mime.setText("\n".join(line_text))

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
