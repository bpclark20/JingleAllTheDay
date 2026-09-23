from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMenu, QMessageBox, QTableWidgetItem

from app_helpers import (
    QMediaPlayer,
    RESERVED_INTERNAL_TAG_RECENT,
    merge_tags as _merge_tags,
    normalize_tags as _normalize_tags,
    remove_tags as _remove_tags,
    runtime_app_dir as _runtime_app_dir,
    sanitize_user_tags as _sanitize_user_tags,
    tags_to_text as _tags_to_text,
)
from dialogs import AboutDialog
from models_store import JingleRecord

_HERE = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(__file__).resolve().parent  # type: ignore[attr-defined]


class MainWindowTableMixin:
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

    def selected_playlist_candidates(self) -> list[dict[str, str]]:
        payload: list[dict[str, str]] = []
        for record_index in self._selected_record_indices():
            if record_index < 0 or record_index >= len(self._records):
                continue
            record = self._records[record_index]
            payload.append(
                {
                    "name": record.name,
                    "path": str(record.path),
                }
            )
        return payload

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
        requested_categories = _normalize_tags(self._table.item(item.row(), 1).text())
        new_categories, rejected = _sanitize_user_tags(requested_categories)
        if rejected:
            QMessageBox.warning(
                self,
                "Reserved Tag",
                f"'{RESERVED_INTERNAL_TAG_RECENT}' is an internal tag and cannot be assigned manually.",
            )

        record.categories = new_categories
        self._store.set(record.path, new_categories)
        self._store.save()

        self._apply_filters()

    def _on_apply_bulk_to_selected(self) -> None:
        selected_rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if not selected_rows:
            self._status.showMessage("Select one or more rows first.")
            return

        mode_data = self._bulk_mode_combo.currentData()
        mode = str(mode_data) if mode_data is not None else "replace"
        requested_categories = _normalize_tags(self._bulk_category_edit.text())
        categories, rejected = _sanitize_user_tags(requested_categories)
        if rejected:
            QMessageBox.warning(
                self,
                "Reserved Tag",
                f"Ignored internal reserved tag '{RESERVED_INTERNAL_TAG_RECENT}' in bulk tags.",
            )

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
                    label = f"Pad {i + 1} — {stem}"
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

        menu.addSeparator()
        add_to_playlist_action = menu.addAction("Add to Playlist")
        add_to_playlist_action.setEnabled(selected_count > 0)
        add_to_playlist_action.triggered.connect(self._on_add_selected_to_playlist)

        add_to_remote_queue_action = menu.addAction("Add to Remote Queue")
        add_to_remote_queue_action.setEnabled(selected_count == 1 and self._remote_queue_is_addable())
        add_to_remote_queue_action.triggered.connect(self._on_add_selected_to_remote_queue)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _on_add_selected_to_playlist(self) -> None:
        payload = self.selected_playlist_candidates()
        if not payload:
            self._status.showMessage("Select one or more jingles first.")
            return
        playlists_window = self._ensure_playlists_window()
        playlists_window.add_payload_items(payload)
        playlists_window.show()
        playlists_window.raise_()
        playlists_window.activateWindow()
        self._status.showMessage(f"Added {len(payload)} jingle(s) to playlist.")

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
        if self._using_main_playback_engine() and self._main_playback_state in ("playing", "paused"):
            self._on_stop_clicked()
            self._on_play_clicked()
            return
        if (
            self._player is not None
            and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        ):
            self._player.stop()
            self._interrupt_active_remote_queue_for_local_play()
            self._reset_continuous_queue()
            self._current_playing_name = ""
            self._current_playing_path = ""
            self._clear_playlist_state()
        self._on_play_clicked()

    def _on_table_selection_changed(self) -> None:
        record = self._selected_record()
        if record is not None:
            self._bulk_category_edit.setText(_tags_to_text(record.categories))
        else:
            self._bulk_category_edit.clear()
        self._refresh_status_summary()


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
