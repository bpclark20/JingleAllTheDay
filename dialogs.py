from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from app_helpers import coerce_volume_percent as _coerce_volume_percent
from app_helpers import coerce_recent_window_days as _coerce_recent_window_days
from app_helpers import format_duration_hms as _format_duration_hms
from app_helpers import format_size_label as _format_size_label
from app_helpers import (
    SAMPLE_PAD_BLOCKSIZE_OPTIONS,
    QMediaDevices,
    _coerce_sample_pad_blocksize,
    _coerce_sample_pad_streaming_min_seconds,
    _has_qt_multimedia,
)
from app_settings import OptionsSettings
from broadcast_routing import (
    broadcast_route_warnings,
    format_audio_device_label,
    virtual_device_guide,
)
import remote_server as _remote_server
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
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
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import QTimer
from recording_engine import RecordingConfig, get_recording_engine

class OptionsDialog(QDialog):
    def __init__(
        self,
        options: OptionsSettings,
        samples_dir: Path | None = None,
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

        broadcast_row = QHBoxLayout()
        broadcast_label = QLabel("Broadcast Device")
        broadcast_label.setFixedWidth(100)
        broadcast_row.addWidget(broadcast_label)

        self._broadcast_device_combo = QComboBox()
        self._broadcast_device_combo.setMinimumWidth(340)
        self._broadcast_device_combo.setToolTip(
            "Optional virtual or loopback output used for mixer audio and live sample-pad routing in broadcast workflows."
        )
        broadcast_row.addWidget(self._broadcast_device_combo)

        root.addLayout(broadcast_row)

        preview_row = QHBoxLayout()
        preview_label = QLabel("Preview Device")
        preview_label.setFixedWidth(100)
        preview_row.addWidget(preview_label)

        self._preview_device_combo = QComboBox()
        self._preview_device_combo.setMinimumWidth(340)
        self._preview_device_combo.setToolTip("Audio output used in Preview mode.")
        preview_row.addWidget(self._preview_device_combo)

        root.addLayout(preview_row)

        mixer_enabled_row = QHBoxLayout()
        mixer_enabled_label = QLabel("Mixer Mode")
        mixer_enabled_label.setFixedWidth(100)
        mixer_enabled_row.addWidget(mixer_enabled_label)

        self._mixer_enabled_checkbox = QCheckBox("Enable in-app microphone + jingle mixer")
        self._mixer_enabled_checkbox.setChecked(bool(options.mixer_enabled))
        self._mixer_enabled_checkbox.setToolTip(
            "Foundation setting for future internal microphone+jingle mixing. "
            "Current builds persist this setting but do not yet switch playback behavior."
        )
        mixer_enabled_row.addWidget(self._mixer_enabled_checkbox)
        mixer_enabled_row.addStretch()

        root.addLayout(mixer_enabled_row)

        microphone_row = QHBoxLayout()
        microphone_label = QLabel("Microphone")
        microphone_label.setFixedWidth(100)
        microphone_row.addWidget(microphone_label)

        self._microphone_device_combo = QComboBox()
        self._microphone_device_combo.setMinimumWidth(340)
        self._microphone_device_combo.setToolTip(
            "Input device reserved for future in-app microphone+jingle mixing."
        )
        microphone_row.addWidget(self._microphone_device_combo)

        root.addLayout(microphone_row)

        recording_row = QHBoxLayout()
        recording_label = QLabel("Recording")
        recording_label.setFixedWidth(100)
        recording_row.addWidget(recording_label)

        self._recording_device_combo = QComboBox()
        self._recording_device_combo.setMinimumWidth(340)
        self._recording_device_combo.setToolTip(
            "Audio input used by the Tools > Record Jingle window and Sample Pads recording mode."
        )
        recording_row.addWidget(self._recording_device_combo)

        root.addLayout(recording_row)

        microphone_gain_row = QHBoxLayout()
        microphone_gain_label = QLabel("Mic Gain")
        microphone_gain_label.setFixedWidth(100)
        microphone_gain_row.addWidget(microphone_gain_label)

        self._microphone_gain_slider = QSlider(Qt.Orientation.Horizontal)
        self._microphone_gain_slider.setRange(0, 200)
        self._microphone_gain_slider.setPageStep(5)
        self._microphone_gain_slider.setValue(max(0, min(200, int(options.microphone_gain_percent))))
        self._microphone_gain_slider.setToolTip(
            "Input gain for future internal microphone mixing. 100% is unity gain."
        )
        microphone_gain_row.addWidget(self._microphone_gain_slider)

        self._microphone_gain_value_label = QLabel()
        self._microphone_gain_value_label.setFixedWidth(52)
        microphone_gain_row.addWidget(self._microphone_gain_value_label)

        root.addLayout(microphone_gain_row)

        live_volume_row = QHBoxLayout()
        live_volume_label = QLabel("Live Volume")
        live_volume_label.setFixedWidth(100)
        live_volume_row.addWidget(live_volume_label)

        self._live_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._live_volume_slider.setRange(0, 100)
        self._live_volume_slider.setPageStep(5)
        self._live_volume_slider.setValue(_coerce_volume_percent(options.live_volume_percent))
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
        self._preview_volume_slider.setValue(_coerce_volume_percent(options.preview_volume_percent))
        self._preview_volume_slider.setToolTip("Playback volume used in Preview mode.")
        preview_volume_row.addWidget(self._preview_volume_slider)

        self._preview_volume_value_label = QLabel()
        self._preview_volume_value_label.setFixedWidth(44)
        preview_volume_row.addWidget(self._preview_volume_value_label)

        root.addLayout(preview_volume_row)

        recent_window_row = QHBoxLayout()
        recent_window_label = QLabel("Recent Window")
        recent_window_label.setFixedWidth(100)
        recent_window_row.addWidget(recent_window_label)

        self._recent_window_days_spin = QSpinBox()
        self._recent_window_days_spin.setRange(1, 3650)
        self._recent_window_days_spin.setSingleStep(1)
        self._recent_window_days_spin.setSuffix(" days")
        self._recent_window_days_spin.setValue(_coerce_recent_window_days(options.recent_window_days))
        self._recent_window_days_spin.setToolTip(
            "How many days back should count as an internal Recent jingle tag."
        )
        self._recent_window_days_spin.setMinimumWidth(160)
        recent_window_row.addWidget(self._recent_window_days_spin)
        recent_window_row.addStretch()

        root.addLayout(recent_window_row)

        sample_pad_blocksize_row = QHBoxLayout()
        sample_pad_blocksize_label = QLabel("Pad Blocksize")
        sample_pad_blocksize_label.setFixedWidth(100)
        sample_pad_blocksize_row.addWidget(sample_pad_blocksize_label)

        self._sample_pad_blocksize_combo = QComboBox()
        self._sample_pad_blocksize_combo.setMinimumWidth(160)
        self._sample_pad_blocksize_combo.setToolTip(
            "Audio buffer size for live sample-pad playback. Lower values reduce trigger latency; higher values increase stability."
        )
        selected_blocksize = _coerce_sample_pad_blocksize(options.sample_pad_blocksize)
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
            _coerce_sample_pad_streaming_min_seconds(options.sample_pad_streaming_min_seconds)
        )
        self._sample_pad_streaming_min_seconds_spin.setToolTip(
            "Duration threshold for first-trigger streaming on cache miss. "
            "Files at or above this length stream immediately. Set to 0 to always allow streaming."
        )
        self._sample_pad_streaming_min_seconds_spin.setMinimumWidth(160)
        sample_pad_streaming_row.addWidget(self._sample_pad_streaming_min_seconds_spin)
        sample_pad_streaming_row.addStretch()

        root.addLayout(sample_pad_streaming_row)

        server_enabled_row = QHBoxLayout()
        server_enabled_label = QLabel("Remote Server")
        server_enabled_label.setFixedWidth(100)
        server_enabled_row.addWidget(server_enabled_label)
        self._server_enabled_checkbox = QCheckBox("Auto-connect on launch")
        self._server_enabled_checkbox.setChecked(bool(options.server_enabled))
        self._server_enabled_checkbox.setToolTip(
            "When enabled, the desktop app automatically connects to the remote-control server "
            "when it opens. It can always be connected/disconnected manually from the Server menu."
        )
        server_enabled_row.addWidget(self._server_enabled_checkbox)
        server_enabled_row.addStretch()
        root.addLayout(server_enabled_row)

        server_address_row = QHBoxLayout()
        server_address_label = QLabel("Server Address")
        server_address_label.setFixedWidth(100)
        server_address_row.addWidget(server_address_label)
        self._server_address_edit = QLineEdit(str(options.server_address or ""))
        self._server_address_edit.setPlaceholderText("e.g. jingles.brianpclark.com or 192.168.1.50:47030")
        self._server_address_edit.setMinimumWidth(220)
        server_address_row.addWidget(self._server_address_edit)
        server_address_row.addStretch()
        root.addLayout(server_address_row)

        server_token_row = QHBoxLayout()
        server_token_label = QLabel("Device Token")
        server_token_label.setFixedWidth(100)
        server_token_row.addWidget(server_token_label)
        self._server_device_token_edit = QLineEdit(str(options.server_device_token or ""))
        self._server_device_token_edit.setPlaceholderText("From 'jingleserver adddevice <label>' on the server")
        self._server_device_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._server_device_token_edit.setMinimumWidth(220)
        server_token_row.addWidget(self._server_device_token_edit)
        server_token_row.addStretch()
        root.addLayout(server_token_row)

        server_test_row = QHBoxLayout()
        server_test_spacer = QLabel("")
        server_test_spacer.setFixedWidth(100)
        server_test_row.addWidget(server_test_spacer)
        test_connection_btn = QPushButton("Test Connection")
        test_connection_btn.clicked.connect(self._on_test_connection_clicked)
        server_test_row.addWidget(test_connection_btn)
        self._server_test_result_label = QLabel("")
        self._server_test_result_label.setWordWrap(True)
        server_test_row.addWidget(self._server_test_result_label, 1)
        root.addLayout(server_test_row)

        cache_reminder_row = QHBoxLayout()
        cache_reminder_label = QLabel("Cache Backup")
        cache_reminder_label.setFixedWidth(100)
        cache_reminder_row.addWidget(cache_reminder_label)
        self._cache_backup_reminder_spin = QSpinBox()
        self._cache_backup_reminder_spin.setRange(1, 90)
        self._cache_backup_reminder_spin.setValue(max(1, min(90, int(options.cache_backup_reminder_days))))
        self._cache_backup_reminder_spin.setSuffix(" day(s) between reminders")
        self._cache_backup_reminder_spin.setMinimumWidth(220)
        cache_reminder_row.addWidget(self._cache_backup_reminder_spin)
        cache_reminder_row.addStretch()
        root.addLayout(cache_reminder_row)

        refresh_btn = QPushButton("Refresh Devices")
        refresh_btn.clicked.connect(self._on_refresh_clicked)
        refresh_row = QHBoxLayout()
        refresh_row.addWidget(refresh_btn)
        refresh_row.addStretch()


        root.addLayout(refresh_row)

        self._broadcast_warning_label = QLabel()
        self._broadcast_warning_label.setWordWrap(True)
        root.addWidget(self._broadcast_warning_label)

        virtual_hint = QLabel(
            "Tip: choose a [Virtual] Broadcast Device for Discord/OBS style routing while keeping Live Device for local monitoring."
        )
        virtual_hint.setWordWrap(True)
        root.addWidget(virtual_hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._populate_devices(
            options.output_device,
            options.preview_output_device,
            options.broadcast_output_device,
            options.microphone_input_device,
            options.recording_input_device,
        )
        self._live_volume_slider.valueChanged.connect(self._sync_volume_labels)
        self._preview_volume_slider.valueChanged.connect(self._sync_volume_labels)
        self._microphone_gain_slider.valueChanged.connect(self._sync_volume_labels)
        self._live_device_combo.currentIndexChanged.connect(self._refresh_broadcast_warning)
        self._broadcast_device_combo.currentIndexChanged.connect(self._refresh_broadcast_warning)
        self._mixer_enabled_checkbox.toggled.connect(self._refresh_broadcast_warning)
        self._sync_volume_labels()
        self._refresh_broadcast_warning()

    def _populate_devices(
        self,
        live_selected: str,
        preview_selected: str,
        broadcast_selected: str,
        microphone_selected: str,
        recording_selected: int | str | None,
    ) -> None:
        self._populate_device_combo(self._live_device_combo, live_selected)
        self._populate_device_combo(self._preview_device_combo, preview_selected)
        self._populate_device_combo(self._broadcast_device_combo, broadcast_selected)
        self._populate_input_device_combo(self._microphone_device_combo, microphone_selected)
        self._populate_recording_device_combo(self._recording_device_combo, recording_selected)

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
                    combo.addItem(format_audio_device_label(name), name)
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
                combo.addItem(f"{format_audio_device_label(target)} (Unavailable)", target)
                combo.setCurrentIndex(combo.count() - 1)
        else:
            combo.setCurrentIndex(0)

        combo.blockSignals(False)

    def _on_refresh_clicked(self) -> None:
        live_current = self._live_device_combo.currentData()
        preview_current = self._preview_device_combo.currentData()
        broadcast_current = self._broadcast_device_combo.currentData()
        microphone_current = self._microphone_device_combo.currentData()
        recording_current = self._recording_device_combo.currentData()
        live_selected = str(live_current).strip() if live_current is not None else ""
        preview_selected = str(preview_current).strip() if preview_current is not None else ""
        broadcast_selected = str(broadcast_current).strip() if broadcast_current is not None else ""
        microphone_selected = str(microphone_current).strip() if microphone_current is not None else ""
        recording_selected = recording_current if isinstance(recording_current, int) else None
        self._populate_devices(
            live_selected,
            preview_selected,
            broadcast_selected,
            microphone_selected,
            recording_selected,
        )
        self._refresh_broadcast_warning()

    def _populate_input_device_combo(self, combo: QComboBox, selected_device: str) -> None:
        combo.blockSignals(True)
        combo.clear()

        default_name = ""
        if _has_qt_multimedia:
            try:
                default_name = QMediaDevices.defaultAudioInput().description().strip()
                seen: set[str] = set()
                for device in QMediaDevices.audioInputs():
                    name = device.description().strip()
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    combo.addItem(format_audio_device_label(name), name)
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
                combo.addItem(f"{format_audio_device_label(target)} (Unavailable)", target)
                combo.setCurrentIndex(combo.count() - 1)
        else:
            combo.setCurrentIndex(0)

        combo.blockSignals(False)

    def _populate_recording_device_combo(self, combo: QComboBox, selected_device: int | str | None) -> None:
        combo.blockSignals(True)
        combo.clear()

        engine = get_recording_engine()
        available_devices = engine.get_available_devices()
        names_by_id = {device_id: device_name for device_id, device_name in available_devices}
        name_counts: dict[str, int] = {}
        for _device_id, device_name in available_devices:
            key = device_name.strip().casefold()
            if not key:
                continue
            name_counts[key] = name_counts.get(key, 0) + 1

        default_name = ""
        try:
            default_device_id, _sample_rate, _wav_subtype = engine.resolve_recording_settings(
                RecordingConfig(device_id=None)
            )
            if isinstance(default_device_id, int):
                default_name = names_by_id.get(default_device_id, "").strip()
        except Exception:
            default_name = ""

        default_label = "System Default"
        if default_name:
            default_label = f"System Default ({format_audio_device_label(default_name)})"

        combo.insertItem(0, default_label, -1)

        seen: set[int] = set()
        for device_id, device_name in available_devices:
            if device_id in seen:
                continue
            seen.add(device_id)
            base_label = format_audio_device_label(device_name)
            if name_counts.get(device_name.strip().casefold(), 0) > 1:
                display_label = f"{base_label} (Device {device_id})"
            else:
                display_label = base_label
            combo.addItem(display_label, device_id)

        target_value: int | None = None
        if isinstance(selected_device, int):
            target_value = selected_device if selected_device >= 0 else None
        elif isinstance(selected_device, str):
            try:
                parsed = int(selected_device)
            except (TypeError, ValueError):
                parsed = -1
            target_value = parsed if parsed >= 0 else None

        if target_value is None:
            combo.setCurrentIndex(0)
        else:
            idx = combo.findData(target_value)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.addItem(f"Device {target_value} (Unavailable)", target_value)
                combo.setCurrentIndex(combo.count() - 1)

        combo.blockSignals(False)

    def selected_options(self) -> OptionsSettings:
        recording_device = self.selected_recording_device()
        return OptionsSettings(
            output_device=self._combo_text(self._live_device_combo),
            preview_output_device=self._combo_text(self._preview_device_combo),
            broadcast_output_device=self._combo_text(self._broadcast_device_combo),
            mixer_enabled=bool(self._mixer_enabled_checkbox.isChecked()),
            microphone_input_device=self._combo_text(self._microphone_device_combo),
            recording_input_device=-1 if recording_device is None else recording_device,
            microphone_gain_percent=max(0, min(200, int(self._microphone_gain_slider.value()))),
            live_volume_percent=_coerce_volume_percent(self._live_volume_slider.value()),
            preview_volume_percent=_coerce_volume_percent(self._preview_volume_slider.value()),
            recent_window_days=_coerce_recent_window_days(self._recent_window_days_spin.value()),
            sample_pad_blocksize=_coerce_sample_pad_blocksize(
                self._sample_pad_blocksize_combo.currentData()
            ),
            sample_pad_streaming_min_seconds=_coerce_sample_pad_streaming_min_seconds(
                self._sample_pad_streaming_min_seconds_spin.value()
            ),
            server_enabled=bool(self._server_enabled_checkbox.isChecked()),
            server_address=self.selected_server_address(),
            server_device_token=self.selected_server_device_token(),
            cache_backup_reminder_days=max(1, min(90, int(self._cache_backup_reminder_spin.value()))),
        )

    @staticmethod
    def _combo_text(combo: QComboBox) -> str:
        value = combo.currentData()
        return str(value).strip() if value is not None else ""

    def _refresh_broadcast_warning(self) -> None:
        live = str(self._live_device_combo.currentData() or "").strip()
        broadcast = str(self._broadcast_device_combo.currentData() or "").strip()
        mixer_enabled = bool(self._mixer_enabled_checkbox.isChecked())
        messages = broadcast_route_warnings(live, broadcast, mixer_enabled)
        self._broadcast_warning_label.setText("\n".join(messages))

    def selected_recording_device(self) -> int | None:
        recording = self._recording_device_combo.currentData()
        if recording is None:
            return None
        if isinstance(recording, int):
            return recording if recording >= 0 else None
        try:
            parsed = int(str(recording).strip())
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    def selected_server_address(self) -> str:
        return self._server_address_edit.text().strip()

    def selected_server_device_token(self) -> str:
        return self._server_device_token_edit.text().strip()

    def _on_test_connection_clicked(self) -> None:
        address = self.selected_server_address()
        token = self.selected_server_device_token()
        if not address or not token:
            self._server_test_result_label.setText("Enter a Server Address and Device Token first.")
            return
        self._server_test_result_label.setText("Testing...")
        QApplication.processEvents()
        ok, message = _remote_server.test_connection(address, token)
        prefix = "✓" if ok else "✗"
        self._server_test_result_label.setText(f"{prefix} {message}")

    def selected_folder(self) -> Path | None:
        text = self._folder_edit.text().strip()
        if not text:
            return None
        p = Path(text)
        return p if p.exists() and p.is_dir() else None

    def _on_browse_folder(self) -> None:
        current = self._folder_edit.text().strip()
        start = current if current else str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Choose Samples Folder", start)
        if not selected:
            return
        path = Path(selected)
        if path.exists() and path.is_dir():
            self._folder_edit.setText(str(path))

    def _sync_volume_labels(self) -> None:
        self._live_volume_value_label.setText(f"{self._live_volume_slider.value()}%")
        self._preview_volume_value_label.setText(f"{self._preview_volume_slider.value()}%")
        self._microphone_gain_value_label.setText(f"{self._microphone_gain_slider.value()}%")


class AudioDiagnosticsDialog(QDialog):
    def __init__(
        self,
        snapshot_provider: Callable[[], dict[str, Any]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Audio Diagnostics")
        self.resize(860, 640)
        self._snapshot_provider = snapshot_provider

        root = QVBoxLayout(self)

        intro = QLabel(
            "Inspect current audio routes, mixer state, and virtual-device candidates for broadcast workflows."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        self._report_edit = QPlainTextEdit(self)
        self._report_edit.setReadOnly(True)
        self._report_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._report_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self._report_edit, 1)

        button_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_report)
        button_row.addWidget(refresh_btn)

        copy_btn = QPushButton("Copy Report")
        copy_btn.clicked.connect(self._copy_report)
        button_row.addWidget(copy_btn)

        guide = virtual_device_guide()
        if guide is not None:
            for label, commands in (
                ("Copy Create Broadcast Devices", guide.create_commands),
                ("Copy Delete Broadcast Devices", guide.delete_commands),
                ("Copy List Audio Devices", guide.list_commands),
            ):
                if not commands:
                    continue
                guide_btn = QPushButton(label)
                guide_btn.clicked.connect(lambda _checked=False, text=commands: self._copy_text(text))
                button_row.addWidget(guide_btn)

        button_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_row.addWidget(close_btn)
        root.addLayout(button_row)

        self.refresh_report()

    def refresh_report(self) -> None:
        self._report_edit.setPlainText(self._format_snapshot(self._snapshot_provider()))

    def _copy_report(self) -> None:
        self._copy_text(self._report_edit.toPlainText())

    def _copy_text(self, text: str) -> None:
        app = QApplication.instance()
        if app is None:
            return
        clipboard = app.clipboard()
        if clipboard is None:
            return
        clipboard.setText(text)

    def _format_snapshot(self, snapshot: dict[str, Any]) -> str:
        def fmt_value(value: Any, *, empty: str = "(none)") -> str:
            text = str(value).strip() if value is not None else ""
            return text if text else empty

        def fmt_list(values: list[str], *, empty: str = "(none)") -> str:
            if not values:
                return empty
            return "\n".join(f"- {value}" for value in values)

        sections = [
            "Current State",
            f"- Mixer: {fmt_value(snapshot.get('mixer_status'), empty='Unknown')}",
            f"- Active route: {fmt_value(snapshot.get('active_output_route'))}",
            f"- Live device setting: {fmt_value(snapshot.get('live_output_setting'), empty='System Default')}",
            f"- Preview device setting: {fmt_value(snapshot.get('preview_output_setting'), empty='System Default')}",
            f"- Broadcast device setting: {fmt_value(snapshot.get('broadcast_output_setting'), empty='(disabled)')}",
            f"- Mixer microphone setting: {fmt_value(snapshot.get('microphone_setting'), empty='System Default')}",
            f"- Qt playback device: {fmt_value(snapshot.get('qt_output_device'))}",
            f"- PortAudio output route: {fmt_value(snapshot.get('portaudio_output_device'))}",
            f"- PortAudio input route: {fmt_value(snapshot.get('portaudio_input_device'))}",
            "",
            "Warnings",
            fmt_list(list(snapshot.get('warnings', []))),
            "",
            "Virtual / Loopback Candidates",
            fmt_list(list(snapshot.get('virtual_candidates', []))),
            "",
            "Qt Output Devices",
            fmt_list(list(snapshot.get('qt_output_devices', []))),
            "",
            "Qt Input Devices",
            fmt_list(list(snapshot.get('qt_input_devices', []))),
            "",
            "PortAudio Output Devices",
            fmt_list(list(snapshot.get('portaudio_output_devices', []))),
            "",
            "PortAudio Input Devices",
            fmt_list(list(snapshot.get('portaudio_input_devices', []))),
            "",
            fmt_value(snapshot.get('virtual_device_assistant_title'), empty='Virtual Device Assistant'),
            fmt_list(list(snapshot.get('virtual_device_assistant', []))),
            "",
            "Broadcast Workflow",
            "- Pick a [Virtual] Broadcast Device in Options when sending mixer audio to Discord, OBS, or a loopback sink.",
            *(f"- {note}" for note in snapshot.get('virtual_device_workflow_notes', [])),
            "- Use Refresh after changing audio devices or mixer settings.",
        ]
        return "\n".join(sections)


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
            default_label.setStyleSheet("color: palette(mid);")
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


def show_silent_message(
    parent: QWidget | None,
    title: str,
    text: str,
    icon: QStyle.StandardPixmap = QStyle.StandardPixmap.SP_MessageBoxInformation,
) -> None:
    """Modal message box that does not play a Windows notification sound.

    QMessageBox raises an accessibility alert when shown, which Windows answers
    with a system sound. That sound reaches the Live output (and so Discord
    listeners) when Live is the default device, so use this for messages that
    can appear on their own while the app is broadcasting.
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    root = QVBoxLayout(dialog)

    row = QHBoxLayout()
    icon_label = QLabel()
    icon_size = dialog.style().pixelMetric(QStyle.PixelMetric.PM_MessageBoxIconSize)
    icon_label.setPixmap(dialog.style().standardIcon(icon).pixmap(icon_size, icon_size))
    icon_label.setAlignment(Qt.AlignmentFlag.AlignTop)
    row.addWidget(icon_label)
    text_label = QLabel(text)
    text_label.setWordWrap(True)
    text_label.setMinimumWidth(320)
    text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    row.addWidget(text_label, 1)
    root.addLayout(row)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
    buttons.accepted.connect(dialog.accept)
    root.addWidget(buttons)
    dialog.exec()


class OfflineCacheBackupDialog(QDialog):
    def __init__(
        self,
        progress_provider: Callable[[], dict[str, Any]],
        start_callback: Callable[[], None],
        cancel_callback: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Offline Cache Backup")
        self.setMinimumWidth(560)
        self.setModal(False)
        self._progress_provider = progress_provider
        self._start_callback = start_callback
        self._cancel_callback = cancel_callback

        root = QVBoxLayout(self)
        self._summary_label = QLabel("Ready to back up the jingle library to the remote cache.", self)
        self._summary_label.setWordWrap(True)
        root.addWidget(self._summary_label)

        self._progress_bar = QProgressBar(self)
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(0)
        root.addWidget(self._progress_bar)

        self._detail_label = QLabel("Click Start to begin.", self)
        self._detail_label.setWordWrap(True)
        root.addWidget(self._detail_label)

        buttons = QDialogButtonBox(self)
        self._start_button = buttons.addButton("Start", QDialogButtonBox.ButtonRole.AcceptRole)
        self._cancel_button = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        self._start_button.clicked.connect(self._on_start)
        self._cancel_button.clicked.connect(self._on_cancel)
        root.addWidget(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.refresh)

    def _on_start(self) -> None:
        self._start_button.setEnabled(False)
        self._cancel_button.setText("Cancel Backup")
        self._start_callback()
        self._timer.start()
        self.refresh()

    def _on_cancel(self) -> None:
        progress = self._progress_provider()
        if not progress.get("running"):
            self.reject()
            return
        self._cancel_button.setEnabled(False)
        self._cancel_button.setText("Cancelling...")
        self._detail_label.setText("Cancelling after the current file finishes...")
        self._cancel_callback()

    def refresh(self) -> None:
        progress = self._progress_provider()
        total = max(1, int(progress.get("total", 0)))
        completed = min(total, int(progress.get("completed", 0)))
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(completed)
        self._summary_label.setText(
            f"{completed} of {int(progress.get('total', 0))} files processed | "
            f"{int(progress.get('uploaded', 0))} uploaded | "
            f"{int(progress.get('skipped', 0))} already up to date"
        )
        self._detail_label.setText(str(progress.get("detail", "Preparing...")))
        if not progress.get("running") and progress.get("started"):
            self._timer.stop()

    def finish(self) -> None:
        self._timer.stop()
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: ANN001 - Qt override signature
        if self._progress_provider().get("running"):
            self._on_cancel()
            event.ignore()
            return
        self._timer.stop()
        super().closeEvent(event)


class RemoteDiagnosticsDialog(QDialog):
    def __init__(
        self,
        snapshot_provider: Callable[[], dict[str, Any]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Remote Relay Diagnostics")
        self.resize(720, 480)
        self._snapshot_provider = snapshot_provider

        root = QVBoxLayout(self)

        self._status_label = QLabel(self)
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        root.addWidget(QLabel("Recent Relayed Commands", self))
        self._log_table = QTableWidget(0, 4, self)
        self._log_table.setHorizontalHeaderLabels(["Time", "Action", "Target", "Result"])
        self._log_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._log_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        root.addWidget(self._log_table, 2)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        root.addWidget(buttons)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start()
        self.refresh()

    def refresh(self) -> None:
        snapshot = self._snapshot_provider()
        connected = bool(snapshot.get("connected", False))
        address = str(snapshot.get("address", ""))
        last_error = str(snapshot.get("last_error", ""))
        reconnect_count = int(snapshot.get("reconnect_count", 0))
        if connected:
            connected_at = float(snapshot.get("connected_at", 0.0))
            timestamp = time.strftime("%H:%M:%S", time.localtime(connected_at)) if connected_at else ""
            self._status_label.setText(f"Connected to {address} since {timestamp}. Reconnects: {reconnect_count}.")
        elif last_error:
            self._status_label.setText(
                f"Not connected. Last error: {last_error} (reconnect attempts: {reconnect_count})"
            )
        else:
            self._status_label.setText("Not connected.")

        log_entries = snapshot.get("log", [])
        self._log_table.setRowCount(len(log_entries))
        for row, entry in enumerate(log_entries):
            timestamp = time.strftime("%H:%M:%S", time.localtime(float(entry.get("time", 0.0))))
            result_text = "OK" if entry.get("ok") else f"Failed: {entry.get('detail', '')}"
            self._log_table.setItem(row, 0, QTableWidgetItem(timestamp))
            self._log_table.setItem(row, 1, QTableWidgetItem(str(entry.get("action", ""))))
            self._log_table.setItem(row, 2, QTableWidgetItem(str(entry.get("target", ""))))
            self._log_table.setItem(row, 3, QTableWidgetItem(result_text))

    def closeEvent(self, event) -> None:  # noqa: ANN001 - Qt override signature
        self._refresh_timer.stop()
        super().closeEvent(event)


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
