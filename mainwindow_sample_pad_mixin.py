from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from app_helpers import QMediaPlayer, _has_pynput, _has_windows_native_hotkeys
from models_store import JingleRecord
from recording_engine import RecordingConfig, get_recording_engine
from sample_pad_audio_engine import SamplePadAudioEngine as _SamplePadAudioEngine
import sample_pad_audio_engine as _sp_engine_mod
from sample_pads import SamplePadsWindow
from waveform_cache import load_waveform_peaks as _load_waveform_peaks


class MainWindowSamplePadMixin:
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
        pad_mode: the SamplePad mode string
            (one_shot / loop / release_os / release_l).
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
            release_mode = pad_mode in ("release_os", "release_l", "release")
            release_loops_at_end = pad_mode in ("release_l", "release")
            loop = (pad_mode == "loop") or release_loops_at_end
            local_target = (
                self._output_device
                if is_live_mode
                else (self._preview_output_device if self._can_use_preview_mode() else self._output_device)
            )
            local_ok = self._trigger_sample_pad_engine(
                self._sp_monitor_engine,
                local_target,
                jingle_data['path'],
                clip_start,
                clip_stop,
                loop,
                pad_index,
                pad_volume_percent,
                pad_pan_percent,
                pad_is_muted,
                pad_is_solo,
                notify_errors=True,
            )

            if is_live_mode:
                broadcast_target = self._resolved_mixer_output_device()
                if (
                    broadcast_target
                    and self._normalize_device_key(broadcast_target)
                    != self._normalize_device_key(local_target)
                ):
                    self._trigger_sample_pad_engine(
                        self._sp_engine,
                        broadcast_target,
                        jingle_data['path'],
                        clip_start,
                        clip_stop,
                        loop,
                        pad_index,
                        pad_volume_percent,
                        pad_pan_percent,
                        pad_is_muted,
                        pad_is_solo,
                        notify_errors=False,
                    )

            if local_ok:
                name = jingle_data.get('name', Path(jingle_data['path']).name)
                self._status.showMessage(f"Playing: {name}")
                return

        # ------------------------------------------------------------------
        # Preview mode (or engine unavailable): use QMediaPlayer as before.
        # ------------------------------------------------------------------
        release_mode = pad_mode in ("release_os", "release_l", "release")
        release_loops_at_end = pad_mode in ("release_l", "release")
        # Use native seamless looping (no seek gap) for untrimmed full-file loop-style playback.
        self._sample_pad_looping = (pad_mode == "loop") or release_loops_at_end
        self._sample_pad_release_looping = release_mode
        self._current_sample_pad_index = pad_index
        if self._sample_pad_looping:
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

    def _trigger_sample_pad_engine(
        self,
        engine: _SamplePadAudioEngine,
        target_device: str,
        path: str,
        clip_start: float,
        clip_stop: float,
        loop: bool,
        pad_index: int,
        pad_volume_percent: int,
        pad_pan_percent: int,
        pad_is_muted: bool,
        pad_is_solo: bool,
        *,
        notify_errors: bool,
    ) -> bool:
        try:
            stream_needs_reopen = (
                engine._stream_device != target_device
                or engine._stream_blocksize != self._sample_pad_blocksize
            )
            engine.set_device(
                target_device,
                blocksize=self._sample_pad_blocksize,
            )
            if stream_needs_reopen:
                self._sp_engine_preload_all_pads()
            self._sync_sample_pad_engine_gain()
            if pad_index >= 0:
                engine.set_pad_mix(
                    pad_index,
                    pad_volume_percent,
                    pad_pan_percent,
                    pad_is_muted,
                    pad_is_solo,
                )
            engine.trigger(
                path=path,
                volume=1.0,
                clip_start_seconds=clip_start,
                clip_stop_seconds=clip_stop,
                loop=loop,
                pad_index=pad_index,
            )
            return True
        except Exception as exc:
            if notify_errors:
                self._status.showMessage(
                    f"Sample pad low-latency routing unavailable ({exc}); using standard playback path."
                )
            return False

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
            self._sp_monitor_engine.stop(None if pad_index == -1 else pad_index)
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
            self._sp_monitor_engine.stop(None)
            self._status.showMessage("All sample pad playback stopped.")
            return
        self.stop_sample_pad_jingle(-1)

    # ------------------------------------------------------------------
    # Remote control API (called from remote_server.py's Qt-thread bridge).
    # These wrap the private main-window click handlers with a stable,
    # path-addressable surface so remote_server.py never needs to reach
    # into private `_on_*_clicked` UI handlers directly.
    # ------------------------------------------------------------------

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
            self._sp_monitor_engine.set_pad_mix(
                pad_index,
                volume_percent,
                pan_percent,
                is_muted,
                is_solo,
            )
        except Exception:
            return

    def sample_pad_meter_levels(self) -> dict[int, float]:
        if not _sp_engine_mod.is_available():
            return {}
        try:
            levels = self._sp_monitor_engine.meter_levels()
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
        sample_left = 0.0
        sample_right = 0.0
        if _sp_engine_mod.is_available():
            try:
                levels = self._sp_monitor_engine.output_meter_levels()
                if isinstance(levels, tuple) and len(levels) == 2:
                    sample_left = max(0.0, min(1.0, float(levels[0])))
                    sample_right = max(0.0, min(1.0, float(levels[1])))
            except Exception:
                pass
        elif self.sample_pad_meter_levels():
            try:
                mono = max(0.0, min(1.0, max(float(level) for level in self.sample_pad_meter_levels().values())))
            except Exception:
                mono = 0.0
            sample_left = mono
            sample_right = mono

        main_left = 0.0
        main_right = 0.0
        if self._should_include_main_playback_in_sample_pad_meter(bool(_is_live_mode)):
            main_left, main_right = self._main_window_playback_meter_levels()

        return max(sample_left, main_left), max(sample_right, main_right)


    def _sample_pad_mode_output_device_key(self, is_live_mode: bool) -> str:
        device_name = self._output_device
        if not bool(is_live_mode) and self._can_use_preview_mode():
            device_name = self._preview_output_device
        return self._normalize_device_key(device_name)

    def _active_main_output_device_key(self) -> str:
        return self._normalize_device_key(self._active_output_device())

    def _should_include_main_playback_in_sample_pad_meter(self, is_live_mode: bool) -> bool:
        if not _has_qt_multimedia or self._player is None:
            return False
        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return False
        return self._sample_pad_mode_output_device_key(is_live_mode) == self._active_main_output_device_key()

    def _main_window_playback_meter_levels(self) -> tuple[float, float]:
        if self._player is None:
            return 0.0, 0.0
        active_gain = max(0.0, min(1.0, self._active_volume_percent() / 100.0))
        position_ms = max(0, int(self._player.position()))

        # Always provide a dynamic fallback so mixer metering remains visible
        # even if waveform peaks are unavailable or still loading.
        pulse_a = abs(math.sin(position_ms * 0.013))
        pulse_b = abs(math.sin(position_ms * 0.041))
        fallback_level = max(0.0, min(1.0, (0.12 + 0.22 * pulse_a + 0.16 * pulse_b) * active_gain))

        source_url = self._player.source()
        source_path = source_url.toLocalFile().strip() if source_url is not None else ""
        if source_path:
            path_obj = Path(source_path)
            if path_obj.exists():
                self._ensure_main_playback_meter_peaks(path_obj)

        duration_ms = max(0, int(self._player.duration()))
        clip_start_ms = max(0, int(self._current_clip_start_ms))
        clip_stop_ms = int(self._current_clip_stop_ms)
        if clip_stop_ms <= clip_start_ms:
            clip_stop_ms = duration_ms
        if clip_stop_ms <= clip_start_ms:
            clip_start_ms = 0
            clip_stop_ms = max(duration_ms, 1)

        position_ms = max(clip_start_ms, min(max(clip_start_ms, clip_stop_ms), int(self._player.position())))
        clip_span = max(1, clip_stop_ms - clip_start_ms)
        ratio = max(0.0, min(1.0, (position_ms - clip_start_ms) / float(clip_span)))
        peaks = self._main_playback_meter_peaks
        if peaks:
            idx = min(len(peaks) - 1, max(0, int(round(ratio * (len(peaks) - 1)))))
            peak_level = max(0.0, min(1.0, float(peaks[idx]) * active_gain))
            level = max(fallback_level * 0.45, peak_level)
            return level, level
        return fallback_level, fallback_level

    def _ensure_main_playback_meter_peaks(self, path: Path) -> None:
        path_key = str(path)
        if path_key == self._main_playback_meter_peaks_path and self._main_playback_meter_peaks:
            return
        if self._main_playback_meter_loading and self._main_playback_meter_loading_path == path_key:
            return

        self._main_playback_meter_loading = True
        self._main_playback_meter_loading_path = path_key

        def _worker() -> None:
            peaks: list[float] = []
            try:
                peaks = _load_waveform_peaks(
                    path,
                    bucket_count=900,
                    cache_dir=self._app_data_dir / "waveform-cache",
                )
            except Exception:
                peaks = []

            self._main_playback_meter_peaks_path = path_key
            self._main_playback_meter_peaks = [
                max(0.0, min(1.0, float(value)))
                for value in peaks
            ]
            self._main_playback_meter_loading = False
            self._main_playback_meter_loading_path = ""

        threading.Thread(target=_worker, daemon=True).start()

    def is_sample_pad_playing(self, pad_index: int) -> bool:
        if pad_index < 0:
            return False
        if _sp_engine_mod.is_available():
            return (
                self._sp_monitor_engine.is_pad_playing(pad_index)
                or self._sp_engine.is_pad_playing(pad_index)
            )

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
        self._show_sample_pad_backend_warning_if_needed()
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

    def _show_sample_pad_backend_warning_if_needed(self) -> None:
        if _sp_engine_mod.is_available() or self._sample_pad_backend_warning_shown:
            return
        self._sample_pad_backend_warning_shown = True

        message = (
            "Sample pad low-latency audio backend is unavailable. "
            "Sample pads will use the standard playback path instead."
        )
        if sys.platform.startswith("linux"):
            message += "\n\nInstall system packages: libportaudio2 and libsndfile1."

        self._status.showMessage(message)
        QMessageBox.warning(self, "Sample Pad Audio Backend Unavailable", message)

    def _ensure_sample_pads_window(self) -> SamplePadsWindow:
        if self._sample_pads_window is None:
            self._sample_pads_window = SamplePadsWindow(num_pads=20, num_boards=5, parent=self)
            self._sample_pads_window.setModal(False)
            self._sample_pads_window.finished.connect(self._on_sample_pads_window_closed)
            self._sample_pads_window.layoutLoaded.connect(self._on_sample_pads_layout_selected)
            self._sample_pads_window.layoutSaved.connect(self._on_sample_pads_layout_selected)
            self._sample_pads_window.modeChanged.connect(self._on_sample_pads_mode_changed)
            self._sample_pads_window.recordingModeToggled.connect(
                self._on_sample_pads_recording_mode_toggled
            )
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
            self._sample_pads_window.set_recording_mode_enabled(self._sample_pad_recording_mode_enabled)
            self._update_sample_pad_recording_ui()
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
            if self._sample_pad_recording_active and self._sample_pad_recording_slot_index >= 0:
                self._status.showMessage(
                    f"Recording is active. Press pad {self._sample_pad_recording_slot_index + 1} again to stop."
                )
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
        engines = [self._sp_monitor_engine, self._sp_engine]

        def _preload_worker():
            for engine in engines:
                sr = engine._stream_samplerate or 44100
                ch = engine._stream_channels or 2
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
        engines = [self._sp_monitor_engine, self._sp_engine]

        def _preload_one() -> None:
            for engine in engines:
                sr = engine._stream_samplerate or 44100
                ch = engine._stream_channels or 2
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
                self._sp_monitor_engine._stream_device != target_device
                or self._sp_monitor_engine._stream_blocksize != self._sample_pad_blocksize
            )
            self._sp_monitor_engine.set_device(
                target_device,
                blocksize=self._sample_pad_blocksize,
            )
            self._sync_sample_pad_engine_gain()
            if stream_needs_reopen:
                self._sp_engine_preload_all_pads()
            if is_live_mode:
                broadcast_target = self._resolved_mixer_output_device()
                if (
                    broadcast_target
                    and self._normalize_device_key(broadcast_target)
                    != self._normalize_device_key(target_device)
                ):
                    self._sp_engine.set_device(
                        broadcast_target,
                        blocksize=self._sample_pad_blocksize,
                    )
        except Exception:
            # Playback path already handles/report errors at trigger time.
            pass

    def _on_sample_pads_recording_mode_toggled(self, enabled: bool) -> None:
        self._sample_pad_recording_mode_enabled = bool(enabled)
        self._settings.setValue(
            "samplePads/recordingModeEnabled",
            "true" if self._sample_pad_recording_mode_enabled else "false",
        )
        if not self._sample_pad_recording_mode_enabled and self._sample_pad_recording_active:
            self._stop_sample_pad_recording(save_to_pad=True)
        if self._sample_pads_window is not None and not self._sample_pad_recording_mode_enabled:
            self._sample_pads_window.update_recording_status(False, -1, -1, 0.0, "")
        state = "enabled" if self._sample_pad_recording_mode_enabled else "disabled"
        self._status.showMessage(f"Sample pad recording mode: {state}.")

    def _on_sample_pad_recording_ui_tick(self) -> None:
        if not self._sample_pad_recording_active:
            self._sample_pad_recording_ui_timer.stop()
            return
        self._sample_pad_recording_blink_on = not self._sample_pad_recording_blink_on
        if self._sample_pads_window is not None:
            self._sample_pads_window.set_recording_blink_phase(self._sample_pad_recording_blink_on)
        self._update_sample_pad_recording_ui()

    def _update_sample_pad_recording_ui(self) -> None:
        if self._sample_pads_window is None:
            return

        if not self._sample_pad_recording_active:
            self._sample_pads_window.update_recording_status(False, -1, -1, 0.0, "")
            return

        board_index = self._sample_pad_recording_board_index
        slot_index = self._sample_pad_recording_slot_index
        output_name = self._sample_pad_recording_output_path.name if self._sample_pad_recording_output_path else ""

        elapsed_seconds = 0.0
        latest_metrics = get_recording_engine().get_latest_metrics()
        if latest_metrics is not None:
            elapsed_seconds = max(0.0, float(latest_metrics.current_seconds))

        if board_index >= 0 and slot_index >= 0:
            self._sample_pads_window.set_pad_recording_indicator(board_index, slot_index, True)
        self._sample_pads_window.update_recording_status(
            True,
            board_index,
            slot_index,
            elapsed_seconds,
            output_name,
        )

    def sample_pad_recording_mode_enabled(self) -> bool:
        return bool(self._sample_pad_recording_mode_enabled)

    def handle_sample_pad_activation(self, pad: Any, is_live_mode: bool) -> bool:
        del is_live_mode
        if not self.sample_pad_recording_mode_enabled():
            return False

        pad_index = int(getattr(pad, "pad_index", -1))
        board_index = int(getattr(pad, "board_index", -1))
        slot_index = int(getattr(pad, "slot_index", -1))
        jingle = getattr(pad, "jingle", None)

        if self._sample_pad_recording_active:
            same_absolute_pad = pad_index == self._sample_pad_recording_pad_index
            same_slot_hotkey = slot_index == self._sample_pad_recording_slot_index
            if not (same_absolute_pad or same_slot_hotkey):
                if self._sample_pad_recording_slot_index >= 0:
                    self._status.showMessage(
                        f"Recording is active. Press pad {self._sample_pad_recording_slot_index + 1} again to stop."
                    )
                return True
            self._stop_sample_pad_recording(save_to_pad=True)
            return True

        if isinstance(jingle, dict) and jingle.get("path"):
            self._status.showMessage(f"Pad {slot_index + 1} already has audio. Recording mode ignores occupied pads.")
            return True

        self._start_sample_pad_recording(board_index, slot_index, pad_index)
        return True

    def _start_sample_pad_recording(self, board_index: int, slot_index: int, pad_index: int) -> None:
        output_path = self._next_recording_path()
        if output_path is None:
            self._status.showMessage("Choose a samples folder before recording to a pad.")
            return

        engine = get_recording_engine()
        config = RecordingConfig(
            device_id=self._resolved_recording_input_device(),
            sample_rate=None,
            channels=2,
            wav_subtype=self._recording_wav_subtype,
        )
        error = engine.start_recording(output_path, config)
        if error:
            self._status.showMessage(error)
            return

        self._sample_pad_recording_active = True
        self._sample_pad_recording_pad_index = pad_index
        self._sample_pad_recording_board_index = board_index
        self._sample_pad_recording_slot_index = slot_index
        self._sample_pad_recording_output_path = output_path
        self._sample_pad_recording_blink_on = True
        if self._sample_pads_window is not None:
            self._sample_pads_window.set_recording_blink_phase(True)
            self._sample_pads_window.set_pad_recording_indicator(board_index, slot_index, True)
        self._update_sample_pad_recording_ui()
        if not self._sample_pad_recording_ui_timer.isActive():
            self._sample_pad_recording_ui_timer.start()
        self._status.showMessage(
            f"Recording pad {slot_index + 1} to {output_path.name}. Press the same pad/hotkey again to stop."
        )

    def _stop_sample_pad_recording(self, *, save_to_pad: bool) -> None:
        if not self._sample_pad_recording_active:
            return

        engine = get_recording_engine()
        engine.stop_recording()

        output_path = self._sample_pad_recording_output_path
        pad_index = self._sample_pad_recording_pad_index
        board_index = self._sample_pad_recording_board_index
        slot_index = self._sample_pad_recording_slot_index
        self._sample_pad_recording_active = False
        self._sample_pad_recording_pad_index = -1
        self._sample_pad_recording_board_index = -1
        self._sample_pad_recording_slot_index = -1
        self._sample_pad_recording_output_path = None
        self._sample_pad_recording_ui_timer.stop()
        self._sample_pad_recording_blink_on = True

        if self._sample_pads_window is not None and board_index >= 0 and slot_index >= 0:
            self._sample_pads_window.set_pad_recording_indicator(board_index, slot_index, False)
            self._sample_pads_window.set_recording_blink_phase(True)
            self._sample_pads_window.update_recording_status(False, -1, -1, 0.0, "")

        if output_path is None or not output_path.exists():
            self._status.showMessage("Sample pad recording stopped before a file was written.")
            return

        self._store.mark_recorded(output_path, True)
        self._store.save()
        self._rescan_library()

        if save_to_pad and self._sample_pads_window is not None:
            board = self._sample_pads_window.board_pads(board_index)
            if 0 <= slot_index < len(board):
                board[slot_index].assign_jingle(
                    {
                        "name": output_path.stem,
                        "path": str(output_path),
                    }
                )
                self._sp_engine_preload_all_pads()
        self._status.showMessage(f"Saved pad recording to {output_path.name}.")

    def _trigger_sample_pad(self, pad_index: int) -> bool:
        pads_window = self._ensure_sample_pads_window()
        return pads_window.trigger_pad(pad_index)

    def _release_sample_pad(self, pad_index: int) -> None:
        if self._sample_pads_window is not None:
            self._sample_pads_window.release_pad(pad_index)



if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
