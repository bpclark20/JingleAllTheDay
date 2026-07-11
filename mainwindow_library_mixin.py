from __future__ import annotations

from pathlib import Path
import time
from typing import cast

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from app_helpers import (
    RESERVED_INTERNAL_TAG_RECENT,
    chip_palette_for_tag_seed as _chip_palette_for_tag_seed,
    format_duration_hms as _format_duration_hms,
    format_size_label as _format_size_label,
    normalize_tags as _normalize_tags,
    probe_duration_seconds as _probe_duration_seconds,
    tags_to_text as _tags_to_text,
)
from mainwindow_contracts import MainWindowLibraryHost
from models_store import JingleRecord
from waveform_cache import build_waveform_previews


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
        reserved_folder_paths: set[Path] = set()
        if not root.exists() or not root.is_dir():
            self._last_reserved_recent_folders = []
            return files
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            try:
                rel_parts = path.relative_to(root).parts
            except ValueError:
                rel_parts = path.parts

            blocked = False
            # Exclude files under folders named "Recent" (internal reserved tag scope).
            for index, part in enumerate(rel_parts[:-1]):
                if part.strip().casefold() != RESERVED_INTERNAL_TAG_RECENT.casefold():
                    continue
                reserved_folder_paths.add(root.joinpath(*rel_parts[: index + 1]))
                blocked = True
                break

            if blocked:
                continue
            files.append(path)

        files.sort(key=self._file_sort_key)
        self._last_reserved_recent_folders = sorted(
            reserved_folder_paths,
            key=lambda p: str(p).casefold(),
        )
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
            clip_profiles, active_profile_index = self._store.get_clip_profiles(path, duration_seconds)
            clip_start_seconds, clip_stop_seconds = clip_profiles[active_profile_index]

            records.append(
                JingleRecord(
                    path=path,
                    categories=categories,
                    added_at_epoch_seconds=self._store.get_added_at(path) or 0,
                    size_bytes=size_bytes,
                    duration_seconds=duration_seconds,
                    clip_start_seconds=clip_start_seconds,
                    clip_stop_seconds=clip_stop_seconds,
                    clip_profiles=clip_profiles,
                    active_clip_profile_index=active_profile_index,
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
            self._maybe_warn_reserved_recent_folders()
            self._refresh_library_watcher_paths(files)
            existing_paths = {path_key for path_key, _ in self._store.iter_entries()}
            new_paths = [path for path in files if str(path) not in existing_paths]
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
                added_at_epoch_seconds = self._store.ensure_added_at(
                    path,
                    max(0, int(mtime_ns / 1_000_000_000)) if mtime_ns > 0 else int(time.time()),
                )

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
                clip_profiles, active_profile_index = self._store.get_clip_profiles(path, duration_seconds)
                clip_start_seconds, clip_stop_seconds = clip_profiles[active_profile_index]

                records.append(
                    JingleRecord(
                        path=path,
                        categories=categories,
                        added_at_epoch_seconds=added_at_epoch_seconds,
                        size_bytes=size_bytes,
                        duration_seconds=duration_seconds,
                        clip_start_seconds=clip_start_seconds,
                        clip_stop_seconds=clip_stop_seconds,
                        clip_profiles=clip_profiles,
                        active_clip_profile_index=active_profile_index,
                    )
                )

            self._records = records
            self._store.save()

            waveform_generated_count = 0
            if self._auto_generate_waveforms and new_paths:
                _total, waveform_generated_count, _cached_count = build_waveform_previews(
                    new_paths,
                    cache_dir=self._app_data_dir / "waveform-cache",
                    bucket_count=900,
                )

            if self._auto_folder_tags:
                self._apply_folder_titles_to_records(self._records, preserve_existing=True)

            self._apply_filters()
            status_message = (
                f"Rescan complete: {len(records)} jingles ({changed_count} changed/new files re-probed)."
            )
            if self._auto_generate_waveforms and new_paths:
                status_message += (
                    f" Waveforms generated for {waveform_generated_count}/{len(new_paths)} new file(s)."
                )
            self._status.showMessage(status_message)
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
            is_recent = self._is_record_recent(record)
            if selected_categories:
                record_keys = {tag.casefold() for tag in record.categories}
                selected_keys = {tag.casefold() for tag in selected_categories}
                if is_recent:
                    record_keys.add(RESERVED_INTERNAL_TAG_RECENT.casefold())
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
                    tags_for_search = list(record.categories)
                    if is_recent:
                        tags_for_search.append(RESERVED_INTERNAL_TAG_RECENT)
                    haystack = _tags_to_text(tags_for_search).casefold()
                elif search_scope == "path":
                    haystack = str(record.path).casefold()
                else:
                    tags_for_search = list(record.categories)
                    if is_recent:
                        tags_for_search.append(RESERVED_INTERNAL_TAG_RECENT)
                    haystack = " ".join(
                        [
                            record.name,
                            _tags_to_text(tags_for_search),
                            str(record.path),
                        ]
                    ).casefold()
                if query not in haystack:
                    continue

            visible.append(index)

        self._visible_indices = visible
        self._rebuild_table()
        self._refresh_status_summary()

    def _is_record_recent(self, record: JingleRecord) -> bool:
        added_epoch = int(getattr(record, "added_at_epoch_seconds", 0) or 0)
        if added_epoch <= 0:
            return False
        window_days = max(1, int(getattr(self, "_recent_window_days", 14) or 14))
        age_seconds = max(0, int(time.time()) - added_epoch)
        return age_seconds <= (window_days * 24 * 60 * 60)

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
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
