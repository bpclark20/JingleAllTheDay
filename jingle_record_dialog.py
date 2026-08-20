from __future__ import annotations

import shutil
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt, QUrl
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jingle_edit_dialog import WaveformWidget
from recording_engine import RecordingConfig, get_recording_engine
from waveform_cache import load_waveform_peaks

_has_qt_multimedia = False
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

    _has_qt_multimedia = True
except ModuleNotFoundError:
    QAudioOutput = None  # type: ignore[assignment]
    QMediaPlayer = None  # type: ignore[assignment]


class JingleRecordDialog(QDialog):
    def __init__(
        self,
        default_output_path: Path,
        input_device: int | str | None,
        initial_wav_subtype: str = "PCM_16",
        waveform_cache_dir: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Record Jingle")
        self.resize(980, 520)

        self._engine = get_recording_engine()
        self._default_output_path = Path(default_output_path)
        self._current_output_path = Path(default_output_path)
        self._input_device = input_device
        self._waveform_cache_dir = waveform_cache_dir
        self._live_peaks: list[float] = []
        self._recording = False
        self._has_recorded_audio = False
        self._wav_subtype = self._normalize_wav_subtype(initial_wav_subtype)

        self._player: QMediaPlayer | None = None
        self._audio_output: QAudioOutput | None = None
        if _has_qt_multimedia:
            self._player = QMediaPlayer(self)
            self._audio_output = QAudioOutput(self)
            self._player.setAudioOutput(self._audio_output)

        self._preview = WaveformWidget(self)
        self._preview.setMinimumHeight(220)
        self._preview.set_waveform([], 1.0)
        self._preview.set_playhead_active(False)

        self._status_label = QLabel(self)
        self._status_label.setWordWrap(True)
        self._status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self._quality_combo = QComboBox(self)
        self._quality_combo.addItem("16-bit PCM", "PCM_16")
        self._quality_combo.addItem("24-bit PCM", "PCM_24")
        self._quality_combo.addItem("32-bit float", "FLOAT")
        self._quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        selected_index = self._quality_combo.findData(self._wav_subtype)
        if selected_index >= 0:
            self._quality_combo.setCurrentIndex(selected_index)

        quality_row = QHBoxLayout()
        quality_row.addWidget(QLabel("WAV Quality:"))
        quality_row.addWidget(self._quality_combo)
        quality_row.addStretch()

        self._record_btn = QPushButton("Record")
        self._record_btn.clicked.connect(self._on_record_clicked)
        self._record_btn.setStyleSheet("QPushButton { background: #b71c1c; color: white; font-weight: bold; }")

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        self._stop_btn.setEnabled(False)

        self._play_btn = QPushButton("Play")
        self._play_btn.clicked.connect(self._on_play_clicked)
        self._play_btn.setEnabled(False)

        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._on_save_clicked)
        self._save_btn.setEnabled(False)

        button_row = QHBoxLayout()
        button_row.addWidget(self._record_btn)
        button_row.addWidget(self._stop_btn)
        button_row.addWidget(self._play_btn)
        button_row.addWidget(self._save_btn)
        button_row.addStretch()

        root = QVBoxLayout(self)
        root.addWidget(self._preview)
        root.addWidget(self._status_label)
        root.addLayout(quality_row)
        root.addLayout(button_row)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(50)
        self._poll_timer.timeout.connect(self._poll_recording_state)

        if isinstance(self._input_device, int) and self._input_device >= 0:
            self._set_status(f"Ready to record from device #{self._input_device}.")
        else:
            self._set_status("Ready to record from the default input device.")

    def _set_status(self, message: str) -> None:
        self._status_label.setText(message)

    @staticmethod
    def _normalize_wav_subtype(value: str | None) -> str:
        candidate = str(value or "").strip().upper().replace("-", "_")
        if candidate == "PCM24":
            candidate = "PCM_24"
        elif candidate == "PCM16":
            candidate = "PCM_16"
        elif candidate in {"FLOAT32", "PCM_FLOAT"}:
            candidate = "FLOAT"
        if candidate not in {"PCM_16", "PCM_24", "FLOAT"}:
            return "PCM_16"
        return candidate

    def saved_output_path(self) -> Path | None:
        if self._has_recorded_audio and self._current_output_path.exists():
            return self._current_output_path
        return None

    def selected_wav_subtype(self) -> str:
        return self._normalize_wav_subtype(self._wav_subtype)

    def _reset_live_preview(self) -> None:
        self._live_peaks = []
        self._preview.set_waveform([], 1.0)
        self._preview.set_playhead_seconds(None)
        self._preview.set_playhead_active(False)

    def _on_quality_changed(self) -> None:
        subtype = self._quality_combo.currentData()
        self._wav_subtype = self._normalize_wav_subtype(str(subtype) if subtype is not None else "PCM_16")

    def _start_recording(self) -> None:
        self._current_output_path.parent.mkdir(parents=True, exist_ok=True)
        self._reset_live_preview()
        config = RecordingConfig(
            device_id=self._input_device,
            sample_rate=None,
            channels=2,
            wav_subtype=self._wav_subtype,
        )
        error = self._engine.start_recording(self._current_output_path, config)
        if error:
            QMessageBox.warning(self, "Recording Failed", error)
            self._set_status(error)
            return

        self._recording = True
        self._has_recorded_audio = False
        self._record_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._play_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._poll_timer.start()
        self._set_status(f"Recording to {self._current_output_path.name}...")

    def _finish_recording(self) -> None:
        self._engine.stop_recording()
        self._recording = False
        self._poll_timer.stop()
        self._record_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._play_btn.setEnabled(True)
        self._save_btn.setEnabled(True)
        self._has_recorded_audio = self._current_output_path.exists()
        self._render_final_preview()
        if self._has_recorded_audio:
            self._set_status(f"Recording saved to {self._current_output_path}")
        else:
            self._set_status("Recording stopped.")

    def _render_final_preview(self) -> None:
        if not self._has_recorded_audio:
            return
        try:
            peaks = load_waveform_peaks(
                self._current_output_path,
                cache_dir=self._waveform_cache_dir,
            )
        except Exception:
            peaks = list(self._live_peaks)
        duration_seconds = max(1.0, float(len(peaks)) / 6.0)
        self._preview.set_waveform(peaks, duration_seconds)
        self._preview.set_playhead_seconds(None)

    def _on_record_clicked(self) -> None:
        if self._recording:
            return
        self._start_recording()

    def _on_stop_clicked(self) -> None:
        if not self._recording:
            return
        self._finish_recording()

    def _on_play_clicked(self) -> None:
        if not self._has_recorded_audio:
            return
        if self._player is None or self._audio_output is None:
            QMessageBox.information(self, "Playback Unavailable", "Audio playback is unavailable on this system.")
            return

        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.stop()
            self._play_btn.setText("Play")
            return

        self._player.setSource(QUrl.fromLocalFile(str(self._current_output_path)))
        self._player.play()
        self._play_btn.setText("Stop")

    def _on_save_clicked(self) -> None:
        if not self._has_recorded_audio or not self._current_output_path.exists():
            return

        target_path_text, _ = QFileDialog.getSaveFileName(
            self,
            "Save Recorded Jingle",
            str(self._current_output_path),
            "WAV Files (*.wav);;All Files (*)",
        )
        if not target_path_text:
            return

        target_path = Path(target_path_text)
        if target_path.suffix.lower() != ".wav":
            target_path = target_path.with_suffix(".wav")
        try:
            if target_path.resolve() != self._current_output_path.resolve():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self._current_output_path, target_path)
                self._current_output_path = target_path
            self._set_status(f"Saved recording to {self._current_output_path}")
            self.accept()
        except OSError as exc:
            QMessageBox.warning(self, "Save Failed", f"Could not save the recording.\n\n{exc}")

    def _poll_recording_state(self) -> None:
        latest_error = self._engine.get_latest_error()
        if latest_error:
            self._poll_timer.stop()
            self._recording = False
            self._record_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            self._set_status(latest_error)
            QMessageBox.warning(self, "Recording Error", latest_error)
            return

        latest_metrics = self._engine.get_latest_metrics()
        if latest_metrics is not None and self._recording:
            self._live_peaks.append(max(0.0, min(1.0, latest_metrics.peak_level)))
            while len(self._live_peaks) > 1200:
                reduced: list[float] = []
                for idx in range(0, len(self._live_peaks), 2):
                    pair = self._live_peaks[idx : idx + 2]
                    reduced.append(sum(pair) / len(pair))
                self._live_peaks = reduced
            duration_seconds = max(1.0, float(latest_metrics.current_seconds))
            self._preview.set_waveform(list(self._live_peaks), duration_seconds)
            self._preview.set_playhead_seconds(latest_metrics.current_seconds)

        if self._recording and not self._engine.is_recording():
            self._finish_recording()

    def closeEvent(self, event) -> None:
        if self._recording:
            self._finish_recording()
        super().closeEvent(event)