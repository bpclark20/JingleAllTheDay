"""UI widgets for sequencer timeline editor."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QRect, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget, QVBoxLayout


class TimelineRuler(QWidget):
    """Ruler showing beat numbers or time (HH:MM:SS)."""

    beat_clicked = pyqtSignal(float)  # User clicked on a beat position
    
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHeight(40)
        self.pixels_per_beat = 40  # 40 pixels = 1 beat
        self.show_time_format = False  # False = beats, True = HH:MM:SS
        self.bpm = 120.0
        self.current_beat = 0.0
        self.setMinimumHeight(40)
        self.setMaximumHeight(40)

    def setHeight(self, height: int) -> None:
        """Set height of ruler."""
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)

    def set_display_mode(self, show_time: bool) -> None:
        """Toggle between beat and time display."""
        self.show_time_format = show_time
        self.update()

    def set_bpm(self, bpm: float) -> None:
        """Set BPM for time calculations."""
        self.bpm = max(1.0, bpm)

    def set_pixels_per_beat(self, ppb: float) -> None:
        """Set zoom level (pixels per beat)."""
        self.pixels_per_beat = max(10.0, min(200.0, ppb))
        self.update()

    def set_current_beat(self, beat: float) -> None:
        """Update playhead position."""
        self.current_beat = beat
        self.update()

    def paintEvent(self, event) -> None:
        """Draw the ruler."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(240, 240, 240))

        # Draw beat grid
        painter.setPen(QPen(QColor(200, 200, 200), 1))
        
        beat = 0
        while beat * self.pixels_per_beat < self.width() + 100:
            x = int(beat * self.pixels_per_beat)
            
            # Major beat line (every 4 beats = measure)
            if int(beat) % 4 == 0:
                painter.drawLine(x, self.height() - 10, x, self.height())
                # Draw text label
                if self.show_time_format:
                    seconds = beat / (self.bpm / 60.0)
                    minutes, secs = divmod(int(seconds), 60)
                    label = f"{minutes}:{secs:02d}"
                else:
                    label = str(int(beat))
                painter.setFont(QFont("Arial", 9))
                painter.setPen(QColor(100, 100, 100))
                painter.drawText(
                    x + 5,
                    15,
                    40,
                    20,
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    label,
                )
            else:
                # Minor beat line
                painter.setPen(QPen(QColor(200, 200, 200), 1))
                painter.drawLine(x, self.height() - 5, x, self.height())
            
            beat += 0.5

        # Draw playhead
        playhead_x = int(self.current_beat * self.pixels_per_beat)
        painter.setPen(QPen(QColor(255, 0, 0), 2))
        painter.drawLine(playhead_x, 0, playhead_x, self.height())

        painter.end()

    def mousePressEvent(self, event) -> None:
        """Handle click to seek."""
        if event.button() == Qt.MouseButton.LeftButton:
            beat = event.pos().x() / self.pixels_per_beat
            self.beat_clicked.emit(max(0.0, beat))


class TrackView(QWidget):
    """Single track timeline with triggers displayed as boxes."""

    trigger_selected = pyqtSignal(int, int)  # Track index, trigger index
    trigger_double_clicked = pyqtSignal(int, int)  # Track index, trigger index
    empty_clicked = pyqtSignal(int, float)  # Track index, beat
    trigger_moved = pyqtSignal(int, int, float)  # Track index, trigger index, beat
    trigger_resized = pyqtSignal(int, int, float)  # Track index, trigger index, duration
    trigger_range_changed = pyqtSignal(int, int, float, float)  # track, trigger, beat, duration
    trigger_edit_finished = pyqtSignal(int, int)  # Track index, trigger index
    
    def __init__(self, track_index: int, track_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.track_index = track_index
        self.track_name = track_name
        self.triggers = []  # List of (beat_position, duration_beats) tuples
        self.waveform_peaks: list[float] = []
        self.source_duration_seconds: float | None = None
        self.bpm = 120.0
        self.pixels_per_beat = 40
        self.selected_trigger_index = -1
        self._drag_mode: str | None = None  # "move", "resize_left", or "resize_right"
        self._drag_trigger_index = -1
        self._drag_anchor_beat = 0.0
        self._drag_original_start = 0.0
        self._drag_original_duration = 1.0
        self._drag_changed = False
        self.setMinimumHeight(60)
        self.setMaximumHeight(60)

    def set_triggers(self, triggers: list[tuple[float, float]]) -> None:
        """Update triggers for display."""
        self.triggers = triggers
        self.update()

    def set_pixels_per_beat(self, ppb: float) -> None:
        """Set zoom level."""
        self.pixels_per_beat = max(10.0, min(200.0, ppb))
        self.update()

    def set_bpm(self, bpm: float) -> None:
        """Set BPM for beat-to-seconds conversion in waveform rendering."""
        self.bpm = max(1.0, float(bpm))
        self.update()

    def set_selected_trigger(self, index: int) -> None:
        """Highlight selected trigger."""
        self.selected_trigger_index = index
        self.update()

    def set_waveform_peaks(self, peaks: list[float]) -> None:
        """Set normalized waveform peaks [0..1] for this track source."""
        self.waveform_peaks = [max(0.0, min(1.0, float(v))) for v in peaks] if peaks else []
        self.update()

    def set_source_duration_seconds(self, duration_seconds: float | None) -> None:
        """Set source clip duration for loop-aware waveform preview."""
        if duration_seconds is None:
            self.source_duration_seconds = None
        else:
            self.source_duration_seconds = max(0.0, float(duration_seconds))
        self.update()

    def paintEvent(self, event) -> None:
        """Draw track with triggers."""
        painter = QPainter(self)
        
        # Background
        if self.selected_trigger_index >= 0:
            painter.fillRect(self.rect(), QColor(250, 250, 200))
        else:
            painter.fillRect(self.rect(), QColor(255, 255, 255))

        # Draw track label
        painter.setPen(QColor(100, 100, 100))
        painter.setFont(QFont("Arial", 9))
        painter.drawText(
            5,
            5,
            100,
            20,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.track_name[:15],
        )

        # Draw grid
        painter.setPen(QPen(QColor(220, 220, 220), 1))
        beat = 0
        while beat * self.pixels_per_beat < self.width() + 100:
            x = int(beat * self.pixels_per_beat)
            if int(beat) % 4 == 0:
                painter.drawLine(x, 25, x, self.height())
            beat += 1

        # Draw triggers as boxes
        for i, (beat_pos, duration) in enumerate(self.triggers):
            x = int(beat_pos * self.pixels_per_beat)
            width = int(duration * self.pixels_per_beat)
            rect = QRect(x, 30, max(5, width), 25)

            # Color based on selection
            if i == self.selected_trigger_index:
                painter.fillRect(rect, QColor(100, 150, 255))
                painter.setPen(QPen(QColor(50, 100, 200), 2))
            else:
                painter.fillRect(rect, QColor(150, 200, 255))
                painter.setPen(QPen(QColor(100, 150, 200), 1))
            
            painter.drawRect(rect)

            # Draw waveform preview inside trigger bounds when available.
            if self.waveform_peaks:
                self._draw_waveform_in_rect(painter, rect, float(duration))

        # Border
        painter.setPen(QPen(QColor(180, 180, 180), 1))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)

        painter.end()

    def _draw_waveform_in_rect(self, painter: QPainter, rect: QRect, trigger_duration_beats: float) -> None:
        """Render a lightweight peak waveform inside a trigger rectangle."""
        if rect.width() <= 2 or rect.height() <= 2 or not self.waveform_peaks:
            return

        center_y = rect.y() + rect.height() // 2
        half_h = max(1, (rect.height() // 2) - 2)
        peaks = self.waveform_peaks
        peak_count = len(peaks)
        if peak_count <= 0:
            return

        painter.setPen(QPen(QColor(20, 70, 120, 140), 1))
        draw_width = max(1, rect.width() - 2)

        # Compute how many source-clip lengths this trigger spans.
        repeats = 1.0
        if self.source_duration_seconds and self.source_duration_seconds > 0.0 and self.bpm > 0.0:
            trigger_seconds = max(0.0, float(trigger_duration_beats)) / (self.bpm / 60.0)
            repeats = max(0.0, trigger_seconds / self.source_duration_seconds)

        for px in range(draw_width):
            px_fraction = px / max(1, draw_width - 1)
            if repeats <= 1.0:
                # Show only the played prefix of the source clip when shorter than full length.
                played_fraction = max(0.0, min(1.0, repeats))
                sample_index = int(px_fraction * peak_count * played_fraction)
            else:
                # Tile waveform when trigger duration extends beyond source clip duration.
                loop_phase = (px_fraction * repeats) % 1.0
                sample_index = int(loop_phase * peak_count)

            if sample_index >= peak_count:
                sample_index = peak_count - 1
            amp = peaks[sample_index]
            line_half = max(1, int(amp * half_h))
            x = rect.x() + 1 + px
            painter.drawLine(x, center_y - line_half, x, center_y + line_half)

    def mousePressEvent(self, event) -> None:
        """Handle click on trigger."""
        if event.button() == Qt.MouseButton.LeftButton:
            trigger_index = self._trigger_index_at(event.pos().x(), event.pos().y())
            if trigger_index >= 0:
                self.selected_trigger_index = trigger_index
                self.trigger_selected.emit(self.track_index, trigger_index)
                self._begin_drag(trigger_index, event.pos().x())
                self.update()
                return

            self.selected_trigger_index = -1
            self.update()
            beat_pos = max(0.0, event.pos().x() / self.pixels_per_beat)
            self.empty_clicked.emit(self.track_index, beat_pos)

    def mouseDoubleClickEvent(self, event) -> None:
        """Handle double click on trigger for duration editing."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        trigger_index = self._trigger_index_at(event.pos().x(), event.pos().y())
        if trigger_index >= 0:
            self.trigger_double_clicked.emit(self.track_index, trigger_index)

    def mouseMoveEvent(self, event) -> None:
        """Drag selected trigger to move/resize."""
        if self._drag_mode is None or self._drag_trigger_index < 0:
            return
        if self._drag_trigger_index >= len(self.triggers):
            return

        beat_pos = max(0.0, event.pos().x() / self.pixels_per_beat)

        if self._drag_mode == "move":
            delta = beat_pos - self._drag_anchor_beat
            new_start = max(0.0, self._drag_original_start + delta)
            self.trigger_moved.emit(self.track_index, self._drag_trigger_index, new_start)
            self._drag_changed = True
            return

        if self._drag_mode == "resize_right":
            new_duration = max(0.1, beat_pos - self._drag_original_start)
            self.trigger_resized.emit(self.track_index, self._drag_trigger_index, new_duration)
            self._drag_changed = True
            return

        if self._drag_mode == "resize_left":
            original_end = self._drag_original_start + self._drag_original_duration
            new_start = max(0.0, min(beat_pos, original_end - 0.1))
            new_duration = max(0.1, original_end - new_start)
            self.trigger_range_changed.emit(
                self.track_index,
                self._drag_trigger_index,
                new_start,
                new_duration,
            )
            self._drag_changed = True

    def mouseReleaseEvent(self, event) -> None:
        """Finish drag editing."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._drag_mode is not None and self._drag_trigger_index >= 0 and self._drag_changed:
            self.trigger_edit_finished.emit(self.track_index, self._drag_trigger_index)
        self._drag_mode = None
        self._drag_trigger_index = -1
        self._drag_changed = False

    def _begin_drag(self, trigger_index: int, x_pos: int) -> None:
        """Initialize drag context for move/resize."""
        if trigger_index < 0 or trigger_index >= len(self.triggers):
            return
        beat_start, duration = self.triggers[trigger_index]
        self._drag_trigger_index = trigger_index
        self._drag_anchor_beat = max(0.0, x_pos / self.pixels_per_beat)
        self._drag_original_start = float(beat_start)
        self._drag_original_duration = float(duration)
        self._drag_changed = False

        trigger_start_x = int(beat_start * self.pixels_per_beat)
        trigger_end_x = int((beat_start + duration) * self.pixels_per_beat)
        if abs(x_pos - trigger_start_x) <= 8:
            self._drag_mode = "resize_left"
        elif abs(x_pos - trigger_end_x) <= 8:
            self._drag_mode = "resize_right"
        else:
            self._drag_mode = "move"

    def _trigger_index_at(self, x_pos: int, y_pos: int) -> int:
        """Return trigger index under cursor, or -1 if none."""
        if not 25 < y_pos < 60:
            return -1
        for i, (beat, duration) in enumerate(self.triggers):
            trigger_x_start = beat * self.pixels_per_beat
            trigger_x_end = (beat + duration) * self.pixels_per_beat
            if trigger_x_start <= x_pos <= trigger_x_end:
                return i
        return -1


class TimelineCanvas(QWidget):
    """Scrollable canvas with all tracks and timeline."""

    trigger_selected = pyqtSignal(int, int)  # Track index, trigger index
    trigger_double_clicked = pyqtSignal(int, int)  # Track index, trigger index
    empty_clicked = pyqtSignal(int, float)  # Track index, beat
    trigger_moved = pyqtSignal(int, int, float)  # Track index, trigger index, beat
    trigger_resized = pyqtSignal(int, int, float)  # Track index, trigger index, duration
    trigger_range_changed = pyqtSignal(int, int, float, float)  # track, trigger, beat, duration
    trigger_edit_finished = pyqtSignal(int, int)  # Track index, trigger index

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.track_views = []
        self.ruler: TimelineRuler | None = None
        self.pixels_per_beat = 40
        self.bpm = 120.0
        self.current_beat = 0.0
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._refresh_vertical_extent()

    def add_track(self, track_index: int, track_name: str) -> TrackView:
        """Add a new track to the timeline."""
        track_view = TrackView(track_index, track_name)
        track_view.set_pixels_per_beat(self.pixels_per_beat)
        track_view.set_bpm(self.bpm)
        track_view.trigger_selected.connect(self.trigger_selected.emit)
        track_view.trigger_double_clicked.connect(self.trigger_double_clicked.emit)
        track_view.empty_clicked.connect(self.empty_clicked.emit)
        track_view.trigger_moved.connect(self.trigger_moved.emit)
        track_view.trigger_resized.connect(self.trigger_resized.emit)
        track_view.trigger_range_changed.connect(self.trigger_range_changed.emit)
        track_view.trigger_edit_finished.connect(self.trigger_edit_finished.emit)
        self.track_views.append(track_view)
        self.layout().addWidget(track_view)
        self._refresh_vertical_extent()
        return track_view

    def clear_tracks(self) -> None:
        """Remove all track widgets."""
        for track_view in self.track_views:
            self.layout().removeWidget(track_view)
            track_view.deleteLater()
        self.track_views.clear()
        self._refresh_vertical_extent()

    def set_ruler(self, ruler: TimelineRuler) -> None:
        """Set the ruler widget."""
        self.ruler = ruler
        self.layout().insertWidget(0, ruler)
        self._refresh_vertical_extent()

    def _refresh_vertical_extent(self) -> None:
        """Keep the canvas sized to its content so rows stay stacked at the top."""
        ruler_height = self.ruler.height() if self.ruler is not None else 0
        track_height = sum(track_view.maximumHeight() for track_view in self.track_views)
        total_height = max(0, ruler_height + track_height)
        self.setMinimumHeight(total_height)

    def set_zoom(self, pixels_per_beat: float) -> None:
        """Set zoom level for all tracks."""
        self.pixels_per_beat = max(10.0, min(200.0, pixels_per_beat))
        if self.ruler:
            self.ruler.set_pixels_per_beat(self.pixels_per_beat)
        for track_view in self.track_views:
            track_view.set_pixels_per_beat(self.pixels_per_beat)

    def set_bpm(self, bpm: float) -> None:
        """Set BPM for all elements."""
        self.bpm = bpm
        if self.ruler:
            self.ruler.set_bpm(bpm)
        for track_view in self.track_views:
            track_view.set_bpm(bpm)

    def set_current_beat(self, beat: float) -> None:
        """Update playhead position."""
        self.current_beat = beat
        if self.ruler:
            self.ruler.set_current_beat(beat)

    def update_track_triggers(self, track_index: int, triggers: list[tuple[float, float]]) -> None:
        """Update triggers for a specific track."""
        if 0 <= track_index < len(self.track_views):
            self.track_views[track_index].set_triggers(triggers)

    def set_track_waveform_peaks(self, track_index: int, peaks: list[float]) -> None:
        """Set waveform peaks for a specific track."""
        if 0 <= track_index < len(self.track_views):
            self.track_views[track_index].set_waveform_peaks(peaks)

    def set_track_source_duration_seconds(self, track_index: int, duration_seconds: float | None) -> None:
        """Set source duration metadata for a specific track."""
        if 0 <= track_index < len(self.track_views):
            self.track_views[track_index].set_source_duration_seconds(duration_seconds)

    def set_selected_trigger(self, track_index: int, trigger_index: int) -> None:
        """Highlight selected trigger on one track and clear other selections."""
        for i, track_view in enumerate(self.track_views):
            if i == track_index:
                track_view.set_selected_trigger(trigger_index)
            else:
                track_view.set_selected_trigger(-1)


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
