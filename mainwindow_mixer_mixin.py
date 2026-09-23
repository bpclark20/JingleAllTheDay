from __future__ import annotations

from PyQt6.QtCore import QUrl

from app_helpers import (
    QMediaDevices,
    QMediaPlayer,
    _has_qt_multimedia,
    coerce_volume_percent as _coerce_volume_percent,
)
import sample_pad_audio_engine as _sp_engine_mod


class MainWindowMixerMixin:
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
        if self._broadcast_audio_output is not None:
            self._broadcast_audio_output.setMuted(muted)
        self._apply_active_volume()
        self._refresh_mute_button_state()

    def _on_mute_clicked(self) -> None:
        if self._audio_output is None and not self._using_main_playback_engine():
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
        if not self._can_use_preview_mode():
            return self._live_volume_percent
        return self._live_volume_percent if is_live_mode else self._preview_volume_percent

    def set_sample_pad_mode_volume_percent(self, is_live_mode: bool, value: int) -> None:
        percent = _coerce_volume_percent(value)
        if not self._can_use_preview_mode():
            # When both modes route to the same physical output, keep both
            # mode volumes identical regardless of which UI surface changed.
            self._live_volume_percent = percent
            self._preview_volume_percent = percent
        elif is_live_mode:
            self._live_volume_percent = percent
        else:
            self._preview_volume_percent = percent
        self._save_volume_settings()

        # Apply to the currently-audible main bus only.
        if (
            not self._can_use_preview_mode()
            or (self._is_preview_mode and not is_live_mode)
            or (not self._is_preview_mode and is_live_mode)
        ):
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
            self._sp_monitor_engine.set_master_gain(mode_percent / 100.0)
        except Exception:
            pass
        try:
            self._sp_engine.set_master_gain(self._live_volume_percent / 100.0)
        except Exception:
            pass

    def _apply_active_volume(self) -> None:
        if self._audio_output is None:
            if self._broadcast_audio_output is None:
                if self._main_playback_engine is None:
                    return
        volume = self._active_volume_percent() / 100.0
        if self._clip_seek_muted_temporarily and not self._is_muted:
            volume = 0.0
        if self._audio_output is not None:
            self._audio_output.setVolume(volume)
        if self._broadcast_audio_output is not None:
            self._broadcast_audio_output.setVolume(volume)
        if self._main_playback_engine is not None:
            self._main_playback_engine.set_master_gain(0.0 if self._is_muted else volume)

    def _broadcast_route_enabled(self) -> bool:
        return bool(self._broadcast_output_device.strip()) and not self._broadcast_route_conflicts_main_output

    def _sync_broadcast_player_to_main(self) -> None:
        if (
            self._player is None
            or self._broadcast_player is None
            or self._broadcast_audio_output is None
        ):
            return

        if not self._broadcast_route_enabled():
            self._broadcast_player.stop()
            self._broadcast_player.setSource(QUrl())
            return

        source = self._player.source()
        if source is None or source.isEmpty():
            self._broadcast_player.stop()
            return

        if self._broadcast_player.source() != source:
            self._broadcast_player.setSource(source)

        main_pos = int(self._player.position())
        if abs(int(self._broadcast_player.position()) - main_pos) > 150:
            self._broadcast_player.setPosition(main_pos)

        state = self._player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._broadcast_player.play()
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self._broadcast_player.pause()
        else:
            self._broadcast_player.stop()

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
            if not self._can_use_preview_mode():
                # Keep preview volume synchronized while both modes use the
                # same output device.
                self._preview_volume_percent = percent
        self._volume_value_label.setText(f"{percent}%")
        self._save_volume_settings()
        self._apply_active_volume()
        self._sync_sample_pad_engine_gain()
        if self._sample_pads_window is not None:
            self._sample_pads_window.refresh_mode_volume_controls()

    def _resolved_microphone_input_device(self) -> str:
        selected = self._microphone_input_device.strip()
        if selected:
            return selected
        if not _has_qt_multimedia:
            return ""
        try:
            return QMediaDevices.defaultAudioInput().description().strip()
        except Exception:
            return ""

    def _resolved_recording_input_device(self) -> int | None:
        device_id = self._recording_input_device
        if isinstance(device_id, int) and device_id >= 0:
            return device_id
        return None

    def _resolved_live_engine_output_device(self) -> str:
        selected = self._broadcast_output_device.strip() or self._output_device.strip()
        if selected:
            return selected
        if not _has_qt_multimedia:
            return ""
        try:
            return QMediaDevices.defaultAudioOutput().description().strip()
        except Exception:
            return ""

    def _resolved_mixer_output_device(self) -> str:
        return self._resolved_live_engine_output_device()

    def _apply_mixer_input_device(self, *, notify_errors: bool) -> None:
        if not _sp_engine_mod.is_available():
            return
        if not self._mixer_enabled:
            self._sp_engine.disable_input_device()
            return

        target_input = self._resolved_microphone_input_device()
        if not target_input:
            self._sp_engine.disable_input_device()
            if notify_errors:
                self._status.showMessage(
                    "Mixer mode enabled, but no microphone input device is available."
                )
            return

        try:
            self._sp_engine.set_input_device(
                target_input,
                channels=1,
                blocksize=self._sample_pad_blocksize,
            )
            self._apply_mixer_engine_output_route(notify_errors=notify_errors)
        except Exception as exc:
            self._sp_engine.disable_input_device()
            if notify_errors:
                self._status.showMessage(
                    f"Could not start microphone capture for mixer mode: {exc}"
                )

    def _apply_mixer_engine_output_route(self, *, notify_errors: bool) -> None:
        if not _sp_engine_mod.is_available() or not self._mixer_enabled:
            return

        target_output = self._resolved_mixer_output_device()
        if not target_output:
            if notify_errors:
                self._status.showMessage(
                    "Mixer mode enabled, but no output device is available for the engine route."
                )
            return
        try:
            self._sp_engine.set_device(
                target_output,
                blocksize=self._sample_pad_blocksize,
            )
            self._sync_sample_pad_engine_gain()
        except Exception as exc:
            if notify_errors:
                self._status.showMessage(
                    f"Could not start mixer output route: {exc}"
                )

    def _apply_output_device(self) -> None:
        self._broadcast_route_conflicts_main_output = False
        if self._audio_output is not None and _has_qt_multimedia:
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
            self._audio_output.setMuted(self._is_muted)
            self._apply_active_volume()

        if self._broadcast_audio_output is not None and _has_qt_multimedia:
            selected = self._broadcast_output_device.strip()
            if selected:
                target_device = QMediaDevices.defaultAudioOutput()
                matched = None
                for device in QMediaDevices.audioOutputs():
                    if device.description().strip() == selected:
                        matched = device
                        break
                if matched is not None:
                    target_device = matched
                    if self._broadcast_audio_output.device().id() != target_device.id():
                        self._broadcast_audio_output.setDevice(target_device)
                if self._audio_output is not None:
                    try:
                        self._broadcast_route_conflicts_main_output = (
                            self._broadcast_audio_output.device().id() == self._audio_output.device().id()
                        )
                    except Exception:
                        self._broadcast_route_conflicts_main_output = False
                self._broadcast_audio_output.setMuted(self._is_muted)
                self._apply_active_volume()
            elif self._broadcast_player is not None:
                self._broadcast_player.stop()

        self._apply_mixer_engine_output_route(notify_errors=hasattr(self, "_status"))
        self._sync_broadcast_player_to_main()

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

    def _on_mode_toggled(self, checked: bool) -> None:
        try:
            self._on_mode_toggled_impl(checked)
        finally:
            self._publish_remote_state()

    def _on_mode_toggled_impl(self, checked: bool) -> None:
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
        if self._using_main_playback_engine():
            self._apply_main_playback_output_route()
        else:
            self._apply_output_device()
        if self._sample_pads_window is not None:
            self._sample_pads_window.refresh_mode_volume_controls()


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
