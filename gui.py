#!/usr/bin/env python3
"""Jingle browser GUI with category filters and playback device selection."""

from __future__ import annotations

import sys
import json
from pathlib import Path
from typing import Any

from app_helpers import (
    APPEARANCE_MODE_DARK,
    APPEARANCE_MODE_LIGHT,
    APPEARANCE_MODE_SYSTEM,
    SAMPLE_PAD_BLOCKSIZE_OPTIONS,
    QAudioOutput,
    QMediaPlayer,
    _coerce_sample_pad_blocksize,
    _coerce_sample_pad_streaming_min_seconds,
    _has_qt_multimedia,
    _normalize_recording_wav_subtype,
    apply_app_appearance_mode as _apply_app_appearance_mode,
    apply_windows_titlebar_theme as _apply_windows_titlebar_theme,
    apply_windows_taskbar_icon as _apply_windows_taskbar_icon,
    chip_palette_for_tag_seed as _chip_palette_for_tag_seed,
    coerce_appearance_mode as _coerce_appearance_mode,
    coerce_recent_window_days as _coerce_recent_window_days,
    coerce_volume_percent as _coerce_volume_percent,
    ensure_qt_logging_rules as _ensure_qt_logging_rules,
    format_duration_hms as _format_duration_hms,
    format_size_label as _format_size_label,
    probe_duration_seconds as _probe_duration_seconds,
)
from dialogs import (
    AudioDiagnosticsDialog,
    OptionsDialog,
)
from mainwindow_appearance_mixin import MainWindowAppearanceMixin
from mainwindow_audio_diagnostics_mixin import MainWindowAudioDiagnosticsMixin
from mainwindow_button_fx_mixin import MainWindowButtonFxMixin
from mainwindow_cache_backup_mixin import MainWindowCacheBackupMixin
from mainwindow_file_edit_mixin import MainWindowFileEditMixin
from mainwindow_hotkeys_mixin import MainWindowHotkeysMixin
from mainwindow_library_mixin import MainWindowLibraryMixin
from mainwindow_menu_mixin import MainWindowMenuMixin
from mainwindow_mixer_mixin import MainWindowMixerMixin
from mainwindow_playback_mixin import MainWindowPlaybackMixin
from mainwindow_remote_mixin import MainWindowRemoteMixin
from mainwindow_sample_pad_mixin import MainWindowSamplePadMixin
from mainwindow_server_mixin import MainWindowServerMixin
from mainwindow_shortcuts_mixin import MainWindowShortcutsMixin
from mainwindow_table_mixin import MainWindowTableMixin
from mainwindow_tools_mixin import MainWindowToolsMixin
from models_store import JingleRecord, LibraryStore

from widgets import DeselectableTableWidget
# Import SamplePadsWindow for sample pad feature
from sample_pads import SamplePadsWindow
from playlists_window import PLAYLIST_DRAG_MIME_TYPE, PlaylistsWindow
from sample_pad_audio_engine import SamplePadAudioEngine as _SamplePadAudioEngine
import sample_pad_audio_engine as _sp_engine_mod


_ensure_qt_logging_rules()
_HERE = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(__file__).resolve().parent  # type: ignore[attr-defined]

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QFileSystemWatcher,
    QObject,
    QPropertyAnimation,
    QSettings,
    QTimer,
    Qt,
    QStandardPaths,
    pyqtSignal,
)
from PyQt6.QtGui import QAction, QActionGroup, QCursor, QIcon, QKeyEvent, QMouseEvent, QPixmap, QShowEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QMenu,
    QGridLayout,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QStatusBar,
    QHeaderView,
    QSizePolicy,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

DEFAULT_APP_NAME = "JingleAllTheDay"
DEFAULT_APP_VERSION = "0.0.0"

DEFAULT_KEYBOARD_SHORTCUTS: dict[str, str] = {
    "rename": "F2",
    "delete": "Delete",
    "skip_previous": "Left",
    "skip_next": "Right",
    "select_up": "Up",
    "select_down": "Down",
}

class MainWindow(
    MainWindowAppearanceMixin,
    MainWindowAudioDiagnosticsMixin,
    MainWindowButtonFxMixin,
    MainWindowCacheBackupMixin,
    MainWindowHotkeysMixin,
    MainWindowMenuMixin,
    MainWindowMixerMixin,
    MainWindowPlaybackMixin,
    MainWindowRemoteMixin,
    MainWindowSamplePadMixin,
    MainWindowServerMixin,
    MainWindowShortcutsMixin,
    MainWindowTableMixin,
    MainWindowFileEditMixin,
    MainWindowToolsMixin,
    MainWindowLibraryMixin,
    QMainWindow,
):
    _sample_pad_hotkey_requested = pyqtSignal(int)
    _sample_pad_release_requested = pyqtSignal(int)
    _sample_pad_board_switch_requested = pyqtSignal(int)
    _cache_backup_finished = pyqtSignal(object, object)

    def __init__(self, app_name: str = DEFAULT_APP_NAME, app_version: str = DEFAULT_APP_VERSION) -> None:
        super().__init__()
        self.setWindowTitle("JingleAllTheDay")
        self.resize(1200, 740)
        self._app_name = app_name
        self._app_version = app_version

        _icon_path = _HERE / "icon.png"
        if _icon_path.exists():
            self.setWindowIcon(QIcon(str(_icon_path)))

        app_data_location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        self._app_data_dir = Path(app_data_location)
        self._app_data_dir.mkdir(parents=True, exist_ok=True)
        self._settings = self._create_settings_store()
        self._appearance_mode = _coerce_appearance_mode(
            self._settings.value("options/appearanceMode", APPEARANCE_MODE_SYSTEM)
        )
        self._appearance_effective_mode = APPEARANCE_MODE_LIGHT
        self._playlists_last_playlist_path = str(
            self._settings.value("playlists/lastPlaylistPath", "")
        ).strip()
        self._playlists_autosave_path = str(
            (self._app_data_dir / "playlists_autosave.json").resolve()
        )
        self._settings.setValue("playlists/autosavePath", self._playlists_autosave_path)
        self._output_device = str(self._settings.value("options/outputDevice", "")).strip()
        self._preview_output_device = str(self._settings.value("options/previewOutputDevice", "")).strip()
        self._broadcast_output_device = str(
            self._settings.value("options/broadcastOutputDevice", "")
        ).strip()
        self._mixer_enabled = (
            str(self._settings.value("options/mixerEnabled", "false")).strip().lower() == "true"
        )
        self._microphone_input_device = str(
            self._settings.value("options/microphoneInputDevice", "")
        ).strip()
        try:
            self._recording_input_device = int(self._settings.value("options/recordingInputDevice", -1))
        except (TypeError, ValueError):
            self._recording_input_device = -1
        if self._recording_input_device < 0:
            self._recording_input_device = -1
        self._recording_wav_subtype = _normalize_recording_wav_subtype(
            self._settings.value("options/recordingWavSubtype", "PCM_16")
        )
        try:
            self._microphone_gain_percent = int(
                self._settings.value("options/microphoneGainPercent", 100)
            )
        except (TypeError, ValueError):
            self._microphone_gain_percent = 100
        self._microphone_gain_percent = max(0, min(200, self._microphone_gain_percent))
        self._live_volume_percent = _coerce_volume_percent(
            self._settings.value("options/liveVolumePercent", 100)
        )
        self._preview_volume_percent = _coerce_volume_percent(
            self._settings.value("options/previewVolumePercent", 100)
        )
        self._sample_pad_blocksize = _coerce_sample_pad_blocksize(
            self._settings.value("options/samplePadBlocksize", 128)
        )
        self._sample_pad_streaming_min_seconds = _coerce_sample_pad_streaming_min_seconds(
            self._settings.value("options/samplePadStreamingMinSeconds", 120)
        )
        self._recent_window_days = _coerce_recent_window_days(
            self._settings.value("options/recentWindowDays", 14)
        )
        self._server_enabled = (
            str(self._settings.value("server/enabled", "true")).strip().lower() == "true"
        )
        self._server_address = str(self._settings.value("server/address", "")).strip()
        self._server_device_token = str(self._settings.value("server/deviceToken", "")).strip()
        try:
            self._cache_backup_reminder_days = int(
                self._settings.value("server/cacheBackupReminderDays", 7)
            )
        except (TypeError, ValueError):
            self._cache_backup_reminder_days = 7
        if self._cache_backup_reminder_days < 1:
            self._cache_backup_reminder_days = 7
        try:
            self._cache_backup_last_epoch = float(
                self._settings.value("server/lastCacheBackupEpoch", 0.0)
            )
        except (TypeError, ValueError):
            self._cache_backup_last_epoch = 0.0
        try:
            self._cache_backup_snooze_until_epoch = float(
                self._settings.value("server/cacheBackupSnoozeUntilEpoch", 0.0)
            )
        except (TypeError, ValueError):
            self._cache_backup_snooze_until_epoch = 0.0
        self._sample_pads_last_layout_path = str(
            self._settings.value("samplePads/lastLayoutPath", "")
        ).strip()
        self._audio_diagnostics_dialog: AudioDiagnosticsDialog | None = None
        self._sample_pad_global_hotkeys_enabled = (
            str(self._settings.value("samplePads/globalHotkeysEnabled", "false")).strip().lower()
            == "true"
        )
        self._sample_pad_alt_modifier = (
            str(self._settings.value("samplePads/globalHotkeysAltModifier", "false")).strip().lower()
            == "true"
        )
        self._sample_pad_board_switch_requires_ctrl = (
            str(self._settings.value("samplePads/boardSwitchRequiresCtrl", "false")).strip().lower()
            == "true"
        )
        self._sample_pad_active_board_index = int(
            self._settings.value("samplePads/activeBoardIndex", 0)
        )
        if self._sample_pad_active_board_index < 0 or self._sample_pad_active_board_index > 4:
            self._sample_pad_active_board_index = 0
        self._sample_pad_recording_mode_enabled = (
            str(self._settings.value("samplePads/recordingModeEnabled", "false")).strip().lower()
            == "true"
        )
        self._sample_pad_listener: Any | None = None
        self._sample_pad_hotkey_backend = "none"
        self._sample_pad_win_filter: _WindowsHotkeyEventFilter | None = None
        self._sample_pad_hotkey_id_to_pad: dict[int, int] = {}
        self._sample_pad_backend_warning_shown = False
        self._sample_pad_looping: bool = False
        self._sample_pad_release_looping: bool = False
        self._sample_pad_native_looping: bool = False
        self._current_sample_pad_index: int = -1
        self._sample_pad_recording_active: bool = False
        self._sample_pad_recording_pad_index: int = -1
        self._sample_pad_recording_board_index: int = -1
        self._sample_pad_recording_slot_index: int = -1
        self._sample_pad_recording_output_path: Path | None = None
        self._sample_pad_recording_blink_on: bool = True
        self._sample_pad_recording_ui_timer = QTimer(self)
        self._sample_pad_recording_ui_timer.setInterval(350)
        self._sample_pad_recording_ui_timer.timeout.connect(self._on_sample_pad_recording_ui_tick)
        self._main_playback_meter_peaks_path: str = ""
        self._main_playback_meter_peaks: list[float] = []
        self._main_playback_meter_loading_path: str = ""
        self._main_playback_meter_loading: bool = False
        # Low-latency engine used for live-mode sample pad playback
        self._sp_engine: _SamplePadAudioEngine = _SamplePadAudioEngine(name="broadcast")
        self._sp_monitor_engine: _SamplePadAudioEngine = _SamplePadAudioEngine(name="monitor")
        self._sp_engine.set_streaming_min_seconds(self._sample_pad_streaming_min_seconds)
        self._sp_engine.set_mixer_enabled(self._mixer_enabled)
        self._sp_engine.set_input_gain(self._microphone_gain_percent / 100.0)
        self._sp_monitor_engine.set_streaming_min_seconds(self._sample_pad_streaming_min_seconds)
        self._sp_monitor_engine.set_mixer_enabled(False)
        self._sample_pads_dirty: bool = False
        self._sample_pads_last_saved_signature: str = ""
        self._sample_pads_autosave_in_progress: bool = False
        self._playlists_window: PlaylistsWindow | None = None
        self._samples_dir: Path | None = self._load_samples_dir()
        self._auto_folder_tags: bool = self._load_auto_folder_tags()
        self._auto_generate_waveforms: bool = self._load_auto_generate_waveforms()
        self._watch_library_changes: bool = self._load_watch_library_changes()
        self._default_keyboard_shortcuts = dict(DEFAULT_KEYBOARD_SHORTCUTS)
        self._keyboard_shortcuts = self._load_keyboard_shortcuts()

        self._rename_action: QAction | None = None
        self._delete_action: QAction | None = None
        self._appearance_action_group: QActionGroup | None = None
        self._appearance_action_system: QAction | None = None
        self._appearance_action_light: QAction | None = None
        self._appearance_action_dark: QAction | None = None

        library_path = self._app_data_dir / "jingle-library.json"
        self._store = LibraryStore(library_path)

        self._records: list[JingleRecord] = []
        self._visible_indices: list[int] = []
        self._updating_table = False
        self._is_rescanning = False
        self._last_reserved_recent_folders: list[Path] = []
        self._recent_folder_warning_signature = ""
        self._recent_folder_warning_time = 0.0

        self._player: QMediaPlayer | None = None
        self._audio_output: QAudioOutput | None = None
        self._broadcast_player: QMediaPlayer | None = None
        self._broadcast_audio_output: QAudioOutput | None = None
        self._broadcast_route_conflicts_main_output = False
        self._is_muted = False
        self._slider_pressed = False
        self._playback_mode = "off"
        self._continuous_queue: list[int] = []
        self._continuous_queue_position = -1
        # True while `_continuous_queue` holds a remote user's ordered "My Queue" send
        # (as opposed to the desktop's own filtered-list continuous playback).
        self._continuous_queue_is_remote = False
        # Snapshot of a remote queue a local operator interrupted, so the webapp can
        # offer to resume it: {"queue_indices": list[int], "position": int}.
        self._interrupted_remote_queue: dict[str, Any] | None = None
        self._current_playing_name = ""
        self._current_playing_path = ""
        self._playlist_active = False
        self._playlist_loop_enabled = False
        self._playlist_saved_playback_mode: str | None = None
        self._current_clip_start_ms = 0
        self._current_clip_stop_ms = -1
        self._clip_start_seek_pending = False
        self._clip_seek_muted_temporarily = False
        self._clip_seek_ramp_token = 0
        self._clip_boundary_handling = False
        self._main_playback_engine: _SamplePadAudioEngine | None = (
            _SamplePadAudioEngine(name="main") if _sp_engine_mod.is_available() else None
        )
        self._main_playback_pad_index = 10001
        self._main_playback_state = "stopped"
        self._main_playback_duration_ms = 0
        self._main_playback_paused_position_ms = 0
        # FIFO of remote play requests deferred while a main jingle is already playing;
        # drained only on natural end-of-clip, never on a manual local Stop/override.
        self._pending_remote_play_queue: list[dict[str, Any]] = []
        self._remote_queue_id_counter = 0
        self._main_playback_timer = QTimer(self)
        self._main_playback_timer.setInterval(30)
        self._main_playback_timer.timeout.connect(self._on_main_playback_timer)
        self._titlebar_reassert_timer = QTimer(self)
        self._titlebar_reassert_timer.setInterval(150)
        self._titlebar_reassert_timer.timeout.connect(self._on_titlebar_reassert_tick)
        self._titlebar_reassert_remaining = 0
        self._is_preview_mode = False
        self._loop_breath_effect: QGraphicsOpacityEffect | None = None
        self._loop_breath_anim: QPropertyAnimation | None = None
        self._play_stop_breath_effect: QGraphicsOpacityEffect | None = None
        self._play_stop_breath_anim: QPropertyAnimation | None = None
        self._stop_btn_breath_effect: QGraphicsOpacityEffect | None = None
        self._stop_btn_breath_anim: QPropertyAnimation | None = None
        self._mode_live_breath_effect: QGraphicsOpacityEffect | None = None
        self._mode_live_breath_anim: QPropertyAnimation | None = None

        if _has_qt_multimedia:
            self._player = QMediaPlayer(self)
            self._audio_output = QAudioOutput(self)
            self._player.setAudioOutput(self._audio_output)
            self._broadcast_player = QMediaPlayer(self)
            self._broadcast_audio_output = QAudioOutput(self)
            self._broadcast_player.setAudioOutput(self._broadcast_audio_output)
            self._apply_output_device()

        central = QWidget(self)
        self._central_widget = central
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            style_hints = app.styleHints()
            color_scheme_changed = getattr(style_hints, "colorSchemeChanged", None)
            if color_scheme_changed is not None:
                try:
                    color_scheme_changed.connect(self._on_system_color_scheme_changed)
                except Exception:
                    pass
            self._appearance_effective_mode = _apply_app_appearance_mode(app, self._appearance_mode)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        filter_row = QHBoxLayout()

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search by jingle name, tags, or path")
        self._search_edit.textChanged.connect(self._apply_filters)
        filter_row.addWidget(self._search_edit, 2)

        self._search_scope_combo = QComboBox()
        self._search_scope_combo.addItem("Name + Path + Tag", "all")
        self._search_scope_combo.addItem("Name Only", "name")
        self._search_scope_combo.addItem("Tag Only", "tag")
        self._search_scope_combo.addItem("Path Only", "path")
        self._search_scope_combo.currentIndexChanged.connect(self._on_search_scope_changed)
        self._search_scope_combo.setToolTip("Choose what fields the search box matches.")
        filter_row.addWidget(self._search_scope_combo, 0)

        self._category_filter_edit = QLineEdit()
        self._category_filter_edit.setPlaceholderText("Filter categories (comma-separated)")
        self._category_filter_edit.textChanged.connect(self._apply_filters)
        filter_row.addWidget(self._category_filter_edit, 1)

        self._category_filter_mode = QComboBox()
        self._category_filter_mode.addItem("Match Any", "any")
        self._category_filter_mode.addItem("Match All", "all")
        self._category_filter_mode.setCurrentIndex(1)
        self._category_filter_mode.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self._category_filter_mode, 0)

        root.addLayout(filter_row)

        chips_row = QHBoxLayout()
        chips_label = QLabel("Active Filters")
        chips_label.setFixedWidth(100)
        chips_row.addWidget(chips_label)

        self._chips_scroll = QScrollArea()
        self._chips_scroll.setWidgetResizable(True)
        self._chips_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._chips_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._chips_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._chips_scroll.setMinimumHeight(34)
        self._chips_scroll.setMaximumHeight(40)

        self._chips_container = QWidget()
        self._chips_layout = QHBoxLayout(self._chips_container)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(6)
        self._chips_layout.addStretch()
        self._chips_scroll.setWidget(self._chips_container)

        chips_row.addWidget(self._chips_scroll, 1)

        self._clear_filters_btn = QPushButton("Clear All")
        self._clear_filters_btn.setToolTip("Remove all active category filter tags")
        self._clear_filters_btn.clicked.connect(self._clear_all_filter_tags)
        self._clear_filters_btn.setEnabled(False)
        chips_row.addWidget(self._clear_filters_btn)

        root.addLayout(chips_row)

        bulk_grid = QGridLayout()
        bulk_grid.addWidget(QLabel("Set Categories"), 0, 0)
        self._bulk_category_edit = QLineEdit()
        self._bulk_category_edit.setPlaceholderText("Comma-separated, e.g. Holiday, Radio")
        bulk_grid.addWidget(self._bulk_category_edit, 0, 1)

        self._bulk_mode_combo = QComboBox()
        self._bulk_mode_combo.addItem("Replace tags", "replace")
        self._bulk_mode_combo.addItem("Append tags", "append")
        self._bulk_mode_combo.addItem("Remove tags", "remove")
        self._bulk_mode_combo.setCurrentIndex(1)
        self._bulk_mode_combo.setToolTip("Choose how bulk tags are applied to selected rows.")
        bulk_grid.addWidget(self._bulk_mode_combo, 0, 2)

        self._update_from_folders_selected_btn = QPushButton("From Folders (Selected)")
        self._update_from_folders_selected_btn.setToolTip(
            "Update selected rows from folder titles using preserve/overwrite mode."
        )
        self._update_from_folders_selected_btn.clicked.connect(
            self._on_update_selected_from_folders_clicked
        )
        bulk_grid.addWidget(self._update_from_folders_selected_btn, 0, 3)

        self._apply_selected_btn = QPushButton("Apply To Selected")
        self._apply_selected_btn.clicked.connect(self._on_apply_bulk_to_selected)
        bulk_grid.addWidget(self._apply_selected_btn, 0, 4)

        root.addLayout(bulk_grid)

        self._table = DeselectableTableWidget(0, 3)
        self._table.set_preserve_selection_callback(self._should_preserve_selected_row)
        self._table.setHorizontalHeaderLabels(["Jingle", "Categories", "Folder"])
        self._table.itemChanged.connect(self._on_table_item_changed)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._table.setDragEnabled(True)
        self._table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self._table.setDefaultDropAction(Qt.DropAction.CopyAction)
        self._table.set_drag_payload_callback(
            self.selected_playlist_candidates,
            PLAYLIST_DRAG_MIME_TYPE,
        )
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu_requested)
        header = self._table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(False)
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)

        self._table.setColumnWidth(1, 120)
        self._table.setColumnWidth(2, 160)
        self._table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self._table.itemDoubleClicked.connect(self._on_table_item_double_clicked)
        root.addWidget(self._table, 1)

        playback_row = QHBoxLayout()
        self._play_btn = QPushButton("Play Selected")
        self._play_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._play_btn.clicked.connect(self._on_play_clicked)
        playback_row.addWidget(self._play_btn)
        self._set_play_button_state("stopped")

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._stop_btn.setStyleSheet(
            "QPushButton { background-color: #c62828; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #d32f2f; }"
        )
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        playback_row.addWidget(self._stop_btn)

        self._mute_btn = QPushButton("Mute")
        self._mute_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._mute_btn.clicked.connect(self._on_mute_clicked)
        playback_row.addWidget(self._mute_btn)
        self._refresh_mute_button_state()

        self._loop_btn = QPushButton("Loop Off")
        self._loop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._loop_btn.clicked.connect(self._on_loop_clicked)
        playback_row.addWidget(self._loop_btn)
        self._refresh_playback_mode_button()

        self._mode_btn = QPushButton("Mode: Live")
        self._mode_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._mode_btn.setCheckable(True)
        self._mode_btn.toggled.connect(self._on_mode_toggled)
        playback_row.addWidget(self._mode_btn)
        self._refresh_mode_toggle_state(notify_if_disabled=False)

        self._position_slider = QSlider(Qt.Orientation.Horizontal)
        self._position_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._position_slider.setRange(0, 0)
        self._position_slider.sliderPressed.connect(self._on_slider_pressed)
        self._position_slider.sliderReleased.connect(self._on_slider_released)
        playback_row.addWidget(self._position_slider, 1)

        self._time_label = QLabel("00:00 / 00:00")
        self._time_label.setFixedWidth(110)
        playback_row.addWidget(self._time_label)

        self._volume_mode_label = QLabel("Live Vol")
        self._volume_mode_label.setFixedWidth(70)
        playback_row.addWidget(self._volume_mode_label)

        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setPageStep(5)
        self._volume_slider.setFixedWidth(140)
        self._volume_slider.valueChanged.connect(self._on_volume_slider_changed)
        playback_row.addWidget(self._volume_slider)

        self._volume_value_label = QLabel("100%")
        self._volume_value_label.setFixedWidth(44)
        playback_row.addWidget(self._volume_value_label)

        # --- Sample Pads Feature ---
        self._sample_pads_window = None
        self._sample_pads_btn = QPushButton("Show Sample Pads")
        self._sample_pads_btn.setCheckable(True)
        self._sample_pads_btn.setToolTip("Show or hide the Sample Pads window")
        self._sample_pads_btn.clicked.connect(self._on_sample_pads_btn_clicked)
        playback_row.addWidget(self._sample_pads_btn)

        self._refresh_volume_controls()
        root.addLayout(playback_row)

        self._status = QStatusBar(self)
        self.setStatusBar(self._status)
        self._apply_output_device()
        self._apply_mixer_input_device(notify_errors=False)

        self._library_watcher = QFileSystemWatcher(self)
        self._library_watcher.directoryChanged.connect(self._on_library_watch_path_changed)
        self._library_watcher.fileChanged.connect(self._on_library_watch_path_changed)
        self._watch_rescan_timer = QTimer(self)
        self._watch_rescan_timer.setSingleShot(True)
        self._watch_rescan_timer.timeout.connect(self._on_library_watch_rescan_timeout)

        self._build_menu()
        self._connect_player_signals()
        self._on_search_scope_changed()
        self._refresh_filter_chips([])

        self._sample_pad_hotkey_requested.connect(self._on_sample_pad_hotkey_requested)
        self._sample_pad_release_requested.connect(self._on_sample_pad_hotkey_released)
        self._sample_pad_board_switch_requested.connect(self._on_sample_pad_board_switch_requested)
        self._cache_backup_finished.connect(self._on_cache_backup_finished)
        if self._sample_pad_global_hotkeys_enabled:
            self._set_sample_pad_global_hotkeys(True)

        self._init_remote_server()

        # Defer the initial scan so the window can render immediately.
        QTimer.singleShot(0, self._maybe_run_first_time_setup)

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:
        if watched is self and event is not None and event.type() == QEvent.Type.WindowActivate:
            self._refresh_system_appearance_fallback()
        if (
            isinstance(watched, QWidget)
            and event is not None
            and event.type() == QEvent.Type.MouseButtonPress
            and isinstance(event, QMouseEvent)
            and event.button() == Qt.MouseButton.LeftButton
        ):
            widget = watched
            preserve_selection_widgets = (
                self._table,
                self._play_btn,
                self._stop_btn,
                self._mute_btn,
                self._loop_btn,
                self._mode_btn,
                self._bulk_category_edit,
                self._bulk_mode_combo,
                self._apply_selected_btn,
                self._update_from_folders_selected_btn,
            )
            preserve_selection = any(
                widget is candidate or candidate.isAncestorOf(widget)
                for candidate in preserve_selection_widgets
            )
            if widget.inherits("QMenuBar") or widget.inherits("QMenu"):
                preserve_selection = True
            if widget.window() is self and not preserve_selection:
                if self._should_preserve_selected_row():
                    return super().eventFilter(watched, event)
                self._table.clearSelection()
                self._table.setCurrentCell(-1, -1)
                self._refresh_status_summary()
        if event is not None and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if self._handle_media_key_event(event):
                return True
        return super().eventFilter(watched, event)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        _apply_windows_titlebar_theme(
            self,
            self._appearance_effective_mode == APPEARANCE_MODE_DARK,
        )

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is None:
            super().keyPressEvent(event)
            return

        # Shift+1-5 or Ctrl+Shift+1-5 switches the active sample pad board,
        # depending on the current board hotkey modifier setting.
        if not event.isAutoRepeat():
            mods = event.modifiers()
            ctrl_held = bool(mods & Qt.KeyboardModifier.ControlModifier)
            shift_held = bool(mods & Qt.KeyboardModifier.ShiftModifier)
            board_combo_active = (
                shift_held and ctrl_held
                if self._sample_pad_board_switch_requires_ctrl
                else shift_held and not ctrl_held
            )
            if board_combo_active:
                key_to_board = {
                    int(Qt.Key.Key_1): 0,
                    int(Qt.Key.Key_2): 1,
                    int(Qt.Key.Key_3): 2,
                    int(Qt.Key.Key_4): 3,
                    int(Qt.Key.Key_5): 4,
                }
                board_index = key_to_board.get(int(event.key()))
                if board_index is None:
                    board_index = {
                        '!': 0,
                        '@': 1,
                        '#': 2,
                        '$': 3,
                        '%': 4,
                    }.get(event.text())
                if board_index is not None:
                    self._switch_sample_pad_board(board_index)
                    event.accept()
                    return
                # The configured board-switch combo is reserved for board switching only.
                super().keyPressEvent(event)
                return

        if self._handle_sample_pad_key_event(event):
            event.accept()
            return

        # Check if playback is currently active (playing or paused)
        is_playback_active = self._is_playback_active()

        # Handle arrow keys for skipping during active playback
        if is_playback_active:
            if self._event_matches_shortcut(event, "skip_previous"):
                self._skip_to_previous()
                event.accept()
                return
            elif self._event_matches_shortcut(event, "skip_next"):
                self._skip_to_next()
                event.accept()
                return
        else:
            # Handle arrow keys for table navigation when playback is not active
            if self._event_matches_shortcut(event, "select_up"):
                self._move_selection_up()
                event.accept()
                return
            elif self._event_matches_shortcut(event, "select_down"):
                self._move_selection_down()
                event.accept()
                return

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent | None) -> None:
        if event is None:
            super().keyReleaseEvent(event)
            return
        # Auto-repeat releases are not real releases; ignore them.
        if event.isAutoRepeat():
            super().keyReleaseEvent(event)
            return
        # When a global listener is active it handles releases; skip in-app handling.
        if self._sample_pad_hotkey_backend in ("pynput", "windows-native"):
            super().keyReleaseEvent(event)
            return
        modifiers = event.modifiers()
        ctrl_held = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift_held = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)

        board_combo_active = (
            shift_held and ctrl_held
            if self._sample_pad_board_switch_requires_ctrl
            else shift_held and not ctrl_held
        )
        if board_combo_active:
            super().keyReleaseEvent(event)
            return

        # Ctrl+1-0 → pads 10-19
        if ctrl_held:
            ctrl_key_to_pad = {
                int(Qt.Key.Key_1): 10,
                int(Qt.Key.Key_2): 11,
                int(Qt.Key.Key_3): 12,
                int(Qt.Key.Key_4): 13,
                int(Qt.Key.Key_5): 14,
                int(Qt.Key.Key_6): 15,
                int(Qt.Key.Key_7): 16,
                int(Qt.Key.Key_8): 17,
                int(Qt.Key.Key_9): 18,
                int(Qt.Key.Key_0): 19,
            }
            pad_index = ctrl_key_to_pad.get(int(event.key()))
            if pad_index is not None:
                self._release_sample_pad(pad_index)
            super().keyReleaseEvent(event)
            return

        key_to_pad = {
            int(Qt.Key.Key_1): 0,
            int(Qt.Key.Key_2): 1,
            int(Qt.Key.Key_3): 2,
            int(Qt.Key.Key_4): 3,
            int(Qt.Key.Key_5): 4,
            int(Qt.Key.Key_6): 5,
            int(Qt.Key.Key_7): 6,
            int(Qt.Key.Key_8): 7,
            int(Qt.Key.Key_9): 8,
            int(Qt.Key.Key_0): 9,
        }
        pad_index = key_to_pad.get(int(event.key()))
        if pad_index is not None:
            self._release_sample_pad(pad_index)
        super().keyReleaseEvent(event)

    def closeEvent(self, event: QEvent | None) -> None:
        self._autosave_sample_pad_layout()
        if self._sample_pads_window is not None:
            try:
                self._sample_pads_window.close()
            except Exception:
                pass
        self._stop_sample_pad_global_hotkeys()
        if hasattr(self, "_remote_manager"):
            self._remote_manager.stop()
        self._sp_engine.close()
        self._sp_monitor_engine.close()
        if self._main_playback_engine is not None:
            self._main_playback_engine.close()
        super().closeEvent(event)

    def _create_settings_store(self) -> QSettings:
        settings_path = self._app_data_dir / "settings.ini"
        settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
        settings.setFallbacksEnabled(False)
        return settings

    def _on_open_options(self) -> None:
        dialog = OptionsDialog(
            self._output_device,
            self._preview_output_device,
            self._broadcast_output_device,
            self._mixer_enabled,
            self._microphone_input_device,
            self._recording_input_device,
            self._microphone_gain_percent,
            self._live_volume_percent,
            self._preview_volume_percent,
            self._recent_window_days,
            self._sample_pad_blocksize,
            self._sample_pad_streaming_min_seconds,
            self._samples_dir,
            self._server_enabled,
            self._server_address,
            self._server_device_token,
            self._cache_backup_reminder_days,
            self,
        )
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return

        (
            self._output_device,
            self._preview_output_device,
            self._broadcast_output_device,
        ) = dialog.selected_devices()
        (
            self._mixer_enabled,
            self._microphone_input_device,
            self._microphone_gain_percent,
        ) = dialog.selected_mixer_config()
        self._recording_input_device = dialog.selected_recording_device() or -1
        self._live_volume_percent, self._preview_volume_percent = dialog.selected_volumes()
        selected_recent_window_days = dialog.selected_recent_window_days()
        self._sample_pad_blocksize = dialog.selected_sample_pad_blocksize()
        self._sample_pad_streaming_min_seconds = (
            dialog.selected_sample_pad_streaming_min_seconds()
        )
        self._settings.setValue("options/outputDevice", self._output_device)
        self._settings.setValue("options/previewOutputDevice", self._preview_output_device)
        self._settings.setValue("options/broadcastOutputDevice", self._broadcast_output_device)
        self._settings.setValue(
            "options/mixerEnabled", "true" if self._mixer_enabled else "false"
        )
        self._settings.setValue(
            "options/microphoneInputDevice", self._microphone_input_device
        )
        self._settings.setValue("options/recordingInputDevice", self._recording_input_device)
        self._settings.setValue(
            "options/microphoneGainPercent", self._microphone_gain_percent
        )
        self._settings.setValue("options/recentWindowDays", selected_recent_window_days)
        self._settings.setValue("options/samplePadBlocksize", self._sample_pad_blocksize)
        self._settings.setValue(
            "options/samplePadStreamingMinSeconds",
            self._sample_pad_streaming_min_seconds,
        )

        new_server_enabled = dialog.selected_server_enabled()
        new_server_address = dialog.selected_server_address()
        new_server_device_token = dialog.selected_server_device_token()
        new_cache_backup_reminder_days = dialog.selected_cache_backup_reminder_days()
        connection_settings_changed = (
            new_server_address != self._server_address or new_server_device_token != self._server_device_token
        )
        self._server_enabled = new_server_enabled
        self._server_address = new_server_address
        self._server_device_token = new_server_device_token
        self._cache_backup_reminder_days = new_cache_backup_reminder_days
        self._settings.setValue("server/enabled", "true" if self._server_enabled else "false")
        self._settings.setValue("server/address", self._server_address)
        self._settings.setValue("server/deviceToken", self._server_device_token)
        self._settings.setValue("server/cacheBackupReminderDays", self._cache_backup_reminder_days)
        if hasattr(self, "_remote_manager"):
            if self._remote_manager.is_running() and connection_settings_changed:
                self._remote_manager.restart()
            elif self._server_enabled and not self._remote_manager.is_running():
                self._start_remote_server(announce=False)
            elif not self._server_enabled and self._remote_manager.is_running():
                self._remote_manager.stop()

        if selected_recent_window_days != self._recent_window_days:
            self._recent_window_days = selected_recent_window_days
            self._refresh_recent_runtime_from_store()
        self._sp_engine.set_streaming_min_seconds(self._sample_pad_streaming_min_seconds)
        self._sp_engine.set_mixer_enabled(self._mixer_enabled)
        self._sp_engine.set_input_gain(self._microphone_gain_percent / 100.0)
        self._apply_mixer_input_device(notify_errors=True)
        self._save_volume_settings()
        self._refresh_mode_toggle_state(notify_if_disabled=True)
        self._refresh_volume_controls()
        self._apply_output_device()

        new_dir = dialog.selected_folder()
        if new_dir != self._samples_dir:
            self._samples_dir = new_dir
            self._save_samples_dir()
            self._rescan_library()

        if not self._can_use_preview_mode():
            QMessageBox.information(
                self,
                "Preview/Live Disabled",
                "Live and Preview devices are currently the same.\n\n"
                "Preview/Live switching is disabled until the Preview device is set to a different output.",
            )
        route_warnings = self._broadcast_route_warnings()
        if route_warnings:
            QMessageBox.information(
                self,
                "Broadcast Routing Notes",
                "\n\n".join(route_warnings),
            )
        self._status.showMessage("Options saved.")


def main() -> None:
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
