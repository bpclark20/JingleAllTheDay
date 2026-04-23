#!/usr/bin/env python3
"""Jingle browser GUI with category filters and playback device selection."""

from __future__ import annotations

import sys
import ctypes
import json
from pathlib import Path
from typing import Any, Callable

from app_helpers import (
    apply_windows_taskbar_icon as _apply_windows_taskbar_icon,
    chip_palette_for_tag_seed as _chip_palette_for_tag_seed,
    coerce_volume_percent as _coerce_volume_percent,
    ensure_qt_logging_rules as _ensure_qt_logging_rules,
    format_duration_hms as _format_duration_hms,
    format_size_label as _format_size_label,
    merge_tags as _merge_tags,
    normalize_tags as _normalize_tags,
    probe_duration_seconds as _probe_duration_seconds,
    remove_tags as _remove_tags,
    runtime_app_dir as _runtime_app_dir,
    tags_to_text as _tags_to_text,
)
from dialogs import AboutDialog, OptionsDialog, SAMPLE_PAD_BLOCKSIZE_OPTIONS
from mainwindow_file_edit_mixin import MainWindowFileEditMixin
from mainwindow_library_mixin import MainWindowLibraryMixin
from mainwindow_menu_mixin import MainWindowMenuMixin
from mainwindow_shortcuts_mixin import MainWindowShortcutsMixin
from mainwindow_tools_mixin import MainWindowToolsMixin
from models_store import JingleRecord, LibraryStore

from widgets import DeselectableTableWidget
# Import SamplePadsWindow for sample pad feature
from sample_pads import SamplePadsWindow
from sample_pad_audio_engine import SamplePadAudioEngine as _SamplePadAudioEngine
import sample_pad_audio_engine as _sp_engine_mod


def _coerce_sample_pad_blocksize(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 128
    if parsed in SAMPLE_PAD_BLOCKSIZE_OPTIONS:
        return parsed
    return 128


def _coerce_sample_pad_streaming_min_seconds(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 120
    return max(0, min(3600, parsed))


_ensure_qt_logging_rules()
_HERE = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(__file__).resolve().parent  # type: ignore[attr-defined]

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QFileSystemWatcher,
    QObject,
    QAbstractNativeEventFilter,
    QPropertyAnimation,
    QSettings,
    QTimer,
    Qt,
    QStandardPaths,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import QAction, QCursor, QIcon, QKeyEvent, QMouseEvent, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
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
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

_has_qt_multimedia = False
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

    _has_qt_multimedia = True
except ModuleNotFoundError:
    pass

_has_pynput = False
_pynput_keyboard: Any | None = None
try:
    from pynput import keyboard as _pynput_keyboard  # type: ignore[import-not-found]

    _has_pynput = True
except Exception:
    _pynput_keyboard = None

_has_windows_native_hotkeys = sys.platform == "win32"
_WM_HOTKEY = 0x0312
_MOD_NOREPEAT = 0x4000
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004


class _WindowsHotkeyEventFilter(QAbstractNativeEventFilter):
    def __init__(
        self,
        id_to_pad: dict[int, int],
        on_pad_hotkey: Callable[[int], None],
    ) -> None:
        super().__init__()
        self._id_to_pad = id_to_pad
        self._on_pad_hotkey = on_pad_hotkey

    def nativeEventFilter(self, event_type: Any, message: Any) -> tuple[bool, int]:
        if not _has_windows_native_hotkeys:
            return False, 0
        event_name = bytes(event_type).decode("utf-8", errors="ignore")
        if event_name not in ("windows_generic_MSG", "windows_dispatcher_MSG"):
            return False, 0
        try:
            msg = ctypes.wintypes.MSG.from_address(int(message))
        except Exception:
            return False, 0
        if int(msg.message) != _WM_HOTKEY:
            return False, 0

        hotkey_id = int(msg.wParam)
        pad_index = self._id_to_pad.get(hotkey_id)
        if pad_index is None:
            return False, 0

        self._on_pad_hotkey(pad_index)
        return True, 0

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
    MainWindowMenuMixin,
    MainWindowShortcutsMixin,
    MainWindowFileEditMixin,
    MainWindowToolsMixin,
    MainWindowLibraryMixin,
    QMainWindow,
):
    _sample_pad_hotkey_requested = pyqtSignal(int)
    _sample_pad_release_requested = pyqtSignal(int)
    _sample_pad_board_switch_requested = pyqtSignal(int)

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
        self._output_device = str(self._settings.value("options/outputDevice", "")).strip()
        self._preview_output_device = str(self._settings.value("options/previewOutputDevice", "")).strip()
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
        self._sample_pads_last_layout_path = str(
            self._settings.value("samplePads/lastLayoutPath", "")
        ).strip()
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
        self._sample_pad_listener: Any | None = None
        self._sample_pad_hotkey_backend = "none"
        self._sample_pad_win_filter: _WindowsHotkeyEventFilter | None = None
        self._sample_pad_hotkey_id_to_pad: dict[int, int] = {}
        self._sample_pad_looping: bool = False
        self._sample_pad_release_looping: bool = False
        self._sample_pad_native_looping: bool = False
        self._current_sample_pad_index: int = -1
        # Low-latency engine used for live-mode sample pad playback
        self._sp_engine: _SamplePadAudioEngine = _SamplePadAudioEngine()
        self._sp_engine.set_streaming_min_seconds(self._sample_pad_streaming_min_seconds)
        self._sample_pads_dirty: bool = False
        self._sample_pads_last_saved_signature: str = ""
        self._sample_pads_autosave_in_progress: bool = False
        self._samples_dir: Path | None = self._load_samples_dir()
        self._auto_folder_tags: bool = self._load_auto_folder_tags()
        self._auto_generate_waveforms: bool = self._load_auto_generate_waveforms()
        self._watch_library_changes: bool = self._load_watch_library_changes()
        self._default_keyboard_shortcuts = dict(DEFAULT_KEYBOARD_SHORTCUTS)
        self._keyboard_shortcuts = self._load_keyboard_shortcuts()

        self._rename_action: QAction | None = None
        self._delete_action: QAction | None = None

        library_path = self._app_data_dir / "jingle-library.json"
        self._store = LibraryStore(library_path)

        self._records: list[JingleRecord] = []
        self._visible_indices: list[int] = []
        self._updating_table = False
        self._is_rescanning = False

        self._player: QMediaPlayer | None = None
        self._audio_output: QAudioOutput | None = None
        self._is_muted = False
        self._slider_pressed = False
        self._playback_mode = "off"
        self._continuous_queue: list[int] = []
        self._continuous_queue_position = -1
        self._current_playing_name = ""
        self._current_clip_start_ms = 0
        self._current_clip_stop_ms = -1
        self._clip_start_seek_pending = False
        self._clip_seek_muted_temporarily = False
        self._clip_boundary_handling = False
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
            self._apply_output_device()

        central = QWidget(self)
        self._central_widget = central
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
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
        self._set_play_button_state(False)

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
        if self._sample_pad_global_hotkeys_enabled:
            self._set_sample_pad_global_hotkeys(True)

        # Defer the initial scan so the window can render immediately.
        QTimer.singleShot(0, self._maybe_run_first_time_setup)

    def play_sample_pad_jingle(
        self,
        jingle_data,
        is_live_mode,
        *,
        pad_mode: str = "one_shot",
        pad_index: int = -1,
        pad_volume_percent: int = 100,
        pad_pan_percent: int = 0,
        pad_is_muted: bool = False,
        pad_is_solo: bool = False,
    ):
        """
        Play a jingle from a sample pad in live or preview mode.
        jingle_data: dict with at least 'name' and 'path'.
        is_live_mode: bool, True for live, False for preview.
        pad_mode: the SamplePad mode string (one_shot / loop / release).
        pad_index: which pad is triggering playback (-1 if unknown).
        """
        if not jingle_data or 'path' not in jingle_data:
            self._status.showMessage("No jingle assigned to this pad.")
            return
        record = self._record_for_sample_pad_jingle(jingle_data)
        clip_start, clip_stop = self._resolved_sample_pad_clip_seconds(jingle_data, record)

        # ------------------------------------------------------------------
        # Route sample-pad playback through the low-latency engine for both
        # Live and Preview modes so retriggers are handled at the PCM level
        # with a short crossfade instead of QMediaPlayer pipeline resets.
        # ------------------------------------------------------------------
        if _sp_engine_mod.is_available():
            loop = pad_mode in ("loop", "release")
            if is_live_mode:
                target_device = self._output_device
            else:
                target_device = self._preview_output_device if self._can_use_preview_mode() else self._output_device
            try:
                stream_needs_reopen = (
                    self._sp_engine._stream_device != target_device
                    or self._sp_engine._stream_blocksize != self._sample_pad_blocksize
                )
                self._sp_engine.set_device(
                    target_device,
                    blocksize=self._sample_pad_blocksize,
                )
                if stream_needs_reopen:
                    # Stream just (re)opened — preload all pads at the new
                    # samplerate so subsequent triggers are instantaneous.
                    self._sp_engine_preload_all_pads()
                self._sync_sample_pad_engine_gain()
                if pad_index >= 0:
                    self._sp_engine.set_pad_mix(
                        pad_index,
                        pad_volume_percent,
                        pad_pan_percent,
                        pad_is_muted,
                        pad_is_solo,
                    )
                self._sp_engine.trigger(
                    path=jingle_data['path'],
                    volume=1.0,
                    clip_start_seconds=clip_start,
                    clip_stop_seconds=clip_stop,
                    loop=loop,
                    pad_index=pad_index,
                )
            except Exception as exc:
                self._status.showMessage(f"Audio engine error: {exc}")
                return
            name = jingle_data.get('name', Path(jingle_data['path']).name)
            self._status.showMessage(f"Playing: {name}")
            return

        # ------------------------------------------------------------------
        # Preview mode (or engine unavailable): use QMediaPlayer as before.
        # ------------------------------------------------------------------
        # Use native seamless looping (no seek gap) for untrimmed full-file loop/release mode.
        # stop() works fine with Infinite loops, so release mode can use native looping too.
        self._sample_pad_looping = pad_mode in ("loop", "release")
        self._sample_pad_release_looping = (pad_mode == "release")
        self._current_sample_pad_index = pad_index
        if pad_mode in ("loop", "release"):
            self._sample_pad_native_looping = self._sample_pad_clip_is_full_file(
                record,
                clip_start,
                clip_stop,
            )
        else:
            self._sample_pad_native_looping = False
        # Set the preview/live flag so _play_record's _apply_output_device() call
        # routes to the correct device and volume.
        self._is_preview_mode = not is_live_mode
        # Play using record if found, else fallback to path
        if record:
            has_clip_override = (
                abs(record.clip_start_seconds - clip_start) >= 0.0005
                or abs(record.clip_stop_seconds - clip_stop) >= 0.0005
            )
            if has_clip_override:
                original_start = record.clip_start_seconds
                original_stop = record.clip_stop_seconds
                try:
                    record.clip_start_seconds = clip_start
                    record.clip_stop_seconds = clip_stop
                    self._play_record(self._records.index(record))
                finally:
                    record.clip_start_seconds = original_start
                    record.clip_stop_seconds = original_stop
            else:
                self._play_record(self._records.index(record))
        else:
            # Fallback: play the file directly (no clip window)
            if self._player:
                self._reset_clip_playback_window()
                self._prepare_clip_start_seek(temporary_mute_for_seek=False)
                self._player.setSource(QUrl.fromLocalFile(str(jingle_data['path'])))
                if clip_start > 0.0:
                    self._player.setPosition(max(0, int(round(clip_start * 1000.0))))
                self._player.play()
                self._status.showMessage(f"Playing: {jingle_data.get('name', jingle_data['path'])}")

    def _record_for_sample_pad_jingle(self, jingle_data: dict[str, Any]) -> JingleRecord | None:
        path_text = str(jingle_data.get("path", "")).strip()
        if not path_text:
            return None
        for record in self._records:
            if str(record.path) == path_text:
                return record
        return None

    def _resolved_sample_pad_clip_seconds(
        self,
        jingle_data: dict[str, Any],
        record: JingleRecord | None,
    ) -> tuple[float, float]:
        if record is not None:
            profile_index_raw = jingle_data.get("clip_profile_index")
            if isinstance(profile_index_raw, int):
                profiles, active_index = self._store.get_clip_profiles(
                    record.path,
                    record.duration_seconds,
                )
                if profiles:
                    profile_index = max(0, min(int(profile_index_raw), len(profiles) - 1))
                    return profiles[profile_index]
                return record.clip_start_seconds, record.clip_stop_seconds
            return record.clip_start_seconds, record.clip_stop_seconds

        clip_start_raw = jingle_data.get("clip_start_seconds")
        clip_stop_raw = jingle_data.get("clip_stop_seconds")
        if isinstance(clip_start_raw, (int, float)) and isinstance(clip_stop_raw, (int, float)):
            return max(0.0, float(clip_start_raw)), max(0.0, float(clip_stop_raw))
        return 0.0, 0.0

    def _sample_pad_clip_is_full_file(
        self,
        record: JingleRecord | None,
        clip_start: float,
        clip_stop: float,
    ) -> bool:
        if record is None:
            return clip_start <= 0.0005 and clip_stop <= 0.0005

        duration = max(0.0, float(record.duration_seconds))
        start = max(0.0, float(clip_start))
        stop = max(0.0, float(clip_stop))
        if duration <= 0.0:
            return start <= 0.0005 and stop <= 0.0005

        start = min(start, duration)
        if stop <= 0.0:
            stop = duration
        else:
            stop = min(stop, duration)
            if stop <= start:
                start = 0.0
                stop = duration
        return start <= 0.0005 and abs(duration - stop) <= 0.0005

    def stop_sample_pad_jingle(self, pad_index: int = -1) -> None:
        """Stop playback from a sample pad (used by Release mode)."""
        if _sp_engine_mod.is_available():
            self._sp_engine.stop(None if pad_index == -1 else pad_index)
            return

        # Only stop if this pad currently owns playback; ignore stale releases
        # from a previously-held pad that was superseded by another.
        if pad_index != -1 and self._current_sample_pad_index != pad_index:
            return
        self._sample_pad_looping = False
        self._sample_pad_release_looping = False
        self._sample_pad_native_looping = False
        self._current_sample_pad_index = -1
        # Also stop QMediaPlayer in case the preview path was active.
        # Mute before stopping so the abrupt buffer cutoff is inaudible.
        # _on_stop_clicked restores the correct mute state at its end.
        if self._audio_output is not None and not self._is_muted:
            self._audio_output.setMuted(True)
        self._on_stop_clicked()

    def stop_all_sample_pad_playback(self) -> None:
        """Stop all currently active sample-pad playback voices."""
        if _sp_engine_mod.is_available():
            self._sp_engine.stop(None)
            self._status.showMessage("All sample pad playback stopped.")
            return
        self.stop_sample_pad_jingle(-1)

    def set_sample_pad_mix(
        self,
        pad_index: int,
        volume_percent: int,
        pan_percent: int,
        is_muted: bool,
        is_solo: bool,
    ) -> None:
        if not _sp_engine_mod.is_available() or pad_index < 0:
            return
        try:
            self._sp_engine.set_pad_mix(pad_index, volume_percent, pan_percent, is_muted, is_solo)
        except Exception:
            return

    def sample_pad_meter_levels(self) -> dict[int, float]:
        if not _sp_engine_mod.is_available():
            return {}
        try:
            levels = self._sp_engine.meter_levels()
            if isinstance(levels, dict):
                return levels
        except Exception:
            pass
        return {}

    def sample_pad_output_meter_level(self, _is_live_mode: bool) -> float:
        """Return normalized output meter level for the sample-pad mixer output strip."""
        try:
            left, right = self.sample_pad_output_meter_levels(_is_live_mode)
            return max(0.0, min(1.0, max(float(left), float(right))))
        except Exception:
            pass
        levels = self.sample_pad_meter_levels()
        if not levels:
            return 0.0
        try:
            peak = max(float(level) for level in levels.values())
        except Exception:
            return 0.0
        return max(0.0, min(1.0, peak))

    def sample_pad_output_meter_levels(self, _is_live_mode: bool) -> tuple[float, float]:
        """Return normalized output meter levels for the sample-pad mixer as (left, right)."""
        if _sp_engine_mod.is_available():
            try:
                levels = self._sp_engine.output_meter_levels()
                if isinstance(levels, tuple) and len(levels) == 2:
                    left = max(0.0, min(1.0, float(levels[0])))
                    right = max(0.0, min(1.0, float(levels[1])))
                    return left, right
            except Exception:
                pass
        levels = self.sample_pad_meter_levels()
        if levels:
            try:
                mono = max(0.0, min(1.0, max(float(level) for level in levels.values())))
            except Exception:
                mono = 0.0
        else:
            mono = 0.0
        return mono, mono

    def is_sample_pad_playing(self, pad_index: int) -> bool:
        if pad_index < 0:
            return False
        if _sp_engine_mod.is_available():
            return self._sp_engine.is_pad_playing(pad_index)

        if self._current_sample_pad_index != pad_index:
            return False
        if self._player is None:
            return False
        state = self._player.playbackState()
        return state in (
            QMediaPlayer.PlaybackState.PlayingState,
            QMediaPlayer.PlaybackState.PausedState,
        )

    def _on_sample_pads_btn_clicked(self):
        if self._sample_pads_btn.isChecked():
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self._ensure_sample_pads_window()
                # Warm stream/device and begin background decode for assigned pads
                # as soon as the sampler window opens.
                self._on_sample_pads_mode_changed(self._sample_pads_window.is_live_mode)
                self._sp_engine_preload_all_pads()
            finally:
                QApplication.restoreOverrideCursor()
            self._sample_pads_window.show()
            self._sample_pads_window.raise_()
            self._sample_pads_window.activateWindow()
            self._sample_pads_btn.setText("Hide Sample Pads")
        else:
            self._sample_pads_window.hide()
            self._autosave_sample_pad_layout()
            self._sample_pads_btn.setText("Show Sample Pads")

    def _on_sample_pads_window_closed(self):
        self._autosave_sample_pad_layout()
        self._sample_pads_btn.setChecked(False)
        self._sample_pads_btn.setText("Show Sample Pads")

    def _ensure_sample_pads_window(self) -> SamplePadsWindow:
        if self._sample_pads_window is None:
            self._sample_pads_window = SamplePadsWindow(num_pads=20, num_boards=5, parent=self)
            self._sample_pads_window.setModal(False)
            self._sample_pads_window.finished.connect(self._on_sample_pads_window_closed)
            self._sample_pads_window.layoutLoaded.connect(self._on_sample_pads_layout_selected)
            self._sample_pads_window.layoutSaved.connect(self._on_sample_pads_layout_selected)
            self._sample_pads_window.modeChanged.connect(self._on_sample_pads_mode_changed)
            self._sample_pads_window.globalHotkeysToggled.connect(
                self._on_sample_pads_global_hotkeys_toggled
            )
            self._sample_pads_window.altModifierToggled.connect(
                self._on_sample_pads_alt_modifier_toggled
            )
            self._sample_pads_window.boardSwitchCtrlModifierToggled.connect(
                self._on_sample_pads_board_switch_ctrl_modifier_toggled
            )
            self._sample_pads_window.padStateChanged.connect(
                self._mark_sample_pads_dirty
            )
            self._sample_pads_window.activeBoardChanged.connect(
                self._on_sample_pads_active_board_changed
            )
            global_hotkeys_available = _has_windows_native_hotkeys or _has_pynput
            global_hotkeys_reason = ""
            if not global_hotkeys_available:
                global_hotkeys_reason = (
                    "Global hotkeys unavailable on this platform. "
                    "Install the optional 'pynput' package to enable a fallback listener."
                )
            self._sample_pads_window.set_global_hotkeys_available(
                global_hotkeys_available,
                global_hotkeys_reason,
            )
            self._sample_pads_window.set_global_hotkeys_enabled(
                self._sample_pad_global_hotkeys_enabled
            )
            self._sample_pads_window.set_alt_modifier_enabled(
                self._sample_pad_alt_modifier
            )
            self._sample_pads_window.set_board_switch_ctrl_modifier_enabled(
                self._sample_pad_board_switch_requires_ctrl
            )
            self._sample_pads_window.set_active_board(self._sample_pad_active_board_index)
            # Prefer autosave state; fall back to last manually loaded layout
            autosave_path = self._app_data_dir / "sample_pads_autosave.json"
            if autosave_path.exists():
                self._sample_pads_window.load_layout_from_path(
                    str(autosave_path),
                    show_errors=False,
                )
            elif self._sample_pads_last_layout_path:
                loaded = self._sample_pads_window.load_layout_from_path(
                    self._sample_pads_last_layout_path,
                    show_errors=False,
                )
                if loaded:
                    self._status.showMessage(
                        f"Loaded sample pad layout: {self._sample_pads_last_layout_path}"
                    )
                else:
                    missing_path = self._sample_pads_last_layout_path
                    self._sample_pads_last_layout_path = ""
                    self._settings.setValue("samplePads/lastLayoutPath", "")
                    self._status.showMessage(
                        f"Previous sample pad layout not found: {missing_path}"
                    )
            self._sample_pads_last_saved_signature = self._sample_pad_layout_signature()
            self._sample_pads_dirty = False
        return self._sample_pads_window

    def _sample_pad_layout_signature(self) -> str:
        if self._sample_pads_window is None:
            return ""
        payload = self._sample_pads_window.layout_payload()
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _mark_sample_pads_dirty(self) -> None:
        self._sample_pads_dirty = True

    def _on_sample_pads_active_board_changed(self, board_index: int) -> None:
        self._sample_pad_active_board_index = board_index
        self._settings.setValue("samplePads/activeBoardIndex", board_index)
        self._sample_pads_dirty = True
        self._status.showMessage(f"Active sample pad board: {board_index + 1}")
        self._autosave_sample_pad_layout()

    def _switch_sample_pad_board(self, board_index: int) -> bool:
        pads_window = self._ensure_sample_pads_window()
        changed = pads_window.set_active_board(board_index)
        if changed:
            self._sample_pad_active_board_index = board_index
            self._settings.setValue("samplePads/activeBoardIndex", board_index)
            self._sample_pads_dirty = True
            self._status.showMessage(f"Switched to sample pad board {board_index + 1}")
            self._autosave_sample_pad_layout()
        return changed

    def _on_sample_pads_layout_selected(self, file_path: str) -> None:
        autosave_path = (self._app_data_dir / "sample_pads_autosave.json").resolve()
        selected_path = Path(file_path).resolve()
        if self._sample_pads_autosave_in_progress and selected_path == autosave_path:
            return

        self._sample_pads_last_layout_path = file_path.strip()
        self._settings.setValue("samplePads/lastLayoutPath", self._sample_pads_last_layout_path)
        if self._sample_pads_last_layout_path:
            self._status.showMessage(
                f"Sample pad layout ready: {self._sample_pads_last_layout_path}"
            )
        # Also autosave so the manually loaded state survives a restart
        self._autosave_sample_pad_layout()
        # Preload all pad audio into the engine cache so the first trigger
        # on each pad is instantaneous.
        self._sp_engine_preload_all_pads()

    def _sp_engine_preload_all_pads(self) -> None:
        """Decode and cache every assigned pad's audio in a background thread."""
        if not _sp_engine_mod.is_available() or self._sample_pads_window is None:
            return
        pads_window = self._sample_pads_window
        board_count = pads_window.board_count
        # Collect all unique (path, clip_start, clip_stop) tuples
        jobs: list[tuple[str, float, float]] = []
        seen: set[tuple[str, float, float]] = set()
        for board_idx in range(board_count):
            for pad in pads_window.board_pads(board_idx):
                jingle = pad.jingle
                if not isinstance(jingle, dict) or 'path' not in jingle:
                    continue
                path = jingle['path']
                record = self._record_for_sample_pad_jingle(jingle)
                cs, ce = self._resolved_sample_pad_clip_seconds(jingle, record)
                key = (path, cs, ce)
                if key not in seen:
                    seen.add(key)
                    jobs.append(key)
        if not jobs:
            return
        engine = self._sp_engine
        sr = engine._stream_samplerate or 44100
        ch = engine._stream_channels or 2

        def _preload_worker():
            for path, cs, ce in jobs:
                try:
                    engine.preload(path, samplerate=sr, channels=ch,
                                   clip_start_seconds=cs, clip_stop_seconds=ce)
                except Exception:
                    pass

        import threading as _threading
        _threading.Thread(target=_preload_worker, daemon=True).start()

    def preload_sample_pad_jingle(self, jingle_data: dict[str, Any]) -> None:
        """Decode/cache one assigned sample pad jingle in a background thread."""
        if not _sp_engine_mod.is_available() or not isinstance(jingle_data, dict):
            return
        path = str(jingle_data.get("path", "")).strip()
        if not path:
            return

        record = self._record_for_sample_pad_jingle(jingle_data)
        cs, ce = self._resolved_sample_pad_clip_seconds(jingle_data, record)
        engine = self._sp_engine
        sr = engine._stream_samplerate or 44100
        ch = engine._stream_channels or 2

        def _preload_one() -> None:
            try:
                engine.preload(
                    path,
                    samplerate=sr,
                    channels=ch,
                    clip_start_seconds=cs,
                    clip_stop_seconds=ce,
                )
            except Exception:
                pass

        import threading as _threading
        _threading.Thread(target=_preload_one, daemon=True).start()

    def _autosave_sample_pad_layout(self) -> None:
        if self._sample_pads_window is None:
            return
        if self._sample_pads_autosave_in_progress:
            return
        signature = self._sample_pad_layout_signature()
        if (
            signature == self._sample_pads_last_saved_signature
            and not self._sample_pads_dirty
        ):
            return
        autosave_path = self._app_data_dir / "sample_pads_autosave.json"
        previous_signature = self._sample_pads_last_saved_signature
        previous_dirty = self._sample_pads_dirty
        self._sample_pads_autosave_in_progress = True
        self._sample_pads_last_saved_signature = signature
        self._sample_pads_dirty = False
        try:
            self._sample_pads_window.save_layout_to_path(str(autosave_path))
        except Exception:
            self._sample_pads_last_saved_signature = previous_signature
            self._sample_pads_dirty = previous_dirty
        finally:
            self._sample_pads_autosave_in_progress = False

    def _on_sample_pads_global_hotkeys_toggled(self, enabled: bool) -> None:
        self._set_sample_pad_global_hotkeys(enabled)

    def _on_sample_pads_mode_changed(self, is_live_mode: bool) -> None:
        """Warm the sample-pad engine stream on mode switch.

        This avoids making the first trigger after a Live/Preview toggle pay
        stream startup cost, which can cause the first post-toggle trigger to
        be inaudible on some drivers.
        """
        if not _sp_engine_mod.is_available():
            return
        target_device = (
            self._output_device
            if is_live_mode
            else (self._preview_output_device if self._can_use_preview_mode() else self._output_device)
        )
        try:
            stream_needs_reopen = (
                self._sp_engine._stream_device != target_device
                or self._sp_engine._stream_blocksize != self._sample_pad_blocksize
            )
            self._sp_engine.set_device(
                target_device,
                blocksize=self._sample_pad_blocksize,
            )
            self._sync_sample_pad_engine_gain()
            if stream_needs_reopen:
                self._sp_engine_preload_all_pads()
        except Exception:
            # Playback path already handles/report errors at trigger time.
            pass

    def _on_sample_pads_alt_modifier_toggled(self, enabled: bool) -> None:
        self._sample_pad_alt_modifier = enabled
        self._settings.setValue(
            "samplePads/globalHotkeysAltModifier",
            "true" if enabled else "false",
        )
        # Restart the active listener so it picks up the new modifier
        if self._sample_pad_hotkey_backend != "none":
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self._stop_sample_pad_global_hotkeys()
                self._start_sample_pad_global_hotkeys()
            finally:
                QApplication.restoreOverrideCursor()

    def _on_sample_pads_board_switch_ctrl_modifier_toggled(self, enabled: bool) -> None:
        self._sample_pad_board_switch_requires_ctrl = enabled
        self._settings.setValue(
            "samplePads/boardSwitchRequiresCtrl",
            "true" if enabled else "false",
        )
        if self._sample_pad_hotkey_backend != "none":
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self._stop_sample_pad_global_hotkeys()
                self._start_sample_pad_global_hotkeys()
            finally:
                QApplication.restoreOverrideCursor()

    def _set_sample_pad_global_hotkeys(self, enabled: bool) -> None:
        target_enabled = bool(enabled)
        global_hotkeys_available = _has_windows_native_hotkeys or _has_pynput
        if target_enabled and not global_hotkeys_available:
            target_enabled = False
            self._status.showMessage(
                "Global sample pad hotkeys unavailable on this system."
            )

        if target_enabled:
            self._start_sample_pad_global_hotkeys()
        else:
            self._stop_sample_pad_global_hotkeys()

        self._sample_pad_global_hotkeys_enabled = target_enabled
        self._settings.setValue(
            "samplePads/globalHotkeysEnabled",
            "true" if target_enabled else "false",
        )

        if self._sample_pads_window is not None:
            self._sample_pads_window.set_global_hotkeys_enabled(target_enabled)

    def _start_sample_pad_global_hotkeys(self) -> None:
        if self._sample_pad_hotkey_backend != "none":
            return

        # Prefer non-exclusive listener first so number keys still type normally
        # in other applications while global pad triggers are enabled.
        if _has_pynput and _pynput_keyboard is not None:
            if self._start_pynput_hotkeys():
                return

        # Fallback to Windows native registration only when pynput is unavailable.
        if _has_windows_native_hotkeys and self._start_windows_native_hotkeys():
            self._status.showMessage(
                "Global hotkeys running in Windows-native fallback mode; number keys may be captured system-wide."
            )
            return

        self._sample_pad_global_hotkeys_enabled = False
        self._settings.setValue("samplePads/globalHotkeysEnabled", "false")
        if self._sample_pads_window is not None:
            self._sample_pads_window.set_global_hotkeys_enabled(False)
        self._status.showMessage("Could not start global hotkeys listener.")

    def _start_pynput_hotkeys(self) -> bool:
        if not _has_pynput or _pynput_keyboard is None:
            return False

        use_alt = self._sample_pad_alt_modifier
        board_switch_requires_ctrl = self._sample_pad_board_switch_requires_ctrl
        alt_keys = (
            _pynput_keyboard.Key.alt,
            _pynput_keyboard.Key.alt_l,
            _pynput_keyboard.Key.alt_r,
            _pynput_keyboard.Key.alt_gr,
        )
        ctrl_keys = (
            _pynput_keyboard.Key.ctrl,
            _pynput_keyboard.Key.ctrl_l,
            _pynput_keyboard.Key.ctrl_r,
        )
        shift_keys = (
            _pynput_keyboard.Key.shift,
            _pynput_keyboard.Key.shift_l,
            _pynput_keyboard.Key.shift_r,
        )
        _alt_held = [False]
        _ctrl_held = [False]
        _shift_held = [False]
        _keys_held: set[str] = set()
        # char → pad index for bare/alt keys (pads 0-9)
        _char_to_pad = {'1': 0, '2': 1, '3': 2, '4': 3, '5': 4,
                        '6': 5, '7': 6, '8': 7, '9': 8, '0': 9}
        _vk_to_board = {49: 0, 50: 1, 51: 2, 52: 3, 53: 4}
        # char → pad index for Ctrl+key (pads 10-15)
        # VK codes for digit keys: '1'=49...'9'=57, '0'=48
        # When Ctrl is held pynput sets key.char=None for digits, so we use key.vk
        _vk_to_ctrl_pad = {49: 10, 50: 11, 51: 12, 52: 13, 53: 14,
                           54: 15, 55: 16, 56: 17, 57: 18, 48: 19}

        def _on_press(key: Any) -> None:
            # Track modifier keys
            if key in ctrl_keys:
                _ctrl_held[0] = True
                return
            if key in shift_keys:
                _shift_held[0] = True
                return
            if use_alt:
                if key in alt_keys:
                    _alt_held[0] = True
                    return
            board_combo_active = (
                _shift_held[0] and _ctrl_held[0]
                if board_switch_requires_ctrl
                else _shift_held[0] and not _ctrl_held[0]
            )
            if board_combo_active:
                try:
                    vk = key.vk
                except AttributeError:
                    return
                board_index = _vk_to_board.get(vk)
                if board_index is not None:
                    held_key = f'board+{vk}'
                    if held_key not in _keys_held:
                        _keys_held.add(held_key)
                        self._sample_pad_board_switch_requested.emit(board_index)
                return
            # Ctrl+digit → pads 10-19: use VK codes because key.char is None when Ctrl held
            if _ctrl_held[0]:
                try:
                    vk = key.vk
                except AttributeError:
                    return
                pad_index = _vk_to_ctrl_pad.get(vk)
                if pad_index is not None:
                    held_key = f'ctrl+{vk}'
                    if held_key not in _keys_held:
                        _keys_held.add(held_key)
                        self._sample_pad_hotkey_requested.emit(pad_index)
                return
            # Normal pads 0-9 with optional alt modifier
            if use_alt and not _alt_held[0]:
                return
            try:
                char = key.char
            except AttributeError:
                return
            if char is None or char not in _char_to_pad:
                return
            # Suppress auto-repeat: only emit if this key wasn't already held
            if char in _keys_held:
                return
            _keys_held.add(char)
            self._sample_pad_hotkey_requested.emit(_char_to_pad[char])

        def _on_release(key: Any) -> None:
            # Ctrl released: emit releases for all held ctrl+vk pads
            if key in ctrl_keys:
                _ctrl_held[0] = False
                to_release = [k for k in list(_keys_held) if k.startswith('ctrl+')]
                for held_key in to_release:
                    _keys_held.discard(held_key)
                    vk = int(held_key[5:])
                    pad_index = _vk_to_ctrl_pad.get(vk)
                    if pad_index is not None:
                        self._sample_pad_release_requested.emit(pad_index)
                for held_key in [k for k in list(_keys_held) if k.startswith('board+')]:
                    _keys_held.discard(held_key)
                return
            if key in shift_keys:
                _shift_held[0] = False
                for held_key in [k for k in list(_keys_held) if k.startswith('board+')]:
                    _keys_held.discard(held_key)
                return
            if use_alt and key in alt_keys:
                _alt_held[0] = False
                return
            board_combo_active = (
                _shift_held[0] and _ctrl_held[0]
                if board_switch_requires_ctrl
                else _shift_held[0] and not _ctrl_held[0]
            )
            if board_combo_active:
                try:
                    vk = key.vk
                except AttributeError:
                    return
                _keys_held.discard(f'board+{vk}')
                return
            # Check for ctrl+digit release via VK (digit released while Ctrl still held)
            try:
                vk = key.vk
            except AttributeError:
                vk = None
            if vk is not None:
                held_key = f'ctrl+{vk}'
                if held_key in _keys_held:
                    _keys_held.discard(held_key)
                    pad_index = _vk_to_ctrl_pad.get(vk)
                    if pad_index is not None:
                        self._sample_pad_release_requested.emit(pad_index)
                    return
            # Normal pad release
            try:
                char = key.char
            except AttributeError:
                return
            if char is None or char not in _char_to_pad:
                return
            _keys_held.discard(char)
            self._sample_pad_release_requested.emit(_char_to_pad[char])

        try:
            self._sample_pad_listener = _pynput_keyboard.Listener(
                on_press=_on_press,
                on_release=_on_release,
            )
            self._sample_pad_listener.daemon = True
            self._sample_pad_listener.start()
            self._sample_pad_hotkey_backend = "pynput"
            return True
        except Exception:
            self._sample_pad_listener = None
            self._sample_pad_hotkey_backend = "none"
            return False

    def _start_windows_native_hotkeys(self) -> bool:
        if not _has_windows_native_hotkeys:
            return False

        app = QApplication.instance()
        if app is None:
            return False

        user32 = ctypes.windll.user32
        registered_ids: list[int] = []
        id_to_pad: dict[int, int] = {}
        mod_flags = _MOD_NOREPEAT | (_MOD_ALT if self._sample_pad_alt_modifier else 0)
        ctrl_mod_flags = _MOD_NOREPEAT | _MOD_CONTROL
        board_mod_flags = _MOD_NOREPEAT | _MOD_SHIFT
        if self._sample_pad_board_switch_requires_ctrl:
            board_mod_flags |= _MOD_CONTROL

        # Pads 0-9: keys 1-9 and 0 (with optional alt modifier)
        pad_key_specs = [
            (5000, int(Qt.Key.Key_1), 0), (5001, int(Qt.Key.Key_2), 1),
            (5002, int(Qt.Key.Key_3), 2), (5003, int(Qt.Key.Key_4), 3),
            (5004, int(Qt.Key.Key_5), 4), (5005, int(Qt.Key.Key_6), 5),
            (5006, int(Qt.Key.Key_7), 6), (5007, int(Qt.Key.Key_8), 7),
            (5008, int(Qt.Key.Key_9), 8), (5009, int(Qt.Key.Key_0), 9),
        ]
        # Pads 10-15: Ctrl+1-6 (always, regardless of alt modifier)
        ctrl_key_specs = [
            (5010, int(Qt.Key.Key_1), 10), (5011, int(Qt.Key.Key_2), 11),
            (5012, int(Qt.Key.Key_3), 12), (5013, int(Qt.Key.Key_4), 13),
            (5014, int(Qt.Key.Key_5), 14), (5015, int(Qt.Key.Key_6), 15),
            (5016, int(Qt.Key.Key_7), 16), (5017, int(Qt.Key.Key_8), 17),
            (5018, int(Qt.Key.Key_9), 18), (5019, int(Qt.Key.Key_0), 19),
        ]
        board_key_specs = [
            (5020, int(Qt.Key.Key_1), 100), (5021, int(Qt.Key.Key_2), 101),
            (5022, int(Qt.Key.Key_3), 102), (5023, int(Qt.Key.Key_4), 103),
            (5024, int(Qt.Key.Key_5), 104),
        ]

        for hotkey_id, vk_code, pad_idx in pad_key_specs:
            if not user32.RegisterHotKey(None, hotkey_id, mod_flags, vk_code):
                for existing_id in registered_ids:
                    user32.UnregisterHotKey(None, existing_id)
                return False
            registered_ids.append(hotkey_id)
            id_to_pad[hotkey_id] = pad_idx

        for hotkey_id, vk_code, pad_idx in ctrl_key_specs:
            if not user32.RegisterHotKey(None, hotkey_id, ctrl_mod_flags, vk_code):
                for existing_id in registered_ids:
                    user32.UnregisterHotKey(None, existing_id)
                return False
            registered_ids.append(hotkey_id)
            id_to_pad[hotkey_id] = pad_idx

        for hotkey_id, vk_code, board_marker in board_key_specs:
            if not user32.RegisterHotKey(None, hotkey_id, board_mod_flags, vk_code):
                for existing_id in registered_ids:
                    user32.UnregisterHotKey(None, existing_id)
                return False
            registered_ids.append(hotkey_id)
            id_to_pad[hotkey_id] = board_marker

        self._sample_pad_hotkey_id_to_pad = id_to_pad
        self._sample_pad_win_filter = _WindowsHotkeyEventFilter(
            id_to_pad=self._sample_pad_hotkey_id_to_pad,
            on_pad_hotkey=lambda pad_index: self._sample_pad_board_switch_requested.emit(pad_index - 100)
            if pad_index >= 100
            else self._sample_pad_hotkey_requested.emit(pad_index),
        )
        app.installNativeEventFilter(self._sample_pad_win_filter)
        self._sample_pad_hotkey_backend = "windows-native"
        return True

    def _stop_sample_pad_global_hotkeys(self) -> None:
        if self._sample_pad_hotkey_backend == "windows-native":
            app = QApplication.instance()
            if app is not None and self._sample_pad_win_filter is not None:
                try:
                    app.removeNativeEventFilter(self._sample_pad_win_filter)
                except Exception:
                    pass
            if _has_windows_native_hotkeys:
                user32 = ctypes.windll.user32
                for hotkey_id in list(self._sample_pad_hotkey_id_to_pad.keys()):
                    try:
                        user32.UnregisterHotKey(None, hotkey_id)
                    except Exception:
                        pass
            self._sample_pad_hotkey_id_to_pad = {}
            self._sample_pad_win_filter = None
            self._sample_pad_hotkey_backend = "none"
            return

        if self._sample_pad_listener is None:
            self._sample_pad_hotkey_backend = "none"
            return
        try:
            self._sample_pad_listener.stop()
        except Exception:
            pass
        self._sample_pad_listener = None
        self._sample_pad_hotkey_backend = "none"

    def _on_sample_pad_hotkey_requested(self, pad_index: int) -> None:
        self._trigger_sample_pad(pad_index)

    def _on_sample_pad_board_switch_requested(self, board_index: int) -> None:
        self._switch_sample_pad_board(board_index)

    def _on_sample_pad_hotkey_released(self, pad_index: int) -> None:
        if self._sample_pads_window is not None:
            self._sample_pads_window.release_pad(pad_index)

    def _trigger_sample_pad(self, pad_index: int) -> bool:
        pads_window = self._ensure_sample_pads_window()
        return pads_window.trigger_pad(pad_index)

    def _release_sample_pad(self, pad_index: int) -> None:
        if self._sample_pads_window is not None:
            self._sample_pads_window.release_pad(pad_index)

    def _handle_sample_pad_key_event(self, event: QKeyEvent) -> bool:
        # Ignore auto-repeat to avoid re-triggering while a key is held.
        if event.isAutoRepeat():
            return False

        # When a global listener is active it handles triggers from everywhere
        # (including when the app has focus), so skip local handling to avoid
        # double-triggering the same pad.
        if self._sample_pad_hotkey_backend in ("windows-native", "pynput"):
            return False

        modifiers = event.modifiers()
        ctrl_held = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift_held = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)

        board_combo_active = (
            shift_held and ctrl_held
            if self._sample_pad_board_switch_requires_ctrl
            else shift_held and not ctrl_held
        )
        if board_combo_active:
            return False

        # Ctrl+1-0 → pads 10-19 (always, regardless of alt modifier setting)
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
                return self._trigger_sample_pad(pad_index)
            return False

        # Determine which modifier (if any) is required for pads 0-9.
        use_alt = self._sample_pad_alt_modifier
        if use_alt:
            required = Qt.KeyboardModifier.AltModifier
            if modifiers & ~Qt.KeyboardModifier.KeypadModifier != required:
                return False
        else:
            if modifiers not in (
                Qt.KeyboardModifier.NoModifier,
                Qt.KeyboardModifier.KeypadModifier,
            ):
                return False
            # Don't steal bare number keys from text input widgets.
            focus_widget = QApplication.focusWidget()
            if focus_widget is not None and (
                focus_widget.inherits("QLineEdit")
                or focus_widget.inherits("QTextEdit")
                or focus_widget.inherits("QPlainTextEdit")
            ):
                return False

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
        if pad_index is None:
            return False
        return self._trigger_sample_pad(pad_index)

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:
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

    def _should_preserve_selected_row(self) -> bool:
        table = getattr(self, "_table", None)
        return (
            self._player is not None
            and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and table is not None
            and bool(table.selectedItems())
        )

    def _is_playback_active(self) -> bool:
        return (
            self._player is not None
            and self._player.playbackState()
            in (
                QMediaPlayer.PlaybackState.PlayingState,
                QMediaPlayer.PlaybackState.PausedState,
            )
        )

    def _handle_media_key_event(self, event: QKeyEvent) -> bool:
        if self._player is None:
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
        if self._player is None:
            return
        state = self._player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self._status.showMessage("Playback paused.")
            return
        if state == QMediaPlayer.PlaybackState.PausedState:
            self._player.play()
            self._status.showMessage("Playback resumed.")
            return
        self._on_play_clicked()

    def _resume_or_start_playback(self) -> None:
        if self._player is None:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
            self._player.play()
            self._status.showMessage("Playback resumed.")
            return
        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._on_play_clicked()

    def _pause_playback(self) -> None:
        if self._player is None:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self._status.showMessage("Playback paused.")

    def _refresh_mute_button_state(self) -> None:
        if self._is_muted:
            self._mute_btn.setText("Unmute")
            self._mute_btn.setStyleSheet(
                "QPushButton { background-color: #546e7a; color: white; font-weight: bold; }"
                "QPushButton:hover { background-color: #607d8b; }"
            )
        else:
            self._mute_btn.setText("Mute")
            self._mute_btn.setStyleSheet(
                "QPushButton { background-color: #455a64; color: white; font-weight: bold; }"
                "QPushButton:hover { background-color: #546e7a; }"
            )

    def _set_muted(self, muted: bool) -> None:
        self._is_muted = muted
        if self._audio_output is not None:
            if self._clip_seek_muted_temporarily and not muted:
                self._audio_output.setMuted(True)
            else:
                self._audio_output.setMuted(muted)
        self._refresh_mute_button_state()

    def _on_mute_clicked(self) -> None:
        if self._audio_output is None:
            self._status.showMessage("Playback unavailable: PyQt6 multimedia is not installed.")
            return
        self._set_muted(not self._is_muted)
        self._status.showMessage("Audio muted." if self._is_muted else "Audio unmuted.")

    def _active_volume_percent(self) -> int:
        if self._is_preview_mode and self._can_use_preview_mode():
            return self._preview_volume_percent
        return self._live_volume_percent

    def _save_volume_settings(self) -> None:
        self._settings.setValue("options/liveVolumePercent", self._live_volume_percent)
        self._settings.setValue("options/previewVolumePercent", self._preview_volume_percent)

    def sample_pad_mode_volume_percent(self, is_live_mode: bool) -> int:
        return self._live_volume_percent if is_live_mode else self._preview_volume_percent

    def set_sample_pad_mode_volume_percent(self, is_live_mode: bool, value: int) -> None:
        percent = _coerce_volume_percent(value)
        if is_live_mode:
            self._live_volume_percent = percent
        else:
            self._preview_volume_percent = percent
        self._save_volume_settings()

        # Apply to the currently-audible main bus only.
        if (self._is_preview_mode and not is_live_mode) or (not self._is_preview_mode and is_live_mode):
            self._apply_active_volume()

        self._refresh_volume_controls()
        if self._sample_pads_window is not None:
            self._sample_pads_window.refresh_mode_volume_controls()
        self._sync_sample_pad_engine_gain()

    def _sync_sample_pad_engine_gain(self) -> None:
        if not _sp_engine_mod.is_available() or self._sample_pads_window is None:
            return
        mode_is_live = bool(self._sample_pads_window.is_live_mode)
        mode_percent = self._live_volume_percent if mode_is_live else self._preview_volume_percent
        try:
            self._sp_engine.set_master_gain(mode_percent / 100.0)
        except Exception:
            pass

    def _apply_active_volume(self) -> None:
        if self._audio_output is None:
            return
        self._audio_output.setVolume(self._active_volume_percent() / 100.0)

    def _refresh_volume_controls(self) -> None:
        if not hasattr(self, "_volume_slider"):
            return
        mode_text = "Preview Vol" if self._is_preview_mode and self._can_use_preview_mode() else "Live Vol"
        self._volume_mode_label.setText(mode_text)
        value = self._active_volume_percent()
        self._volume_slider.blockSignals(True)
        self._volume_slider.setValue(value)
        self._volume_slider.blockSignals(False)
        self._volume_value_label.setText(f"{value}%")
        self._volume_slider.setToolTip(
            "Adjust preview volume." if mode_text == "Preview Vol" else "Adjust live volume."
        )

    def _on_volume_slider_changed(self, value: int) -> None:
        percent = _coerce_volume_percent(value)
        if self._is_preview_mode and self._can_use_preview_mode():
            self._preview_volume_percent = percent
        else:
            self._live_volume_percent = percent
        self._volume_value_label.setText(f"{percent}%")
        self._save_volume_settings()
        self._apply_active_volume()
        self._sync_sample_pad_engine_gain()
        if self._sample_pads_window is not None:
            self._sample_pads_window.refresh_mode_volume_controls()

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
        if self._player is None:
            super().keyPressEvent(event)
            return

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
        self._sp_engine.close()
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

    def _move_selection_up(self) -> None:
        """Move the selected row up by one (when playback is not active)."""
        selected_ranges = self._table.selectedRanges()
        if not selected_ranges:
            # No selection, select the first row
            if self._visible_indices:
                self._table.selectRow(0)
            return

        current_row = selected_ranges[0].topRow()
        if current_row > 0:
            # Move up
            self._table.selectRow(current_row - 1)
        else:
            # At the top, wrap to the bottom
            if self._visible_indices:
                self._table.selectRow(len(self._visible_indices) - 1)

    def _move_selection_down(self) -> None:
        """Move the selected row down by one (when playback is not active)."""
        selected_ranges = self._table.selectedRanges()
        if not selected_ranges:
            # No selection, select the first row
            if self._visible_indices:
                self._table.selectRow(0)
            return

        current_row = selected_ranges[0].topRow()
        max_row = len(self._visible_indices) - 1
        if current_row < max_row:
            # Move down
            self._table.selectRow(current_row + 1)
        else:
            # At the bottom, wrap to the top
            self._table.selectRow(0)

    def _on_help_about(self) -> None:
        library_count = len(self._records)
        library_duration_seconds = sum(max(0.0, record.duration_seconds) for record in self._records)
        library_size_bytes = sum(max(0, record.size_bytes) for record in self._records)
        runtime_dir = _runtime_app_dir()
        revision_log_path = runtime_dir / "rev.log"
        resolved_revision_log = revision_log_path if revision_log_path.is_file() else None
        dialog = AboutDialog(
            app_name=self._app_name,
            app_version=self._app_version,
            icon_path=_HERE / "icon.png",
            library_count=library_count,
            library_duration_seconds=library_duration_seconds,
            library_size_bytes=library_size_bytes,
            revision_log_path=resolved_revision_log,
            parent=self,
        )
        dialog.exec()

    def _connect_player_signals(self) -> None:
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

    def _load_samples_dir(self) -> Path | None:
        raw = str(self._settings.value("library/samplesDir", "")).strip()
        if raw:
            candidate = Path(raw)
            if candidate.exists() and candidate.is_dir():
                return candidate
        return None

    def _save_samples_dir(self) -> None:
        if self._samples_dir is not None:
            self._settings.setValue("library/samplesDir", str(self._samples_dir))

    def _load_auto_folder_tags(self) -> bool:
        return str(self._settings.value("library/autoFolderTags", "")).strip().lower() == "true"

    def _save_auto_folder_tags(self) -> None:
        self._settings.setValue("library/autoFolderTags", "true" if self._auto_folder_tags else "false")

    def _load_auto_generate_waveforms(self) -> bool:
        return str(self._settings.value("library/autoGenerateWaveforms", "")).strip().lower() == "true"

    def _save_auto_generate_waveforms(self) -> None:
        self._settings.setValue(
            "library/autoGenerateWaveforms",
            "true" if self._auto_generate_waveforms else "false",
        )

    def _load_watch_library_changes(self) -> bool:
        raw = str(self._settings.value("library/watchLibraryChanges", "true")).strip().lower()
        return raw != "false"

    def _save_watch_library_changes(self) -> None:
        self._settings.setValue(
            "library/watchLibraryChanges",
            "true" if self._watch_library_changes else "false",
        )

    def _maybe_run_first_time_setup(self) -> None:
        is_first_run = str(self._settings.value("library/samplesDir", "")).strip() == ""
        if is_first_run:
            self._run_first_time_setup()
        else:
            loaded_count = self._load_cached_records_for_selected_root()
            if loaded_count > 0:
                self._status.showMessage(
                    f"Loaded {loaded_count} jingles from cache. Checking for library changes..."
                )
            QTimer.singleShot(0, self._rescan_library)

    def _run_first_time_setup(self) -> None:
        QMessageBox.information(
            self,
            "Welcome to JingleAllTheDay",
            "Let's get started.\n\nFirst, select the folder where your jingles and audio samples are stored.",
        )

        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose Samples / Jingles Folder",
            str(Path.home()),
        )
        if not selected:
            QMessageBox.information(
                self,
                "Setup Cancelled",
                "No folder was selected. The application will now close.\n\n"
                "You will be prompted again on next launch.",
            )
            QApplication.quit()
            return

        path = Path(selected)
        if not path.exists() or not path.is_dir():
            QMessageBox.warning(self, "Invalid Folder", "The selected path is not a valid folder. The application will now close.")
            QApplication.quit()
            return

        self._samples_dir = path
        self._save_samples_dir()

        reply = QMessageBox.question(
            self,
            "Auto-tag from Folders?",
            "Would you like to automatically assign tags to jingles based on their folder names?\n\n"
            "For example, a file in Samples/Holiday/Christmas/ would receive the tags \"Holiday\" and \"Christmas\".\n\n"
            "You can change this later via Tools > Options.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        self._auto_folder_tags = reply == QMessageBox.StandardButton.Yes
        self._save_auto_folder_tags()

        self._rescan_library()

    def _selected_record_indices(self) -> list[int]:
        selected_rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        selected_record_indices: list[int] = []
        seen: set[int] = set()
        for row in selected_rows:
            if row < 0 or row >= len(self._visible_indices):
                continue
            record_index = self._visible_indices[row]
            if record_index in seen:
                continue
            seen.add(record_index)
            selected_record_indices.append(record_index)
        return selected_record_indices

    def _rebuild_table(self) -> None:
        self._updating_table = True
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._visible_indices))

        for row, record_index in enumerate(self._visible_indices):
            record = self._records[record_index]
            jingle_item = QTableWidgetItem(record.name)
            jingle_item.setFlags(jingle_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            jingle_item.setData(Qt.ItemDataRole.UserRole, record_index)

            category_item = QTableWidgetItem(_tags_to_text(record.categories))
            category_item.setToolTip("Comma or semicolon separated tags")
            category_item.setData(Qt.ItemDataRole.UserRole, record_index)

            folder_item = QTableWidgetItem(record.folder)
            folder_item.setFlags(folder_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            folder_item.setData(Qt.ItemDataRole.UserRole, record_index)
            folder_item.setToolTip(str(record.path))

            self._table.setItem(row, 0, jingle_item)
            self._table.setItem(row, 1, category_item)
            self._table.setItem(row, 2, folder_item)

        self._table.blockSignals(False)
        self._updating_table = False
        self._table.resizeColumnToContents(1)
        self._table.resizeColumnToContents(2)

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_table:
            return

        col = item.column()
        if col != 1:
            return

        record_index = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(record_index, int):
            return
        if record_index < 0 or record_index >= len(self._records):
            return

        record = self._records[record_index]
        new_categories = _normalize_tags(self._table.item(item.row(), 1).text())

        record.categories = new_categories
        self._store.set(record.path, new_categories)
        self._store.save()

        self._apply_filters()

    def _on_apply_bulk_to_selected(self) -> None:
        selected_rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if not selected_rows:
            self._status.showMessage("Select one or more rows first.")
            return

        categories = _normalize_tags(self._bulk_category_edit.text())
        mode_data = self._bulk_mode_combo.currentData()
        mode = str(mode_data) if mode_data is not None else "replace"

        if mode in {"append", "remove"} and not categories:
            verb = "append" if mode == "append" else "remove"
            self._status.showMessage(f"Enter one or more tags to {verb}.")
            return

        updated = 0
        for row in selected_rows:
            if row < 0 or row >= len(self._visible_indices):
                continue
            record_index = self._visible_indices[row]
            record = self._records[record_index]

            if mode == "append":
                new_categories = _merge_tags(record.categories, categories)
            elif mode == "remove":
                new_categories = _remove_tags(record.categories, categories)
            else:
                new_categories = list(categories)

            record.categories = new_categories
            self._store.set(record.path, new_categories)
            updated += 1

        self._store.save()
        self._apply_filters()
        action_text = {
            "append": "Appended",
            "remove": "Removed",
            "replace": "Updated",
        }.get(mode, "Updated")
        self._status.showMessage(f"{action_text} tags for {updated} jingle(s).")

    def _selected_record(self) -> JingleRecord | None:
        selected = self._table.selectedRanges()
        if not selected:
            return None
        row = selected[0].topRow()
        if row < 0 or row >= len(self._visible_indices):
            return None
        return self._records[self._visible_indices[row]]

    def _on_table_context_menu_requested(self, pos: Any) -> None:
        row = self._table.rowAt(pos.y())
        if row >= 0:
            clicked_item = self._table.item(row, 0)
            if clicked_item is not None and not clicked_item.isSelected():
                self._table.selectRow(row)

        selected_count = len(self._selected_record_indices())

        menu = QMenu(self)
        edit_jingle_action = menu.addAction("Edit Jingle")
        edit_jingle_action.setEnabled(selected_count == 1)
        edit_jingle_action.triggered.connect(self._on_edit_jingle)

        menu.addSeparator()

        # --- Sample Pad assignment ---
        pads_window = self._ensure_sample_pads_window()
        send_to_pad_menu = menu.addMenu("Send to Sample Pad")
        for board_index in range(pads_window.board_count):
            board_menu = send_to_pad_menu.addMenu(f"Board {board_index + 1}")
            for i, pad in enumerate(pads_window.board_pads(board_index)):
                if pad.jingle and 'path' in pad.jingle:
                    stem = Path(pad.jingle['path']).stem
                    label = f"Pad {i + 1} \u2014 {stem}"
                else:
                    label = f"Pad {i + 1}"
                pad_menu = board_menu.addMenu(label)

                default_action = pad_menu.addAction("Default Profile")
                default_action.setEnabled(selected_count == 1)
                default_action.triggered.connect(
                    lambda checked, b_idx=board_index, p_idx=i: self._assign_selected_to_sample_pad(
                        p_idx,
                        board_index=b_idx,
                        profile_index=None,
                    )
                )

                selected_indices = self._selected_record_indices()
                selected_record = (
                    self._records[selected_indices[0]]
                    if selected_count == 1 and selected_indices
                    else None
                )
                profile_count = 0
                if selected_record is not None:
                    clip_profiles, _active_profile_index = self._store.get_clip_profiles(
                        selected_record.path,
                        selected_record.duration_seconds,
                    )
                    profile_count = len(clip_profiles)

                pad_menu.addSeparator()
                for profile_index in range(4):
                    profile_label = f"Profile P{profile_index + 1}"
                    if selected_count == 1 and profile_index < profile_count and selected_record is not None:
                        start_seconds, stop_seconds = clip_profiles[profile_index]
                        profile_label = (
                            f"Profile P{profile_index + 1} "
                            f"({start_seconds:.2f}s - {stop_seconds:.2f}s)"
                        )
                    profile_action = pad_menu.addAction(profile_label)
                    profile_action.setEnabled(selected_count == 1 and profile_index < profile_count)
                    profile_action.triggered.connect(
                        lambda checked, b_idx=board_index, p_idx=i, prof_idx=profile_index: self._assign_selected_to_sample_pad(
                            p_idx,
                            board_index=b_idx,
                            profile_index=prof_idx,
                        )
                    )

        rename_action = menu.addAction("Rename")
        rename_action.setEnabled(selected_count == 1)
        rename_action.triggered.connect(self._on_edit_rename)

        delete_action = menu.addAction("Delete")
        delete_action.setEnabled(selected_count > 0)
        delete_action.triggered.connect(self._on_edit_delete)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _assign_selected_to_sample_pad(
        self,
        pad_index: int,
        board_index: int | None = None,
        profile_index: int | None = None,
    ):
        # Assign the first selected jingle to the given pad index
        selected_indices = self._selected_record_indices()
        if not selected_indices or self._sample_pads_window is None:
            return
        record_index = selected_indices[0]
        record = self._records[record_index]

        clip_profiles, active_profile_index = self._store.get_clip_profiles(
            record.path,
            record.duration_seconds,
        )

        target_profile_index = active_profile_index
        if isinstance(profile_index, int) and clip_profiles:
            target_profile_index = max(0, min(profile_index, len(clip_profiles) - 1))

        active_start, active_stop = clip_profiles[target_profile_index]
        record.clip_profiles = clip_profiles
        record.active_clip_profile_index = active_profile_index
        record.clip_start_seconds, record.clip_stop_seconds = clip_profiles[active_profile_index]

        # Include the active profile clip points so pad assignments can pin
        # different profile windows for the same source file.
        jingle_data = {
            'name': record.name,
            'path': str(record.path),
            'clip_start_seconds': active_start,
            'clip_stop_seconds': active_stop,
            'clip_profile_index': target_profile_index,
        }
        if board_index is None:
            self._sample_pads_window.assign_jingle_to_pad(pad_index, jingle_data)
        else:
            self._sample_pads_window.assign_jingle_to_board_pad(board_index, pad_index, jingle_data)
        # Preload the newly assigned audio so the first trigger is instant.
        self._sp_engine_preload_all_pads()

    def _on_table_item_double_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        self._table.selectRow(item.row())
        if (
            self._player is not None
            and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        ):
            self._player.stop()
            self._reset_continuous_queue()
            self._current_playing_name = ""
        self._on_play_clicked()

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

    def _reset_clip_playback_window(self) -> None:
        self._current_clip_start_ms = 0
        self._current_clip_stop_ms = -1
        self._clip_start_seek_pending = False
        self._clip_seek_muted_temporarily = False
        self._clip_boundary_handling = False

    def _prepare_clip_start_seek(self, temporary_mute_for_seek: bool) -> int:
        start_ms = max(0, int(self._current_clip_start_ms))
        self._clip_boundary_handling = False
        self._clip_start_seek_pending = start_ms > 0
        if self._audio_output is not None:
            # Also pre-mute when start_ms == 0: rapid sample retriggers can still
            # produce audible discontinuities when the media source is replaced.
            if temporary_mute_for_seek and not self._is_muted:
                # Ensure _on_position_changed clears temporary mute on first
                # position callback even when no explicit seek is needed.
                if not self._clip_start_seek_pending:
                    self._clip_start_seek_pending = True
                self._clip_seek_muted_temporarily = True
                self._audio_output.setMuted(True)
            else:
                self._clip_seek_muted_temporarily = False
                self._audio_output.setMuted(self._is_muted)
        return start_ms

    def _restart_current_clip_from_start(self, temporary_mute_for_seek: bool) -> None:
        if self._player is None:
            return
        start_ms = self._prepare_clip_start_seek(temporary_mute_for_seek)
        self._player.setPosition(start_ms)
        self._player.play()

    def _clip_window_for_record(self, record: JingleRecord) -> tuple[int, int]:
        duration_ms = max(0, int(round(record.duration_seconds * 1000.0)))
        start_ms = max(0, int(round(record.clip_start_seconds * 1000.0)))
        stop_ms = max(0, int(round(record.clip_stop_seconds * 1000.0)))

        if duration_ms > 0:
            start_ms = min(start_ms, duration_ms)
            stop_ms = min(stop_ms, duration_ms)
            if stop_ms <= start_ms:
                start_ms = 0
                stop_ms = duration_ms

        # For the default full-file window, let the backend reach EndOfMedia
        # naturally. Enforcing the stop position manually can cut off very short
        # clips because the media clock reaches the end before buffered audio has
        # fully drained to the output device.
        if duration_ms > 0 and start_ms == 0 and stop_ms >= duration_ms:
            return 0, -1

        return start_ms, stop_ms

    def _play_record(self, record_index: int) -> bool:
        if self._player is None:
            return False
        if record_index < 0 or record_index >= len(self._records):
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
        self._current_playing_name = record.path.name
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

    def _play_next_continuous_record(self) -> bool:
        if self._player is None:
            return False

        next_position = self._continuous_queue_position + 1
        while next_position < len(self._continuous_queue):
            record_index = self._continuous_queue[next_position]
            self._continuous_queue_position = next_position
            if self._play_record(record_index):
                return True
            next_position += 1

        self._reset_continuous_queue()
        return False

    def _on_play_clicked(self) -> None:
        if self._player is None:
            self._status.showMessage("Playback unavailable: PyQt6 multimedia is not installed.")
            return

        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self._status.showMessage("Playback paused.")
            return

        if self._player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
            self._player.play()
            self._status.showMessage("Playback resumed.")
            return

        selected_record_index = self._selected_record_index()
        if selected_record_index is None:
            self._status.showMessage("Select a jingle first.")
            return

        if self._playback_mode == "continuous":
            if not self._start_continuous_playback():
                self._current_playing_name = ""
                self._status.showMessage("No playable jingles were found from the selected row onward.")
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
        if self._player is None:
            self._status.showMessage("Playback unavailable: PyQt6 multimedia is not installed.")
            return

        if self._player.playbackState() in (
            QMediaPlayer.PlaybackState.PlayingState,
            QMediaPlayer.PlaybackState.PausedState,
        ):
            self._player.stop()
            self._player.setPosition(self._current_clip_start_ms)
            self._reset_continuous_queue()
            self._reset_clip_playback_window()
            self._sample_pad_looping = False
            self._sample_pad_release_looping = False
            self._sample_pad_native_looping = False
            self._current_sample_pad_index = -1
            if self._audio_output is not None:
                self._audio_output.setMuted(self._is_muted)
            self._current_playing_name = ""
            self._status.showMessage("Playback stopped.")

    def _on_table_selection_changed(self) -> None:
        record = self._selected_record()
        if record is not None:
            self._bulk_category_edit.setText(_tags_to_text(record.categories))
        else:
            self._bulk_category_edit.clear()
        self._refresh_status_summary()

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
                    self._clip_seek_muted_temporarily = False
                    if self._audio_output is not None:
                        self._audio_output.setMuted(self._is_muted)

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
                self._clip_boundary_handling = False
                if ended_name:
                    self._status.showMessage(f"Playback finished: {ended_name}")
                else:
                    self._status.showMessage("Playback finished.")
                return

        if not self._slider_pressed:
            self._position_slider.setValue(position_ms)
        duration = self._player.duration() if self._player is not None else 0
        self._update_time_label(position_ms, duration)

    def _on_playback_state_changed(self, _state: Any) -> None:
        if self._player is None:
            return
        is_playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self._set_play_button_state(is_playing)
        # Only reset slider when stopped, not when paused
        is_stopped = self._player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
        if is_stopped:
            self._position_slider.setValue(0)
            if self._clip_seek_muted_temporarily:
                self._clip_seek_muted_temporarily = False
                if self._audio_output is not None:
                    self._audio_output.setMuted(self._is_muted)
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
            self._current_playing_name = ""
            if ended_name:
                self._status.showMessage(f"Playback finished: {ended_name}")
            else:
                self._status.showMessage("Playback finished.")

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
            and self._player is not None
            and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and not self._continuous_queue
        ):
            current_index = self._selected_record_index()
            if current_index is not None:
                start_row = self._visible_row_for_record_index(current_index)
                if start_row >= 0:
                    self._continuous_queue = list(self._visible_indices[start_row:])
                    # Position 0 is the currently playing track; next advance starts at 1.
                    self._continuous_queue_position = 0

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
        else:
            # Manual loop control keeps clip offsets consistent across backends.
            self._player.setLoops(1)

    def _set_loop_breathing(self, enabled: bool) -> None:
        if not enabled:
            if self._loop_breath_anim is not None:
                self._loop_breath_anim.stop()
            if self._loop_breath_effect is not None:
                try:
                    self._loop_breath_effect.setOpacity(1.0)
                except RuntimeError:
                    pass
            self._loop_btn.setGraphicsEffect(None)
            self._loop_breath_anim = None
            self._loop_breath_effect = None
            return

        if self._loop_breath_effect is None:
            self._loop_breath_effect = QGraphicsOpacityEffect(self._loop_btn)
        try:
            self._loop_btn.setGraphicsEffect(self._loop_breath_effect)
        except RuntimeError:
            self._loop_breath_effect = QGraphicsOpacityEffect(self._loop_btn)
            self._loop_btn.setGraphicsEffect(self._loop_breath_effect)
            self._loop_breath_anim = None

        if self._loop_breath_anim is None:
            self._loop_breath_anim = QPropertyAnimation(
                self._loop_breath_effect,
                b"opacity",
                self,
            )
            self._loop_breath_anim.setDuration(1100)
            self._loop_breath_anim.setStartValue(1.0)
            self._loop_breath_anim.setKeyValueAt(0.5, 0.72)
            self._loop_breath_anim.setEndValue(1.0)
            self._loop_breath_anim.setLoopCount(-1)
            self._loop_breath_anim.setEasingCurve(QEasingCurve.Type.InOutSine)

        self._loop_breath_anim.start()

    def _set_play_button_state(self, is_playing: bool) -> None:
        if is_playing:
            self._play_btn.setText("Pause")
            self._play_btn.setStyleSheet(
                "QPushButton { background-color: #f57c00; color: white; font-weight: bold; }"
                "QPushButton:hover { background-color: #e65100; }"
            )
            self._set_play_stop_breathing(self._play_btn.isEnabled())
        else:
            self._play_btn.setText("Play Selected")
            self._play_btn.setStyleSheet(
                "QPushButton { background-color: #2e7d32; color: white; font-weight: bold; }"
                "QPushButton:hover { background-color: #388e3c; }"
            )
            self._set_play_stop_breathing(False)

    def _set_play_stop_breathing(self, enabled: bool) -> None:
        if not enabled:
            if self._play_stop_breath_anim is not None:
                self._play_stop_breath_anim.stop()
            if self._play_stop_breath_effect is not None:
                try:
                    self._play_stop_breath_effect.setOpacity(1.0)
                except RuntimeError:
                    pass
            self._play_btn.setGraphicsEffect(None)
            self._play_stop_breath_anim = None
            self._play_stop_breath_effect = None
            return

        if self._play_stop_breath_effect is None:
            self._play_stop_breath_effect = QGraphicsOpacityEffect(self._play_btn)
        try:
            self._play_btn.setGraphicsEffect(self._play_stop_breath_effect)
        except RuntimeError:
            self._play_stop_breath_effect = QGraphicsOpacityEffect(self._play_btn)
            self._play_btn.setGraphicsEffect(self._play_stop_breath_effect)
            self._play_stop_breath_anim = None

        if self._play_stop_breath_anim is None:
            self._play_stop_breath_anim = QPropertyAnimation(
                self._play_stop_breath_effect,
                b"opacity",
                self,
            )
            self._play_stop_breath_anim.setDuration(1000)
            self._play_stop_breath_anim.setStartValue(1.0)
            self._play_stop_breath_anim.setKeyValueAt(0.5, 0.68)
            self._play_stop_breath_anim.setEndValue(1.0)
            self._play_stop_breath_anim.setLoopCount(-1)
            self._play_stop_breath_anim.setEasingCurve(QEasingCurve.Type.InOutSine)

        self._play_stop_breath_anim.start()

    def _set_stop_button_breathing(self, enabled: bool) -> None:
        if not enabled:
            if self._stop_btn_breath_anim is not None:
                self._stop_btn_breath_anim.stop()
            if self._stop_btn_breath_effect is not None:
                try:
                    self._stop_btn_breath_effect.setOpacity(1.0)
                except RuntimeError:
                    pass
            self._stop_btn.setGraphicsEffect(None)
            self._stop_btn_breath_anim = None
            self._stop_btn_breath_effect = None
            return

        if self._stop_btn_breath_effect is None:
            self._stop_btn_breath_effect = QGraphicsOpacityEffect(self._stop_btn)
        try:
            self._stop_btn.setGraphicsEffect(self._stop_btn_breath_effect)
        except RuntimeError:
            self._stop_btn_breath_effect = QGraphicsOpacityEffect(self._stop_btn)
            self._stop_btn.setGraphicsEffect(self._stop_btn_breath_effect)
            self._stop_btn_breath_anim = None

        if self._stop_btn_breath_anim is None:
            self._stop_btn_breath_anim = QPropertyAnimation(
                self._stop_btn_breath_effect,
                b"opacity",
                self,
            )
            self._stop_btn_breath_anim.setDuration(1000)
            self._stop_btn_breath_anim.setStartValue(1.0)
            self._stop_btn_breath_anim.setKeyValueAt(0.5, 0.68)
            self._stop_btn_breath_anim.setEndValue(1.0)
            self._stop_btn_breath_anim.setLoopCount(-1)
            self._stop_btn_breath_anim.setEasingCurve(QEasingCurve.Type.InOutSine)

        self._stop_btn_breath_anim.start()

    def _on_slider_pressed(self) -> None:
        self._slider_pressed = True

    def _on_slider_released(self) -> None:
        self._slider_pressed = False
        if self._player is not None:
            self._player.setPosition(int(self._position_slider.value()))

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
            self._live_volume_percent,
            self._preview_volume_percent,
            self._sample_pad_blocksize,
            self._sample_pad_streaming_min_seconds,
            self._samples_dir,
            self,
        )
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return

        self._output_device, self._preview_output_device = dialog.selected_devices()
        self._live_volume_percent, self._preview_volume_percent = dialog.selected_volumes()
        self._sample_pad_blocksize = dialog.selected_sample_pad_blocksize()
        self._sample_pad_streaming_min_seconds = (
            dialog.selected_sample_pad_streaming_min_seconds()
        )
        self._settings.setValue("options/outputDevice", self._output_device)
        self._settings.setValue("options/previewOutputDevice", self._preview_output_device)
        self._settings.setValue("options/samplePadBlocksize", self._sample_pad_blocksize)
        self._settings.setValue(
            "options/samplePadStreamingMinSeconds",
            self._sample_pad_streaming_min_seconds,
        )
        self._sp_engine.set_streaming_min_seconds(self._sample_pad_streaming_min_seconds)
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
        self._status.showMessage("Options saved.")

    def _apply_output_device(self) -> None:
        if self._audio_output is None or not _has_qt_multimedia:
            return

        selected = self._active_output_device().strip()
        target_device = QMediaDevices.defaultAudioOutput()

        if selected:
            matched = None
            for device in QMediaDevices.audioOutputs():
                if device.description().strip() == selected:
                    matched = device
                    break
            if matched is not None:
                target_device = matched
            else:
                self._status.showMessage(
                    f"Selected device unavailable. Using system default: {target_device.description()}"
                )

        # Only switch the device when it actually changes; calling setDevice()
        # unnecessarily flushes/resets the audio pipeline mid-stream and can
        # produce clicks or pops even when the same device is re-selected.
        if self._audio_output.device().id() != target_device.id():
            self._audio_output.setDevice(target_device)
        # Respect any temporary seek-mute that is still active.  A back-to-back
        # one-shot retrigger calls _apply_output_device before the first
        # position callback has cleared _clip_seek_muted_temporarily, so
        # blindly applying self._is_muted would briefly unmute the output while
        # the new media source is being swapped in, causing an audible pop.
        effective_muted = self._is_muted or self._clip_seek_muted_temporarily
        self._audio_output.setMuted(effective_muted)
        self._apply_active_volume()

    def _normalize_device_key(self, value: str) -> str:
        return value.strip().casefold()

    def _can_use_preview_mode(self) -> bool:
        return self._normalize_device_key(self._output_device) != self._normalize_device_key(
            self._preview_output_device
        )

    def _active_output_device(self) -> str:
        if self._is_preview_mode and self._can_use_preview_mode():
            return self._preview_output_device
        return self._output_device

    def _refresh_mode_toggle_state(self, notify_if_disabled: bool) -> None:
        can_use = self._can_use_preview_mode()
        if not can_use and self._is_preview_mode:
            self._is_preview_mode = False
            self._mode_btn.blockSignals(True)
            self._mode_btn.setChecked(False)
            self._mode_btn.blockSignals(False)

        self._mode_btn.setEnabled(can_use)
        if can_use:
            self._mode_btn.setToolTip("Toggle between Live and Preview output devices")
        else:
            self._mode_btn.setToolTip(
                "Preview/Live switch is disabled because Live and Preview devices are the same"
            )

        self._set_mode_button_visual()
        self._refresh_volume_controls()

        if notify_if_disabled and not can_use:
            self._status.showMessage(
                "Preview/Live switch disabled: set Preview device to a different output in Options."
            )

    def _set_mode_button_visual(self) -> None:
        if self._is_preview_mode:
            self._mode_btn.setText("Mode: Preview")
            self._mode_btn.setStyleSheet(
                "QPushButton { background-color: #1565c0; color: white; font-weight: bold; }"
                "QPushButton:hover { background-color: #1976d2; }"
            )
            self._set_mode_live_breathing(False)
        else:
            self._mode_btn.setText("Mode: Live")
            self._mode_btn.setStyleSheet(
                "QPushButton { background-color: #b71c1c; color: white; font-weight: bold; }"
                "QPushButton:hover { background-color: #c62828; }"
            )
            self._set_mode_live_breathing(self._mode_btn.isEnabled())

    def _set_mode_live_breathing(self, enabled: bool) -> None:
        if not enabled:
            if self._mode_live_breath_anim is not None:
                self._mode_live_breath_anim.stop()
            if self._mode_live_breath_effect is not None:
                try:
                    self._mode_live_breath_effect.setOpacity(1.0)
                except RuntimeError:
                    pass
            self._mode_btn.setGraphicsEffect(None)
            # Qt may delete the installed effect when detached; reset cached refs.
            self._mode_live_breath_anim = None
            self._mode_live_breath_effect = None
            return

        if self._mode_live_breath_effect is None:
            self._mode_live_breath_effect = QGraphicsOpacityEffect(self._mode_btn)
        try:
            self._mode_btn.setGraphicsEffect(self._mode_live_breath_effect)
        except RuntimeError:
            self._mode_live_breath_effect = QGraphicsOpacityEffect(self._mode_btn)
            self._mode_btn.setGraphicsEffect(self._mode_live_breath_effect)
            self._mode_live_breath_anim = None

        if self._mode_live_breath_anim is None:
            self._mode_live_breath_anim = QPropertyAnimation(
                self._mode_live_breath_effect,
                b"opacity",
                self,
            )
            self._mode_live_breath_anim.setDuration(1300)
            self._mode_live_breath_anim.setStartValue(1.0)
            self._mode_live_breath_anim.setKeyValueAt(0.5, 0.72)
            self._mode_live_breath_anim.setEndValue(1.0)
            self._mode_live_breath_anim.setLoopCount(-1)
            self._mode_live_breath_anim.setEasingCurve(QEasingCurve.Type.InOutSine)

        self._mode_live_breath_anim.start()

    def _on_mode_toggled(self, checked: bool) -> None:
        if checked and not self._can_use_preview_mode():
            self._mode_btn.blockSignals(True)
            self._mode_btn.setChecked(False)
            self._mode_btn.blockSignals(False)
            self._is_preview_mode = False
            self._set_mode_button_visual()
            self._refresh_volume_controls()
            return

        self._is_preview_mode = bool(checked)
        self._set_mode_button_visual()
        self._refresh_volume_controls()
        self._apply_output_device()
        if self._sample_pads_window is not None:
            self._sample_pads_window.refresh_mode_volume_controls()

    def _on_browse_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose Samples Folder",
            str(self._samples_dir) if self._samples_dir else str(Path.home()),
        )
        if not selected:
            return

        path = Path(selected)
        if not path.exists() or not path.is_dir():
            QMessageBox.warning(self, "Invalid Folder", "Please choose a valid folder.")
            return

        self._samples_dir = path
        self._save_samples_dir()
        self._rescan_library()


def main() -> None:
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
