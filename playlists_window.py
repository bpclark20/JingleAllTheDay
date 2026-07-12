from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QMimeData, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

PLAYLIST_DRAG_MIME_TYPE = "application/x-jatd-jingles"


@dataclass
class PlaylistItem:
    name: str
    path: str
    relative_path: str = ""


class _PlaylistListWidget(QListWidget):
    recordsDropped = pyqtSignal(list)
    orderChanged = pyqtSignal()

    def __init__(self, parent: QDialog | None = None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)

    def dragEnterEvent(self, event: Any) -> None:
        if self._can_accept(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: Any) -> None:
        if self._can_accept(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: Any) -> None:
        source = event.source()
        if source is self:
            super().dropEvent(event)
            self.orderChanged.emit()
            return

        payload = self._payload_from_mime(event.mimeData())
        if payload:
            self.recordsDropped.emit(payload)
            event.acceptProposedAction()
            return

        super().dropEvent(event)

    def _can_accept(self, mime_data: QMimeData | None) -> bool:
        if mime_data is None:
            return False
        if mime_data.hasFormat(PLAYLIST_DRAG_MIME_TYPE):
            return True
        if mime_data.hasUrls():
            return True
        if mime_data.hasText():
            return True
        return False

    def _payload_from_mime(self, mime_data: QMimeData | None) -> list[dict[str, str]]:
        if mime_data is None:
            return []

        if mime_data.hasFormat(PLAYLIST_DRAG_MIME_TYPE):
            try:
                raw = bytes(mime_data.data(PLAYLIST_DRAG_MIME_TYPE)).decode("utf-8")
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    payload: list[dict[str, str]] = []
                    for item in parsed:
                        if not isinstance(item, dict):
                            continue
                        path_text = str(item.get("path", "")).strip()
                        if not path_text:
                            continue
                        payload.append(
                            {
                                "name": str(item.get("name", "")).strip(),
                                "path": path_text,
                            }
                        )
                    if payload:
                        return payload
            except Exception:
                return []

        payload_from_urls: list[dict[str, str]] = []
        if mime_data.hasUrls():
            for url in mime_data.urls():
                if not url.isLocalFile():
                    continue
                local_path = url.toLocalFile().strip()
                if not local_path:
                    continue
                payload_from_urls.append(
                    {
                        "name": Path(local_path).stem,
                        "path": local_path,
                    }
                )
        if payload_from_urls:
            return payload_from_urls

        payload_from_text: list[dict[str, str]] = []
        if mime_data.hasText():
            for line in mime_data.text().splitlines():
                text = line.strip()
                if not text:
                    continue
                payload_from_text.append(
                    {
                        "name": Path(text).stem,
                        "path": text,
                    }
                )
        return payload_from_text


class PlaylistsWindow(QDialog):
    def __init__(self, main_window: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._main_window = main_window
        self.setWindowTitle("Playlists")
        self.resize(560, 560)
        self.setModal(False)

        root = QVBoxLayout(self)

        top_row = QHBoxLayout()
        self._add_selected_btn = QPushButton("Add Selected")
        self._add_selected_btn.clicked.connect(self._on_add_selected)
        top_row.addWidget(self._add_selected_btn)

        self._remove_btn = QPushButton("Remove Selected")
        self._remove_btn.clicked.connect(self._on_remove_selected)
        top_row.addWidget(self._remove_btn)

        self._move_up_btn = QPushButton("Move Up")
        self._move_up_btn.clicked.connect(self._on_move_up)
        top_row.addWidget(self._move_up_btn)

        self._move_down_btn = QPushButton("Move Down")
        self._move_down_btn.clicked.connect(self._on_move_down)
        top_row.addWidget(self._move_down_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._on_clear)
        top_row.addWidget(self._clear_btn)

        root.addLayout(top_row)

        self._list = _PlaylistListWidget(self)
        self._list.recordsDropped.connect(self.add_payload_items)
        self._list.orderChanged.connect(self._on_playlist_order_changed)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        root.addWidget(self._list, 1)

        file_row = QHBoxLayout()
        self._load_btn = QPushButton("Load Playlist")
        self._load_btn.clicked.connect(self._on_load_playlist)
        file_row.addWidget(self._load_btn)

        self._save_btn = QPushButton("Save Playlist")
        self._save_btn.clicked.connect(self._on_save_playlist)
        file_row.addWidget(self._save_btn)

        file_row.addStretch(1)
        root.addLayout(file_row)

        controls_row = QHBoxLayout()
        self._mode_btn = QPushButton("Mode: Live")
        self._mode_btn.setCheckable(True)
        self._mode_btn.toggled.connect(self._on_mode_toggled)
        controls_row.addWidget(self._mode_btn)

        self._play_pause_btn = QPushButton("Play")
        self._play_pause_btn.clicked.connect(self._on_play_pause_clicked)
        controls_row.addWidget(self._play_pause_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        controls_row.addWidget(self._stop_btn)

        self._loop_btn = QPushButton("Loop Off")
        self._loop_btn.setCheckable(True)
        self._loop_btn.toggled.connect(self._on_loop_toggled)
        controls_row.addWidget(self._loop_btn)

        controls_row.addStretch(1)
        root.addLayout(controls_row)

        self._status_label = QLabel("Drop jingles here or use Add Selected.")
        root.addWidget(self._status_label)

        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(200)
        self._sync_timer.timeout.connect(self._refresh_from_main)
        self._sync_timer.start()

        self._autosave_path = self._playlist_autosave_path()
        self._last_playlist_path = self._playlist_last_path()

        self._refresh_from_main()
        self._refresh_status()
        self._restore_last_playlist()

    def _playlist_last_path(self) -> str:
        if hasattr(self._main_window, "playlist_last_playlist_path"):
            try:
                return str(self._main_window.playlist_last_playlist_path()).strip()
            except Exception:
                return ""
        return ""

    def _set_playlist_last_path(self, path_text: str) -> None:
        self._last_playlist_path = str(path_text).strip()
        if hasattr(self._main_window, "set_playlist_last_playlist_path"):
            try:
                self._main_window.set_playlist_last_playlist_path(self._last_playlist_path)
            except Exception:
                pass

    def _playlist_autosave_path(self) -> str:
        if hasattr(self._main_window, "playlist_autosave_path"):
            try:
                value = str(self._main_window.playlist_autosave_path()).strip()
                if value:
                    return value
            except Exception:
                pass
        app_data_dir = getattr(self._main_window, "_app_data_dir", Path.home())
        return str((Path(app_data_dir) / "playlists_autosave.json").resolve())

    def _restore_last_playlist(self) -> None:
        autosave_path = Path(self._autosave_path)
        if autosave_path.exists() and self._load_playlist_from_path(autosave_path, show_errors=False):
            self._status_label.setText("Restored last playlist from autosave.")
            return

        if self._last_playlist_path:
            last_path = Path(self._last_playlist_path)
            if last_path.exists() and self._load_playlist_from_path(last_path, show_errors=False):
                self._status_label.setText(f"Restored last playlist: {last_path}")

    def _on_playlist_order_changed(self) -> None:
        self._persist_autosave()
        self._refresh_status()

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        items = self._all_playlist_items()
        if not items:
            self._status_label.setText("Playlist is empty.")
            return
        start_index = self._list.row(item)
        if start_index < 0 or start_index >= len(items):
            start_index = 0

        paths = [playlist_item.path for playlist_item in items]
        started = self._main_window.start_playlist_playback(
            paths,
            start_index=start_index,
            loop_enabled=self._loop_btn.isChecked(),
            use_preview_mode=self._mode_btn.isChecked(),
        )
        if started:
            self._status_label.setText(f"Playing from playlist item {start_index + 1}.")
        else:
            self._status_label.setText("No playable playlist items were found.")
        self._refresh_from_main()

    def add_payload_items(self, payload: list[dict[str, str]]) -> None:
        added = 0
        for entry in payload:
            path_text = str(entry.get("path", "")).strip()
            if not path_text:
                continue
            name_text = str(entry.get("name", "")).strip() or Path(path_text).stem
            self._append_playlist_item(
                PlaylistItem(
                    name=name_text,
                    path=path_text,
                    relative_path=self._relative_path_for(path_text),
                )
            )
            added += 1
        if added > 0:
            self._status_label.setText(f"Added {added} jingle(s) to playlist.")
            self._persist_autosave()
        self._refresh_status()

    def _append_playlist_item(self, item: PlaylistItem) -> None:
        list_item = QListWidgetItem(self._label_for_item(item))
        list_item.setData(Qt.ItemDataRole.UserRole, item.path)
        list_item.setData(Qt.ItemDataRole.UserRole + 1, item.name)
        list_item.setData(Qt.ItemDataRole.UserRole + 2, item.relative_path)
        self._list.addItem(list_item)

    def _on_add_selected(self) -> None:
        payload = self._main_window.selected_playlist_candidates()
        if not payload:
            self._status_label.setText("Select one or more jingles in the main table first.")
            return
        self.add_payload_items(payload)

    def _on_remove_selected(self) -> None:
        selected_items = self._list.selectedItems()
        if not selected_items:
            self._status_label.setText("Select one or more playlist rows to remove.")
            return
        for item in selected_items:
            row = self._list.row(item)
            self._list.takeItem(row)
        self._persist_autosave()
        self._refresh_status()

    def _on_clear(self) -> None:
        if self._list.count() == 0:
            self._status_label.setText("Playlist is already empty.")
            return
        self._list.clear()
        self._persist_autosave()
        self._refresh_status()
        self._status_label.setText("Playlist cleared.")

    def _on_move_up(self) -> None:
        rows = sorted({self._list.row(item) for item in self._list.selectedItems()})
        if not rows or rows[0] <= 0:
            return
        for row in rows:
            item = self._list.takeItem(row)
            self._list.insertItem(row - 1, item)
            item.setSelected(True)
        self._persist_autosave()
        self._refresh_status()

    def _on_move_down(self) -> None:
        rows = sorted({self._list.row(item) for item in self._list.selectedItems()}, reverse=True)
        if not rows or rows[0] >= self._list.count() - 1:
            return
        for row in rows:
            item = self._list.takeItem(row)
            self._list.insertItem(row + 1, item)
            item.setSelected(True)
        self._persist_autosave()
        self._refresh_status()

    def _on_mode_toggled(self, checked: bool) -> None:
        if not self._main_window.set_playlist_preview_mode(bool(checked)):
            self._mode_btn.blockSignals(True)
            self._mode_btn.setChecked(False)
            self._mode_btn.blockSignals(False)
            checked = False
        mode_text = "Preview" if checked else "Live"
        self._mode_btn.setText(f"Mode: {mode_text}")

    def _on_loop_toggled(self, checked: bool) -> None:
        self._main_window.set_playlist_loop_enabled(bool(checked))
        self._loop_btn.setText("Loop On" if checked else "Loop Off")

    def _on_play_pause_clicked(self) -> None:
        snapshot = self._main_window.playlist_playback_snapshot()
        state = str(snapshot.get("state", "stopped"))
        active = bool(snapshot.get("active", False))

        if active and state in {"playing", "paused"}:
            self._main_window.toggle_playlist_pause_resume()
            self._refresh_from_main()
            return

        items = self._all_playlist_items()
        if not items:
            self._status_label.setText("Playlist is empty.")
            return

        start_index = 0

        paths = [item.path for item in items]
        started = self._main_window.start_playlist_playback(
            paths,
            start_index=start_index,
            loop_enabled=self._loop_btn.isChecked(),
            use_preview_mode=self._mode_btn.isChecked(),
        )
        if not started:
            self._status_label.setText("No playable playlist items were found.")
        self._refresh_from_main()

    def _on_stop_clicked(self) -> None:
        self._main_window.stop_playlist_playback()
        self._refresh_from_main()

    def _refresh_from_main(self) -> None:
        snapshot = self._main_window.playlist_playback_snapshot()
        state = str(snapshot.get("state", "stopped"))
        current_path = str(snapshot.get("current_path", "")).strip()
        loop_enabled = bool(snapshot.get("loop_enabled", False))
        preview_mode = bool(snapshot.get("is_preview_mode", False))
        queue_position_raw = snapshot.get("queue_position", -1)
        try:
            queue_position = int(queue_position_raw)
        except (TypeError, ValueError):
            queue_position = -1

        self._loop_btn.blockSignals(True)
        self._loop_btn.setChecked(loop_enabled)
        self._loop_btn.setText("Loop On" if loop_enabled else "Loop Off")
        self._loop_btn.blockSignals(False)

        self._mode_btn.blockSignals(True)
        self._mode_btn.setChecked(preview_mode)
        self._mode_btn.setText("Mode: Preview" if preview_mode else "Mode: Live")
        self._mode_btn.blockSignals(False)

        if state == "playing":
            self._play_pause_btn.setText("Pause")
        elif state == "paused":
            self._play_pause_btn.setText("Resume")
        else:
            self._play_pause_btn.setText("Play")

        if state in {"playing", "paused"} and queue_position >= 0 and queue_position < self._list.count():
            self._list.setCurrentRow(queue_position)
        else:
            self._highlight_current_path(current_path)

    def _highlight_current_path(self, current_path: str) -> None:
        if not current_path:
            return
        normalized_current = str(Path(current_path))
        for row in range(self._list.count()):
            item = self._list.item(row)
            path_text = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
            if not path_text:
                continue
            try:
                if str(Path(path_text)) == normalized_current:
                    self._list.setCurrentRow(row)
                    break
            except Exception:
                continue

    def _all_playlist_items(self) -> list[PlaylistItem]:
        items: list[PlaylistItem] = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            path_text = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
            if not path_text:
                continue
            name_text = str(item.data(Qt.ItemDataRole.UserRole + 1) or "").strip() or Path(path_text).stem
            relative_text = str(item.data(Qt.ItemDataRole.UserRole + 2) or "").strip()
            items.append(
                PlaylistItem(
                    name=name_text,
                    path=path_text,
                    relative_path=relative_text,
                )
            )
        return items

    def _relative_path_for(self, path_text: str) -> str:
        samples_dir = getattr(self._main_window, "_samples_dir", None)
        if not isinstance(samples_dir, Path):
            return ""
        try:
            return str(Path(path_text).resolve().relative_to(samples_dir.resolve()))
        except Exception:
            return ""

    def _label_for_item(self, item: PlaylistItem) -> str:
        path_obj = Path(item.path)
        exists_marker = "" if path_obj.exists() else " [missing]"
        folder_name = path_obj.parent.name if path_obj.parent.name else str(path_obj.parent)
        display_name = item.name.strip() or path_obj.stem
        return f"{display_name} ({folder_name}){exists_marker}"

    def _on_save_playlist(self) -> None:
        items = self._all_playlist_items()
        if not items:
            self._status_label.setText("Playlist is empty.")
            return

        initial_dir = getattr(self._main_window, "_samples_dir", None)
        default_path_obj: Path
        if self._last_playlist_path:
            default_path_obj = Path(self._last_playlist_path)
        else:
            if not isinstance(initial_dir, Path):
                initial_dir = getattr(self._main_window, "_app_data_dir", Path.home())
            default_path_obj = Path(initial_dir) / "playlist.json"

        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Playlist",
            str(default_path_obj),
            "Playlist JSON (*.json)",
        )
        if not selected_path:
            return

        if not self._save_playlist_to_path(Path(selected_path), items, show_errors=True):
            return
        self._set_playlist_last_path(selected_path)
        self._persist_autosave()
        self._status_label.setText(f"Saved playlist: {selected_path}")

    def _on_load_playlist(self) -> None:
        initial_dir = getattr(self._main_window, "_samples_dir", None)
        initial_open_path: Path
        if self._last_playlist_path:
            initial_open_path = Path(self._last_playlist_path)
        else:
            if not isinstance(initial_dir, Path):
                initial_dir = getattr(self._main_window, "_app_data_dir", Path.home())
            initial_open_path = Path(initial_dir)

        selected_path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Playlist",
            str(initial_open_path),
            "Playlist JSON (*.json)",
        )
        if not selected_path:
            return

        selected = Path(selected_path)
        if not self._load_playlist_from_path(selected, show_errors=True):
            return
        self._set_playlist_last_path(str(selected))
        self._persist_autosave()
        self._status_label.setText(f"Playlist loaded: {selected}")

    def _save_playlist_to_path(
        self,
        target_path: Path,
        items: list[PlaylistItem],
        show_errors: bool,
    ) -> bool:
        payload = {
            "schema_version": 1,
            "loop_enabled": self._loop_btn.isChecked(),
            "preview_mode": self._mode_btn.isChecked(),
            "items": [
                {
                    "name": item.name,
                    "path": item.path,
                    "relative_path": item.relative_path,
                }
                for item in items
            ],
        }
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            if show_errors:
                QMessageBox.warning(self, "Save Playlist", f"Could not save playlist:\n{exc}")
            return False
        return True

    def _load_playlist_from_path(self, source_path: Path, show_errors: bool) -> bool:
        try:
            raw = source_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            if show_errors:
                QMessageBox.warning(self, "Load Playlist", f"Could not load playlist:\n{exc}")
            return False

        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list):
            if show_errors:
                QMessageBox.warning(self, "Load Playlist", "Playlist file is invalid.")
            return False

        loaded_loop_enabled = bool(payload.get("loop_enabled", False)) if isinstance(payload, dict) else False
        loaded_preview_mode = bool(payload.get("preview_mode", False)) if isinstance(payload, dict) else False

        loaded_items: list[dict[str, str]] = []
        missing_count = 0
        for entry in raw_items:
            if not isinstance(entry, dict):
                continue
            name_text = str(entry.get("name", "")).strip()
            absolute_path = str(entry.get("path", "")).strip()
            relative_path = str(entry.get("relative_path", "")).strip()
            resolved_path = self._resolve_loaded_path(absolute_path, relative_path)
            if not resolved_path:
                continue
            if not Path(resolved_path).exists():
                missing_count += 1
            loaded_items.append(
                {
                    "name": name_text or Path(resolved_path).stem,
                    "path": resolved_path,
                }
            )

        self._list.clear()
        self.add_payload_items(loaded_items)
        self._loop_btn.setChecked(loaded_loop_enabled)
        self._main_window.set_playlist_loop_enabled(loaded_loop_enabled)
        self._mode_btn.setChecked(loaded_preview_mode)
        self._main_window.set_playlist_preview_mode(loaded_preview_mode)
        if missing_count > 0:
            self._status_label.setText(
                f"Loaded playlist with {missing_count} missing file(s)."
            )
        self._persist_autosave()
        return True

    def _persist_autosave(self) -> None:
        items = self._all_playlist_items()
        self._save_playlist_to_path(Path(self._autosave_path), items, show_errors=False)

    def _resolve_loaded_path(self, absolute_path: str, relative_path: str) -> str:
        samples_dir = getattr(self._main_window, "_samples_dir", None)
        if relative_path and isinstance(samples_dir, Path):
            try:
                candidate = (samples_dir / relative_path).resolve()
                if candidate.exists():
                    return str(candidate)
            except Exception:
                pass
        if absolute_path:
            return str(Path(absolute_path))
        if relative_path and isinstance(samples_dir, Path):
            return str(samples_dir / relative_path)
        return ""

    def _refresh_status(self) -> None:
        count = self._list.count()
        if count == 0:
            self.setWindowTitle("Playlists")
            return
        self.setWindowTitle(f"Playlists ({count})")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._sync_timer.stop()
        self._main_window.stop_playlist_playback()
        super().closeEvent(event)


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
