"""Level meter widgets shared by the sample pad and sequencer mixers."""

from __future__ import annotations

import math

from PyQt6 import QtCore, QtGui, QtWidgets

_METER_DB_FLOOR = -60.0
_METER_DB_TOP_SPLIT = -20.0
_METER_TOP_PORTION = 0.62


def _meter_ratio_for_db(db_value: float) -> float:
    """Map dB to a vertical ratio with expanded upper-range resolution."""
    db = max(_METER_DB_FLOOR, min(0.0, float(db_value)))
    if db >= _METER_DB_TOP_SPLIT:
        # Allocate most vertical space to the critical -20..0 dB region.
        return ((0.0 - db) / (0.0 - _METER_DB_TOP_SPLIT)) * _METER_TOP_PORTION
    return _METER_TOP_PORTION + (
        ((_METER_DB_TOP_SPLIT - db) / (_METER_DB_TOP_SPLIT - _METER_DB_FLOOR))
        * (1.0 - _METER_TOP_PORTION)
    )


class DbScaleWidget(QtWidgets.QWidget):
    def __init__(self, strip_height: int, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(28)
        self.setFixedHeight(strip_height)

    def set_strip_height(self, strip_height: int) -> None:
        self.setFixedHeight(max(80, int(strip_height)))

    @staticmethod
    def _y_for_db(db_value: float, height: int) -> int:
        top_pad = 6
        bottom_pad = 6
        usable = max(1, height - top_pad - bottom_pad)
        ratio = _meter_ratio_for_db(db_value)
        return int(round(top_pad + ratio * usable))

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
        width = self.width()
        height = self.height()
        if width <= 0 or height <= 0:
            return

        major_candidates = [0, -3, -6, -9, -12, -15, -18, -20, -24, -30, -36, -42, -48, -54, -60]
        min_major_gap = max(12, int(height / 13))
        major_ticks: list[int] = []
        last_major_y: int | None = None
        for idx, tick in enumerate(major_candidates):
            y = self._y_for_db(float(tick), height)
            force_keep = idx == 0 or idx == len(major_candidates) - 1
            if force_keep or last_major_y is None or (y - last_major_y) >= min_major_gap:
                major_ticks.append(tick)
                last_major_y = y

        minor_ticks: list[float] = []
        for idx in range(len(major_ticks) - 1):
            top_db = float(major_ticks[idx])
            bottom_db = float(major_ticks[idx + 1])
            y_top = self._y_for_db(top_db, height)
            y_bottom = self._y_for_db(bottom_db, height)
            gap_px = abs(y_bottom - y_top)
            if gap_px >= 14:
                minor_ticks.append((top_db + bottom_db) * 0.5)
            if gap_px >= 28:
                minor_ticks.append(top_db + (bottom_db - top_db) * 0.25)
                minor_ticks.append(top_db + (bottom_db - top_db) * 0.75)

        major_pen = QtGui.QPen(QtGui.QColor("#90a4ae"))
        minor_pen = QtGui.QPen(QtGui.QColor("#607d8b"))
        text_pen = QtGui.QPen(QtGui.QColor("#90a4ae"))

        painter.setPen(minor_pen)
        for tick in minor_ticks:
            y = self._y_for_db(float(tick), height)
            painter.drawLine(0, y, 7, y)

        painter.setPen(major_pen)
        for tick in major_ticks:
            y = self._y_for_db(float(tick), height)
            painter.drawLine(0, y, 10, y)

        painter.setPen(text_pen)
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        for tick in major_ticks:
            y = self._y_for_db(float(tick), height)
            label = "-inf" if tick <= -60 else str(tick)
            text_rect = QtCore.QRect(12, y - 6, max(1, width - 12), 12)
            painter.drawText(text_rect, QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter, label)


class PeakMeterWidget(QtWidgets.QWidget):
    def __init__(self, strip_height: int, color: str = "#26a69a", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._level = 0.0
        self._peak_hold = 0.0
        self._bar_color = QtGui.QColor(color)
        self._hold_color = QtGui.QColor("#fff59d")
        self.setFixedWidth(12)
        self.setFixedHeight(strip_height)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to reset peak hold")

    def set_strip_height(self, strip_height: int) -> None:
        self.setFixedHeight(max(80, int(strip_height)))

    def set_bar_color(self, color: str) -> None:
        self._bar_color = QtGui.QColor(color)
        self.update()

    def set_level(self, level: float) -> None:
        lvl = max(0.0, min(1.0, float(level)))
        if lvl >= self._peak_hold:
            self._peak_hold = lvl
        else:
            self._peak_hold = max(lvl, self._peak_hold * 0.985 - 0.001)
        self._level = lvl
        self.update()

    @staticmethod
    def _db_from_level(level: float) -> float:
        lvl = max(0.000001, min(1.0, float(level)))
        return max(_METER_DB_FLOOR, min(0.0, 20.0 * math.log10(lvl)))

    @staticmethod
    def _y_for_db(db_value: float, height: int) -> int:
        top_pad = 2
        bottom_pad = 2
        usable = max(1, height - top_pad - bottom_pad)
        ratio = _meter_ratio_for_db(db_value)
        return int(round(top_pad + ratio * usable))

    def reset_peak_hold(self) -> None:
        self._peak_hold = self._level
        self.update()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.reset_peak_hold()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return

        rect = self.rect()
        painter.setPen(QtGui.QPen(QtGui.QColor("#2f3a40")))
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#11161a")))
        painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 2, 2)

        level_db = self._db_from_level(self._level)
        level_y = self._y_for_db(level_db, h)
        bottom_y = h - 2
        level_h = max(0, bottom_y - level_y + 1)
        if level_h > 0:
            bar_rect = QtCore.QRect(1, level_y, max(1, w - 2), level_h)
            painter.fillRect(bar_rect, self._bar_color)

        hold_db = self._db_from_level(self._peak_hold)
        hold_y = self._y_for_db(hold_db, h)
        hold_y = max(1, min(h - 2, hold_y))
        painter.setPen(QtGui.QPen(self._hold_color, 1))
        painter.drawLine(1, hold_y, w - 2, hold_y)


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
