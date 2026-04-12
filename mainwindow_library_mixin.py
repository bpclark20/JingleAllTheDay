from __future__ import annotations

from pathlib import Path
from typing import cast

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from app_helpers import (
    chip_palette_for_tag_seed as _chip_palette_for_tag_seed,
    format_duration_hms as _format_duration_hms,
    format_size_label as _format_size_label,
    normalize_tags as _normalize_tags,
    probe_duration_seconds as _probe_duration_seconds,
    tags_to_text as _tags_to_text,
)
from mainwindow_contracts import MainWindowLibraryHost
from models_store import JingleRecord


AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".ogg",
    ".flac",
    ".m4a",
    ".aac",
    ".wma",
    ".aiff",
    ".aif",
}


class MainWindowLibraryMixin:
    def _contract(self) -> MainWindowLibraryHost:
        return cast(MainWindowLibraryHost, self)

    def _scan_audio_files(self, root: Path) -> list[Path]:
        files: list[Path] = []
        if not root.exists() or not root.is_dir():
            return files
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
                files.append(path)

        files.sort(key=self._file_sort_key)
        return files

    def _file_sort_key(self, path: Path) -> tuple[int, str, str, str, str]:
        root = self._samples_dir
        try:
            if root is not None:
                rel_parent = path.relative_to(root).parent
                parent_depth = len(rel_parent.parts)
                rel_parent_text = str(rel_parent).lower()
            else:
                raise ValueError
        except ValueError:
            parent_depth = 999
            rel_parent_text = str(path.parent).lower()

        folder_block = path.parent.name.lower()
        filename = path.stem.lower()
        full_path = str(path).lower()
        return (parent_depth, folder_block, rel_parent_text, filename, full_path)

    def _load_cached_records_for_selected_root(self) -> int:
        if self._samples_dir is None:
            return 0

        records: list[JingleRecord] = []
        for path_key, _info in self._store.iter_entries():
            path = Path(path_key)
            try:
                path.relative_to(self._samples_dir)
            except ValueError:
                continue

            if path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue

            categories = self._store.get(path)
            media_cache = self._store.get_media_cache(path)
            if media_cache is None:
                size_bytes = 0
                duration_seconds = 0.0
            else:
                size_bytes, duration_seconds, _mtime_ns = media_cache

            records.append(
                JingleRecord(
                    path=path,
                    categories=categories,
                    size_bytes=size_bytes,
                    duration_seconds=duration_seconds,
                )
            )

        records.sort(key=lambda record: self._file_sort_key(record.path))
        self._records = records
        self._apply_filters()
        return len(records)

    def _refresh_library_watcher_paths(self, files: list[Path]) -> None:
        if self._samples_dir is None or not self._watch_library_changes:
            self._library_watcher.removePaths(self._library_watcher.directories())
            self._library_watcher.removePaths(self._library_watcher.files())
            return

        watch_dirs: set[str] = {str(self._samples_dir)}
        for path in files:
            watch_dirs.add(str(path.parent))

        current_dirs = set(self._library_watcher.directories())
        remove_dirs = sorted(current_dirs - watch_dirs)
        add_dirs = sorted(watch_dirs - current_dirs)

        if remove_dirs:
            self._library_watcher.removePaths(remove_dirs)
        if add_dirs:
            self._library_watcher.addPaths(add_dirs)

    def _on_library_watch_path_changed(self, _path: str) -> None:
        if self._samples_dir is None or not self._watch_library_changes:
            return
        # Coalesce bursty filesystem events into one incremental reconcile pass.
        self._watch_rescan_timer.start(700)

    def _on_library_watch_rescan_timeout(self) -> None:
        if self._samples_dir is None or self._is_rescanning:
            return
        self._status.showMessage("Library changes detected. Refreshing...")
        self._rescan_library()

    def _rescan_library(self) -> None:
        if self._samples_dir is None:
            self._refresh_library_watcher_paths([])
            self._records = []
            self._apply_filters()
            return
        if self._is_rescanning:
            return
        self._is_rescanning = True
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            files = self._scan_audio_files(self._samples_dir)
            self._refresh_library_watcher_paths(files)
            self._store.sync_with_files(files)

            records: list[JingleRecord] = []
            changed_count = 0
            for path in files:
                categories = self._store.get(path)
                try:
                    stat = path.stat()
                    size_bytes = int(stat.st_size)
                    mtime_ns = int(stat.st_mtime_ns)
                except OSError:
                    size_bytes = 0
                    mtime_ns = 0

                cached = self._store.get_media_cache(path)
                if (
                    cached is not None
                    and cached[0] == size_bytes
                    and cached[2] == mtime_ns
                ):
                    duration_seconds = cached[1]
                else:
                    duration_seconds = _probe_duration_seconds(path)
                    changed_count += 1
                self._store.set_media_cache(path, size_bytes, duration_seconds, mtime_ns)

                records.append(
                    JingleRecord(
                        path=path,
                        categories=categories,
                        size_bytes=size_bytes,
                        duration_seconds=duration_seconds,
                    )
                )

            self._records = records
            self._store.save()

            if self._auto_folder_tags:
                self._apply_folder_titles_to_records(self._records, preserve_existing=True)

            self._apply_filters()
            self._status.showMessage(
                f"Rescan complete: {len(records)} jingles ({changed_count} changed/new files re-probed)."
            )
        finally:
            QApplication.restoreOverrideCursor()
            self._is_rescanning = False

    def _apply_filters(self) -> None:
        query = self._search_edit.text().strip().casefold()
        scope_data = self._search_scope_combo.currentData()
        search_scope = str(scope_data) if scope_data is not None else "all"
        selected_categories = _normalize_tags(self._category_filter_edit.text())
        mode_data = self._category_filter_mode.currentData()
        category_mode = str(mode_data) if mode_data is not None else "any"
        self._refresh_filter_chips(selected_categories)

        visible: list[int] = []
        for index, record in enumerate(self._records):
            if selected_categories:
                record_keys = {tag.casefold() for tag in record.categories}
                selected_keys = {tag.casefold() for tag in selected_categories}
                if category_mode == "all":
                    if not selected_keys.issubset(record_keys):
                        continue
                else:
                    if record_keys.isdisjoint(selected_keys):
                        continue

            if query:
                if search_scope == "name":
                    haystack = record.name.casefold()
                elif search_scope == "tag":
                    haystack = _tags_to_text(record.categories).casefold()
                elif search_scope == "path":
                    haystack = str(record.path).casefold()
                else:
                    haystack = " ".join(
                        [
                            record.name,
                            _tags_to_text(record.categories),
                            str(record.path),
                        ]
                    ).casefold()
                if query not in haystack:
                    continue

            visible.append(index)

        self._visible_indices = visible
        self._rebuild_table()
        self._refresh_status_summary()

    def _refresh_status_summary(self) -> None:
        total_count = len(self._records)
        shown_count = len(self._visible_indices)
        shown_bytes = sum(self._records[i].size_bytes for i in self._visible_indices)
        shown_seconds = sum(self._records[i].duration_seconds for i in self._visible_indices)

        message = (
            f"Showing {shown_count} of {total_count} jingles - "
            f"({_format_size_label(shown_bytes)}, {_format_duration_hms(shown_seconds)})"
        )

        selected_indices = self._selected_record_indices()
        selected_count = len(selected_indices)
        if selected_count > 0:
            selected_bytes = sum(self._records[i].size_bytes for i in selected_indices)
            selected_seconds = sum(self._records[i].duration_seconds for i in selected_indices)
            message += (
                f" | Selected {selected_count} "
                f"- ({_format_size_label(selected_bytes)}, {_format_duration_hms(selected_seconds)})"
            )

        self._status.showMessage(message)

    def _refresh_filter_chips(self, selected_categories: list[str]) -> None:
        self._clear_filters_btn.setEnabled(bool(selected_categories))

        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not selected_categories:
            empty = QLabel("None")
            empty.setStyleSheet("color: #888;")
            self._chips_layout.addWidget(empty)
            self._chips_layout.addStretch()
            return

        for tag in selected_categories:
            btn = QPushButton(f"{tag}  x")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            # Keep chip color stable while typing by seeding on the first character.
            seed = tag.strip()[:1] or "_"
            bg, border, hover = _chip_palette_for_tag_seed(seed)
            btn.setStyleSheet(
                "QPushButton {"
                f" border: 1px solid {border};"
                " border-radius: 10px;"
                " padding: 3px 8px;"
                f" background: {bg};"
                " color: #ffffff;"
                "}"
                f"QPushButton:hover {{ background: {hover}; }}"
            )
            btn.clicked.connect(lambda _checked=False, t=tag: self._remove_filter_tag(t))
            self._chips_layout.addWidget(btn)

        self._chips_layout.addStretch()

    def _remove_filter_tag(self, tag_to_remove: str) -> None:
        current = _normalize_tags(self._category_filter_edit.text())
        keep = [tag for tag in current if tag.casefold() != tag_to_remove.casefold()]
        self._category_filter_edit.setText(_tags_to_text(keep))

    def _clear_all_filter_tags(self) -> None:
        self._search_edit.clear()
        self._category_filter_edit.clear()

    def _on_search_scope_changed(self) -> None:
        scope_data = self._search_scope_combo.currentData()
        scope = str(scope_data) if scope_data is not None else "all"
        if scope == "name":
            self._search_edit.setPlaceholderText("Search by jingle name")
        elif scope == "tag":
            self._search_edit.setPlaceholderText("Search by tags")
        elif scope == "path":
            self._search_edit.setPlaceholderText("Search by path")
        else:
            self._search_edit.setPlaceholderText("Search by jingle name, tags, or path")
        self._apply_filters()


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch gui.py to start JingleAllTheDay.")
    raise SystemExit(1)
