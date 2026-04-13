from __future__ import annotations

import json
import shutil
from pathlib import Path

from jingle_edit_dialog import JingleEditDialog
from PyQt6.QtWidgets import QFileDialog, QInputDialog, QMessageBox


class MainWindowFileEditMixin:
    def _on_file_export_tag_database(self) -> None:
        default_name = "jingle-tags-backup.json"
        start_dir = str(self._samples_dir) if self._samples_dir else str(Path.home())
        start_path = str(Path(start_dir) / default_name)
        target_str, _ = QFileDialog.getSaveFileName(
            self,
            "Export Tag Database",
            start_path,
            "JSON Files (*.json);;All Files (*)",
        )
        if not target_str:
            return

        target_path = Path(target_str)
        if not target_path.suffix:
            target_path = target_path.with_suffix(".json")

        try:
            self._store.export_to(target_path)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Export Failed",
                f"Could not export tag database.\n\n{exc}",
            )
            self._status.showMessage("Tag database export failed.")
            return

        self._status.showMessage(f"Exported tag database to {target_path.name}")

    def _on_file_import_tag_database(self) -> None:
        source_str, _ = QFileDialog.getOpenFileName(
            self,
            "Import Tag Database",
            str(self._samples_dir) if self._samples_dir else str(Path.home()),
            "JSON Files (*.json);;All Files (*)",
        )
        if not source_str:
            return

        reply = QMessageBox.question(
            self,
            "Import Tag Database",
            "Importing will replace the current in-memory tag database and apply it to loaded jingles.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self._status.showMessage("Tag database import cancelled.")
            return

        source_path = Path(source_str)
        try:
            self._store.import_from(source_path)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            QMessageBox.critical(
                self,
                "Import Failed",
                f"Could not import tag database.\n\n{exc}",
            )
            self._status.showMessage("Tag database import failed.")
            return

        for record in self._records:
            record.categories = self._store.get(record.path)

        self._store.save()
        self._apply_filters()
        self._status.showMessage(f"Imported tag database from {source_path.name}")

    def _selected_single_record_index(self) -> int | None:
        selected_indices = self._selected_record_indices()
        if not selected_indices:
            self._status.showMessage("Select one jingle first.")
            return None
        if len(selected_indices) != 1:
            self._status.showMessage("Select exactly one jingle for this action.")
            return None
        return selected_indices[0]

    def _validate_new_filename(self, value: str) -> str | None:
        name = value.strip()
        if not name:
            return "Filename cannot be empty."

        forbidden_chars = '<>:"/\\|?*'
        if any(ch in forbidden_chars for ch in name):
            return "Filename contains illegal characters: < > : \" / \\ | ? *"

        if any(ord(ch) < 32 for ch in name):
            return "Filename contains illegal control characters."

        if name.endswith((" ", ".")):
            return "Filename cannot end with a space or period."

        stem = Path(name).stem.strip().upper()
        if stem in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            "COM1",
            "COM2",
            "COM3",
            "COM4",
            "COM5",
            "COM6",
            "COM7",
            "COM8",
            "COM9",
            "LPT1",
            "LPT2",
            "LPT3",
            "LPT4",
            "LPT5",
            "LPT6",
            "LPT7",
            "LPT8",
            "LPT9",
        }:
            return "Filename uses a reserved Windows device name."

        return None

    def _on_edit_rename(self) -> None:
        record_index = self._selected_single_record_index()
        if record_index is None:
            return

        record = self._records[record_index]
        source_path = record.path
        current_name = source_path.name

        while True:
            new_name, accepted = QInputDialog.getText(
                self,
                "Rename Jingle",
                "New filename:",
                text=current_name,
            )
            if not accepted:
                self._status.showMessage("Rename cancelled.")
                return

            candidate = new_name.strip()
            if not Path(candidate).suffix:
                candidate = f"{candidate}{source_path.suffix}"

            validation_error = self._validate_new_filename(candidate)
            if validation_error is not None:
                QMessageBox.warning(self, "Invalid Filename", validation_error)
                current_name = candidate
                continue

            destination_path = source_path.with_name(candidate)
            if destination_path == source_path:
                self._status.showMessage("Rename cancelled: filename is unchanged.")
                return

            if destination_path.exists():
                QMessageBox.warning(
                    self,
                    "Filename Exists",
                    "A file with that name already exists. Choose a different filename.",
                )
                current_name = destination_path.name
                continue

            try:
                source_path.rename(destination_path)
            except OSError as exc:
                QMessageBox.critical(self, "Rename Failed", f"Could not rename file.\n\n{exc}")
                self._status.showMessage("Rename failed.")
                return

            record.path = destination_path
            self._store.rename(source_path, destination_path)
            self._store.save()
            self._apply_filters()
            self._status.showMessage(f"Renamed to {destination_path.name}")
            return

    def _on_edit_jingle(self) -> None:
        record_index = self._selected_single_record_index()
        if record_index is None:
            return

        record = self._records[record_index]
        if not record.path.exists():
            self._status.showMessage("Selected file no longer exists.")
            return

        dialog = JingleEditDialog(
            file_path=record.path,
            duration_seconds=record.duration_seconds,
            start_seconds=record.clip_start_seconds,
            stop_seconds=record.clip_stop_seconds,
            live_output_device=self._output_device,
            preview_output_device=self._preview_output_device,
            live_volume_percent=self._live_volume_percent,
            preview_volume_percent=self._preview_volume_percent,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            self._status.showMessage("Edit Jingle cancelled.")
            return

        start_seconds, stop_seconds = dialog.selected_clip_points()
        if (
            abs(start_seconds - record.clip_start_seconds) < 0.0005
            and abs(stop_seconds - record.clip_stop_seconds) < 0.0005
        ):
            self._status.showMessage("No jingle trim changes to save.")
            return

        self._store.set_clip_points(record.path, start_seconds, stop_seconds)
        self._store.save()

        refreshed_start, refreshed_stop = self._store.get_clip_points(record.path, record.duration_seconds)
        record.clip_start_seconds = refreshed_start
        record.clip_stop_seconds = refreshed_stop
        self._status.showMessage(f"Saved trim range for {record.path.name}.")

    def _on_edit_delete(self) -> None:
        selected_indices = self._selected_record_indices()
        if not selected_indices:
            self._status.showMessage("Select one or more jingles first.")
            return

        count = len(selected_indices)
        reply = QMessageBox.question(
            self,
            "Delete Jingle(s)",
            f"Delete {count} selected file(s)? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self._status.showMessage("Delete cancelled.")
            return

        removed_count = 0
        failed_paths: list[Path] = []
        for record_index in selected_indices:
            if record_index < 0 or record_index >= len(self._records):
                continue
            path = self._records[record_index].path
            try:
                path.unlink()
            except OSError:
                failed_paths.append(path)
                continue

            self._store.remove(path)
            removed_count += 1

        self._store.save()
        self._rescan_library()

        if failed_paths:
            QMessageBox.warning(
                self,
                "Delete Incomplete",
                f"Deleted {removed_count} file(s), but {len(failed_paths)} could not be deleted.",
            )
            self._status.showMessage(
                f"Deleted {removed_count} file(s); {len(failed_paths)} failed."
            )
            return

        self._status.showMessage(f"Deleted {removed_count} file(s).")

    def _copy_or_move_selected_jingle(self, move: bool) -> None:
        record_index = self._selected_single_record_index()
        if record_index is None:
            return

        record = self._records[record_index]
        source_path = record.path
        source_categories = list(record.categories)
        source_ext = source_path.suffix.lower()
        ext_label = source_ext[1:].upper() if source_ext.startswith(".") and len(source_ext) > 1 else "Source"
        ext_pattern = f"*{source_ext}" if source_ext else "*"
        file_filter = f"{ext_label} Files ({ext_pattern});;All Files (*)"

        operation_name = "Move" if move else "Copy"
        target_str, _ = QFileDialog.getSaveFileName(
            self,
            f"{operation_name} Jingle To",
            str(source_path),
            file_filter,
        )
        if not target_str:
            self._status.showMessage(f"{operation_name} cancelled.")
            return

        destination_path = Path(target_str)
        if destination_path.suffix == "":
            destination_path = destination_path.with_suffix(source_path.suffix)

        if destination_path == source_path:
            self._status.showMessage(f"{operation_name} cancelled: source and destination are the same.")
            return

        if destination_path.exists():
            overwrite = QMessageBox.question(
                self,
                f"{operation_name} Jingle",
                f"{destination_path.name} already exists. Overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if overwrite != QMessageBox.StandardButton.Yes:
                self._status.showMessage(f"{operation_name} cancelled.")
                return

        try:
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination_path)
            try:
                dest_stat = destination_path.stat()
                self._store.set_media_cache(
                    destination_path,
                    int(dest_stat.st_size),
                    record.duration_seconds,
                    int(dest_stat.st_mtime_ns),
                )
            except OSError:
                pass
            self._store.set(destination_path, source_categories)
            if move:
                source_path.unlink()
        except OSError as exc:
            QMessageBox.critical(self, f"{operation_name} Failed", f"Could not {operation_name.lower()} file.\n\n{exc}")
            self._status.showMessage(f"{operation_name} failed.")
            return

        if move:
            self._store.remove(source_path)
        self._store.save()

        self._rescan_library()
        self._status.showMessage(f"{operation_name} complete: {destination_path.name}")

    def _on_edit_copy_to(self) -> None:
        self._copy_or_move_selected_jingle(move=False)

    def _on_edit_move_to(self) -> None:
        self._copy_or_move_selected_jingle(move=True)


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch gui.py to start JingleAllTheDay.")
    raise SystemExit(1)
