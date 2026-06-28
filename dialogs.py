from __future__ import annotations

import threading
import time
import math
from pathlib import Path

from app_helpers import coerce_volume_percent as _coerce_volume_percent
from app_helpers import format_duration_hms as _format_duration_hms
from app_helpers import format_size_label as _format_size_label
from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QKeySequenceEdit,
    QSizePolicy,
    QSpinBox,
    QSlider,
    QVBoxLayout,
    QWidget,
)

_has_qt_multimedia = False
try:
    from PyQt6.QtMultimedia import QMediaDevices

    _has_qt_multimedia = True
except ModuleNotFoundError:
    pass


SAMPLE_PAD_BLOCKSIZE_OPTIONS = (1024, 512, 384, 256, 224, 192, 128, 64)


def _coerce_sample_pad_blocksize(value: int | str | None) -> int:
    try:
        parsed = int(value) if value is not None else 128
    except (TypeError, ValueError):
        parsed = 128
    if parsed in SAMPLE_PAD_BLOCKSIZE_OPTIONS:
        return parsed
    return 128


def _coerce_sample_pad_streaming_min_seconds(value: int | str | None) -> int:
    try:
        parsed = int(value) if value is not None else 120
    except (TypeError, ValueError):
        parsed = 120
    return max(0, min(3600, parsed))


class OptionsDialog(QDialog):
    def __init__(
        self,
        live_output_device: str,
        preview_output_device: str,
        live_volume_percent: int,
        preview_volume_percent: int,
        sample_pad_blocksize: int,
        sample_pad_streaming_min_seconds: int,
        samples_dir: Path | None = None,
        recording_input_device: str | None = None,
        recording_output_folder: Path | None = None,
        use_wasapi_loopback: bool = False,
        prompt_filename_on_stop: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)

        folder_row = QHBoxLayout()
        folder_label = QLabel("Samples Folder")
        folder_label.setFixedWidth(120)
        folder_row.addWidget(folder_label)
        self._folder_edit = QLineEdit(str(samples_dir) if samples_dir else "")
        self._folder_edit.setReadOnly(True)
        self._folder_edit.setPlaceholderText("No folder selected")
        folder_row.addWidget(self._folder_edit)
        folder_browse_btn = QPushButton("Browse")
        folder_browse_btn.clicked.connect(self._on_browse_folder)
        folder_row.addWidget(folder_browse_btn)
        root.addLayout(folder_row)

        live_row = QHBoxLayout()
        live_label = QLabel("Live Device")
        live_label.setFixedWidth(100)
        live_row.addWidget(live_label)

        self._live_device_combo = QComboBox()
        self._live_device_combo.setMinimumWidth(340)
        self._live_device_combo.setToolTip("Audio output used in Live mode.")
        live_row.addWidget(self._live_device_combo)

        root.addLayout(live_row)

        preview_row = QHBoxLayout()
        preview_label = QLabel("Preview Device")
        preview_label.setFixedWidth(100)
        preview_row.addWidget(preview_label)

        self._preview_device_combo = QComboBox()
        self._preview_device_combo.setMinimumWidth(340)
        self._preview_device_combo.setToolTip("Audio output used in Preview mode.")
        preview_row.addWidget(self._preview_device_combo)

        root.addLayout(preview_row)

        live_volume_row = QHBoxLayout()
        live_volume_label = QLabel("Live Volume")
        live_volume_label.setFixedWidth(100)
        live_volume_row.addWidget(live_volume_label)

        self._live_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._live_volume_slider.setRange(0, 100)
        self._live_volume_slider.setPageStep(5)
        self._live_volume_slider.setValue(_coerce_volume_percent(live_volume_percent))
        self._live_volume_slider.setToolTip("Playback volume used in Live mode.")
        live_volume_row.addWidget(self._live_volume_slider)

        self._live_volume_value_label = QLabel()
        self._live_volume_value_label.setFixedWidth(44)
        live_volume_row.addWidget(self._live_volume_value_label)

        root.addLayout(live_volume_row)

        preview_volume_row = QHBoxLayout()
        preview_volume_label = QLabel("Preview Volume")
        preview_volume_label.setFixedWidth(100)
        preview_volume_row.addWidget(preview_volume_label)

        self._preview_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._preview_volume_slider.setRange(0, 100)
        self._preview_volume_slider.setPageStep(5)
        self._preview_volume_slider.setValue(_coerce_volume_percent(preview_volume_percent))
        self._preview_volume_slider.setToolTip("Playback volume used in Preview mode.")
        preview_volume_row.addWidget(self._preview_volume_slider)

        self._preview_volume_value_label = QLabel()
        self._preview_volume_value_label.setFixedWidth(44)
        preview_volume_row.addWidget(self._preview_volume_value_label)

        root.addLayout(preview_volume_row)

        sample_pad_blocksize_row = QHBoxLayout()
        sample_pad_blocksize_label = QLabel("Pad Blocksize")
        sample_pad_blocksize_label.setFixedWidth(100)
        sample_pad_blocksize_row.addWidget(sample_pad_blocksize_label)

        self._sample_pad_blocksize_combo = QComboBox()
        self._sample_pad_blocksize_combo.setMinimumWidth(160)
        self._sample_pad_blocksize_combo.setToolTip(
            "Audio buffer size for live sample-pad playback. Lower values reduce trigger latency; higher values increase stability."
        )
        selected_blocksize = _coerce_sample_pad_blocksize(sample_pad_blocksize)
        for option in SAMPLE_PAD_BLOCKSIZE_OPTIONS:
            self._sample_pad_blocksize_combo.addItem(str(option), option)
            if option == selected_blocksize:
                self._sample_pad_blocksize_combo.setCurrentIndex(
                    self._sample_pad_blocksize_combo.count() - 1
                )
        sample_pad_blocksize_row.addWidget(self._sample_pad_blocksize_combo)
        sample_pad_blocksize_row.addStretch()

        root.addLayout(sample_pad_blocksize_row)

        sample_pad_streaming_row = QHBoxLayout()
        sample_pad_streaming_label = QLabel("Pad Stream Min")
        sample_pad_streaming_label.setFixedWidth(100)
        sample_pad_streaming_row.addWidget(sample_pad_streaming_label)

        self._sample_pad_streaming_min_seconds_spin = QSpinBox()
        self._sample_pad_streaming_min_seconds_spin.setRange(0, 3600)
        self._sample_pad_streaming_min_seconds_spin.setSingleStep(15)
        self._sample_pad_streaming_min_seconds_spin.setSuffix(" s")
        self._sample_pad_streaming_min_seconds_spin.setValue(
            _coerce_sample_pad_streaming_min_seconds(sample_pad_streaming_min_seconds)
        )
        self._sample_pad_streaming_min_seconds_spin.setToolTip(
            "Duration threshold for first-trigger streaming on cache miss. "
            "Files at or above this length stream immediately. Set to 0 to always allow streaming."
        )
        self._sample_pad_streaming_min_seconds_spin.setMinimumWidth(160)
        sample_pad_streaming_row.addWidget(self._sample_pad_streaming_min_seconds_spin)
        sample_pad_streaming_row.addStretch()

        root.addLayout(sample_pad_streaming_row)

        # Recording settings section
        recording_folder_row = QHBoxLayout()
        recording_folder_label = QLabel("Recording Folder")
        recording_folder_label.setFixedWidth(120)
        recording_folder_row.addWidget(recording_folder_label)
        self._recording_folder_edit = QLineEdit(str(recording_output_folder) if recording_output_folder else "")
        self._recording_folder_edit.setReadOnly(True)
        self._recording_folder_edit.setPlaceholderText("No folder selected")
        recording_folder_row.addWidget(self._recording_folder_edit)
        recording_browse_btn = QPushButton("Browse")
        recording_browse_btn.clicked.connect(self._on_browse_recording_folder)
        recording_folder_row.addWidget(recording_browse_btn)
        root.addLayout(recording_folder_row)

        recording_device_row = QHBoxLayout()
        recording_device_label = QLabel("Recording Device")
        recording_device_label.setFixedWidth(100)
        recording_device_row.addWidget(recording_device_label)

        self._recording_device_combo = QComboBox()
        self._recording_device_combo.setMinimumWidth(340)
        self._recording_device_combo.setToolTip("Audio input device for recording jingles.")
        recording_device_row.addWidget(self._recording_device_combo)

        root.addLayout(recording_device_row)

        recording_wasapi_row = QHBoxLayout()
        recording_wasapi_row.addSpacing(120)
        self._use_wasapi_loopback_checkbox = QCheckBox("Use WASAPI Loopback (Stereo Mix)")
        self._use_wasapi_loopback_checkbox.setChecked(use_wasapi_loopback)
        self._use_wasapi_loopback_checkbox.setToolTip(
            "When checked, record system audio via stereo mix/loopback device instead of physical input."
        )
        recording_wasapi_row.addWidget(self._use_wasapi_loopback_checkbox)
        recording_wasapi_row.addStretch()
        root.addLayout(recording_wasapi_row)

        recording_prompt_filename_row = QHBoxLayout()
        recording_prompt_filename_row.addSpacing(120)
        self._recording_prompt_filename_checkbox = QCheckBox(
            "Prompt for Filename When Recording Stops?"
        )
        self._recording_prompt_filename_checkbox.setChecked(prompt_filename_on_stop)
        self._recording_prompt_filename_checkbox.setToolTip(
            "When checked, stopping a recording opens Save As before the file is moved from temp storage."
        )
        recording_prompt_filename_row.addWidget(self._recording_prompt_filename_checkbox)
        recording_prompt_filename_row.addStretch()
        root.addLayout(recording_prompt_filename_row)

        refresh_btn = QPushButton("Refresh Devices")
        refresh_btn.clicked.connect(self._on_refresh_clicked)
        refresh_row = QHBoxLayout()
        refresh_row.addWidget(refresh_btn)
        refresh_row.addStretch()

        root.addLayout(refresh_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._populate_devices(live_output_device, preview_output_device)
        self._populate_recording_devices(recording_input_device, use_wasapi_loopback)
        self._live_volume_slider.valueChanged.connect(self._sync_volume_labels)
        self._preview_volume_slider.valueChanged.connect(self._sync_volume_labels)
        self._sync_volume_labels()

    def _populate_devices(self, live_selected: str, preview_selected: str) -> None:
        self._populate_device_combo(self._live_device_combo, live_selected)
        self._populate_device_combo(self._preview_device_combo, preview_selected)

    def _populate_device_combo(self, combo: QComboBox, selected_device: str) -> None:
        combo.blockSignals(True)
        combo.clear()

        default_name = ""
        if _has_qt_multimedia:
            try:
                default_name = QMediaDevices.defaultAudioOutput().description().strip()
                seen: set[str] = set()
                for device in QMediaDevices.audioOutputs():
                    name = device.description().strip()
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    combo.addItem(name, name)
            except Exception:
                pass

        default_label = "System Default"
        if default_name:
            default_label = f"System Default ({default_name})"
        combo.insertItem(0, default_label, "")

        target = selected_device.strip()
        if target:
            idx = combo.findData(target)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.addItem(f"{target} (Unavailable)", target)
                combo.setCurrentIndex(combo.count() - 1)
        else:
            combo.setCurrentIndex(0)

        combo.blockSignals(False)

    def _on_refresh_clicked(self) -> None:
        live_current = self._live_device_combo.currentData()
        preview_current = self._preview_device_combo.currentData()
        live_selected = str(live_current).strip() if live_current is not None else ""
        preview_selected = str(preview_current).strip() if preview_current is not None else ""
        self._populate_devices(live_selected, preview_selected)
        recording_current = self._recording_device_combo.currentData()
        recording_selected = str(recording_current).strip() if recording_current is not None else ""
        use_wasapi = self._use_wasapi_loopback_checkbox.isChecked()
        self._populate_recording_devices(recording_selected, use_wasapi)

    def selected_devices(self) -> tuple[str, str]:
        live = self._live_device_combo.currentData()
        preview = self._preview_device_combo.currentData()
        live_value = str(live).strip() if live is not None else ""
        preview_value = str(preview).strip() if preview is not None else ""
        return live_value, preview_value

    def selected_volumes(self) -> tuple[int, int]:
        return (
            _coerce_volume_percent(self._live_volume_slider.value()),
            _coerce_volume_percent(self._preview_volume_slider.value()),
        )

    def selected_sample_pad_blocksize(self) -> int:
        value = self._sample_pad_blocksize_combo.currentData()
        return _coerce_sample_pad_blocksize(value)

    def selected_sample_pad_streaming_min_seconds(self) -> int:
        return _coerce_sample_pad_streaming_min_seconds(
            self._sample_pad_streaming_min_seconds_spin.value()
        )

    def selected_folder(self) -> Path | None:
        text = self._folder_edit.text().strip()
        if not text:
            return None
        p = Path(text)
        return p if p.exists() and p.is_dir() else None

    def selected_recording_folder(self) -> Path | None:
        text = self._recording_folder_edit.text().strip()
        if not text:
            return None
        p = Path(text)
        return p if p.exists() and p.is_dir() else None

    def selected_recording_device(self) -> str:
        data = self._recording_device_combo.currentData()
        return str(data).strip() if data is not None else ""

    def selected_use_wasapi_loopback(self) -> bool:
        return self._use_wasapi_loopback_checkbox.isChecked()

    def selected_prompt_filename_on_stop(self) -> bool:
        return self._recording_prompt_filename_checkbox.isChecked()

    def _on_browse_folder(self) -> None:
        current = self._folder_edit.text().strip()
        start = current if current else str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Choose Samples Folder", start)
        if not selected:
            return
        path = Path(selected)
        if path.exists() and path.is_dir():
            self._folder_edit.setText(str(path))

    def _on_browse_recording_folder(self) -> None:
        current = self._recording_folder_edit.text().strip()
        start = current if current else str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Choose Recording Output Folder", start)
        if not selected:
            return
        path = Path(selected)
        if path.exists() and path.is_dir():
            self._recording_folder_edit.setText(str(path))

    def _populate_recording_devices(self, selected_device: str, use_wasapi: bool) -> None:
        """Populate recording input device combo."""
        from recording_engine import get_recording_engine

        self._recording_device_combo.blockSignals(True)
        self._recording_device_combo.clear()

        engine = get_recording_engine()
        seen: set[str] = set()

        # Add available input devices
        for device_id, device_name in engine.get_available_devices():
            if device_name in seen:
                continue
            seen.add(device_name)
            self._recording_device_combo.addItem(device_name, device_name)

        # If WASAPI loopback is selected, show those devices too (and prioritize them)
        if use_wasapi:
            wasapi_devices = engine.get_wasapi_loopback_devices()
            for device_id, device_name in wasapi_devices:
                if device_name in seen:
                    continue
                seen.add(device_name)
                label = f"{device_name} (WASAPI Loopback)"
                self._recording_device_combo.addItem(label, device_name)

        # Restore selection
        target = selected_device.strip() if selected_device else ""
        if target:
            idx = self._recording_device_combo.findData(target)
            if idx >= 0:
                self._recording_device_combo.setCurrentIndex(idx)
            else:
                self._recording_device_combo.addItem(f"{target} (Unavailable)", target)
                self._recording_device_combo.setCurrentIndex(self._recording_device_combo.count() - 1)
        else:
            self._recording_device_combo.setCurrentIndex(0 if self._recording_device_combo.count() > 0 else -1)

        self._recording_device_combo.blockSignals(False)

    def _sync_volume_labels(self) -> None:
        self._live_volume_value_label.setText(f"{self._live_volume_slider.value()}%")
        self._preview_volume_value_label.setText(f"{self._preview_volume_slider.value()}%")


class RecordingDialog(QDialog):
    """Dialog for recording audio from input device or WASAPI loopback."""

    recording_ready = pyqtSignal(str, bool)

    def __init__(
        self,
        recording_device: str | None = None,
        use_wasapi_loopback: bool = False,
        prompt_filename_on_stop: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Record Jingle")
        self.setMinimumWidth(480)
        self.setWindowFlags(
            (self.windowFlags() | Qt.WindowType.WindowMinimizeButtonHint)
            & ~Qt.WindowType.WindowMaximizeButtonHint
        )

        from recording_engine import RecordingConfig, get_recording_engine

        self._engine = get_recording_engine()
        self._config = RecordingConfig(
            device_id=recording_device or None,
            sample_rate=44100,
            channels=2,
            blocksize=2048,
            use_wasapi_loopback=use_wasapi_loopback,
        )
        self._recorded_path: Path | None = None
        self._timer = QTimer()
        self._timer.timeout.connect(self._on_update_metrics)
        self._meter_monitor_thread: threading.Thread | None = None
        self._meter_monitor_stop = threading.Event()
        self._meter_monitor_peak = 0.0
        self._meter_monitor_lock = threading.Lock()
        self._minimized_low_power_active = False

        root = QVBoxLayout(self)

        # Status display
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Device:"))
        self._device_label = QLabel(recording_device or "Default")
        status_row.addWidget(self._device_label)
        status_row.addStretch()
        root.addLayout(status_row)

        # Duration display
        duration_row = QHBoxLayout()
        duration_row.addWidget(QLabel("Duration:"))
        self._duration_label = QLabel("00:00:00")
        self._duration_label.setMinimumWidth(80)
        duration_row.addWidget(self._duration_label)
        duration_row.addStretch()
        root.addLayout(duration_row)

        # VU meter (progress bar as visual indicator)
        meter_row = QHBoxLayout()
        meter_row.addWidget(QLabel("Level:"))
        self._peak_meter = QProgressBar()
        self._peak_meter.setRange(0, 100)
        self._peak_meter.setValue(0)
        self._peak_meter.setTextVisible(False)
        self._peak_meter.setMaximumHeight(20)
        meter_row.addWidget(self._peak_meter)
        self._peak_label = QLabel("-inf dB")
        self._peak_label.setMinimumWidth(56)
        meter_row.addWidget(self._peak_label)
        root.addLayout(meter_row)

        prompt_row = QHBoxLayout()
        prompt_row.addSpacing(6)
        self._prompt_filename_on_stop_checkbox = QCheckBox(
            "Prompt for Filename When Recording Stops?"
        )
        self._prompt_filename_on_stop_checkbox.setChecked(prompt_filename_on_stop)
        prompt_row.addWidget(self._prompt_filename_on_stop_checkbox)
        prompt_row.addStretch()
        root.addLayout(prompt_row)

        # Status message
        self._status_label = QLabel("Ready to record")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #666;")
        root.addWidget(self._status_label)

        root.addSpacing(4)

        # Buttons
        button_row = QHBoxLayout()
        self._start_btn = QPushButton("Start Recording")
        self._start_btn.clicked.connect(self._on_start_recording)
        button_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton("Stop Recording")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop_recording)
        button_row.addWidget(self._stop_btn)

        button_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        button_row.addWidget(close_btn)

        root.addLayout(button_row)

        self.setFixedHeight(self.sizeHint().height())

        self.finished.connect(self._cleanup_meter_monitor)
        self._start_meter_monitor()
        self._timer.start(100)

    def _on_start_recording(self) -> None:
        """Start recording audio."""
        import tempfile

        try:
            # Create temp file for recording
            temp_fd, temp_path = tempfile.mkstemp(suffix=".wav")
            import os

            os.close(temp_fd)
            self._recorded_path = Path(temp_path)

            # Avoid opening two input streams on the same device at once.
            self._stop_meter_monitor()
            with self._meter_monitor_lock:
                self._meter_monitor_peak = 0.0

            error = self._engine.start_recording(
                self._recorded_path,
                self._config,
                metrics_callback=self._on_metrics_update,
            )

            if error:
                self._status_label.setText(f"Error: {error}")
                self._recorded_path = None
                self._start_meter_monitor()
                return

            self._start_btn.setEnabled(False)
            self._stop_btn.setEnabled(True)
            self._status_label.setText("Recording...")
            self._status_label.setStyleSheet("color: #080;")

        except Exception as e:
            self._status_label.setText(f"Error: {str(e)}")
            self._recorded_path = None
            self._start_meter_monitor()

    def changeEvent(self, event: QEvent | None) -> None:
        if event is not None and event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                self._enter_minimized_low_power_mode()
            else:
                self._exit_minimized_low_power_mode()
        super().changeEvent(event)

    def _on_stop_recording(self) -> None:
        """Stop recording audio."""
        self._engine.stop_recording()

        # Check for errors
        error = self._engine.get_latest_error()
        if error:
            self._status_label.setText(f"Error: {error}")
            self._status_label.setStyleSheet("color: #800;")
            if self._recorded_path and self._recorded_path.exists():
                self._recorded_path.unlink()
                self._recorded_path = None
        else:
            if self._recorded_path is not None and self._recorded_path.exists():
                self.recording_ready.emit(
                    str(self._recorded_path),
                    self.should_prompt_filename_on_stop(),
                )
            self._recorded_path = None

        self._reset_ready_state()

    def _on_metrics_update(self, metrics) -> None:
        """Callback from recording engine with metrics."""
        pass

    def _on_update_metrics(self) -> None:
        """Poll and update metrics display."""
        metrics = self._engine.get_latest_metrics() if self._engine.is_recording() else None
        if metrics is not None:
            # Update duration
            minutes, seconds = divmod(int(metrics.current_seconds), 60)
            hours, minutes = divmod(minutes, 60)
            self._duration_label.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

            self._apply_peak_level(float(metrics.peak_level))
            return

        # Idle mode metering: keep showing live input level when not recording.
        with self._meter_monitor_lock:
            peak_level = self._meter_monitor_peak
        self._apply_peak_level(peak_level)

    def _apply_peak_level(self, raw_peak: float) -> None:
        peak_level = max(0.0, min(1.0, float(raw_peak)))
        peak_percent = int(peak_level * 100)
        self._peak_meter.setValue(peak_percent)
        if peak_level <= 0.0:
            self._peak_label.setText("-inf dB")
            return
        dbfs = 20.0 * math.log10(peak_level)
        self._peak_label.setText(f"{dbfs:.1f} dB")

    def _start_meter_monitor(self) -> None:
        if self._minimized_low_power_active:
            return
        if self._meter_monitor_thread is not None and self._meter_monitor_thread.is_alive():
            return

        self._meter_monitor_stop.clear()

        def _run_monitor() -> None:
            try:
                import sounddevice as sd
            except Exception:
                return

            def _audio_callback(indata, _frames, _time_info, _status) -> None:
                try:
                    peak = float(abs(indata).max())
                except Exception:
                    peak = 0.0
                with self._meter_monitor_lock:
                    self._meter_monitor_peak = peak

            try:
                with sd.InputStream(
                    device=self._config.device_id,
                    samplerate=self._config.sample_rate,
                    channels=self._config.channels,
                    blocksize=self._config.blocksize,
                    callback=_audio_callback,
                ):
                    while not self._meter_monitor_stop.wait(0.1):
                        pass
            except Exception:
                with self._meter_monitor_lock:
                    self._meter_monitor_peak = 0.0

        self._meter_monitor_thread = threading.Thread(target=_run_monitor, daemon=True)
        self._meter_monitor_thread.start()

    def _stop_meter_monitor(self) -> None:
        self._meter_monitor_stop.set()
        if self._meter_monitor_thread is not None:
            self._meter_monitor_thread.join(timeout=1.0)
        self._meter_monitor_thread = None

    def _cleanup_meter_monitor(self, _result: int) -> None:
        self._timer.stop()
        self._stop_meter_monitor()

    def _reset_ready_state(self) -> None:
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._duration_label.setText("00:00:00")
        self._status_label.setText("Ready to record")
        self._status_label.setStyleSheet("color: #666;")
        if not self._engine.is_recording():
            self._start_meter_monitor()

    def _enter_minimized_low_power_mode(self) -> None:
        if self._minimized_low_power_active:
            return
        self._minimized_low_power_active = True
        self._timer.stop()
        if not self._engine.is_recording():
            self._stop_meter_monitor()

    def _exit_minimized_low_power_mode(self) -> None:
        if not self._minimized_low_power_active:
            return
        self._minimized_low_power_active = False
        if not self._engine.is_recording():
            self._start_meter_monitor()
        if not self._timer.isActive():
            self._timer.start(100)

    def recorded_file_path(self) -> Path | None:
        """Return path to recorded file if recording was successful."""
        return self._recorded_path if self._recorded_path and self._recorded_path.exists() else None

    def should_prompt_filename_on_stop(self) -> bool:
        return self._prompt_filename_on_stop_checkbox.isChecked()

    def sync_recording_settings(
        self,
        recording_device: str | None,
        use_wasapi_loopback: bool,
        prompt_filename_on_stop: bool,
    ) -> None:
        self._config.device_id = recording_device or None
        self._config.use_wasapi_loopback = use_wasapi_loopback
        self._device_label.setText(recording_device or "Default")
        self._prompt_filename_on_stop_checkbox.setChecked(prompt_filename_on_stop)

        # If actively recording, keep the current stream stable and apply on next take.
        if self._engine.is_recording():
            return

        # Apply the device change immediately to idle metering.
        self._stop_meter_monitor()
        with self._meter_monitor_lock:
            self._meter_monitor_peak = 0.0
        self._start_meter_monitor()


class KeyboardShortcutsDialog(QDialog):
    def __init__(
        self,
        current_shortcuts: dict[str, str],
        default_shortcuts: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Keyboard Shortcuts")
        self.resize(520, 280)

        root = QVBoxLayout(self)

        help_label = QLabel("Set shortcut keys. Leave a field empty to disable that shortcut.")
        help_label.setWordWrap(True)
        root.addWidget(help_label)

        grid = QGridLayout()
        root.addLayout(grid)

        self._shortcut_editors: dict[str, QKeySequenceEdit] = {}
        self._row_labels: dict[str, str] = {}
        rows = [
            ("rename", "Rename"),
            ("delete", "Delete"),
            ("skip_previous", "Skip to Previous (while playing/paused)"),
            ("skip_next", "Skip to Next (while playing/paused)"),
            ("select_up", "Select Previous Row (when stopped)"),
            ("select_down", "Select Next Row (when stopped)"),
        ]
        for row_idx, (key, label_text) in enumerate(rows):
            label = QLabel(label_text)
            grid.addWidget(label, row_idx, 0)

            editor = QKeySequenceEdit()
            editor.setKeySequence(QKeySequence(current_shortcuts.get(key, default_shortcuts.get(key, ""))))
            grid.addWidget(editor, row_idx, 1)
            self._shortcut_editors[key] = editor
            self._row_labels[key] = label_text

            default_label = QLabel(f"Default: {default_shortcuts.get(key, '')}")
            default_label.setStyleSheet("color: #666;")
            grid.addWidget(default_label, row_idx, 2)

        controls_row = QHBoxLayout()
        reset_defaults_btn = QPushButton("Reset to Defaults")
        reset_defaults_btn.clicked.connect(self._reset_to_defaults)
        controls_row.addWidget(reset_defaults_btn)
        controls_row.addStretch()
        root.addLayout(controls_row)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

        self._default_shortcuts = dict(default_shortcuts)

    def _reset_to_defaults(self) -> None:
        for key, editor in self._shortcut_editors.items():
            editor.setKeySequence(QKeySequence(self._default_shortcuts.get(key, "")))

    def selected_shortcuts(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, editor in self._shortcut_editors.items():
            out[key] = editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        return out

    def _find_conflicts(self) -> list[tuple[str, list[str]]]:
        assignments: dict[str, list[str]] = {}
        for key, editor in self._shortcut_editors.items():
            seq_text = editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText).strip()
            if not seq_text:
                continue
            assignments.setdefault(seq_text, []).append(self._row_labels.get(key, key))

        conflicts: list[tuple[str, list[str]]] = []
        for seq_text, labels in assignments.items():
            if len(labels) > 1:
                conflicts.append((seq_text, labels))
        return conflicts

    def _on_accept(self) -> None:
        conflicts = self._find_conflicts()
        if conflicts:
            lines = ["These shortcuts are assigned to multiple actions:", ""]
            for seq_text, labels in conflicts:
                lines.append(f"{seq_text}: {', '.join(labels)}")
            lines.append("")
            lines.append("Please resolve conflicts before saving.")
            QMessageBox.warning(self, "Shortcut Conflict", "\n".join(lines))
            return
        self.accept()


class AboutDialog(QDialog):
    def __init__(
        self,
        *,
        app_name: str,
        app_version: str,
        icon_path: Path,
        library_count: int,
        library_duration_seconds: float,
        library_size_bytes: int,
        revision_log_path: Path | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {app_name}")
        self.setMinimumWidth(620)
        self._app_name = app_name
        self._app_version = app_version
        self._revision_log_path = revision_log_path

        self._icon_base = QPixmap(str(icon_path)) if icon_path.is_file() else QPixmap()

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(16)

        icon_wrap = QWidget(self)
        icon_layout = QVBoxLayout(icon_wrap)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.addStretch(1)

        self._icon_label = QLabel(icon_wrap)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setMinimumSize(1, 1)
        self._icon_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        icon_layout.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignHCenter)
        icon_layout.addStretch(1)

        text_wrap = QWidget(self)
        text_layout = QVBoxLayout(text_wrap)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(10)

        self._title_label = QLabel(self._app_name)
        self._title_label.setStyleSheet("font-size: 20px; font-weight: 700;")
        text_layout.addWidget(self._title_label)

        self._version_label = QLabel(f"Version {self._app_version}")
        self._version_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        intro_layout = QVBoxLayout()
        intro_layout.setContentsMargins(0, 0, 0, 0)
        intro_layout.setSpacing(2)
        intro_layout.addWidget(self._version_label)

        self._body_label = QLabel(
            "\nJingleAllTheDay is a desktop jingle library manager and playback board.\n\n"
            "You can scan and organize jingles, tag tracks by category, search and filter quickly, "
            "and trigger reliable playback in Live or Preview mode with selectable output devices.\n\n"
            "The app is built for fast on-air workflows, with keyboard shortcuts, batch tag tools, "
            "duplicate detection, and import/export of your tag database."
        )
        self._body_label.setWordWrap(True)
        self._body_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._body_label.setMinimumWidth(430)
        intro_layout.addWidget(self._body_label)
        text_layout.addLayout(intro_layout)

        count_label = f"{max(0, int(library_count)):,}"
        duration_label = _format_duration_hms(max(0.0, float(library_duration_seconds)))
        size_label = _format_size_label(max(0, int(library_size_bytes)))

        self._library_summary_label = QLabel(
            "Current Library\n"
            f"Jingles: {count_label}\n"
            f"Total Duration: {duration_label}\n"
            f"Total Size: {size_label}"
        )
        self._library_summary_label.setWordWrap(True)
        self._library_summary_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        text_layout.addWidget(self._library_summary_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self._show_revision_history_btn = QPushButton("Show Revision History")
        self._show_revision_history_btn.setEnabled(
            self._revision_log_path is not None and self._revision_log_path.is_file()
        )
        if not self._show_revision_history_btn.isEnabled():
            self._show_revision_history_btn.setToolTip("rev.log was not found in the runtime application folder.")
        self._show_revision_history_btn.clicked.connect(self._on_show_revision_history)
        button_row.addWidget(self._show_revision_history_btn)
        text_layout.addLayout(button_row)

        root.addWidget(icon_wrap, 1)
        root.addWidget(text_wrap, 3)

        text_height = (
            self._title_label.sizeHint().height()
            + text_layout.spacing()
            + self._version_label.sizeHint().height()
            + text_layout.spacing()
            + self._body_label.sizeHint().height()
            + text_layout.spacing()
            + self._library_summary_label.sizeHint().height()
            + text_layout.spacing()
            + self._show_revision_history_btn.sizeHint().height()
        )
        frame_height = root.contentsMargins().top() + root.contentsMargins().bottom() + 8
        initial_height = max(220, text_height + frame_height)
        self.resize(640, initial_height)
        self._refresh_icon()
        self.setFixedSize(self.size())

    def _on_show_revision_history(self) -> None:
        if self._revision_log_path is None or not self._revision_log_path.is_file():
            QMessageBox.information(
                self,
                "Revision History",
                "rev.log was not found in the runtime application folder.",
            )
            return

        try:
            revision_text = self._revision_log_path.read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Revision History",
                f"Unable to read revision history file.\n\n{exc}",
            )
            return

        dialog = RevisionHistoryDialog(
            revision_text=revision_text,
            app_name=self._app_name,
            app_version=self._app_version,
            parent=self,
        )
        dialog.exec()

    def _refresh_icon(self) -> None:
        if self._icon_base.isNull():
            self._icon_label.setText("No icon")
            return

        max_by_height = max(48, self.height() - 80)
        max_by_width = max(48, self.width() // 4)
        side_cap = max(100, self.height() // 3)
        side = max(48, min(max_by_height, max_by_width, side_cap))
        scaled = self._icon_base.scaled(
            side,
            side,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._icon_label.setText("")
        self._icon_label.setPixmap(scaled)


class RevisionHistoryDialog(QDialog):
    def __init__(
        self,
        *,
        revision_text: str,
        app_name: str,
        app_version: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Revision History")
        self.setMinimumSize(700, 420)

        root = QVBoxLayout(self)
        title = QLabel(f"{app_name} {app_version}")
        title.setStyleSheet("font-weight: 700;")
        root.addWidget(title)

        history_view = QPlainTextEdit(self)
        history_view.setReadOnly(True)
        history_view.setPlainText(revision_text)
        root.addWidget(history_view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
