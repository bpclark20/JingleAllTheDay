from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import QTimer

from app_helpers import QMediaPlayer, normalize_tags as _normalize_tags
from mainwindow_library_mixin import filter_jingle_records
import remote_server as _remote_server


class MainWindowRemoteMixin:
    def _init_remote_server(self) -> None:
        self._remote_bridge = _remote_server.RemoteServerBridge(self)
        self._remote_state = _remote_server.RemoteServerState()
        self._remote_diagnostics = _remote_server.RemoteRelayDiagnostics()
        self._remote_manager = _remote_server.RemoteRelayClient(
            self._remote_bridge,
            self._remote_state,
            self._remote_diagnostics,
            address_provider=lambda: self._server_address,
            device_token_provider=lambda: self._server_device_token,
            library_provider=self.remote_get_library,
            audio_path_provider=self.remote_resolve_audio_path,
        )
        if self._server_enabled and self._server_address and self._server_device_token:
            self._start_remote_server(announce=False)
        self._cache_backup_reminder_timer = QTimer(self)
        self._cache_backup_reminder_timer.timeout.connect(self._maybe_show_cache_backup_reminder)
        self._cache_backup_reminder_timer.start(60 * 60 * 1000)
        QTimer.singleShot(5000, self._maybe_show_cache_backup_reminder)

    def _start_remote_server(self, *, announce: bool = True) -> bool:
        if not hasattr(self, "_remote_manager"):
            return False
        if not self._server_address or not self._server_device_token:
            if announce:
                self._status.showMessage("Set a Server Address and Device Token in Options to connect.")
            return False
        started = self._remote_manager.start()
        if announce:
            if started:
                self._status.showMessage(f"Connecting to remote-control server at {self._server_address}...")
            else:
                self._status.showMessage("Remote relay failed to start (see Server > Diagnostics).")
        return started

    def _publish_remote_state(self) -> None:
        if hasattr(self, "_remote_state"):
            self._remote_state.update(self.remote_get_status())

    def remote_get_status(self) -> dict[str, Any]:
        if self._using_main_playback_engine() and self._main_playback_engine is not None:
            if self._main_playback_state == "playing":
                info = self._main_playback_engine.pad_playback_info(self._main_playback_pad_index)
                position_ms = self._current_clip_start_ms + int(
                    round(float(info.get("position_seconds", 0.0)) * 1000.0)
                )
            elif self._main_playback_state == "paused":
                position_ms = self._main_playback_paused_position_ms
            else:
                position_ms = 0
            duration_ms = self._main_playback_duration_ms
            state_name = self._main_playback_state
        else:
            position_ms = self._player.position() if self._player is not None else 0
            duration_ms = self._player.duration() if self._player is not None else 0
            if self._player is None:
                state_name = "stopped"
            elif self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                state_name = "playing"
            elif self._player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
                state_name = "paused"
            else:
                state_name = "stopped"

        return {
            "state": state_name,
            "is_live_mode": not self._is_preview_mode,
            "current_name": self._current_playing_name,
            "current_path": self._current_playing_path,
            "position_seconds": max(0.0, position_ms / 1000.0),
            "duration_seconds": max(0.0, duration_ms / 1000.0),
            "loop_mode": self._playback_mode,
            "queued_jingles": self._queued_remote_jingles_summary(),
            "active_remote_queue": self._active_remote_queue_summary(),
            "has_interrupted_queue": self._interrupted_remote_queue is not None,
        }

    def _queued_remote_jingles_summary(self) -> list[dict[str, Any]]:
        summary: list[dict[str, Any]] = []
        for position, entry in enumerate(self._pending_remote_play_queue):
            if entry.get("kind") == "resume_queue":
                name = "Resume interrupted queue"
            else:
                record_index = self._record_index_for_path(entry["path"])
                name = self._records[record_index].name if record_index is not None else Path(entry["path"]).stem
            summary.append({"queue_id": entry["queue_id"], "name": name, "position": position})
        return summary

    def _active_remote_queue_summary(self) -> list[dict[str, Any]]:
        """Upcoming items in the currently-live (playing or interrupted) remote queue, for the webapp's 'Live Queue' panel."""
        if self._continuous_queue_is_remote:
            indices = self._continuous_queue[self._continuous_queue_position :]
        elif self._interrupted_remote_queue is not None:
            snapshot = self._interrupted_remote_queue
            indices = snapshot["queue_indices"][snapshot["position"] :]
        else:
            return []
        summary: list[dict[str, Any]] = []
        for offset, record_index in enumerate(indices):
            name = self._records[record_index].name if 0 <= record_index < len(self._records) else "Unknown"
            summary.append({"position": offset, "name": name, "current": offset == 0})
        return summary

    def remote_get_library(
        self,
        search: str = "",
        scope: str = "all",
        category: str = "",
        category_mode: str = "any",
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        selected_categories = _normalize_tags(category)
        indices = filter_jingle_records(
            self._records,
            search,
            scope,
            selected_categories,
            category_mode,
            self._recent_window_days,
        )
        total = len(indices)
        page = indices[offset : offset + limit] if limit > 0 else indices
        items = []
        for index in page:
            record = self._records[index]
            items.append(
                {
                    "path": str(record.path),
                    "name": record.name,
                    "folder": record.folder,
                    "categories": list(record.categories),
                    "duration_seconds": record.duration_seconds,
                    "size_bytes": record.size_bytes,
                }
            )
        return {"total": total, "items": items}

    def remote_resolve_audio_path(self, path: str) -> Path | None:
        """Validate a path against the scanned library before streaming it to a browser."""
        record_index = self._record_index_for_path(path)
        if record_index is None:
            return None
        record = self._records[record_index]
        if not record.path.exists():
            return None
        return record.path

    def remote_play(
        self,
        path: str,
        loop_mode: str = "off",
        is_live_mode: bool = True,
        queue: list[str] | None = None,
        owner_label: str = "",
    ) -> dict[str, Any]:
        record_index = self._record_index_for_path(path)
        if record_index is None:
            return {"ok": False, "error": "not_found"}
        record = self._records[record_index]
        if not record.path.exists():
            return {"ok": False, "error": "missing_file"}

        if self._is_main_playback_active():
            queue_id = self._enqueue_remote_play(path, loop_mode, is_live_mode, queue, owner_label)
            return {
                "ok": True,
                "queued": True,
                "queue_id": queue_id,
                **self.remote_get_status(),
            }

        started = self._dispatch_remote_play(record_index, path, loop_mode, is_live_mode, queue)
        if not started:
            return {"ok": False, "error": "playback_failed"}
        return {"ok": True, **self.remote_get_status()}

    def _is_main_playback_active(self) -> bool:
        """True when a main-list jingle is currently playing or paused (sample pads don't count)."""
        if self._using_main_playback_engine():
            return self._main_playback_state in ("playing", "paused")
        if self._player is not None:
            return self._player.playbackState() in (
                QMediaPlayer.PlaybackState.PlayingState,
                QMediaPlayer.PlaybackState.PausedState,
            )
        return False

    def _enqueue_remote_play(
        self,
        path: str,
        loop_mode: str,
        is_live_mode: bool,
        queue: list[str] | None,
        owner_label: str,
    ) -> str:
        self._remote_queue_id_counter += 1
        queue_id = f"q{self._remote_queue_id_counter}"
        self._pending_remote_play_queue.append(
            {
                "queue_id": queue_id,
                "kind": "play",
                "path": path,
                "loop_mode": loop_mode,
                "is_live_mode": is_live_mode,
                "queue": queue,
                "owner_label": owner_label,
            }
        )
        self._publish_remote_state()
        return queue_id

    def _enqueue_resume_queue(self, snapshot: dict[str, Any], owner_label: str) -> str:
        self._remote_queue_id_counter += 1
        queue_id = f"q{self._remote_queue_id_counter}"
        self._pending_remote_play_queue.append(
            {"queue_id": queue_id, "kind": "resume_queue", "snapshot": snapshot, "owner_label": owner_label}
        )
        self._publish_remote_state()
        return queue_id

    def remote_resume_interrupted_queue(self, owner_label: str = "") -> dict[str, Any]:
        if self._interrupted_remote_queue is None:
            return {"ok": False, "error": "no_interrupted_queue"}
        snapshot = self._interrupted_remote_queue
        self._interrupted_remote_queue = None
        if self._is_main_playback_active():
            queue_id = self._enqueue_resume_queue(snapshot, owner_label)
            return {"ok": True, "queued": True, "queue_id": queue_id, **self.remote_get_status()}
        started = self._dispatch_resume_interrupted_queue(snapshot)
        if not started:
            return {"ok": False, "error": "playback_failed"}
        return {"ok": True, **self.remote_get_status()}

    def _dispatch_resume_interrupted_queue(self, snapshot: dict[str, Any]) -> bool:
        resolved = [index for index in snapshot.get("queue_indices", []) if 0 <= index < len(self._records)]
        if not resolved:
            return False
        position = max(0, min(int(snapshot.get("position", 0)), len(resolved) - 1))
        self._on_stop_clicked()
        self._clear_playlist_state()
        self._playback_mode = "continuous"
        self._refresh_playback_mode_button()
        self._apply_player_loop_mode()
        self._continuous_queue = resolved
        self._continuous_queue_position = position - 1
        self._continuous_queue_is_remote = True
        started = self._play_next_continuous_record()
        self._publish_remote_state()
        return started

    def remote_cancel_queued(self, queue_id: str, owner_label: str = "", is_admin: bool = False) -> bool:
        for index, entry in enumerate(self._pending_remote_play_queue):
            if entry.get("queue_id") != queue_id:
                continue
            if not is_admin and entry.get("owner_label", "") != owner_label:
                return False
            cancelled = self._pending_remote_play_queue.pop(index)
            if cancelled.get("kind") == "resume_queue" and self._interrupted_remote_queue is None:
                # Restore the pending-resume state so the webapp's Resume button reappears
                # instead of silently losing the ability to resume this queue.
                self._interrupted_remote_queue = cancelled.get("snapshot")
            self._publish_remote_state()
            return True
        return False

    def _dispatch_remote_play(
        self,
        record_index: int,
        path: str,
        loop_mode: str,
        is_live_mode: bool,
        queue: list[str] | None,
    ) -> bool:
        self._on_stop_clicked()
        self._clear_playlist_state()
        self.remote_set_live_mode(is_live_mode)
        self.remote_set_loop_mode(loop_mode)
        self._select_record_row(record_index)

        if self._playback_mode == "continuous":
            started = self._start_remote_continuous_playback(record_index, queue)
        else:
            self._reset_continuous_queue()
            started = self._play_record(record_index)

        self._publish_remote_state()
        return started

    def _dispatch_next_pending_remote_play(self) -> None:
        """Called after a main jingle naturally finishes; starts the oldest queued remote request, if any."""
        while self._pending_remote_play_queue:
            entry = self._pending_remote_play_queue.pop(0)
            if entry.get("kind") == "resume_queue":
                if self._dispatch_resume_interrupted_queue(entry["snapshot"]):
                    return
                continue
            record_index = self._record_index_for_path(entry["path"])
            if record_index is None or not self._records[record_index].path.exists():
                continue
            if self._dispatch_remote_play(
                record_index,
                entry["path"],
                entry["loop_mode"],
                entry["is_live_mode"],
                entry.get("queue"),
            ):
                return
        self._publish_remote_state()

    def _start_remote_continuous_playback(
        self, record_index: int, queue: list[str] | None
    ) -> bool:
        """Start continuous playback ordered by the remote caller's own filtered
        list (*queue*, a list of paths) rather than the desktop table's current
        filter, so a remote user's continuous playback advances through what
        they see. Falls back to the desktop's visible order when no queue is
        supplied (e.g. an older webclient)."""
        if queue:
            resolved: list[int] = []
            target_position = -1
            for queued_path in queue:
                queued_index = self._record_index_for_path(queued_path)
                if queued_index is None:
                    continue
                if queued_index == record_index:
                    target_position = len(resolved)
                resolved.append(queued_index)
            if resolved and target_position >= 0:
                self._continuous_queue = resolved
                self._continuous_queue_position = target_position - 1
                self._continuous_queue_is_remote = True
                self._interrupted_remote_queue = None
                return self._play_next_continuous_record()

        return self._start_continuous_playback()

    def remote_toggle_pause(self) -> dict[str, Any]:
        self._on_play_clicked()
        self._publish_remote_state()
        return self.remote_get_status()

    def remote_stop(self) -> None:
        self._on_stop_clicked()
        self._publish_remote_state()

    def remote_set_loop_mode(self, loop_mode: str) -> None:
        normalized = loop_mode if loop_mode in ("off", "loop", "continuous") else "off"
        if normalized != self._playback_mode:
            self._playback_mode = normalized
            self._apply_playback_mode_change()
        self._publish_remote_state()

    def remote_set_live_mode(self, is_live_mode: bool) -> bool:
        want_preview = not is_live_mode
        if want_preview and not self._can_use_preview_mode():
            self._publish_remote_state()
            return False
        self._mode_btn.setChecked(want_preview)
        self._publish_remote_state()
        return self._is_preview_mode == want_preview

    def _remote_queue_is_addable(self) -> bool:
        """True while there's a live remote queue (playing or interrupted-awaiting-resume) to append to."""
        return self._continuous_queue_is_remote or self._interrupted_remote_queue is not None

    def _on_add_selected_to_remote_queue(self) -> None:
        selected_indices = self._selected_record_indices()
        if not selected_indices:
            return
        record_index = selected_indices[0]
        if self._continuous_queue_is_remote:
            self._continuous_queue.append(record_index)
        elif self._interrupted_remote_queue is not None:
            self._interrupted_remote_queue["queue_indices"].append(record_index)
        else:
            self._status.showMessage("No remote queue is currently active.")
            return
        self._publish_remote_state()
        self._status.showMessage(f"Added \"{self._records[record_index].name}\" to the remote queue.")


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
