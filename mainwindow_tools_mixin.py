from __future__ import annotations

from pathlib import Path
from typing import cast

from PyQt6.QtWidgets import QInputDialog, QMessageBox

from app_helpers import merge_tags as _merge_tags, normalize_tags as _normalize_tags
from mainwindow_contracts import MainWindowToolsHost
from models_store import JingleRecord


DUPLICATE_FORMAT_PRIORITY = {
    ".wav": 0,
    ".flac": 1,
    ".aiff": 2,
    ".aif": 2,
    ".m4a": 3,
    ".aac": 4,
    ".ogg": 5,
    ".wma": 6,
    ".mp3": 7,
}


class MainWindowToolsMixin:
    def _host(self) -> MainWindowToolsHost:
        return cast(MainWindowToolsHost, self)

    def _on_tools_clear_all_categories(self) -> None:
        host = self._host()
        if not host._records:
            host._status.showMessage("No jingles loaded.")
            return

        reply = QMessageBox.question(
            self,
            "Clear All Categories",
            "This will remove all category tags from every loaded jingle.\n\n"
            "Are you sure you want to continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            host._status.showMessage("Clear all categories cancelled.")
            return

        updated = 0
        for record in host._records:
            if record.categories:
                record.categories = []
                host._store.set(record.path, [])
                updated += 1

        host._store.save()
        self._apply_filters()
        host._status.showMessage(f"Cleared categories for {updated} jingle(s).")

    def _on_auto_folder_tags_toggled(self, checked: bool) -> None:
        host = self._host()
        host._auto_folder_tags = checked
        self._save_auto_folder_tags()
        state = "enabled" if checked else "disabled"
        host._status.showMessage(f"Auto-tag from folders on scan: {state}.")

    def _on_watch_library_changes_toggled(self, checked: bool) -> None:
        host = self._host()
        host._watch_library_changes = checked
        self._save_watch_library_changes()

        if not checked:
            host._watch_rescan_timer.stop()
            host._library_watcher.removePaths(host._library_watcher.directories())
            host._library_watcher.removePaths(host._library_watcher.files())
            host._status.showMessage("Auto-refresh on library changes: disabled.")
            return

        self._refresh_library_watcher_paths([record.path for record in host._records])
        host._status.showMessage("Auto-refresh on library changes: enabled.")

    def _on_tools_update_categories_from_folders(self) -> None:
        host = self._host()
        if not host._records:
            host._status.showMessage("No jingles loaded.")
            return

        preserve_existing = self._prompt_folder_update_mode()
        if preserve_existing is None:
            host._status.showMessage("Folder-derived category update cancelled.")
            return

        updated = self._apply_folder_titles_to_records(host._records, preserve_existing)
        mode_text = "preserved" if preserve_existing else "overwritten"
        host._status.showMessage(
            f"Updated categories from folder titles for {updated} jingle(s); existing tags {mode_text}."
        )

    def _duplicate_format_sort_key(self, path: Path) -> tuple[int, str, str]:
        suffix = path.suffix.lower()
        return (
            DUPLICATE_FORMAT_PRIORITY.get(suffix, 99),
            suffix,
            path.name.casefold(),
        )

    def _find_duplicate_audio_variants(self) -> list[list[Path]]:
        host = self._host()
        if host._samples_dir is None:
            return []

        grouped: dict[tuple[str, str], list[Path]] = {}
        for path in self._scan_audio_files(host._samples_dir):
            key = (str(path.parent).casefold(), path.stem.casefold())
            grouped.setdefault(key, []).append(path)

        duplicates: list[list[Path]] = []
        for variants in grouped.values():
            suffixes = {path.suffix.lower() for path in variants}
            if len(variants) < 2 or len(suffixes) < 2:
                continue

            ordered = sorted(variants, key=self._duplicate_format_sort_key)
            duplicates.append(ordered)

        return duplicates

    def _prompt_duplicate_resolution_mode(self, duplicate_count: int, removal_count: int, sample_paths: list[Path]) -> str | None:
        sample_keep = sample_paths[0]
        sample_remove = sample_paths[1:]
        sample_text = (
            f"\n\nExample:\nKeep: {sample_keep.name}\nRemove: "
            + ", ".join(path.name for path in sample_remove[:3])
        )

        box = QMessageBox(self)
        box.setWindowTitle("Find Duplicates")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            f"Found {duplicate_count} duplicate jingle name group(s) across multiple audio formats.\n\n"
            f"Automatic mode will remove {removal_count} lower-priority file(s), preferring WAV when available."
            f"{sample_text}\n\nHow would you like to resolve duplicates?"
        )
        auto_button = box.addButton("Automatic", QMessageBox.ButtonRole.AcceptRole)
        manual_button = box.addButton("Ask Me Each Time", QMessageBox.ButtonRole.ActionRole)
        cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(auto_button)
        box.exec()

        clicked = box.clickedButton()
        if clicked is auto_button:
            return "auto"
        if clicked is manual_button:
            return "manual"
        if clicked is cancel_button:
            return None
        return None

    def _prompt_duplicate_keep_choice(self, variants: list[Path]) -> Path | None:
        folder_name = variants[0].parent.name or str(variants[0].parent)
        items = [path.name for path in variants]
        choice, accepted = QInputDialog.getItem(
            self,
            "Find Duplicates",
            "Choose which file to keep:\n\n"
            f"Folder: {folder_name}\n"
            f"Jingle: {variants[0].stem}",
            items,
            0,
            False,
        )
        if not accepted:
            return None
        for path in variants:
            if path.name == choice:
                return path
        return None

    def _build_duplicate_removal_plan(
        self, duplicates: list[list[Path]], resolution_mode: str
    ) -> list[tuple[Path, list[Path]]] | None:
        removal_plan: list[tuple[Path, list[Path]]] = []
        for variants in duplicates:
            keep_path = variants[0]
            if resolution_mode == "manual":
                selected_keep = self._prompt_duplicate_keep_choice(variants)
                if selected_keep is None:
                    return None
                keep_path = selected_keep
            remove_paths = [path for path in variants if path != keep_path]
            if remove_paths:
                removal_plan.append((keep_path, remove_paths))
        return removal_plan

    def _on_tools_find_duplicates(self) -> None:
        host = self._host()
        if host._samples_dir is None:
            host._status.showMessage("Choose a samples folder first.")
            return

        is_playing = False
        if host._player is not None:
            try:
                from PyQt6.QtMultimedia import QMediaPlayer

                is_playing = host._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            except ModuleNotFoundError:
                is_playing = False

        if is_playing:
            host._status.showMessage("Stop playback before running Find Duplicates.")
            return

        duplicates = self._find_duplicate_audio_variants()
        if not duplicates:
            host._status.showMessage("No duplicate audio-format variants were found.")
            return

        automatic_removal_count = sum(max(0, len(variants) - 1) for variants in duplicates)
        resolution_mode = self._prompt_duplicate_resolution_mode(
            len(duplicates),
            automatic_removal_count,
            duplicates[0],
        )
        if resolution_mode is None:
            host._status.showMessage("Find Duplicates cancelled.")
            return

        removal_plan = self._build_duplicate_removal_plan(duplicates, resolution_mode)
        if removal_plan is None:
            host._status.showMessage("Find Duplicates cancelled.")
            return

        removed_count = 0
        failed_paths: list[Path] = []
        for _keep_path, remove_paths in removal_plan:
            for path in remove_paths:
                try:
                    path.unlink()
                    removed_count += 1
                except OSError:
                    failed_paths.append(path)

        if removed_count > 0:
            self._rescan_library()

        if failed_paths:
            failed_count = len(failed_paths)
            QMessageBox.warning(
                self,
                "Find Duplicates",
                f"Removed {removed_count} duplicate file(s), but {failed_count} could not be deleted.",
            )
            host._status.showMessage(
                f"Removed {removed_count} duplicate file(s); {failed_count} failed to delete."
            )
            return

        if removed_count > 0:
            host._status.showMessage(f"Removed {removed_count} duplicate file(s).")
        else:
            host._status.showMessage("No duplicate files were removed.")

    def _on_update_selected_from_folders_clicked(self) -> None:
        host = self._host()
        selected_rows = sorted({idx.row() for idx in host._table.selectedIndexes()})
        if not selected_rows:
            host._status.showMessage("Select one or more rows first.")
            return

        selected_records: list[JingleRecord] = []
        seen: set[int] = set()
        for row in selected_rows:
            if row < 0 or row >= len(host._visible_indices):
                continue
            record_index = host._visible_indices[row]
            if record_index in seen:
                continue
            seen.add(record_index)
            selected_records.append(host._records[record_index])

        if not selected_records:
            host._status.showMessage("No valid selected rows found.")
            return

        preserve_existing = self._prompt_folder_update_mode()
        if preserve_existing is None:
            host._status.showMessage("Selected folder-derived category update cancelled.")
            return

        updated = self._apply_folder_titles_to_records(selected_records, preserve_existing)
        mode_text = "preserved" if preserve_existing else "overwritten"
        host._status.showMessage(
            f"Updated selected rows from folder titles for {updated} jingle(s); existing tags {mode_text}."
        )

    def _prompt_folder_update_mode(self) -> bool | None:
        """Return True=preserve, False=overwrite, None=cancel."""
        prompt = QMessageBox(self)
        prompt.setWindowTitle("Update Categories from Folder Titles")
        prompt.setIcon(QMessageBox.Icon.Question)
        prompt.setText(
            "How should folder-derived tags be applied?\n\n"
            "Folder tags are derived from the path under your selected Samples folder."
        )
        preserve_btn = prompt.addButton("Preserve Existing Tags", QMessageBox.ButtonRole.AcceptRole)
        overwrite_btn = prompt.addButton("Overwrite Tags", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = prompt.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        prompt.setDefaultButton(preserve_btn)
        prompt.exec()

        clicked = prompt.clickedButton()
        if clicked is preserve_btn:
            return True
        if clicked is overwrite_btn:
            return False
        if clicked is cancel_btn:
            return None
        return None

    def _derive_folder_tags(self, record: JingleRecord) -> list[str]:
        """Derive folder tags under sample root, excluding the sample root itself."""
        host = self._host()
        if host._samples_dir is None:
            return []
        try:
            rel_parent = record.path.relative_to(host._samples_dir).parent
            return [part.strip() for part in rel_parent.parts if part.strip()]
        except ValueError:
            # If the file is outside selected root unexpectedly, skip derivation.
            return []

    def _apply_folder_titles_to_records(
        self,
        records: list[JingleRecord],
        preserve_existing: bool,
    ) -> int:
        host = self._host()
        updated = 0
        for record in records:
            derived_tags = self._derive_folder_tags(record)
            if preserve_existing:
                new_categories = _merge_tags(record.categories, derived_tags)
            else:
                new_categories = _normalize_tags(derived_tags)

            if new_categories != record.categories:
                record.categories = new_categories
                host._store.set(record.path, new_categories)
                updated += 1

        host._store.save()
        self._apply_filters()
        return updated


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch gui.py to start JingleAllTheDay.")
    raise SystemExit(1)
