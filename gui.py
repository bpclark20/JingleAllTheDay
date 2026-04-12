#!/usr/bin/env python3
"""Jingle browser GUI with category filters and playback device selection."""

from __future__ import annotations

import sys
import ctypes
from pathlib import Path
from typing import Any

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
from dialogs import AboutDialog, OptionsDialog
from mainwindow_file_edit_mixin import MainWindowFileEditMixin
from mainwindow_library_mixin import MainWindowLibraryMixin
from mainwindow_menu_mixin import MainWindowMenuMixin
from mainwindow_shortcuts_mixin import MainWindowShortcutsMixin
from mainwindow_tools_mixin import MainWindowToolsMixin
from models_store import JingleRecord, LibraryStore
from widgets import DeselectableTableWidget


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

ORG_NAME = "JingleAllTheDay"
APP_NAME = "JingleAllTheDay"
APP_VERSION = "1.1.1.041226"

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
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("JingleAllTheDay")
        self.resize(1200, 740)
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
        self._samples_dir: Path | None = self._load_samples_dir()
        self._auto_folder_tags: bool = self._load_auto_folder_tags()
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

        # Defer the initial scan so the window can render immediately.
        QTimer.singleShot(0, self._maybe_run_first_time_setup)

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

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is None:
            super().keyPressEvent(event)
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
            app_name=APP_NAME,
            app_version=APP_VERSION,
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
        rename_action = menu.addAction("Rename")
        rename_action.setEnabled(selected_count == 1)
        rename_action.triggered.connect(self._on_edit_rename)

        delete_action = menu.addAction("Delete")
        delete_action.setEnabled(selected_count > 0)
        delete_action.triggered.connect(self._on_edit_delete)

        menu.exec(self._table.viewport().mapToGlobal(pos))

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

    def _play_record(self, record_index: int) -> bool:
        if self._player is None:
            return False
        if record_index < 0 or record_index >= len(self._records):
            return False

        record = self._records[record_index]
        if not record.path.exists():
            return False

        self._apply_output_device()
        self._apply_player_loop_mode()
        self._player.setSource(QUrl.fromLocalFile(str(record.path)))
        self._player.play()
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
            self._player.setPosition(0)
            self._reset_continuous_queue()
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
            ended_name = self._current_playing_name
            if self._play_next_continuous_record():
                return
            self._reset_continuous_queue()
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

    def _apply_player_loop_mode(self) -> None:
        if self._player is None:
            return
        # Native backend looping avoids manual restart gaps between iterations.
        self._player.setLoops(-1 if self._playback_mode == "loop" else 1)

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
            self._samples_dir,
            self,
        )
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return

        self._output_device, self._preview_output_device = dialog.selected_devices()
        self._live_volume_percent, self._preview_volume_percent = dialog.selected_volumes()
        self._settings.setValue("options/outputDevice", self._output_device)
        self._settings.setValue("options/previewOutputDevice", self._preview_output_device)
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

        self._audio_output.setDevice(target_device)
        self._audio_output.setMuted(self._is_muted)
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
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("JingleAllTheDay.App")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon_path = _HERE / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    _apply_windows_taskbar_icon(window)
    window.show()
    try:
        exit_code = app.exec()
    except KeyboardInterrupt:
        # Gracefully handle Ctrl+C / debugger stop without noisy tracebacks.
        exit_code = 130
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
