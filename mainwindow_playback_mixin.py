from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import QTimer, QUrl, Qt
from PyQt6.QtGui import QKeyEvent

from app_helpers import QMediaPlayer
from models_store import JingleRecord
from playlists_window import PlaylistsWindow

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


class MainWindowPlaybackMixin:
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



if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
