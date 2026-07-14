"""Sequencer window for timeline-based audio sequencing."""

from __future__ import annotations

import json
import time
import wave
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QLineEdit,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QMessageBox,
    QDialog,
    QInputDialog,
    QSplitter,
    QScrollArea,
    QMenu,
)
from PyQt6.QtCore import Qt as QtCore

from sequencer_model import Sequence, SequenceTrack, TriggerEvent, SequenceStore
from sequencer_engine import SequencerEngine, PlaybackState
from sequencer_widgets import TimelineRuler, TimelineCanvas
from waveform_cache import load_waveform_peaks


class SequencerWindow(QMainWindow):
    """Main sequencer/timeline editor window."""

    sequence_changed = pyqtSignal()  # Emitted when sequence is modified
    window_closed = pyqtSignal()  # Emitted when the window is closed

    def __init__(
        self,
        app_data_dir: Path,
        sample_pad_audio_engine: Any = None,
        ensure_audio_ready: Callable[[bool], bool] | None = None,
        can_use_preview_mode: Callable[[], bool] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sequencer")
        self.setMinimumSize(1000, 600)

        self._app_data_dir = Path(app_data_dir)
        self._sample_pad_engine = sample_pad_audio_engine
        self._ensure_audio_ready = ensure_audio_ready
        self._can_use_preview_mode = can_use_preview_mode
        self._sequencer_is_live_mode = True
        self._waveform_cache_dir = self._app_data_dir / "waveform-cache"
        self._source_duration_cache: dict[str, float | None] = {}
        self._sequence_store = SequenceStore(self._app_data_dir / "sequences")
        self._recent_sequences_path = self._app_data_dir / "recent_sequences.json"
        self._recent_sequences: list[str] = self._load_recent_sequences()
        self._recent_sequences_menu: QMenu | None = None
        self._current_sequence = Sequence("Untitled Sequence")
        self._current_sequence_path: Path | None = None
        self._has_unsaved_changes = False
        self._suppress_dirty_tracking = False
        self._allow_hard_close = False
        self._engine = SequencerEngine(sample_pad_audio_engine)
        self._engine.set_sequence(self._current_sequence)
        self._mixer_dialog = None
        self._selected_track_index = -1
        self._selected_trigger_track_index = -1
        self._selected_trigger_index = -1
        self._updating_track_properties = False
        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._on_update_metrics)
        self._update_timer.start(100)  # Update every 100ms

        self._build_ui()
        self._engine.set_metrics_callback(self._on_playback_metrics)
        self.sequence_changed.connect(self._on_sequence_changed)
        self._update_window_title()

        self._delete_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self)
        self._delete_shortcut.activated.connect(self._on_delete_selected_trigger)
        self._save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self._save_shortcut.activated.connect(self._on_save_sequence)
        self._save_as_shortcut = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        self._save_as_shortcut.activated.connect(self._on_save_sequence_as)
        self._load_shortcut = QShortcut(QKeySequence("Ctrl+O"), self)
        self._load_shortcut.activated.connect(self._on_load_sequence)
        self._new_shortcut = QShortcut(QKeySequence("Ctrl+N"), self)
        self._new_shortcut.activated.connect(self._on_new_sequence)

    def _build_ui(self) -> None:
        """Build the UI."""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        file_menu = self.menuBar().addMenu("File")
        new_action = QAction("New", self)
        new_action.triggered.connect(self._on_new_sequence)
        file_menu.addAction(new_action)

        open_action = QAction("Open...", self)
        open_action.triggered.connect(self._on_load_sequence)
        file_menu.addAction(open_action)

        self._recent_sequences_menu = file_menu.addMenu("Recent Sequences")
        self._refresh_recent_sequences_menu()

        file_menu.addSeparator()

        save_action = QAction("Save", self)
        save_action.triggered.connect(self._on_save_sequence)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save As...", self)
        save_as_action.triggered.connect(self._on_save_sequence_as)
        file_menu.addAction(save_as_action)

        # --- Toolbar ---
        toolbar = QHBoxLayout()

        # Sequence file actions
        new_btn = QPushButton("New")
        new_btn.clicked.connect(self._on_new_sequence)
        toolbar.addWidget(new_btn)

        load_btn = QPushButton("Load")
        load_btn.clicked.connect(self._on_load_sequence)
        toolbar.addWidget(load_btn)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._on_save_sequence)
        toolbar.addWidget(save_btn)

        save_as_btn = QPushButton("Save As")
        save_as_btn.clicked.connect(self._on_save_sequence_as)
        toolbar.addWidget(save_as_btn)

        toolbar.addSpacing(20)

        # BPM control
        toolbar.addWidget(QLabel("BPM:"))
        self._bpm_spin = QDoubleSpinBox()
        self._bpm_spin.setRange(1.0, 300.0)
        self._bpm_spin.setValue(self._engine.get_bpm())
        self._bpm_spin.setSingleStep(1.0)
        self._bpm_spin.setMaximumWidth(80)
        self._bpm_spin.valueChanged.connect(self._on_bpm_changed)
        toolbar.addWidget(self._bpm_spin)

        toolbar.addSpacing(20)

        # Playback controls
        self._play_btn = QPushButton("Play")
        self._play_btn.clicked.connect(self._on_play)
        toolbar.addWidget(self._play_btn)

        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setEnabled(False)
        self._pause_btn.clicked.connect(self._on_pause)
        toolbar.addWidget(self._pause_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(self._on_stop)
        toolbar.addWidget(self._stop_btn)

        toolbar.addSpacing(20)

        # Metronome
        self._metronome_check = QCheckBox("Metronome")
        self._metronome_check.setChecked(False)
        self._metronome_check.toggled.connect(self._on_metronome_toggled)
        toolbar.addWidget(self._metronome_check)

        mixer_btn = QPushButton("Mixer...")
        mixer_btn.clicked.connect(self._on_open_mixer)
        toolbar.addWidget(mixer_btn)

        toolbar.addSpacing(12)

        # Sequencer output mode (Live/Preview)
        self._mode_btn = QPushButton("Mode: Live")
        self._mode_btn.setCheckable(True)
        self._mode_btn.clicked.connect(self._on_mode_toggled)
        toolbar.addWidget(self._mode_btn)
        self._refresh_mode_toggle_state()

        toolbar.addSpacing(20)

        # Zoom
        toolbar.addWidget(QLabel("Zoom:"))
        self._zoom_spin = QSpinBox()
        self._zoom_spin.setRange(10, 200)
        self._zoom_spin.setValue(40)
        self._zoom_spin.setSuffix(" px/beat")
        self._zoom_spin.setMaximumWidth(100)
        self._zoom_spin.valueChanged.connect(self._on_zoom_changed)
        toolbar.addWidget(self._zoom_spin)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # --- Timeline display mode toggle ---
        display_row = QHBoxLayout()
        display_row.addStretch()
        self._time_display_label = QLabel("Beats")
        self._time_display_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._time_display_label.setStyleSheet("color: palette(link); text-decoration: underline;")
        self._time_display_label.setMaximumWidth(80)
        display_row.addWidget(self._time_display_label)
        display_row.addSpacing(10)
        self._time_format_beats = True
        layout.addLayout(display_row)

        # --- Main content: Timeline + Track list ---
        content = QSplitter(Qt.Orientation.Horizontal)

        # Timeline area
        timeline_widget = QWidget()
        timeline_layout = QVBoxLayout(timeline_widget)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_layout.setSpacing(0)

        # Create ruler and canvas
        self._ruler = TimelineRuler()
        self._ruler.set_bpm(self._engine.get_bpm())
        self._ruler.set_pixels_per_beat(40)
        self._ruler.beat_clicked.connect(self._on_ruler_clicked)

        self._timeline_canvas = TimelineCanvas()
        self._timeline_canvas.set_ruler(self._ruler)
        self._timeline_canvas.set_bpm(self._engine.get_bpm())
        self._timeline_canvas.set_zoom(40)
        self._timeline_canvas.trigger_selected.connect(self._on_timeline_trigger_selected)
        self._timeline_canvas.trigger_double_clicked.connect(self._on_timeline_trigger_double_clicked)
        self._timeline_canvas.empty_clicked.connect(self._on_timeline_empty_clicked)
        self._timeline_canvas.trigger_moved.connect(self._on_timeline_trigger_moved)
        self._timeline_canvas.trigger_resized.connect(self._on_timeline_trigger_resized)
        self._timeline_canvas.trigger_range_changed.connect(
            self._on_timeline_trigger_range_changed
        )
        self._timeline_canvas.trigger_edit_finished.connect(self._on_timeline_trigger_edit_finished)

        timeline_scroll = QScrollArea()
        timeline_scroll.setWidget(self._timeline_canvas)
        timeline_scroll.setWidgetResizable(True)

        timeline_layout.addWidget(timeline_scroll)
        content.addWidget(timeline_widget)

        # Track list panel
        track_panel = QWidget()
        track_layout = QVBoxLayout(track_panel)

        track_layout.addWidget(QLabel("Tracks"))

        self._track_list = QListWidget()
        self._track_list.itemSelectionChanged.connect(self._on_track_selected)
        self._track_list.itemDoubleClicked.connect(self._on_track_item_double_clicked)
        track_layout.addWidget(self._track_list)

        properties_group = QGroupBox("Track Properties")
        properties_layout = QFormLayout(properties_group)

        self._track_name_edit = QLineEdit()
        self._track_name_edit.editingFinished.connect(self._on_track_name_edited)
        properties_layout.addRow("Name", self._track_name_edit)

        source_container = QWidget()
        source_layout = QHBoxLayout(source_container)
        source_layout.setContentsMargins(0, 0, 0, 0)
        self._track_source_edit = QLineEdit()
        self._track_source_edit.setReadOnly(True)
        self._track_source_edit.setPlaceholderText("No source assigned")
        source_layout.addWidget(self._track_source_edit, 1)

        source_browse_btn = QPushButton("Browse...")
        source_browse_btn.clicked.connect(self._on_set_track_source)
        source_layout.addWidget(source_browse_btn)

        source_clear_btn = QPushButton("Clear")
        source_clear_btn.clicked.connect(self._on_clear_track_source)
        source_layout.addWidget(source_clear_btn)
        properties_layout.addRow("Source", source_container)

        self._track_volume_spin = QSpinBox()
        self._track_volume_spin.setRange(0, 100)
        self._track_volume_spin.setSuffix("%")
        self._track_volume_spin.valueChanged.connect(self._on_track_volume_changed)
        properties_layout.addRow("Volume", self._track_volume_spin)

        self._track_pan_spin = QSpinBox()
        self._track_pan_spin.setRange(-100, 100)
        self._track_pan_spin.valueChanged.connect(self._on_track_pan_changed)
        properties_layout.addRow("Pan", self._track_pan_spin)

        toggle_container = QWidget()
        toggle_layout = QHBoxLayout(toggle_container)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        self._track_mute_check = QCheckBox("Mute")
        self._track_mute_check.toggled.connect(self._on_track_mute_toggled)
        toggle_layout.addWidget(self._track_mute_check)
        self._track_solo_check = QCheckBox("Solo")
        self._track_solo_check.toggled.connect(self._on_track_solo_toggled)
        toggle_layout.addWidget(self._track_solo_check)
        toggle_layout.addStretch()
        properties_layout.addRow("Flags", toggle_container)

        track_layout.addWidget(properties_group)

        # Track buttons
        button_row = QHBoxLayout()
        add_track_btn = QPushButton("Add Track")
        add_track_btn.clicked.connect(self._on_add_track)
        button_row.addWidget(add_track_btn)

        remove_track_btn = QPushButton("Remove")
        remove_track_btn.clicked.connect(self._on_remove_track)
        button_row.addWidget(remove_track_btn)

        remove_trigger_btn = QPushButton("Remove Trigger")
        remove_trigger_btn.clicked.connect(self._on_delete_selected_trigger)
        button_row.addWidget(remove_trigger_btn)
        track_layout.addLayout(button_row)

        source_row = QHBoxLayout()
        set_source_btn = QPushButton("Set Source...")
        set_source_btn.clicked.connect(self._on_set_track_source)
        source_row.addWidget(set_source_btn)

        clear_source_btn = QPushButton("Clear Source")
        clear_source_btn.clicked.connect(self._on_clear_track_source)
        source_row.addWidget(clear_source_btn)

        track_layout.addLayout(source_row)

        content.addWidget(track_panel)
        content.setSizes([700, 300])

        layout.addWidget(content, 1)

        # --- Status bar ---
        self._status_label = QLabel("Ready")
        layout.addWidget(self._status_label)

    def _on_bpm_changed(self, value: float) -> None:
        """BPM changed."""
        self._engine.set_bpm(value)
        self._current_sequence.bpm = value
        self._ruler.set_bpm(value)
        self._timeline_canvas.set_bpm(value)
        self._status_label.setText(f"BPM: {value:.1f}")
        self.sequence_changed.emit()

    def _on_play(self) -> None:
        """Start playback."""
        if self._ensure_audio_ready and not self._ensure_audio_ready(self._sequencer_is_live_mode):
            self._status_label.setText("Audio engine unavailable. Check output device.")
            return

        if not self._current_sequence.tracks:
            self._status_label.setText("No tracks in sequence")
            return

        self._engine.set_sequence(self._current_sequence)
        self._engine.play()
        self._play_btn.setEnabled(False)
        self._pause_btn.setEnabled(True)
        self._status_label.setText("Playing...")

    def _on_mode_toggled(self, checked: bool) -> None:
        """Toggle sequencer output routing between Live and Preview devices."""
        if checked and self._can_use_preview_mode and not self._can_use_preview_mode():
            self._mode_btn.blockSignals(True)
            self._mode_btn.setChecked(False)
            self._mode_btn.blockSignals(False)
            self._sequencer_is_live_mode = True
            self._refresh_mode_toggle_state()
            self._status_label.setText(
                "Preview disabled: set a different Preview output device in Options"
            )
            return

        self._sequencer_is_live_mode = not bool(checked)
        self._refresh_mode_toggle_state()

    def _refresh_mode_toggle_state(self) -> None:
        """Refresh mode toggle state and styling."""
        can_use_preview = self._can_use_preview_mode() if self._can_use_preview_mode else False
        self._mode_btn.setEnabled(can_use_preview)
        self._mode_btn.setToolTip(
            "Route sequencer audio to Preview output" if can_use_preview
            else "Preview output unavailable (same as Live device)"
        )

        if self._sequencer_is_live_mode:
            self._mode_btn.blockSignals(True)
            self._mode_btn.setChecked(False)
            self._mode_btn.blockSignals(False)
            self._mode_btn.setText("Mode: Live")
            self._mode_btn.setStyleSheet(
                "QPushButton { background-color: #b71c1c; color: white; font-weight: bold; }"
                "QPushButton:hover { background-color: #c62828; }"
            )
        else:
            self._mode_btn.blockSignals(True)
            self._mode_btn.setChecked(True)
            self._mode_btn.blockSignals(False)
            self._mode_btn.setText("Mode: Preview")
            self._mode_btn.setStyleSheet(
                "QPushButton { background-color: #1565c0; color: white; font-weight: bold; }"
                "QPushButton:hover { background-color: #1976d2; }"
            )

    def _on_pause(self) -> None:
        """Pause playback."""
        self._engine.pause()
        self._play_btn.setEnabled(True)
        self._pause_btn.setEnabled(False)
        self._status_label.setText("Paused")

    def _on_open_mixer(self) -> None:
        """Open sequencer mixer dialog."""
        dialog = self._ensure_mixer_dialog()
        dialog.refresh_from_sequence()
        dialog.fit_to_screen_if_possible()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def open_mixer(self) -> None:
        """Public helper for showing the mixer dialog."""
        self._on_open_mixer()

    def _ensure_mixer_dialog(self):
        """Create mixer dialog if needed."""
        if self._mixer_dialog is None:
            from sequencer_mixer_dialog import SequencerMixerDialog

            self._mixer_dialog = SequencerMixerDialog(
                self,
                meter_levels_provider=self._sequencer_track_meter_levels,
            )
            self._mixer_dialog.set_sequence(self._current_sequence)
            self._mixer_dialog.mixerChanged.connect(self._on_mixer_changed)
        return self._mixer_dialog

    def _sequencer_track_meter_levels(self) -> dict[int, float]:
        """Map engine pad meter levels to sequencer track indices."""
        if self._sample_pad_engine is None or not hasattr(self._sample_pad_engine, "meter_levels"):
            return {}
        try:
            raw_levels = self._sample_pad_engine.meter_levels()
        except Exception:
            return {}
        if not isinstance(raw_levels, dict):
            return {}

        mapped: dict[int, float] = {}
        offset = self._engine.sequencer_pad_offset()
        for track_index in range(len(self._current_sequence.tracks)):
            pad_index = offset + track_index
            level = raw_levels.get(pad_index, 0.0)
            mapped[track_index] = max(0.0, min(1.0, float(level)))
        return mapped

    def _on_mixer_changed(self) -> None:
        """Handle mixer strip changes for sequencer tracks."""
        self._engine.set_sequence(self._current_sequence)
        self.sequence_changed.emit()

    def _on_stop(self) -> None:
        """Stop playback."""
        self._engine.stop()
        self._play_btn.setEnabled(True)
        self._pause_btn.setEnabled(False)
        self._status_label.setText("Stopped")

    def _on_metronome_toggled(self, checked: bool) -> None:
        """Metronome toggled."""
        self._engine.set_metronome_enabled(checked)

    def _on_zoom_changed(self, value: int) -> None:
        """Zoom level changed."""
        self._timeline_canvas.set_zoom(float(value))
        self._ruler.set_pixels_per_beat(float(value))

    def _on_ruler_clicked(self, beat: float) -> None:
        """User clicked on ruler to seek."""
        self._engine.seek_to_beat(beat)

    def _on_update_metrics(self) -> None:
        """Update display based on engine metrics."""
        metrics = self._engine.get_metrics()
        self._timeline_canvas.set_current_beat(metrics.current_beat)

        # Update status
        if metrics.state == PlaybackState.PLAYING:
            seconds = metrics.current_seconds
            minutes, secs = divmod(int(seconds), 60)
            beat_display = (
                f"Beat {metrics.current_beat:.1f}" if self._time_format_beats 
                else f"{minutes}:{secs:02d}"
            )
            self._status_label.setText(beat_display)

    def _on_time_display_clicked(self) -> None:
        """Toggle between beat and time display."""
        self._time_format_beats = not self._time_format_beats
        self._ruler.set_display_mode(not self._time_format_beats)
        if self._time_format_beats:
            self._time_display_label.setText("Beats")
        else:
            self._time_display_label.setText("Time")

    def _on_playback_metrics(self, metrics) -> None:
        """Callback from engine with playback metrics."""
        # Could use for VU metering, etc.
        pass

    def _on_sequence_changed(self) -> None:
        """Mark current sequence as modified."""
        if self._suppress_dirty_tracking:
            return
        self._has_unsaved_changes = True
        self._update_window_title()

    def _update_window_title(self) -> None:
        """Refresh window title with sequence name/path and dirty marker."""
        display_name = self._current_sequence.name.strip() or "Untitled Sequence"
        if self._current_sequence_path is not None:
            display_name = self._current_sequence_path.stem or display_name
        dirty_marker = "*" if self._has_unsaved_changes else ""
        self.setWindowTitle(f"Sequencer - {display_name}{dirty_marker}")

    def _confirm_discard_unsaved_changes(self, action_name: str) -> bool:
        """Ask user whether to save/discard pending sequence edits."""
        if not self._has_unsaved_changes:
            return True

        response = QMessageBox.warning(
            self,
            "Unsaved Sequence Changes",
            f"Save changes before {action_name}?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if response == QMessageBox.StandardButton.Cancel:
            return False
        if response == QMessageBox.StandardButton.Discard:
            return True
        return self._on_save_sequence()

    def has_unsaved_changes(self) -> bool:
        """Return whether the current sequence has unsaved edits."""
        return bool(self._has_unsaved_changes)

    def confirm_close_for_application(self) -> bool:
        """Confirm save/discard/cancel flow used when the app is shutting down."""
        if not self._has_unsaved_changes:
            return True

        response = QMessageBox.warning(
            self,
            "Unsaved Sequence Changes",
            "Save changes before closing the application?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if response == QMessageBox.StandardButton.Cancel:
            return False
        if response == QMessageBox.StandardButton.Discard:
            return True
        return self._on_save_sequence()

    def prepare_for_application_close(self) -> None:
        """Allow full close path when app is shutting down."""
        self._allow_hard_close = True

    def _save_sequence_to_path(self, target_path: Path) -> bool:
        """Serialize current sequence to disk."""
        path_obj = Path(target_path)
        if path_obj.suffix.lower() != ".jingle-sequence":
            path_obj = path_obj.with_suffix(".jingle-sequence")

        try:
            path_obj.parent.mkdir(parents=True, exist_ok=True)
            with path_obj.open("w", encoding="utf-8") as handle:
                json.dump(
                    {"version": 1, "sequence": self._current_sequence.to_dict()},
                    handle,
                    indent=2,
                )
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Save Failed",
                f"Failed to save sequence to:\n{path_obj}\n\n{exc}",
            )
            return False

        self._current_sequence_path = path_obj
        self._has_unsaved_changes = False
        self._update_window_title()
        self._status_label.setText(f"Saved: {path_obj.name}")
        self._add_recent_sequence(path_obj)
        return True

    def _on_new_sequence(self) -> None:
        """Create a new empty sequence."""
        if not self._confirm_discard_unsaved_changes("creating a new sequence"):
            return

        self._suppress_dirty_tracking = True
        try:
            self._current_sequence = Sequence("Untitled Sequence", bpm=self._engine.get_bpm())
            self._current_sequence_path = None
            self._selected_track_index = -1
            self._selected_trigger_track_index = -1
            self._selected_trigger_index = -1
            self._engine.set_sequence(self._current_sequence)
            self._update_track_list()
        finally:
            self._suppress_dirty_tracking = False

        self._has_unsaved_changes = False
        self._update_window_title()
        self._status_label.setText("New sequence")

    def _on_save_sequence(self) -> bool:
        """Save sequence to current path or prompt for path."""
        if self._current_sequence_path is None:
            return self._on_save_sequence_as()
        return self._save_sequence_to_path(self._current_sequence_path)

    def _on_save_sequence_as(self) -> bool:
        """Prompt for a sequence save path and save."""
        start_dir = self._current_sequence_path.parent if self._current_sequence_path else self._sequence_store.sequences_dir
        suggested_name = self._current_sequence_path.name if self._current_sequence_path else (
            (self._current_sequence.name.strip() or "sequence").replace(" ", "-") + ".jingle-sequence"
        )
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Sequence As",
            str(start_dir / suggested_name),
            "Jingle Sequence (*.jingle-sequence)",
        )
        if not file_path:
            return False
        return self._save_sequence_to_path(Path(file_path))

    def _on_load_sequence(self) -> None:
        """Prompt for sequence file and load it."""
        if not self._confirm_discard_unsaved_changes("loading another sequence"):
            return

        start_dir = self._current_sequence_path.parent if self._current_sequence_path else self._sequence_store.sequences_dir
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Sequence",
            str(start_dir),
            "Jingle Sequence (*.jingle-sequence)",
        )
        if not file_path:
            return
        self._load_sequence_from_path(Path(file_path))

    def _load_sequence_from_path(self, sequence_path: Path) -> bool:
        """Load a sequence directly from a file path."""
        loaded = self._sequence_store.load_sequence(sequence_path)
        if loaded is None:
            QMessageBox.warning(self, "Load Failed", f"Could not load sequence:\n{sequence_path}")
            return False

        self._suppress_dirty_tracking = True
        try:
            self._current_sequence = loaded
            self._current_sequence_path = sequence_path
            self._selected_track_index = -1
            self._selected_trigger_track_index = -1
            self._selected_trigger_index = -1
            self._engine.set_sequence(self._current_sequence)
            self._engine.set_bpm(self._current_sequence.bpm)
            self._bpm_spin.setValue(self._current_sequence.bpm)
            self._update_track_list()
        finally:
            self._suppress_dirty_tracking = False

        self._has_unsaved_changes = False
        self._update_window_title()
        self._status_label.setText(f"Loaded: {sequence_path.name}")
        self._add_recent_sequence(sequence_path)
        return True

    def _load_recent_sequences(self) -> list[str]:
        """Load recent sequence file paths from disk."""
        try:
            if not self._recent_sequences_path.exists():
                return []
            with self._recent_sequences_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return []

        recent: list[str] = []
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, str):
                    continue
                candidate = item.strip()
                if candidate and candidate not in recent:
                    recent.append(candidate)
        return recent[:10]

    def _save_recent_sequences(self) -> None:
        """Persist recent sequence file paths to disk."""
        try:
            self._recent_sequences_path.parent.mkdir(parents=True, exist_ok=True)
            with self._recent_sequences_path.open("w", encoding="utf-8") as handle:
                json.dump(self._recent_sequences[:10], handle, indent=2)
        except OSError:
            pass

    def _add_recent_sequence(self, sequence_path: Path) -> None:
        """Insert a path into the recent list and refresh the menu."""
        candidate = str(Path(sequence_path)).strip()
        if not candidate:
            return
        self._recent_sequences = [item for item in self._recent_sequences if item != candidate]
        self._recent_sequences.insert(0, candidate)
        self._recent_sequences = self._recent_sequences[:10]
        self._save_recent_sequences()
        self._refresh_recent_sequences_menu()

    def _refresh_recent_sequences_menu(self) -> None:
        """Rebuild the recent sequences submenu."""
        if self._recent_sequences_menu is None:
            return

        self._recent_sequences_menu.clear()
        if not self._recent_sequences:
            empty_action = QAction("(No recent sequences)", self)
            empty_action.setEnabled(False)
            self._recent_sequences_menu.addAction(empty_action)
            return

        for sequence_path_text in self._recent_sequences:
            sequence_path = Path(sequence_path_text)
            label = sequence_path.name or sequence_path_text
            action = QAction(label, self)
            action.setToolTip(sequence_path_text)
            action.triggered.connect(
                lambda _checked=False, path=sequence_path: self._on_open_recent_sequence(path)
            )
            self._recent_sequences_menu.addAction(action)

        self._recent_sequences_menu.addSeparator()
        clear_action = QAction("Clear Recent Sequences", self)
        clear_action.triggered.connect(self._on_clear_recent_sequences)
        self._recent_sequences_menu.addAction(clear_action)

    def _on_open_recent_sequence(self, sequence_path: Path) -> None:
        """Open a sequence from the recent list."""
        if not self._confirm_discard_unsaved_changes("loading another sequence"):
            return
        self._load_sequence_from_path(sequence_path)

    def _on_clear_recent_sequences(self) -> None:
        """Clear recent sequence history."""
        self._recent_sequences = []
        self._save_recent_sequences()
        self._refresh_recent_sequences_menu()

    def _on_add_track(self) -> None:
        """Add a new track to sequence."""
        name, ok = QInputDialog.getText(
            self,
            "Add Track",
            "Track name:",
            text=f"Track {len(self._current_sequence.tracks) + 1}",
        )
        if ok and name:
            track = SequenceTrack(
                name=name,
                source_path="",
                is_jingle=False,
            )
            self._current_sequence.add_track(track)
            self._update_track_list()
            self.sequence_changed.emit()

    def _on_remove_track(self) -> None:
        """Remove selected track."""
        if self._selected_track_index < 0:
            return
        self._current_sequence.remove_track(self._selected_track_index)
        self._update_track_list()
        self._selected_track_index = -1
        self.sequence_changed.emit()

    def _on_track_selected(self) -> None:
        """Track selected in list."""
        items = self._track_list.selectedItems()
        if items:
            self._selected_track_index = self._track_list.row(items[0])
        else:
            self._selected_track_index = -1
        self._refresh_track_properties()

    def _on_track_item_double_clicked(self, item: QListWidgetItem) -> None:
        """Rename a track when its list item is double-clicked."""
        track_index = self._track_list.row(item)
        if track_index < 0 or track_index >= len(self._current_sequence.tracks):
            return

        track = self._current_sequence.tracks[track_index]
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Track",
            "Track name:",
            text=track.name,
        )
        if not ok:
            return

        renamed = new_name.strip()
        if not renamed or renamed == track.name:
            return

        track.name = renamed
        self._selected_track_index = track_index
        self._selected_trigger_track_index = -1
        self._selected_trigger_index = -1
        self._update_track_list()
        self._track_list.setCurrentRow(track_index)
        self.sequence_changed.emit()
        self._status_label.setText(f"Track renamed to {renamed}")

    def _refresh_track_properties(self) -> None:
        """Refresh the inline track property editor from the selected track."""
        self._updating_track_properties = True
        try:
            track_index = self._selected_track_index
            if track_index < 0 or track_index >= len(self._current_sequence.tracks):
                self._track_name_edit.clear()
                self._track_source_edit.clear()
                self._track_volume_spin.setValue(100)
                self._track_pan_spin.setValue(0)
                self._track_mute_check.setChecked(False)
                self._track_solo_check.setChecked(False)
                self._set_track_properties_enabled(False)
                return

            track = self._current_sequence.tracks[track_index]
            self._set_track_properties_enabled(True)
            self._track_name_edit.setText(track.name)
            self._track_source_edit.setText(track.source_path)
            self._track_volume_spin.setValue(int(track.volume_percent))
            self._track_pan_spin.setValue(int(track.pan_percent))
            self._track_mute_check.setChecked(bool(track.mute))
            self._track_solo_check.setChecked(bool(track.solo))
        finally:
            self._updating_track_properties = False

    def _set_track_properties_enabled(self, enabled: bool) -> None:
        """Enable or disable inline track property controls."""
        self._track_name_edit.setEnabled(enabled)
        self._track_source_edit.setEnabled(enabled)
        self._track_volume_spin.setEnabled(enabled)
        self._track_pan_spin.setEnabled(enabled)
        self._track_mute_check.setEnabled(enabled)
        self._track_solo_check.setEnabled(enabled)

    def _selected_track(self) -> SequenceTrack | None:
        """Return the currently selected track, if any."""
        track_index = self._selected_track_index
        if track_index < 0 or track_index >= len(self._current_sequence.tracks):
            return None
        return self._current_sequence.tracks[track_index]

    def _on_track_name_edited(self) -> None:
        """Commit inline track name edits."""
        if self._updating_track_properties:
            return
        track = self._selected_track()
        if track is None:
            return
        renamed = self._track_name_edit.text().strip()
        if not renamed or renamed == track.name:
            self._refresh_track_properties()
            return
        track.name = renamed
        self._update_track_list()
        self._track_list.setCurrentRow(self._selected_track_index)
        self.sequence_changed.emit()
        self._status_label.setText(f"Track renamed to {renamed}")

    def _on_track_volume_changed(self, value: int) -> None:
        """Update selected track volume from inline properties."""
        if self._updating_track_properties:
            return
        track = self._selected_track()
        if track is None:
            return
        track.volume_percent = int(value)
        self._engine.set_sequence(self._current_sequence)
        self.sequence_changed.emit()

    def _on_track_pan_changed(self, value: int) -> None:
        """Update selected track pan from inline properties."""
        if self._updating_track_properties:
            return
        track = self._selected_track()
        if track is None:
            return
        track.pan_percent = int(value)
        self._engine.set_sequence(self._current_sequence)
        self.sequence_changed.emit()

    def _on_track_mute_toggled(self, checked: bool) -> None:
        """Update selected track mute flag from inline properties."""
        if self._updating_track_properties:
            return
        track = self._selected_track()
        if track is None:
            return
        track.mute = bool(checked)
        self._engine.set_sequence(self._current_sequence)
        self.sequence_changed.emit()

    def _on_track_solo_toggled(self, checked: bool) -> None:
        """Update selected track solo flag from inline properties."""
        if self._updating_track_properties:
            return
        track = self._selected_track()
        if track is None:
            return
        track.solo = bool(checked)
        self._engine.set_sequence(self._current_sequence)
        self.sequence_changed.emit()

    def _on_set_track_source(self) -> None:
        """Assign an audio file source to the selected track."""
        track_index = self._selected_track_index
        if track_index < 0 or track_index >= len(self._current_sequence.tracks):
            self._status_label.setText("Select a track first")
            return

        track = self._current_sequence.tracks[track_index]
        start_dir = self._sequence_store.sequences_dir
        existing_source = (track.source_path or "").strip()
        if existing_source:
            existing_path = Path(existing_source)
            if existing_path.exists() and existing_path.parent.exists():
                start_dir = existing_path.parent

        source_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Track Source",
            str(start_dir),
            "Audio Files (*.wav *.wave *.mp3 *.flac *.ogg *.m4a *.aac *.aiff *.aif)",
        )
        if not source_path:
            return

        track.source_path = str(Path(source_path))
        track.is_jingle = False
        self._selected_trigger_track_index = -1
        self._selected_trigger_index = -1
        self._update_track_list()
        self._track_list.setCurrentRow(track_index)
        self._refresh_track_properties()
        self.sequence_changed.emit()
        self._status_label.setText(f"Set source for {track.name}")

    def _on_clear_track_source(self) -> None:
        """Clear source file assignment from selected track."""
        track_index = self._selected_track_index
        if track_index < 0 or track_index >= len(self._current_sequence.tracks):
            self._status_label.setText("Select a track first")
            return

        track = self._current_sequence.tracks[track_index]
        if not track.source_path:
            return

        track.source_path = ""
        track.is_jingle = False
        self._selected_trigger_track_index = -1
        self._selected_trigger_index = -1
        self._update_track_list()
        self._track_list.setCurrentRow(track_index)
        self._refresh_track_properties()
        self.sequence_changed.emit()
        self._status_label.setText(f"Cleared source for {track.name}")

    def _on_timeline_empty_clicked(self, track_index: int, beat: float) -> None:
        """Add a trigger when clicking empty track space."""
        if track_index < 0 or track_index >= len(self._current_sequence.tracks):
            return

        snapped_beat = round(max(0.0, beat) * 2.0) / 2.0
        new_trigger = TriggerEvent(beat_position=snapped_beat, duration_beats=1.0)
        self._current_sequence.add_trigger(track_index, new_trigger)

        # Find new trigger index after sort and select it.
        trigger_index = -1
        for i, trigger in enumerate(self._current_sequence.tracks[track_index].triggers):
            if trigger is new_trigger:
                trigger_index = i
                break

        self._selected_track_index = track_index
        self._selected_trigger_track_index = track_index
        self._selected_trigger_index = trigger_index
        self._update_track_list()
        self._timeline_canvas.set_selected_trigger(track_index, trigger_index)
        self.sequence_changed.emit()
        self._status_label.setText(
            f"Added trigger at beat {snapped_beat:.1f} (duration 1.0 beat)"
        )

    def _on_timeline_trigger_selected(self, track_index: int, trigger_index: int) -> None:
        """Select trigger in timeline."""
        if track_index < 0 or trigger_index < 0:
            return
        self._selected_track_index = track_index
        self._selected_trigger_track_index = track_index
        self._selected_trigger_index = trigger_index
        self._timeline_canvas.set_selected_trigger(track_index, trigger_index)
        if 0 <= track_index < self._track_list.count():
            self._track_list.setCurrentRow(track_index)

    def _on_timeline_trigger_double_clicked(self, track_index: int, trigger_index: int) -> None:
        """Edit trigger duration on double click."""
        if track_index < 0 or track_index >= len(self._current_sequence.tracks):
            return
        triggers = self._current_sequence.tracks[track_index].triggers
        if trigger_index < 0 or trigger_index >= len(triggers):
            return

        trigger = triggers[trigger_index]
        new_duration, ok = QInputDialog.getDouble(
            self,
            "Edit Trigger Duration",
            "Duration (beats):",
            value=float(trigger.duration_beats),
            min=0.1,
            max=128.0,
            decimals=2,
        )
        if not ok:
            return

        trigger.duration_beats = max(0.1, float(new_duration))
        self._selected_track_index = track_index
        self._selected_trigger_track_index = track_index
        self._selected_trigger_index = trigger_index
        self._update_track_list()
        self._timeline_canvas.set_selected_trigger(track_index, trigger_index)
        self.sequence_changed.emit()
        self._status_label.setText(
            f"Trigger duration set to {trigger.duration_beats:.2f} beats"
        )

    def _on_delete_selected_trigger(self) -> None:
        """Delete currently selected trigger."""
        track_index = self._selected_trigger_track_index
        trigger_index = self._selected_trigger_index
        if track_index < 0 or track_index >= len(self._current_sequence.tracks):
            return
        triggers = self._current_sequence.tracks[track_index].triggers
        if trigger_index < 0 or trigger_index >= len(triggers):
            return

        self._current_sequence.remove_trigger(track_index, trigger_index)
        self._selected_trigger_track_index = -1
        self._selected_trigger_index = -1
        self._update_track_list()
        self._timeline_canvas.set_selected_trigger(-1, -1)
        self.sequence_changed.emit()
        self._status_label.setText("Trigger removed")

    def _on_timeline_trigger_moved(self, track_index: int, trigger_index: int, beat: float) -> None:
        """Move trigger start while dragging."""
        trigger = self._get_trigger(track_index, trigger_index)
        if trigger is None:
            return

        snapped_beat = round(max(0.0, float(beat)) * 2.0) / 2.0
        trigger.beat_position = snapped_beat
        self._selected_track_index = track_index
        self._selected_trigger_track_index = track_index
        self._selected_trigger_index = trigger_index
        self._refresh_track_timeline(track_index)
        self._timeline_canvas.set_selected_trigger(track_index, trigger_index)
        self._status_label.setText(f"Trigger start: beat {snapped_beat:.1f}")

    def _on_timeline_trigger_resized(
        self,
        track_index: int,
        trigger_index: int,
        duration_beats: float,
    ) -> None:
        """Resize trigger duration while dragging."""
        trigger = self._get_trigger(track_index, trigger_index)
        if trigger is None:
            return

        snapped_duration = round(max(0.5, float(duration_beats)) * 2.0) / 2.0
        trigger.duration_beats = snapped_duration
        self._selected_track_index = track_index
        self._selected_trigger_track_index = track_index
        self._selected_trigger_index = trigger_index
        self._refresh_track_timeline(track_index)
        self._timeline_canvas.set_selected_trigger(track_index, trigger_index)
        self._status_label.setText(f"Trigger duration: {snapped_duration:.1f} beats")

    def _on_timeline_trigger_range_changed(
        self,
        track_index: int,
        trigger_index: int,
        start_beat: float,
        duration_beats: float,
    ) -> None:
        """Update trigger start+duration atomically (used for left-edge resize)."""
        trigger = self._get_trigger(track_index, trigger_index)
        if trigger is None:
            return

        raw_start = max(0.0, float(start_beat))
        raw_end = max(raw_start + 0.1, raw_start + float(duration_beats))
        snapped_start = round(raw_start * 2.0) / 2.0
        snapped_end = round(raw_end * 2.0) / 2.0
        if snapped_end <= snapped_start:
            snapped_end = snapped_start + 0.5

        trigger.beat_position = snapped_start
        trigger.duration_beats = snapped_end - snapped_start

        self._selected_track_index = track_index
        self._selected_trigger_track_index = track_index
        self._selected_trigger_index = trigger_index
        self._refresh_track_timeline(track_index)
        self._timeline_canvas.set_selected_trigger(track_index, trigger_index)
        self._status_label.setText(
            f"Trigger range: {snapped_start:.1f} to {snapped_end:.1f} beats"
        )

    def _on_timeline_trigger_edit_finished(self, track_index: int, trigger_index: int) -> None:
        """Commit drag edits after mouse release."""
        trigger = self._get_trigger(track_index, trigger_index)
        if trigger is None:
            return
        self._current_sequence.last_modified_timestamp = time.time()
        self._engine.set_sequence(self._current_sequence)
        self.sequence_changed.emit()

    def _refresh_track_timeline(self, track_index: int) -> None:
        """Refresh only one track row in the timeline."""
        if track_index < 0 or track_index >= len(self._current_sequence.tracks):
            return
        track = self._current_sequence.tracks[track_index]
        triggers = [
            (t.beat_position, t.duration_beats)
            for t in track.triggers
        ]
        self._timeline_canvas.update_track_triggers(track_index, triggers)
        self._timeline_canvas.set_track_waveform_peaks(
            track_index,
            self._load_track_waveform_peaks(track),
        )
        self._timeline_canvas.set_track_source_duration_seconds(
            track_index,
            self._load_track_source_duration_seconds(track),
        )

    def _get_trigger(self, track_index: int, trigger_index: int) -> TriggerEvent | None:
        """Return trigger object if indexes are valid."""
        if track_index < 0 or track_index >= len(self._current_sequence.tracks):
            return None
        triggers = self._current_sequence.tracks[track_index].triggers
        if trigger_index < 0 or trigger_index >= len(triggers):
            return None
        return triggers[trigger_index]

    def _update_track_list(self) -> None:
        """Refresh track list display."""
        self._track_list.clear()
        self._timeline_canvas.clear_tracks()
        for i, track in enumerate(self._current_sequence.tracks):
            item = QListWidgetItem(f"{i + 1}. {track.name}")
            item.setToolTip(track.source_path or "(No source assigned)")
            item.setData(QtCore.ItemDataRole.UserRole, i)
            self._track_list.addItem(item)
            
            # Update timeline canvas
            self._timeline_canvas.add_track(i, track.name)
            triggers = [(t.beat_position, t.duration_beats) for t in track.triggers]
            self._timeline_canvas.update_track_triggers(i, triggers)
            self._timeline_canvas.set_track_waveform_peaks(
                i,
                self._load_track_waveform_peaks(track),
            )
            self._timeline_canvas.set_track_source_duration_seconds(
                i,
                self._load_track_source_duration_seconds(track),
            )

        if self._selected_trigger_track_index >= 0 and self._selected_trigger_index >= 0:
            if self._selected_trigger_track_index < len(self._current_sequence.tracks):
                trigger_count = len(
                    self._current_sequence.tracks[self._selected_trigger_track_index].triggers
                )
                if self._selected_trigger_index < trigger_count:
                    self._timeline_canvas.set_selected_trigger(
                        self._selected_trigger_track_index,
                        self._selected_trigger_index,
                    )
                else:
                    self._selected_trigger_track_index = -1
                    self._selected_trigger_index = -1
            else:
                self._selected_trigger_track_index = -1
                self._selected_trigger_index = -1

        self._engine.set_sequence(self._current_sequence)
        if self._mixer_dialog is not None:
            self._mixer_dialog.set_sequence(self._current_sequence)
        self._refresh_track_properties()

    def _load_track_waveform_peaks(self, track: SequenceTrack) -> list[float]:
        """Load cached waveform peaks for a track source file, if available."""
        source_path = (track.source_path or "").strip()
        if not source_path:
            return []
        path_obj = Path(source_path)
        if not path_obj.exists() or not path_obj.is_file():
            return []
        try:
            return load_waveform_peaks(
                path_obj,
                bucket_count=700,
                cache_dir=self._waveform_cache_dir,
            )
        except Exception:
            return []

    def _load_track_source_duration_seconds(self, track: SequenceTrack) -> float | None:
        """Resolve source clip duration for loop-aware waveform tiling."""
        source_path = (track.source_path or "").strip()
        if not source_path:
            return None
        if source_path in self._source_duration_cache:
            return self._source_duration_cache[source_path]

        duration: float | None = None
        path_obj = Path(source_path)
        if path_obj.exists() and path_obj.is_file():
            try:
                import soundfile as sf  # type: ignore

                info = sf.info(str(path_obj))
                if info.samplerate > 0:
                    duration = float(info.frames) / float(info.samplerate)
            except Exception:
                duration = None

            if duration is None and path_obj.suffix.lower() in {".wav", ".wave"}:
                try:
                    with wave.open(str(path_obj), "rb") as wav_file:
                        frames = wav_file.getnframes()
                        sample_rate = wav_file.getframerate()
                        if sample_rate > 0:
                            duration = float(frames) / float(sample_rate)
                except Exception:
                    duration = None

        self._source_duration_cache[source_path] = duration
        return duration

    def get_sequence(self) -> Sequence:
        """Get the current sequence."""
        return self._current_sequence

    def set_sequence(self, sequence: Sequence) -> None:
        """Load a sequence."""
        self._suppress_dirty_tracking = True
        try:
            self._current_sequence = sequence
            self._current_sequence_path = None
            self._engine.set_sequence(sequence)
            self._engine.set_bpm(sequence.bpm)
            self._bpm_spin.setValue(sequence.bpm)
            self._update_track_list()
        finally:
            self._suppress_dirty_tracking = False

        self._has_unsaved_changes = False
        self._update_window_title()
        if self._mixer_dialog is not None:
            self._mixer_dialog.set_sequence(self._current_sequence)

    def closeEvent(self, event) -> None:
        """Handle window close."""
        if not self._allow_hard_close:
            self._engine.stop()
            self._play_btn.setEnabled(True)
            self._pause_btn.setEnabled(False)
            self.hide()
            self.window_closed.emit()
            event.ignore()
            return
        if self._mixer_dialog is not None:
            try:
                self._mixer_dialog.close()
            except Exception:
                pass
        self._engine.stop()
        self._update_timer.stop()
        self.window_closed.emit()
        super().closeEvent(event)


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
