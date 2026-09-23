#!/usr/bin/env python3
"""Jingle browser GUI with category filters and playback device selection."""

from __future__ import annotations

import sys
import json
import math
import time
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
    QUrl,
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

MEDIA_PLAY_KEYS = tuple(
    key
    for key in (
        getattr(Qt.Key, "Key_MediaPlay", None),
        getattr(Qt.Key, "Key_AudioPlay", None),
    )
    if key is not None
)
MEDIA_PAUSE_KEYS = tuple(
    key
    for key in (
        getattr(Qt.Key, "Key_MediaPause", None),
        getattr(Qt.Key, "Key_AudioPause", None),
    )
    if key is not None
)
MEDIA_TOGGLE_PLAYBACK_KEYS = tuple(
    key
    for key in (
        getattr(Qt.Key, "Key_MediaTogglePlayPause", None),
        getattr(Qt.Key, "Key_AudioPlay", None),
    )
    if key is not None
)
MEDIA_NEXT_KEYS = tuple(
    key
    for key in (
        getattr(Qt.Key, "Key_MediaNext", None),
        getattr(Qt.Key, "Key_AudioForward", None),
    )
    if key is not None
)
MEDIA_PREVIOUS_KEYS = tuple(
    key
    for key in (
        getattr(Qt.Key, "Key_MediaPrevious", None),
        getattr(Qt.Key, "Key_AudioRewind", None),
    )
    if key is not None
)


class MainWindow(
    MainWindowAppearanceMixin,
    MainWindowAudioDiagnosticsMixin,
    MainWindowButtonFxMixin,
    MainWindowCacheBackupMixin,
    MainWindowHotkeysMixin,
    MainWindowMenuMixin,
    MainWindowMixerMixin,
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

    def _ensure_playlists_window(self) -> PlaylistsWindow:
        if self._playlists_window is None:
            self._playlists_window = PlaylistsWindow(main_window=self, parent=self)
            self._playlists_window.finished.connect(self._on_playlists_window_closed)
        return self._playlists_window

    def _on_playlists_window_closed(self) -> None:
        self.stop_playlist_playback()

    def playlist_last_playlist_path(self) -> str:
        return self._playlists_last_playlist_path

    def set_playlist_last_playlist_path(self, path_text: str) -> None:
        self._playlists_last_playlist_path = str(path_text).strip()
        self._settings.setValue("playlists/lastPlaylistPath", self._playlists_last_playlist_path)

    def playlist_autosave_path(self) -> str:
        return self._playlists_autosave_path

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

    def _should_preserve_selected_row(self) -> bool:
        table = getattr(self, "_table", None)
        is_engine_playing = self._using_main_playback_engine() and self._main_playback_state == "playing"
        is_qt_playing = (
            self._player is not None
            and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        return (
            (is_engine_playing or is_qt_playing)
            and table is not None
            and bool(table.selectedItems())
        )

    def _is_playback_active(self) -> bool:
        if self._using_main_playback_engine():
            return self._main_playback_state in ("playing", "paused")
        return (
            self._player is not None
            and self._player.playbackState()
            in (
                QMediaPlayer.PlaybackState.PlayingState,
                QMediaPlayer.PlaybackState.PausedState,
            )
        )

    def _handle_media_key_event(self, event: QKeyEvent) -> bool:
        if not self._using_main_playback_engine() and self._player is None:
            return False

        key = event.key()
        if key in MEDIA_TOGGLE_PLAYBACK_KEYS:
            self._toggle_play_pause()
            event.accept()
            return True
        if key in MEDIA_PLAY_KEYS:
            self._resume_or_start_playback()
            event.accept()
            return True
        if key in MEDIA_PAUSE_KEYS:
            self._pause_playback()
            event.accept()
            return True
        if key in MEDIA_NEXT_KEYS and self._is_playback_active():
            self._skip_to_next()
            event.accept()
            return True
        if key in MEDIA_PREVIOUS_KEYS and self._is_playback_active():
            self._skip_to_previous()
            event.accept()
            return True
        return False

    def _toggle_play_pause(self) -> None:
        if self._using_main_playback_engine():
            if self._main_playback_state == "playing":
                self._pause_playback()
                return
            self._resume_or_start_playback()
            return
        if self._player is None:
            return
        state = self._player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self._sync_broadcast_player_to_main()
            self._status.showMessage("Playback paused.")
            return
        if state == QMediaPlayer.PlaybackState.PausedState:
            self._player.play()
            self._sync_broadcast_player_to_main()
            self._status.showMessage("Playback resumed.")
            return
        self._on_play_clicked()

    def _resume_or_start_playback(self) -> None:
        if self._using_main_playback_engine():
            if self._main_playback_state == "paused":
                if self._start_main_engine_clip(self._main_playback_paused_position_ms):
                    self._status.showMessage("Playback resumed.")
                return
            if self._main_playback_state != "playing":
                self._on_play_clicked()
            return
        if self._player is None:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
            self._player.play()
            self._sync_broadcast_player_to_main()
            self._status.showMessage("Playback resumed.")
            return
        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._on_play_clicked()

    def _pause_playback(self) -> None:
        if self._using_main_playback_engine():
            if self._main_playback_state == "playing":
                if self._main_playback_engine is not None:
                    info = self._main_playback_engine.pad_playback_info(self._main_playback_pad_index)
                    position_offset_ms = int(round(float(info.get("position_seconds", 0.0)) * 1000.0))
                    self._main_playback_paused_position_ms = self._current_clip_start_ms + position_offset_ms
                    self._main_playback_engine.stop(self._main_playback_pad_index)
                self._main_playback_state = "paused"
                self._main_playback_timer.stop()
                self._set_play_button_state("paused")
                self._set_stop_button_breathing(True)
                self._status.showMessage("Playback paused.")
            return
        if self._player is None:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self._sync_broadcast_player_to_main()
            self._status.showMessage("Playback paused.")

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

    def _skip_to_previous(self) -> None:
        """Skip to the previous jingle in the visible list."""
        if not self._visible_indices:
            self._status.showMessage("No jingles available to skip.")
            return

        current_index = self._selected_record_index()
        if current_index is None:
            # No selection, skip to last visible jingle
            next_visible_index = self._visible_indices[-1]
        else:
            # Find current in visible list and go to previous
            try:
                visible_pos = self._visible_indices.index(current_index)
                if visible_pos > 0:
                    next_visible_index = self._visible_indices[visible_pos - 1]
                else:
                    # At the beginning, wrap to the end
                    next_visible_index = self._visible_indices[-1]
            except ValueError:
                # Current index not in visible list, go to last visible
                next_visible_index = self._visible_indices[-1]

        record = self._records[next_visible_index]
        if not record.path.exists():
            self._status.showMessage("Selected file no longer exists.")
            return

        # Select the row in the table
        visible_row = self._visible_row_for_record_index(next_visible_index)
        if visible_row >= 0:
            self._table.selectRow(visible_row)

        # In continuous mode, continue from the manually selected jingle.
        if self._playback_mode == "continuous":
            try:
                self._continuous_queue_position = self._continuous_queue.index(next_visible_index)
            except ValueError:
                pass

        self._play_record(next_visible_index)
        self._status.showMessage(f"Skipped to: {record.name}")

    def _skip_to_next(self) -> None:
        """Skip to the next jingle in the visible list."""
        if not self._visible_indices:
            self._status.showMessage("No jingles available to skip.")
            return

        current_index = self._selected_record_index()
        if current_index is None:
            # No selection, skip to first visible jingle
            next_visible_index = self._visible_indices[0]
        else:
            # Find current in visible list and go to next
            try:
                visible_pos = self._visible_indices.index(current_index)
                if visible_pos < len(self._visible_indices) - 1:
                    next_visible_index = self._visible_indices[visible_pos + 1]
                else:
                    # At the end, wrap to the beginning
                    next_visible_index = self._visible_indices[0]
            except ValueError:
                # Current index not in visible list, go to first visible
                next_visible_index = self._visible_indices[0]

        record = self._records[next_visible_index]
        if not record.path.exists():
            self._status.showMessage("Selected file no longer exists.")
            return

        # Select the row in the table
        visible_row = self._visible_row_for_record_index(next_visible_index)
        if visible_row >= 0:
            self._table.selectRow(visible_row)

        # In continuous mode, continue from the manually selected jingle.
        if self._playback_mode == "continuous":
            try:
                self._continuous_queue_position = self._continuous_queue.index(next_visible_index)
            except ValueError:
                pass

        self._play_record(next_visible_index)
        self._status.showMessage(f"Skipped to: {record.name}")

    def _connect_player_signals(self) -> None:
        if self._using_main_playback_engine():
            self._set_play_button_state("stopped")
            self._set_stop_button_breathing(False)
            return
        if self._player is None:
            self._play_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            self._mute_btn.setEnabled(False)
            self._loop_btn.setEnabled(False)
            self._mode_btn.setEnabled(False)
            self._volume_slider.setEnabled(False)
            self._set_loop_breathing(False)
            self._status.showMessage("PyQt6 multimedia is not available. Playback is disabled.")
            return

        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._apply_player_loop_mode()

    def _create_settings_store(self) -> QSettings:
        settings_path = self._app_data_dir / "settings.ini"
        settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
        settings.setFallbacksEnabled(False)
        return settings

    def _selected_record_index(self) -> int | None:
        selected = self._table.selectedRanges()
        if not selected:
            return None
        row = selected[0].topRow()
        if row < 0 or row >= len(self._visible_indices):
            return None
        return self._visible_indices[row]

    def _visible_row_for_record_index(self, record_index: int) -> int:
        for row, visible_record_index in enumerate(self._visible_indices):
            if visible_record_index == record_index:
                return row
        return -1

    def _select_record_row(self, record_index: int) -> None:
        row = self._visible_row_for_record_index(record_index)
        if row < 0:
            return
        self._table.selectRow(row)
        self._table.setCurrentCell(row, 0)

    def _reset_continuous_queue(self) -> None:
        self._continuous_queue = []
        self._continuous_queue_position = -1
        self._continuous_queue_is_remote = False

    def _interrupt_active_remote_queue_for_local_play(self) -> None:
        """If a remote 'My Queue' send is currently playing, snapshot it for later resume
        and drop to Loop Off so a local operator's next Play/double-click plays alone."""
        if not self._continuous_queue_is_remote:
            return
        if self._continuous_queue and 0 <= self._continuous_queue_position < len(self._continuous_queue):
            self._interrupted_remote_queue = {
                "queue_indices": list(self._continuous_queue),
                "position": self._continuous_queue_position,
            }
        self._continuous_queue_is_remote = False
        self._playback_mode = "off"
        self._refresh_playback_mode_button()
        self._apply_player_loop_mode()
        self._publish_remote_state()

    def _clear_playlist_state(self) -> None:
        was_active = self._playlist_active
        self._playlist_active = False
        self._playlist_loop_enabled = False
        if was_active and self._playlist_saved_playback_mode is not None:
            self._playback_mode = self._playlist_saved_playback_mode
            self._playlist_saved_playback_mode = None
            self._refresh_playback_mode_button()
            self._apply_player_loop_mode()

    def _reset_clip_playback_window(self) -> None:
        self._current_clip_start_ms = 0
        self._current_clip_stop_ms = -1
        self._clip_start_seek_pending = False
        self._clip_seek_muted_temporarily = False
        self._clip_seek_ramp_token += 1
        self._clip_boundary_handling = False

    def _clear_clip_seek_temporary_silence(self, *, ramp_up: bool) -> None:
        if not self._clip_seek_muted_temporarily:
            return

        self._clip_seek_muted_temporarily = False
        self._clip_seek_ramp_token += 1

        if self._audio_output is not None:
            self._audio_output.setMuted(self._is_muted)
        if self._broadcast_audio_output is not None:
            self._broadcast_audio_output.setMuted(self._is_muted)

        target_volume = self._active_volume_percent() / 100.0
        if self._is_muted or not ramp_up or target_volume <= 0.0:
            self._apply_active_volume()
            return

        ramp_token = self._clip_seek_ramp_token
        ramp_multipliers = (0.35, 0.7, 1.0)
        for step_index, multiplier in enumerate(ramp_multipliers, start=1):
            delay_ms = 4 * step_index

            def _apply_step(mult: float = multiplier, token: int = ramp_token) -> None:
                if token != self._clip_seek_ramp_token:
                    return
                if self._clip_seek_muted_temporarily or self._is_muted:
                    return
                stepped_volume = max(0.0, min(1.0, target_volume * mult))
                if self._audio_output is not None:
                    self._audio_output.setVolume(stepped_volume)
                if self._broadcast_audio_output is not None:
                    self._broadcast_audio_output.setVolume(stepped_volume)

            QTimer.singleShot(delay_ms, _apply_step)

    def _apply_short_start_ramp(self) -> None:
        if self._is_muted or self._clip_seek_muted_temporarily:
            return
        if self._audio_output is None and self._broadcast_audio_output is None:
            return

        target_volume = self._active_volume_percent() / 100.0
        if target_volume <= 0.0:
            return

        self._clip_seek_ramp_token += 1
        ramp_token = self._clip_seek_ramp_token

        if self._audio_output is not None:
            self._audio_output.setVolume(0.0)
        if self._broadcast_audio_output is not None:
            self._broadcast_audio_output.setVolume(0.0)

        ramp_multipliers = (0.4, 0.75, 1.0)
        for step_index, multiplier in enumerate(ramp_multipliers):
            delay_ms = 4 * step_index

            def _apply_step(mult: float = multiplier, token: int = ramp_token) -> None:
                if token != self._clip_seek_ramp_token:
                    return
                if self._clip_seek_muted_temporarily or self._is_muted:
                    return
                stepped_volume = max(0.0, min(1.0, target_volume * mult))
                if self._audio_output is not None:
                    self._audio_output.setVolume(stepped_volume)
                if self._broadcast_audio_output is not None:
                    self._broadcast_audio_output.setVolume(stepped_volume)

            QTimer.singleShot(delay_ms, _apply_step)

    def _prepare_clip_start_seek(self, temporary_mute_for_seek: bool) -> int:
        start_ms = max(0, int(self._current_clip_start_ms))
        self._clip_boundary_handling = False
        self._clip_start_seek_pending = start_ms > 0
        self._clip_seek_ramp_token += 1
        if self._audio_output is not None:
            # Also pre-mute when start_ms == 0: rapid sample retriggers can still
            # produce audible discontinuities when the media source is replaced.
            if temporary_mute_for_seek and not self._is_muted:
                # Ensure _on_position_changed clears temporary mute on first
                # position callback even when no explicit seek is needed.
                if not self._clip_start_seek_pending:
                    self._clip_start_seek_pending = True
                self._clip_seek_muted_temporarily = True
                self._audio_output.setMuted(False)
                self._audio_output.setVolume(0.0)
            else:
                self._clip_seek_muted_temporarily = False
                self._audio_output.setMuted(self._is_muted)
                self._audio_output.setVolume(self._active_volume_percent() / 100.0)
        if self._broadcast_audio_output is not None:
            if temporary_mute_for_seek and not self._is_muted:
                self._broadcast_audio_output.setMuted(False)
                self._broadcast_audio_output.setVolume(0.0)
            else:
                self._broadcast_audio_output.setMuted(self._is_muted)
                self._broadcast_audio_output.setVolume(self._active_volume_percent() / 100.0)
        return start_ms

    def _restart_current_clip_from_start(self, temporary_mute_for_seek: bool) -> None:
        if self._player is None:
            return
        start_ms = self._prepare_clip_start_seek(temporary_mute_for_seek)
        self._player.setPosition(start_ms)
        self._player.play()
        self._sync_broadcast_player_to_main()

    def _clip_window_for_record(self, record: JingleRecord) -> tuple[int, int]:
        duration_ms = max(0, int(round(record.duration_seconds * 1000.0)))
        start_ms = max(0, int(round(record.clip_start_seconds * 1000.0)))
        stop_ms = max(0, int(round(record.clip_stop_seconds * 1000.0)))
        near_end_tolerance_ms = 80

        if duration_ms > 0:
            start_ms = min(start_ms, duration_ms)
            stop_ms = min(stop_ms, duration_ms)
            if stop_ms <= start_ms:
                start_ms = 0
                stop_ms = duration_ms

        # For full-file (or effectively full-file) windows, let the backend
        # reach EndOfMedia naturally. Enforcing the stop position manually can
        # cut off very short tails because the media clock reaches the end
        # before buffered audio has fully drained to the output device.
        if duration_ms > 0 and start_ms == 0 and stop_ms >= duration_ms - near_end_tolerance_ms:
            return 0, -1

        return start_ms, stop_ms

    def _using_main_playback_engine(self) -> bool:
        return self._main_playback_engine is not None

    def _apply_main_playback_output_route(self) -> bool:
        if self._main_playback_engine is None:
            return False
        target_device = self._active_output_device().strip()
        try:
            self._main_playback_engine.set_device(
                target_device,
                blocksize=self._sample_pad_blocksize,
            )
            self._main_playback_engine.set_master_gain(self._active_volume_percent() / 100.0)
            return True
        except Exception as exc:
            self._status.showMessage(f"Main playback engine unavailable: {exc}")
            return False

    def _start_main_engine_clip(self, start_position_ms: int) -> bool:
        if self._main_playback_engine is None:
            return False
        clip_start_ms = self._current_clip_start_ms
        clip_stop_ms = self._current_clip_stop_ms
        start_position_ms = max(clip_start_ms, int(start_position_ms))
        if clip_stop_ms > clip_start_ms:
            start_position_ms = min(start_position_ms, clip_stop_ms)

        clip_stop_seconds = 0.0 if clip_stop_ms < 0 else (clip_stop_ms / 1000.0)
        loop_enabled = self._playback_mode == "loop" or self._sample_pad_looping

        # clip_start_seconds is the loop window's start (unaffected by seeking);
        # start_position_seconds is where playback begins, e.g. a seek target.
        self._main_playback_engine.trigger(
            path=self._current_playing_path,
            volume=1.0,
            clip_start_seconds=clip_start_ms / 1000.0,
            clip_stop_seconds=clip_stop_seconds,
            loop=loop_enabled,
            pad_index=self._main_playback_pad_index,
            start_position_seconds=start_position_ms / 1000.0,
        )
        self._main_playback_state = "playing"
        self._main_playback_paused_position_ms = start_position_ms
        self._main_playback_timer.start()
        self._set_play_button_state("playing")
        self._set_stop_button_breathing(True)
        return True

    def _finish_main_playback(self) -> None:
        ended_name = self._current_playing_name
        if self._play_next_continuous_record():
            return
        self._reset_continuous_queue()
        self._reset_clip_playback_window()
        self._sample_pad_looping = False
        self._sample_pad_release_looping = False
        self._sample_pad_native_looping = False
        self._current_sample_pad_index = -1
        self._current_playing_name = ""
        self._current_playing_path = ""
        self._clear_playlist_state()
        self._main_playback_state = "stopped"
        self._main_playback_paused_position_ms = 0
        self._main_playback_timer.stop()
        self._set_play_button_state("stopped")
        self._set_stop_button_breathing(False)
        self._publish_remote_state()
        if ended_name:
            self._status.showMessage(f"Playback finished: {ended_name}")
        else:
            self._status.showMessage("Playback finished.")
        if self._pending_remote_play_queue:
            self._dispatch_next_pending_remote_play()

    def _on_main_playback_timer(self) -> None:
        if self._main_playback_engine is None:
            return
        if self._main_playback_state == "paused":
            return
        if self._main_playback_state != "playing":
            self._main_playback_timer.stop()
            return

        info = self._main_playback_engine.pad_playback_info(self._main_playback_pad_index)
        is_active = bool(info.get("active", False)) or bool(info.get("pending", False))
        if not is_active:
            self._finish_main_playback()
            return

        position_ms = self._current_clip_start_ms + int(round(float(info.get("position_seconds", 0.0)) * 1000.0))
        if self._main_playback_duration_ms <= 0:
            self._main_playback_duration_ms = int(round(float(info.get("duration_seconds", 0.0)) * 1000.0))
        duration_ms = max(0, self._main_playback_duration_ms)
        if not self._slider_pressed:
            self._position_slider.setValue(max(0, position_ms))
        self._update_time_label(position_ms, duration_ms)
        self._publish_remote_state()

    def _seek_main_engine_to(self, position_ms: int) -> None:
        if self._main_playback_engine is None:
            return
        position_ms = max(self._current_clip_start_ms, int(position_ms))
        if self._current_clip_stop_ms > self._current_clip_start_ms:
            position_ms = min(position_ms, self._current_clip_stop_ms)

        if self._main_playback_state == "paused":
            self._main_playback_paused_position_ms = position_ms
            self._position_slider.setValue(max(0, position_ms))
            self._update_time_label(position_ms, self._main_playback_duration_ms)
            return

        if self._main_playback_state != "playing":
            return

        self._main_playback_engine.stop(self._main_playback_pad_index)
        self._start_main_engine_clip(position_ms)

    def _play_record(self, record_index: int) -> bool:
        if record_index < 0 or record_index >= len(self._records):
            return False
        if self._using_main_playback_engine():
            return self._play_record_via_main_engine(record_index)
        if self._player is None:
            return False

        record = self._records[record_index]
        if not record.path.exists():
            return False

        self._apply_output_device()
        clip_start_ms, clip_stop_ms = self._clip_window_for_record(record)
        self._current_clip_start_ms = clip_start_ms
        self._current_clip_stop_ms = clip_stop_ms
        self._apply_player_loop_mode()
        new_url = QUrl.fromLocalFile(str(record.path))
        # If the same file is already loaded and the pipeline is active, seek
        # back to the start instead of calling setSource().  Reloading the same
        # source tears down and rebuilds the audio pipeline, which causes an
        # audible click/pop on low-latency or pro audio interfaces.  A seek is
        # covered by the temporary mute set in _prepare_clip_start_seek below.
        already_loaded = (
            self._player.source() == new_url
            and self._player.playbackState() != QMediaPlayer.PlaybackState.StoppedState
        )
        # Only apply a temporary seek-mute when audio is actually at risk of
        # discontinuity:
        #   - same source being re-triggered: pipeline is live, seek will cause
        #     a glitch without muting
        #   - non-zero clip start: the seek to clip_start_ms must be hidden
        # A fresh setSource() for a new file starting at position 0 needs no
        # mute — muting it just silences the genuine beginning of the clip
        # until the first position callback fires (the "slight muting" bug).
        need_seek_mute = already_loaded or clip_start_ms > 0
        start_ms = self._prepare_clip_start_seek(temporary_mute_for_seek=need_seek_mute)
        if already_loaded:
            self._player.setPosition(start_ms)
            self._player.play()
        else:
            self._player.setSource(new_url)
            self._player.play()
            if start_ms > 0:
                self._player.setPosition(start_ms)
        if not self._clip_seek_muted_temporarily:
            self._apply_short_start_ramp()
        self._sync_broadcast_player_to_main()
        self._current_playing_name = record.path.name
        self._current_playing_path = str(record.path)
        self._select_record_row(record_index)

        mode_text = ""
        if self._playback_mode == "loop":
            mode_text = " (loop)"
        elif self._continuous_queue:
            total = len(self._continuous_queue)
            current = self._continuous_queue_position + 1 if total > 0 else 1
            if self._playback_mode == "continuous":
                mode_text = f" (continuous {current}/{max(total, 1)})"
            else:
                mode_text = f" (queue {current}/{max(total, 1)})"
        self._status.showMessage(f"Playing: {record.path.name}{mode_text}")
        return True

    def _play_record_via_main_engine(self, record_index: int) -> bool:
        if self._main_playback_engine is None:
            return False
        if record_index < 0 or record_index >= len(self._records):
            return False

        record = self._records[record_index]
        if not record.path.exists():
            return False

        if not self._apply_main_playback_output_route():
            return False
        clip_start_ms, clip_stop_ms = self._clip_window_for_record(record)
        self._current_clip_start_ms = clip_start_ms
        self._current_clip_stop_ms = clip_stop_ms
        self._current_playing_name = record.path.name
        self._current_playing_path = str(record.path)
        self._main_playback_duration_ms = max(0, clip_stop_ms if clip_stop_ms >= 0 else int(round(record.duration_seconds * 1000.0)))
        self._main_playback_paused_position_ms = clip_start_ms

        self._main_playback_engine.stop(self._main_playback_pad_index)
        if not self._start_main_engine_clip(clip_start_ms):
            return False

        self._position_slider.setRange(0, max(0, self._main_playback_duration_ms))
        self._position_slider.setValue(max(0, clip_start_ms))
        self._update_time_label(clip_start_ms, self._main_playback_duration_ms)
        self._select_record_row(record_index)

        mode_text = ""
        if self._playback_mode == "loop":
            mode_text = " (loop)"
        elif self._continuous_queue:
            total = len(self._continuous_queue)
            current = self._continuous_queue_position + 1 if total > 0 else 1
            if self._playback_mode == "continuous":
                mode_text = f" (continuous {current}/{max(total, 1)})"
            else:
                mode_text = f" (queue {current}/{max(total, 1)})"
        self._status.showMessage(f"Playing: {record.path.name}{mode_text}")
        return True

    def _start_continuous_playback(self) -> bool:
        selected_record_index = self._selected_record_index()
        if selected_record_index is None:
            self._status.showMessage("Select a jingle first.")
            return False

        start_row = self._visible_row_for_record_index(selected_record_index)
        if start_row < 0:
            return False

        self._continuous_queue = list(self._visible_indices[start_row:])
        if not self._continuous_queue:
            return False

        self._continuous_queue_position = -1
        return self._play_next_continuous_record()

    def _start_selected_queue_playback(self) -> bool:
        selected_indices = self._selected_record_indices()
        if len(selected_indices) < 2:
            return False

        self._continuous_queue = list(selected_indices)
        self._continuous_queue_position = -1
        return self._play_next_continuous_record()

    def _play_next_continuous_record(self, allow_playlist_restart: bool = True) -> bool:
        if not self._using_main_playback_engine() and self._player is None:
            return False

        next_position = self._continuous_queue_position + 1
        while next_position < len(self._continuous_queue):
            record_index = self._continuous_queue[next_position]
            self._continuous_queue_position = next_position
            if self._play_record(record_index):
                return True
            next_position += 1

        if self._playlist_active and self._playlist_loop_enabled and self._continuous_queue and allow_playlist_restart:
            self._continuous_queue_position = -1
            return self._play_next_continuous_record(allow_playlist_restart=False)

        self._reset_continuous_queue()
        self._clear_playlist_state()
        return False

    def _on_play_clicked(self) -> None:
        try:
            self._on_play_clicked_impl()
        finally:
            self._publish_remote_state()

    def _on_play_clicked_impl(self) -> None:
        if self._using_main_playback_engine():
            if self._main_playback_state == "playing":
                if self._main_playback_engine is not None:
                    info = self._main_playback_engine.pad_playback_info(self._main_playback_pad_index)
                    position_offset_ms = int(round(float(info.get("position_seconds", 0.0)) * 1000.0))
                    self._main_playback_paused_position_ms = self._current_clip_start_ms + position_offset_ms
                    self._main_playback_engine.stop(self._main_playback_pad_index)
                self._main_playback_state = "paused"
                self._main_playback_timer.stop()
                self._set_play_button_state("paused")
                self._set_stop_button_breathing(True)
                self._status.showMessage("Playback paused.")
                return

            if self._main_playback_state == "paused":
                if self._start_main_engine_clip(self._main_playback_paused_position_ms):
                    self._status.showMessage("Playback resumed.")
                return

            self._clear_playlist_state()

            selected_record_index = self._selected_record_index()
            if selected_record_index is None:
                self._status.showMessage("Select a jingle first.")
                return

            if self._playback_mode == "continuous":
                if self._continuous_queue_is_remote:
                    self._interrupt_active_remote_queue_for_local_play()
                elif not self._start_continuous_playback():
                    self._current_playing_name = ""
                    self._current_playing_path = ""
                    self._status.showMessage("No playable jingles were found from the selected row onward.")
                    return
                else:
                    return

            if self._playback_mode == "off" and self._start_selected_queue_playback():
                return

            self._reset_continuous_queue()
            record = self._records[selected_record_index]
            if not record.path.exists():
                self._status.showMessage("Selected file no longer exists.")
                return

            self._play_record(selected_record_index)
            return

        if self._player is None:
            self._status.showMessage("Playback unavailable: PyQt6 multimedia is not installed.")
            return

        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self._sync_broadcast_player_to_main()
            self._status.showMessage("Playback paused.")
            return

        if self._player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
            self._player.play()
            self._sync_broadcast_player_to_main()
            self._status.showMessage("Playback resumed.")
            return

        self._clear_playlist_state()

        selected_record_index = self._selected_record_index()
        if selected_record_index is None:
            self._status.showMessage("Select a jingle first.")
            return

        if self._playback_mode == "continuous":
            if self._continuous_queue_is_remote:
                self._interrupt_active_remote_queue_for_local_play()
            elif not self._start_continuous_playback():
                self._current_playing_name = ""
                self._current_playing_path = ""
                self._status.showMessage("No playable jingles were found from the selected row onward.")
                return
            else:
                return

        if self._playback_mode == "off" and self._start_selected_queue_playback():
            return

        self._reset_continuous_queue()
        record = self._records[selected_record_index]
        if not record.path.exists():
            self._status.showMessage("Selected file no longer exists.")
            return

        self._play_record(selected_record_index)

    def _on_stop_clicked(self) -> None:
        try:
            self._on_stop_clicked_impl()
        finally:
            self._publish_remote_state()

    def _on_stop_clicked_impl(self) -> None:
        if self._using_main_playback_engine():
            if self._main_playback_state in ("playing", "paused"):
                if self._main_playback_engine is not None:
                    self._main_playback_engine.stop(self._main_playback_pad_index)
                self._main_playback_state = "stopped"
                self._main_playback_timer.stop()
                self._main_playback_paused_position_ms = 0
                self._position_slider.setValue(0)
                self._update_time_label(0, self._main_playback_duration_ms)
                self._set_play_button_state("stopped")
                self._set_stop_button_breathing(False)
                self._interrupt_active_remote_queue_for_local_play()
                self._reset_continuous_queue()
                self._reset_clip_playback_window()
                self._sample_pad_looping = False
                self._sample_pad_release_looping = False
                self._sample_pad_native_looping = False
                self._current_sample_pad_index = -1
                self._current_playing_name = ""
                self._current_playing_path = ""
                self._clear_playlist_state()
                self._status.showMessage("Playback stopped.")
            return

        if self._player is None:
            self._status.showMessage("Playback unavailable: PyQt6 multimedia is not installed.")
            return

        if self._player.playbackState() in (
            QMediaPlayer.PlaybackState.PlayingState,
            QMediaPlayer.PlaybackState.PausedState,
        ):
            self._player.stop()
            self._player.setPosition(self._current_clip_start_ms)
            if self._broadcast_player is not None:
                self._broadcast_player.stop()
                self._broadcast_player.setPosition(self._current_clip_start_ms)
            self._interrupt_active_remote_queue_for_local_play()
            self._reset_continuous_queue()
            self._reset_clip_playback_window()
            self._sample_pad_looping = False
            self._sample_pad_release_looping = False
            self._sample_pad_native_looping = False
            self._current_sample_pad_index = -1
            if self._audio_output is not None:
                self._audio_output.setMuted(self._is_muted)
            if self._broadcast_audio_output is not None:
                self._broadcast_audio_output.setMuted(self._is_muted)
            self._current_playing_name = ""
            self._current_playing_path = ""
            self._clear_playlist_state()
            self._status.showMessage("Playback stopped.")

    def _on_duration_changed(self, duration_ms: int) -> None:
        self._position_slider.setRange(0, max(0, duration_ms))
        self._update_time_label(self._player.position() if self._player is not None else 0, duration_ms)

    def _on_position_changed(self, position_ms: int) -> None:
        if self._player is not None:
            if self._clip_start_seek_pending:
                if position_ms + 120 < self._current_clip_start_ms:
                    self._player.setPosition(self._current_clip_start_ms)
                    return
                self._clip_start_seek_pending = False
                if self._clip_seek_muted_temporarily:
                    self._clear_clip_seek_temporary_silence(ramp_up=True)

            clip_stop_ms = self._current_clip_stop_ms
            if (
                clip_stop_ms > self._current_clip_start_ms
                and position_ms >= clip_stop_ms
                and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            ):
                if self._clip_boundary_handling:
                    return
                self._clip_boundary_handling = True
                if self._playback_mode == "loop" or self._sample_pad_looping:
                    # Mute briefly during the seek so any decoder discontinuity
                    # is inaudible (brief silence rather than a pop).
                    self._restart_current_clip_from_start(temporary_mute_for_seek=True)
                    return

                if self._continuous_queue and self._play_next_continuous_record():
                    self._clip_boundary_handling = False
                    return

                self._player.stop()
                self._player.setPosition(self._current_clip_start_ms)
                ended_name = self._current_playing_name
                self._reset_continuous_queue()
                self._reset_clip_playback_window()
                self._sample_pad_looping = False
                self._sample_pad_release_looping = False
                self._sample_pad_native_looping = False
                self._current_playing_name = ""
                self._current_playing_path = ""
                self._clip_boundary_handling = False
                if self._broadcast_player is not None:
                    self._broadcast_player.stop()
                    self._broadcast_player.setPosition(self._current_clip_start_ms)
                if ended_name:
                    self._status.showMessage(f"Playback finished: {ended_name}")
                else:
                    self._status.showMessage("Playback finished.")
                if self._pending_remote_play_queue:
                    self._dispatch_next_pending_remote_play()
                return

        if not self._slider_pressed:
            self._position_slider.setValue(position_ms)
        duration = self._player.duration() if self._player is not None else 0
        self._update_time_label(position_ms, duration)

    def _on_playback_state_changed(self, _state: Any) -> None:
        if self._player is None:
            return
        playback_state = self._player.playbackState()
        if playback_state == QMediaPlayer.PlaybackState.PlayingState:
            self._set_play_button_state("playing")
        elif playback_state == QMediaPlayer.PlaybackState.PausedState:
            self._set_play_button_state("paused")
        else:
            self._set_play_button_state("stopped")
        # Only reset slider when stopped, not when paused
        is_stopped = self._player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
        if is_stopped:
            self._position_slider.setValue(0)
            if self._clip_seek_muted_temporarily:
                self._clear_clip_seek_temporary_silence(ramp_up=False)
        # Make stop button breathe when playing or paused
        is_active = self._player.playbackState() in (
            QMediaPlayer.PlaybackState.PlayingState,
            QMediaPlayer.PlaybackState.PausedState,
        )
        self._set_stop_button_breathing(is_active)

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if self._player is None:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._playback_mode == "loop" or self._sample_pad_looping:
                # Native-looping tracks loop at the codec level; EndOfMedia won't
                # fire per-iteration, but guard here in case it ever does.
                if self._should_use_native_looping():
                    return
                self._restart_current_clip_from_start(temporary_mute_for_seek=True)
                return
            ended_name = self._current_playing_name
            if self._play_next_continuous_record():
                return
            self._reset_continuous_queue()
            self._reset_clip_playback_window()
            self._sample_pad_looping = False
            self._sample_pad_release_looping = False
            self._sample_pad_native_looping = False
            self._current_sample_pad_index = -1
            if self._audio_output is not None:
                self._audio_output.setMuted(self._is_muted)
            if self._broadcast_player is not None:
                self._broadcast_player.stop()
            if self._broadcast_audio_output is not None:
                self._broadcast_audio_output.setMuted(self._is_muted)
            self._current_playing_name = ""
            self._current_playing_path = ""
            self._clear_playlist_state()
            if ended_name:
                self._status.showMessage(f"Playback finished: {ended_name}")
            else:
                self._status.showMessage("Playback finished.")
            if self._pending_remote_play_queue:
                self._dispatch_next_pending_remote_play()

    def _record_index_for_path(self, path_text: str) -> int | None:
        target = Path(path_text)
        try:
            target = target.resolve()
        except Exception:
            target = Path(path_text)
        for index, record in enumerate(self._records):
            candidate = record.path
            try:
                candidate = candidate.resolve()
            except Exception:
                pass
            if candidate == target:
                return index
        return None

    def start_playlist_playback(
        self,
        audio_paths: list[str],
        start_index: int = 0,
        loop_enabled: bool = False,
        use_preview_mode: bool = False,
    ) -> bool:
        if self._player is None:
            self._status.showMessage("Playback unavailable: PyQt6 multimedia is not installed.")
            return False

        if not audio_paths:
            self._status.showMessage("Playlist is empty.")
            return False

        self.set_playlist_preview_mode(bool(use_preview_mode))

        start = max(0, min(start_index, len(audio_paths) - 1))
        ordered_paths = list(audio_paths[start:])
        if not ordered_paths:
            ordered_paths = list(audio_paths)

        queue: list[int] = []
        for path_text in ordered_paths:
            record_index = self._record_index_for_path(path_text)
            if record_index is not None:
                queue.append(record_index)

        if not queue:
            self._status.showMessage("No playable playlist items were found in the current library.")
            return False

        if not self._playlist_active:
            self._playlist_saved_playback_mode = self._playback_mode
        self._playback_mode = "off"
        self._refresh_playback_mode_button()
        self._apply_player_loop_mode()

        self._playlist_active = True
        self._playlist_loop_enabled = bool(loop_enabled)
        self._continuous_queue = queue
        self._continuous_queue_position = -1
        if not self._play_next_continuous_record():
            self._clear_playlist_state()
            return False
        return True

    def toggle_playlist_pause_resume(self) -> str:
        if self._player is None:
            return "unavailable"
        state = self._player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self._sync_broadcast_player_to_main()
            self._status.showMessage("Playlist paused.")
            return "paused"
        if state == QMediaPlayer.PlaybackState.PausedState:
            self._player.play()
            self._sync_broadcast_player_to_main()
            self._status.showMessage("Playlist resumed.")
            return "playing"
        return "stopped"

    def stop_playlist_playback(self) -> None:
        self._clear_playlist_state()
        self._on_stop_clicked()

    def set_playlist_loop_enabled(self, enabled: bool) -> None:
        self._playlist_loop_enabled = bool(enabled)

    def set_playlist_preview_mode(self, preview_mode: bool) -> bool:
        if preview_mode and not self._can_use_preview_mode():
            return False
        self._mode_btn.setChecked(bool(preview_mode))
        return self._is_preview_mode == bool(preview_mode)

    def playlist_playback_snapshot(self) -> dict[str, Any]:
        state = "unavailable"
        if self._player is not None:
            playback_state = self._player.playbackState()
            if playback_state == QMediaPlayer.PlaybackState.PlayingState:
                state = "playing"
            elif playback_state == QMediaPlayer.PlaybackState.PausedState:
                state = "paused"
            else:
                state = "stopped"

        return {
            "state": state,
            "active": self._playlist_active,
            "loop_enabled": self._playlist_loop_enabled,
            "is_preview_mode": self._is_preview_mode,
            "current_path": self._current_playing_path,
            "queue_position": self._continuous_queue_position,
        }

    def _refresh_playback_mode_button(self) -> None:
        if self._playback_mode == "loop":
            self._loop_btn.setText("Loop On")
            self._loop_btn.setStyleSheet(
                "QPushButton { background-color: #0d47a1; color: white; font-weight: bold; }"
                "QPushButton:hover { background-color: #1565c0; }"
            )
            self._set_loop_breathing(self._loop_btn.isEnabled())
        elif self._playback_mode == "continuous":
            self._loop_btn.setText("Continuous")
            self._loop_btn.setStyleSheet(
                "QPushButton { background-color: #ef6c00; color: white; font-weight: bold; }"
                "QPushButton:hover { background-color: #fb8c00; }"
            )
            self._set_loop_breathing(self._loop_btn.isEnabled())
        else:
            self._loop_btn.setText("Loop Off")
            self._loop_btn.setStyleSheet("")
            self._set_loop_breathing(False)

    def _on_loop_clicked(self) -> None:
        if self._playback_mode == "off":
            self._playback_mode = "loop"
        elif self._playback_mode == "loop":
            self._playback_mode = "continuous"
        else:
            self._playback_mode = "off"
        self._apply_playback_mode_change()

    def _apply_playback_mode_change(self) -> None:
        """Shared tail for any `_playback_mode` change (Loop button click or remote set)."""
        self._refresh_playback_mode_button()
        self._apply_player_loop_mode()

        # Switching to Loop Off: clear any active queue so the current track
        # finishes and playback stops naturally.
        if self._playback_mode == "off":
            self._reset_continuous_queue()
            return

        # If switching to continuous while playback is active, seed the queue
        # from the currently playing row so the next EndOfMedia can advance.
        if (
            self._playback_mode == "continuous"
            and self._is_playback_active()
            and not self._continuous_queue
        ):
            current_index = self._selected_record_index()
            if current_index is not None:
                start_row = self._visible_row_for_record_index(current_index)
                if start_row >= 0:
                    self._continuous_queue = list(self._visible_indices[start_row:])
                    # Position 0 is the currently playing track; next advance starts at 1.
                    self._continuous_queue_position = 0

        if self._using_main_playback_engine() and self._main_playback_engine is not None:
            engine_loop_enabled = self._playback_mode == "loop" or self._sample_pad_looping
            self._main_playback_engine.set_pad_loop(self._main_playback_pad_index, engine_loop_enabled)

    def _should_use_native_looping(self) -> bool:
        """True when Qt should handle looping internally (no seek gap between iterations)."""
        if self._sample_pad_native_looping:
            return True
        # Main-window loop button on an untrimmed full-file track.
        if self._playback_mode == "loop" and self._current_clip_stop_ms == -1:
            return True
        return False

    def _apply_player_loop_mode(self) -> None:
        if self._player is None:
            return
        if self._should_use_native_looping():
            # Seamless native looping — no seek/buffer-flush gap between iterations.
            self._player.setLoops(QMediaPlayer.Loops.Infinite)
            if self._broadcast_player is not None:
                self._broadcast_player.setLoops(QMediaPlayer.Loops.Infinite)
        else:
            # Manual loop control keeps clip offsets consistent across backends.
            self._player.setLoops(1)
            if self._broadcast_player is not None:
                self._broadcast_player.setLoops(1)

    def _set_play_button_state(self, state: str) -> None:
        if state == "playing":
            self._play_btn.setText("Pause")
            self._play_btn.setStyleSheet(
                "QPushButton { background-color: #f57c00; color: white; font-weight: bold; }"
                "QPushButton:hover { background-color: #e65100; }"
            )
            self._set_play_stop_breathing(self._play_btn.isEnabled())
        elif state == "paused":
            self._play_btn.setText("Resume")
            self._play_btn.setStyleSheet(
                "QPushButton { background-color: #1565c0; color: white; font-weight: bold; }"
                "QPushButton:hover { background-color: #0d47a1; }"
            )
            self._set_play_stop_breathing(False)
        else:
            self._play_btn.setText("Play Selected")
            self._play_btn.setStyleSheet(
                "QPushButton { background-color: #2e7d32; color: white; font-weight: bold; }"
                "QPushButton:hover { background-color: #388e3c; }"
            )
            self._set_play_stop_breathing(False)


    def _on_slider_pressed(self) -> None:
        self._slider_pressed = True

    def _on_slider_released(self) -> None:
        self._slider_pressed = False
        if self._using_main_playback_engine():
            self._seek_main_engine_to(int(self._position_slider.value()))
            return
        if self._player is not None:
            self._player.setPosition(int(self._position_slider.value()))
            self._sync_broadcast_player_to_main()

    def _update_time_label(self, position_ms: int, duration_ms: int) -> None:
        self._time_label.setText(
            f"{self._fmt_time(position_ms)} / {self._fmt_time(duration_ms)}"
        )

    @staticmethod
    def _fmt_time(ms: int) -> str:
        total = max(0, int(ms / 1000))
        minutes, seconds = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

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
