from __future__ import annotations

import math
import subprocess
import wave
from pathlib import Path
from typing import Any

from app_helpers import format_duration_hms as _format_duration_hms
from app_helpers import probe_duration_seconds as _probe_duration_seconds
from app_helpers import coerce_volume_percent as _coerce_volume_percent
from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent, QPaintEvent, QPainter, QPen
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_has_qt_multimedia = False
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

    _has_qt_multimedia = True
except ModuleNotFoundError:
    pass


class WaveformWidget(QWidget):
    markersChanged = pyqtSignal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._peaks: list[float] = []
        self._start_seconds = 0.0
        self._stop_seconds = 0.0
        self._duration_seconds = 0.0
        self._playhead_seconds: float | None = None
        self._playhead_active = False
        self._drag_target: str | None = None
        self._marker_pick_px = 12
        self.setMouseTracking(True)
        self.setMinimumHeight(180)

    def set_waveform(self, peaks: list[float], duration_seconds: float) -> None:
        self._peaks = peaks
        self._duration_seconds = max(0.0, float(duration_seconds))
        self.update()

    def set_markers(self, start_seconds: float, stop_seconds: float) -> None:
        self._start_seconds = max(0.0, float(start_seconds))
        self._stop_seconds = max(0.0, float(stop_seconds))
        self.update()

    def set_playhead_seconds(self, playhead_seconds: float | None) -> None:
        if playhead_seconds is None:
            self._playhead_seconds = None
        else:
            self._playhead_seconds = max(0.0, float(playhead_seconds))
        self.update()

    def set_playhead_active(self, is_active: bool) -> None:
        self._playhead_active = bool(is_active)
        self.update()

    def _x_for_time(self, seconds: float, width: int) -> int:
        if self._duration_seconds <= 0.0:
            return 0
        clamped = min(max(seconds, 0.0), self._duration_seconds)
        ratio = clamped / self._duration_seconds
        return int(round(ratio * max(0, width - 1)))

    def _time_for_x(self, x: int, width: int) -> float:
        if self._duration_seconds <= 0.0 or width <= 1:
            return 0.0
        clamped_x = min(max(0, x), width - 1)
        ratio = clamped_x / float(width - 1)
        return ratio * self._duration_seconds

    def _nearest_marker(self, x: int) -> str:
        width = self.width()
        start_x = self._x_for_time(self._start_seconds, width)
        stop_x = self._x_for_time(self._stop_seconds, width)
        start_dist = abs(x - start_x)
        stop_dist = abs(x - stop_x)
        if start_dist <= self._marker_pick_px or stop_dist <= self._marker_pick_px:
            return "start" if start_dist <= stop_dist else "stop"
        return "start" if x <= ((start_x + stop_x) // 2) else "stop"

    def _set_marker_from_x(self, target: str, x: int) -> None:
        seconds = self._time_for_x(x, self.width())
        if target == "start":
            self._start_seconds = min(seconds, self._stop_seconds)
        else:
            self._stop_seconds = max(seconds, self._start_seconds)
        self.markersChanged.emit(self._start_seconds, self._stop_seconds)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._duration_seconds <= 0.0:
            super().mousePressEvent(event)
            return
        self._drag_target = self._nearest_marker(event.position().toPoint().x())
        self._set_marker_from_x(self._drag_target, event.position().toPoint().x())
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        x = event.position().toPoint().x()
        if self._drag_target is not None:
            self._set_marker_from_x(self._drag_target, x)
            event.accept()
            return

        if self._duration_seconds > 0.0:
            marker = self._nearest_marker(x)
            width = self.width()
            marker_x = self._x_for_time(
                self._start_seconds if marker == "start" else self._stop_seconds,
                width,
            )
            if abs(x - marker_x) <= self._marker_pick_px:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_target is not None:
            self._drag_target = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        width = self.width()
        height = self.height()
        if width <= 0 or height <= 0:
            return

        painter.fillRect(self.rect(), QColor("#111417"))

        if not self._peaks:
            painter.setPen(QColor("#8a8f96"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Waveform unavailable")
            return

        center_y = height // 2
        waveform_pen = QPen(QColor("#58bfa6"))
        waveform_pen.setWidth(1)
        painter.setPen(waveform_pen)

        peak_count = len(self._peaks)
        scale = max(1, peak_count - 1)
        for i, peak in enumerate(self._peaks):
            x = int(round(i * (width - 1) / scale))
            amplitude = max(0.0, min(1.0, float(peak)))
            half = int(amplitude * (height * 0.44))
            painter.drawLine(x, center_y - half, x, center_y + half)

        start_x = self._x_for_time(self._start_seconds, width)
        stop_x = self._x_for_time(self._stop_seconds, width)
        if stop_x < start_x:
            start_x, stop_x = stop_x, start_x

        # Highlight the active playback window between start and stop.
        painter.fillRect(start_x, 0, max(1, stop_x - start_x), height, QColor(90, 191, 166, 40))

        marker_pen = QPen(QColor("#ffcf5a"))
        marker_pen.setWidth(2)
        painter.setPen(marker_pen)
        painter.drawLine(start_x, 0, start_x, height)

        marker_pen.setColor(QColor("#ff6b6b"))
        painter.setPen(marker_pen)
        painter.drawLine(stop_x, 0, stop_x, height)

        if self._playhead_seconds is not None:
            playhead_x = self._x_for_time(self._playhead_seconds, width)
            if self._playhead_active:
                playhead_pen = QPen(QColor("#6dff7a"))
                playhead_pen.setWidth(3)
            else:
                playhead_pen = QPen(QColor("#2ea043"))
                playhead_pen.setWidth(2)
            painter.setPen(playhead_pen)
            painter.drawLine(playhead_x, 0, playhead_x, height)


class JingleEditDialog(QDialog):
    def __init__(
        self,
        file_path: Path,
        duration_seconds: float,
        start_seconds: float,
        stop_seconds: float,
        live_output_device: str,
        preview_output_device: str,
        live_volume_percent: int,
        preview_volume_percent: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit Jingle - {file_path.name}")
        self.resize(900, 420)

        self._path = file_path
        self._duration_seconds = max(0.0, float(duration_seconds))
        if self._duration_seconds <= 0.0:
            self._duration_seconds = max(0.0, _probe_duration_seconds(file_path))

        default_stop = self._duration_seconds
        self._start_seconds = min(max(0.0, float(start_seconds)), default_stop)
        self._stop_seconds = min(max(0.0, float(stop_seconds)), default_stop)
        if self._stop_seconds <= self._start_seconds:
            self._start_seconds = 0.0
            self._stop_seconds = default_stop

        self._live_output_device = live_output_device.strip()
        self._preview_output_device = preview_output_device.strip()
        self._playback_volume_percent = _coerce_volume_percent(preview_volume_percent)

        self._player: QMediaPlayer | None = None
        self._audio_output: QAudioOutput | None = None
        self._is_muted = False
        self._is_looping = False
        self._seek_to_start_pending = False
        self._seek_muted_temporarily = False
        self._clip_boundary_handling = False
        self._clip_guard_timer = QTimer(self)
        self._clip_guard_timer.setInterval(40)
        self._clip_guard_timer.timeout.connect(self._on_clip_guard_tick)

        root = QVBoxLayout(self)

        self._waveform = WaveformWidget(self)
        peaks = _load_waveform_peaks(file_path)
        self._waveform.set_waveform(peaks, self._duration_seconds)
        self._waveform.set_markers(self._start_seconds, self._stop_seconds)
        root.addWidget(self._waveform)

        self._scale_label = QLabel(self)
        self._scale_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._scale_label)

        self._selection_label = QLabel(self)
        self._selection_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._selection_label)

        controls = QGridLayout()
        controls.setHorizontalSpacing(12)

        self._start_spin = QDoubleSpinBox()
        self._stop_spin = QDoubleSpinBox()

        for spin in (self._start_spin, self._stop_spin):
            spin.setRange(0.0, self._duration_seconds)
            spin.setDecimals(3)
            spin.setSingleStep(0.01)
            spin.setSuffix(" s")

        controls.addWidget(QLabel("Start"), 0, 0)
        controls.addWidget(self._start_spin, 0, 1)

        controls.addWidget(QLabel("Stop"), 1, 0)
        controls.addWidget(self._stop_spin, 1, 1)

        root.addLayout(controls)

        tips_row = QHBoxLayout()
        tips = QLabel("Drag start/stop markers directly on the waveform or type exact values.")
        tips.setStyleSheet("color: #7f8790;")
        tips_row.addWidget(tips)
        tips_row.addStretch()
        root.addLayout(tips_row)

        self._output_label = QLabel("Output: unknown")
        self._output_label.setStyleSheet("color: #7f8790;")
        root.addWidget(self._output_label)

        playback_row = QHBoxLayout()
        self._play_pause_btn = QPushButton("Play")
        self._play_pause_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._play_pause_btn.clicked.connect(self._on_play_pause_clicked)
        playback_row.addWidget(self._play_pause_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        playback_row.addWidget(self._stop_btn)

        self._mute_btn = QPushButton("Mute")
        self._mute_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._mute_btn.clicked.connect(self._on_mute_clicked)
        playback_row.addWidget(self._mute_btn)

        self._loop_btn = QPushButton("Loop Off")
        self._loop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._loop_btn.clicked.connect(self._on_loop_clicked)
        playback_row.addWidget(self._loop_btn)

        root.addLayout(playback_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        if self._duration_seconds <= 0.0:
            self._start_spin.setEnabled(False)
            self._stop_spin.setEnabled(False)

        self._waveform.markersChanged.connect(self._on_waveform_markers_changed)
        self._start_spin.valueChanged.connect(self._on_start_spin_changed)
        self._stop_spin.valueChanged.connect(self._on_stop_spin_changed)

        self._init_player()
        self._sync_controls_from_values()

    def selected_clip_points(self) -> tuple[float, float]:
        return self._start_seconds, self._stop_seconds

    def _init_player(self) -> None:
        if not _has_qt_multimedia:
            self._play_pause_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            self._mute_btn.setEnabled(False)
            self._loop_btn.setEnabled(False)
            self._output_label.setText("Output: Playback unavailable")
            return

        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)
        self._audio_output.setMuted(self._is_muted)
        self._audio_output.setVolume(self._playback_volume_percent / 100.0)
        self._apply_editor_output_device()

        self._player.durationChanged.connect(self._on_player_duration_changed)
        self._player.positionChanged.connect(self._on_player_position_changed)
        self._player.playbackStateChanged.connect(self._on_player_playback_state_changed)
        self._player.mediaStatusChanged.connect(self._on_player_media_status_changed)
        self._update_mute_button_visual()
        self._update_loop_button_visual()

    def _normalize_device_key(self, value: str) -> str:
        return value.strip().casefold()

    def _preferred_device_name(self) -> str:
        return self._preview_output_device

    def _apply_editor_output_device(self) -> None:
        if self._audio_output is None or not _has_qt_multimedia:
            return

        preferred_name = self._preferred_device_name()
        target_device = QMediaDevices.defaultAudioOutput()
        output_source = f"Preview (system default: {target_device.description()})"

        if preferred_name:
            for device in QMediaDevices.audioOutputs():
                if self._normalize_device_key(device.description()) == self._normalize_device_key(preferred_name):
                    target_device = device
                    output_source = f"Preview device: {target_device.description()}"
                    break
            else:
                if self._live_output_device:
                    for device in QMediaDevices.audioOutputs():
                        if self._normalize_device_key(device.description()) == self._normalize_device_key(self._live_output_device):
                            target_device = device
                            output_source = f"Live fallback: {target_device.description()}"
                            break
                else:
                    output_source = f"Preview unavailable, using default: {target_device.description()}"

        self._audio_output.setDevice(target_device)
        self._output_label.setText(f"Output: {output_source}")

    def _on_play_pause_clicked(self) -> None:
        if self._player is None:
            return

        state = self._player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            return

        if state == QMediaPlayer.PlaybackState.PausedState:
            self._player.play()
            return

        self._apply_editor_output_device()
        start_ms = self._prepare_start_seek(temporary_mute_for_seek=True)
        self._player.setSource(QUrl.fromLocalFile(str(self._path)))
        self._player.play()
        if self._seek_to_start_pending:
            self._player.setPosition(start_ms)
        else:
            self._enforce_clip_window(self._player.position())

    def _on_stop_clicked(self) -> None:
        if self._player is None:
            return
        if self._player.playbackState() in (
            QMediaPlayer.PlaybackState.PlayingState,
            QMediaPlayer.PlaybackState.PausedState,
        ):
            self._player.stop()
        start_ms = self._clip_start_ms()
        self._player.setPosition(start_ms)
        self._waveform.set_playhead_seconds(start_ms / 1000.0)
        self._seek_muted_temporarily = False
        if self._audio_output is not None:
            self._audio_output.setMuted(self._is_muted)

    def _update_mute_button_visual(self) -> None:
        self._mute_btn.setText("Unmute" if self._is_muted else "Mute")

    def _on_mute_clicked(self) -> None:
        if self._audio_output is None:
            return
        self._is_muted = not self._is_muted
        if self._seek_muted_temporarily and not self._is_muted:
            self._audio_output.setMuted(True)
        else:
            self._audio_output.setMuted(self._is_muted)
        self._update_mute_button_visual()

    def _update_loop_button_visual(self) -> None:
        self._loop_btn.setText("Loop On" if self._is_looping else "Loop Off")

    def _on_loop_clicked(self) -> None:
        self._is_looping = not self._is_looping
        self._update_loop_button_visual()

    def _clip_start_ms(self) -> int:
        return int(round(self._start_seconds * 1000.0))

    def _prepare_start_seek(self, temporary_mute_for_seek: bool) -> int:
        start_ms = self._clip_start_ms()
        self._clip_boundary_handling = False
        self._seek_to_start_pending = self._start_seconds > 0.0
        if self._audio_output is not None:
            if temporary_mute_for_seek and self._seek_to_start_pending and not self._is_muted:
                self._seek_muted_temporarily = True
                self._audio_output.setMuted(True)
            else:
                self._seek_muted_temporarily = False
                self._audio_output.setMuted(self._is_muted)
        return start_ms

    def _restart_loop_from_start(self) -> None:
        if self._player is None:
            return
        start_ms = self._prepare_start_seek(temporary_mute_for_seek=False)
        self._player.setPosition(start_ms)
        self._player.play()

    def _on_player_duration_changed(self, _duration_ms: int) -> None:
        return

    def _on_player_position_changed(self, position_ms: int) -> None:
        if self._player is None:
            return

        if self._enforce_clip_window(position_ms):
            return

        self._waveform.set_playhead_seconds(position_ms / 1000.0)

    def _on_clip_guard_tick(self) -> None:
        if self._player is None:
            return
        self._enforce_clip_window(self._player.position())

    def _enforce_clip_window(self, position_ms: int) -> bool:
        if self._player is None:
            return False

        start_ms = int(round(self._start_seconds * 1000.0))
        stop_ms = int(round(self._stop_seconds * 1000.0))

        if self._seek_to_start_pending:
            if position_ms + 120 < start_ms:
                self._player.setPosition(start_ms)
                return True
            self._seek_to_start_pending = False
            if self._seek_muted_temporarily:
                self._seek_muted_temporarily = False
                if self._audio_output is not None:
                    self._audio_output.setMuted(self._is_muted)

        if (
            stop_ms > start_ms
            and position_ms >= stop_ms
            and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        ):
            if self._clip_boundary_handling:
                return True
            self._clip_boundary_handling = True
            if self._is_looping:
                self._restart_loop_from_start()
                return True
            self._player.stop()
            self._player.setPosition(start_ms)
            self._clip_boundary_handling = False
            return True
        return False

    def _on_player_playback_state_changed(self, _state: Any) -> None:
        if self._player is None:
            return
        state = self._player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._play_pause_btn.setText("Pause")
            if not self._clip_guard_timer.isActive():
                self._clip_guard_timer.start()
            self._waveform.set_playhead_active(True)
            self._waveform.set_playhead_seconds(self._player.position() / 1000.0)
        else:
            self._play_pause_btn.setText("Play")
            if self._clip_guard_timer.isActive():
                self._clip_guard_timer.stop()
            self._waveform.set_playhead_active(False)
            if self._seek_muted_temporarily:
                self._seek_muted_temporarily = False
                if self._audio_output is not None:
                    self._audio_output.setMuted(self._is_muted)
            if state == QMediaPlayer.PlaybackState.StoppedState:
                self._waveform.set_playhead_seconds(self._clip_start_ms() / 1000.0)

    def _on_player_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if self._player is None:
            return
        if status != QMediaPlayer.MediaStatus.EndOfMedia:
            return
        if not self._is_looping:
            return
        self._restart_loop_from_start()

    def _on_waveform_markers_changed(self, start_seconds: float, stop_seconds: float) -> None:
        self._start_seconds = max(0.0, min(float(start_seconds), self._duration_seconds))
        self._stop_seconds = max(0.0, min(float(stop_seconds), self._duration_seconds))
        if self._stop_seconds < self._start_seconds:
            self._start_seconds, self._stop_seconds = self._stop_seconds, self._start_seconds
        self._sync_controls_from_values(source="waveform")

    def _on_start_spin_changed(self, value: float) -> None:
        self._start_seconds = max(0.0, float(value))
        if self._start_seconds > self._stop_seconds:
            self._stop_seconds = self._start_seconds
        self._sync_controls_from_values(source="start_spin")

    def _on_stop_spin_changed(self, value: float) -> None:
        self._stop_seconds = min(self._duration_seconds, max(0.0, float(value)))
        if self._stop_seconds < self._start_seconds:
            self._start_seconds = self._stop_seconds
        self._sync_controls_from_values(source="stop_spin")

    def _sync_controls_from_values(self, source: str | None = None) -> None:
        if source != "start_spin":
            self._start_spin.blockSignals(True)
            self._start_spin.setValue(self._start_seconds)
            self._start_spin.blockSignals(False)
        if source != "stop_spin":
            self._stop_spin.blockSignals(True)
            self._stop_spin.setValue(self._stop_seconds)
            self._stop_spin.blockSignals(False)

        self._waveform.set_markers(self._start_seconds, self._stop_seconds)
        self._scale_label.setText(
            f"0:00           { _format_duration_hms(self._duration_seconds / 2.0) }           {_format_duration_hms(self._duration_seconds)}"
        )
        selected_duration = max(0.0, self._stop_seconds - self._start_seconds)
        self._selection_label.setText(
            f"Start: {_format_duration_hms(self._start_seconds)} | "
            f"Stop: {_format_duration_hms(self._stop_seconds)} | "
            f"Selection: {_format_duration_hms(selected_duration)}"
        )

    def accept(self) -> None:
        if self._player is not None:
            self._player.stop()
        super().accept()

    def reject(self) -> None:
        if self._player is not None:
            self._player.stop()
        super().reject()


def _load_waveform_peaks(path: Path, bucket_count: int = 900) -> list[float]:
    if bucket_count <= 0:
        return []

    suffix = path.suffix.lower()
    if suffix in {".wav", ".wave"}:
        peaks = _load_wav_waveform_peaks(path, bucket_count)
        if peaks:
            return peaks

    peaks = _load_ffmpeg_waveform_peaks(path, bucket_count)
    if peaks:
        return peaks

    return []


def _load_wav_waveform_peaks(path: Path, bucket_count: int) -> list[float]:
    try:
        with wave.open(str(path), "rb") as wav_file:
            frame_count = wav_file.getnframes()
            channel_count = max(1, wav_file.getnchannels())
            sample_width = wav_file.getsampwidth()
            raw = wav_file.readframes(frame_count)
    except (wave.Error, OSError):
        return []

    if frame_count <= 0 or sample_width not in {1, 2, 4}:
        return []

    # Convert first channel samples to float amplitudes in [0, 1].
    amplitudes: list[float] = []
    step = sample_width * channel_count
    max_value = float((1 << (8 * sample_width - 1)) - 1)
    for frame_idx in range(frame_count):
        offset = frame_idx * step
        sample_bytes = raw[offset : offset + sample_width]
        if len(sample_bytes) != sample_width:
            break
        if sample_width == 1:
            sample = int.from_bytes(sample_bytes, byteorder="little", signed=False) - 128
        else:
            sample = int.from_bytes(sample_bytes, byteorder="little", signed=True)
        amplitudes.append(min(1.0, abs(sample) / max_value if max_value else 0.0))

    return _reduce_to_peaks(amplitudes, bucket_count)


def _load_ffmpeg_waveform_peaks(path: Path, bucket_count: int) -> list[float]:
    ffmpeg = shutil_which("ffmpeg")
    if not ffmpeg:
        return []

    cmd = [
        ffmpeg,
        "-v",
        "error",
        "-i",
        str(path),
        "-ac",
        "1",
        "-ar",
        "8000",
        "-f",
        "s16le",
        "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=8.0, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0 or not result.stdout:
        return []

    raw = result.stdout
    sample_count = len(raw) // 2
    if sample_count <= 0:
        return []

    amplitudes: list[float] = []
    for i in range(sample_count):
        value = int.from_bytes(raw[i * 2 : i * 2 + 2], byteorder="little", signed=True)
        amplitudes.append(min(1.0, abs(value) / 32767.0))

    return _reduce_to_peaks(amplitudes, bucket_count)


def _reduce_to_peaks(amplitudes: list[float], bucket_count: int) -> list[float]:
    if not amplitudes:
        return []

    count = len(amplitudes)
    if count <= bucket_count:
        return amplitudes

    peaks: list[float] = []
    for bucket in range(bucket_count):
        start = int(math.floor(bucket * count / bucket_count))
        end = int(math.floor((bucket + 1) * count / bucket_count))
        if end <= start:
            end = min(count, start + 1)
        window = amplitudes[start:end]
        peaks.append(max(window) if window else 0.0)
    return peaks


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch gui.py to start JingleAllTheDay.")
    raise SystemExit(1)
