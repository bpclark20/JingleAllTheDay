from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Callable

from app_helpers import coerce_volume_percent as _coerce_volume_percent
from app_helpers import coerce_recent_window_days as _coerce_recent_window_days
from app_helpers import format_duration_hms as _format_duration_hms
from app_helpers import format_size_label as _format_size_label
import sample_pad_audio_engine as _sp_engine_mod
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
    QPushButton,
    QKeySequenceEdit,
    QSizePolicy,
    QSpinBox,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import QTimer
from recording_engine import RecordingConfig, get_recording_engine

_has_qt_multimedia = False
try:
    from PyQt6.QtMultimedia import QMediaDevices

    _has_qt_multimedia = True
except ModuleNotFoundError:
    pass


SAMPLE_PAD_BLOCKSIZE_OPTIONS = (1024, 512, 384, 256, 224, 192, 128, 64)

_VIRTUAL_AUDIO_KEYWORDS = (
    "broadcast",
    "blackhole",
    "cable",
    "dante",
    "jack",
    "loopback",
    "monitor",
    "null",
    "sink",
    "soundflower",
    "vb-audio",
    "virtual",
    "voicemeeter",
)


def is_virtual_audio_device_name(name: str) -> bool:
    normalized = name.strip().casefold()
    if not normalized:
        return False
    if any(keyword in normalized for keyword in _VIRTUAL_AUDIO_KEYWORDS):
        return True

    # On Linux, Qt friendly labels for null sinks can be shortened or cleaned up
    # compared with the PortAudio device names. If a matching PortAudio output has
    # a corresponding .monitor input, treat it as a virtual sink.
    if sys.platform.startswith("linux") and _sp_engine_mod.is_available():
        try:
            devices = _sp_engine_mod.list_audio_devices()
        except Exception:
            devices = []
        candidate_names = {
            str(dev.get("name", "")).strip().casefold()
            for dev in devices
            if str(dev.get("name", "")).strip()
        }
        for candidate in candidate_names:
            if normalized not in candidate and candidate not in normalized:
                continue
            if f"{candidate}.monitor" in candidate_names:
                return True
            if candidate.endswith("broadcast") or candidate.endswith("sink"):
                return True
    return False


def format_audio_device_label(name: str) -> str:
    if is_virtual_audio_device_name(name):
        return f"[Virtual] {name}"
    return name


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
        broadcast_output_device: str,
        mixer_enabled: bool,
        microphone_input_device: str,
        recording_input_device: int | str | None,
        microphone_gain_percent: int,
        live_volume_percent: int,
        preview_volume_percent: int,
        recent_window_days: int,
        sample_pad_blocksize: int,
        sample_pad_streaming_min_seconds: int,
        samples_dir: Path | None = None,
        server_enabled: bool = True,
        server_address: str = "",
        server_device_token: str = "",
        cache_backup_reminder_days: int = 7,
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
        self._mixer_enabled_checkbox.setChecked(bool(mixer_enabled))
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
        self._microphone_gain_slider.setValue(max(0, min(200, int(microphone_gain_percent))))
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

        recent_window_row = QHBoxLayout()
        recent_window_label = QLabel("Recent Window")
        recent_window_label.setFixedWidth(100)
        recent_window_row.addWidget(recent_window_label)

        self._recent_window_days_spin = QSpinBox()
        self._recent_window_days_spin.setRange(1, 3650)
        self._recent_window_days_spin.setSingleStep(1)
        self._recent_window_days_spin.setSuffix(" days")
        self._recent_window_days_spin.setValue(_coerce_recent_window_days(recent_window_days))
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

        server_enabled_row = QHBoxLayout()
        server_enabled_label = QLabel("Remote Server")
        server_enabled_label.setFixedWidth(100)
        server_enabled_row.addWidget(server_enabled_label)
        self._server_enabled_checkbox = QCheckBox("Auto-connect on launch")
        self._server_enabled_checkbox.setChecked(bool(server_enabled))
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
        self._server_address_edit = QLineEdit(str(server_address or ""))
        self._server_address_edit.setPlaceholderText("e.g. jingles.brianpclark.com or 192.168.1.50:47030")
        self._server_address_edit.setMinimumWidth(220)
        server_address_row.addWidget(self._server_address_edit)
        server_address_row.addStretch()
        root.addLayout(server_address_row)

        server_token_row = QHBoxLayout()
        server_token_label = QLabel("Device Token")
        server_token_label.setFixedWidth(100)
        server_token_row.addWidget(server_token_label)
        self._server_device_token_edit = QLineEdit(str(server_device_token or ""))
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
        self._cache_backup_reminder_spin.setValue(max(1, min(90, int(cache_backup_reminder_days))))
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
            live_output_device,
            preview_output_device,
            broadcast_output_device,
            microphone_input_device,
            recording_input_device,
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

    def selected_devices(self) -> tuple[str, str, str]:
        live = self._live_device_combo.currentData()
        preview = self._preview_device_combo.currentData()
        broadcast = self._broadcast_device_combo.currentData()
        live_value = str(live).strip() if live is not None else ""
        preview_value = str(preview).strip() if preview is not None else ""
        broadcast_value = str(broadcast).strip() if broadcast is not None else ""
        return live_value, preview_value, broadcast_value

    def _refresh_broadcast_warning(self) -> None:
        live = str(self._live_device_combo.currentData() or "").strip()
        broadcast = str(self._broadcast_device_combo.currentData() or "").strip()
        mixer_enabled = bool(self._mixer_enabled_checkbox.isChecked())

        messages: list[str] = []
        if not mixer_enabled:
            messages.append("Mixer Mode is off, so microphone audio will not be sent to any broadcast route.")
        if not broadcast:
            if live and not is_virtual_audio_device_name(live):
                messages.append(
                    "No Broadcast Device is selected. Discord/OBS routing usually works best with a virtual or loopback broadcast device instead of a physical Live Device."
                )
        else:
            if not is_virtual_audio_device_name(broadcast):
                messages.append(
                    "The selected Broadcast Device does not look like a virtual or loopback device. Physical outputs usually will not behave like an isolated broadcast feed."
                )
            if live.strip().casefold() == broadcast.strip().casefold():
                messages.append(
                    "Broadcast Device matches Live Device, so broadcast and local monitoring are not isolated from each other."
                )
        messages.append(
            "Main library jingles are mirrored to the Broadcast Device. Sample-pad jingles still follow the current Live/Preview monitor route, and live-mode sample pads are also duplicated to the Broadcast Device when it differs from the monitor route."
        )
        self._broadcast_warning_label.setText("\n".join(messages))

    def selected_mixer_config(self) -> tuple[bool, str, int]:
        microphone = self._microphone_device_combo.currentData()
        microphone_value = str(microphone).strip() if microphone is not None else ""
        microphone_gain = max(0, min(200, int(self._microphone_gain_slider.value())))
        return bool(self._mixer_enabled_checkbox.isChecked()), microphone_value, microphone_gain

    def selected_recording_device(self) -> int | str | None:
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

    def selected_volumes(self) -> tuple[int, int]:
        return (
            _coerce_volume_percent(self._live_volume_slider.value()),
            _coerce_volume_percent(self._preview_volume_slider.value()),
        )

    def selected_sample_pad_blocksize(self) -> int:
        value = self._sample_pad_blocksize_combo.currentData()
        return _coerce_sample_pad_blocksize(value)

    def selected_recent_window_days(self) -> int:
        return _coerce_recent_window_days(self._recent_window_days_spin.value())

    def selected_server_enabled(self) -> bool:
        return bool(self._server_enabled_checkbox.isChecked())

    def selected_server_address(self) -> str:
        return self._server_address_edit.text().strip()

    def selected_server_device_token(self) -> str:
        return self._server_device_token_edit.text().strip()

    def selected_cache_backup_reminder_days(self) -> int:
        return max(1, min(90, int(self._cache_backup_reminder_spin.value())))

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

        if sys.platform.startswith("linux"):
            copy_create_btn = QPushButton("Copy Create Broadcast Devices")
            copy_create_btn.clicked.connect(
                lambda: self._copy_text(
                    "pactl load-module module-null-sink sink_name=JingleBroadcast sink_properties=device.description=\"Jingle Broadcast\"\n"
                    "pactl load-module module-remap-source master=JingleBroadcast.monitor source_name=JingleMic source_properties=device.description=\"Jingle Mic\""
                )
            )
            button_row.addWidget(copy_create_btn)

            copy_delete_btn = QPushButton("Copy Delete Broadcast Devices")
            copy_delete_btn.clicked.connect(
                lambda: self._copy_text(
                    "for id in $(pactl list short modules | awk '/module-remap-source/ && /source_name=JingleMic/ {print $1}'); do pactl unload-module \"$id\"; done\n"
                    "for id in $(pactl list short modules | awk '/module-null-sink/ && /sink_name=JingleBroadcast/ {print $1}'); do pactl unload-module \"$id\"; done"
                )
            )
            button_row.addWidget(copy_delete_btn)

            copy_list_btn = QPushButton("Copy List Audio Devices")
            copy_list_btn.clicked.connect(
                lambda: self._copy_text("pactl list short sinks\npactl list short sources")
            )
            button_row.addWidget(copy_list_btn)

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
            "Linux Virtual Sink Assistant",
            fmt_list(list(snapshot.get('linux_assistant', []))),
            "",
            "Broadcast Workflow",
            "- Pick a [Virtual] Broadcast Device in Options when sending mixer audio to Discord, OBS, or a loopback sink.",
            "- On Linux, create both the sink and the Jingle Mic remapped source, then select Jingle Mic in Discord or other chat apps.",
            "- Audacity can usually record from either Jingle Mic or the raw JingleBroadcast.monitor source.",
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
