from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

from PyQt6.QtWidgets import QMessageBox

from dialogs import OfflineCacheBackupDialog
import remote_server as _remote_server


class MainWindowCacheBackupMixin:
    def _maybe_show_cache_backup_reminder(self) -> None:
        if not hasattr(self, "_remote_manager") or not self._remote_manager.is_connected():
            return
        now = time.time()
        if now < self._cache_backup_snooze_until_epoch:
            return
        reminder_seconds = self._cache_backup_reminder_days * 86400
        if now - self._cache_backup_last_epoch < reminder_seconds:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Offline Cache Backup Reminder")
        box.setText(
            "The remote-control server's offline jingle cache may be out of date.\n"
            "Back it up now so guests can still browse the library while the jingle machine is offline?"
        )
        backup_btn = box.addButton("Backup Now", QMessageBox.ButtonRole.AcceptRole)
        remind_btn = box.addButton("Remind me in 24 hours", QMessageBox.ButtonRole.RejectRole)
        ignore_btn = box.addButton(
            f"Ignore for {self._cache_backup_reminder_days} days", QMessageBox.ButtonRole.DestructiveRole
        )
        box.exec()
        clicked = box.clickedButton()
        if clicked is backup_btn:
            self._on_file_offline_cache_backup()
        elif clicked is remind_btn:
            self._cache_backup_snooze_until_epoch = now + 86400
            self._settings.setValue("server/cacheBackupSnoozeUntilEpoch", self._cache_backup_snooze_until_epoch)
        elif clicked is ignore_btn:
            self._cache_backup_snooze_until_epoch = now + reminder_seconds
            self._settings.setValue("server/cacheBackupSnoozeUntilEpoch", self._cache_backup_snooze_until_epoch)

    def _update_offline_cache_backup_action_enabled(self) -> None:
        if hasattr(self, "_offline_cache_backup_action"):
            connected = hasattr(self, "_remote_manager") and self._remote_manager.is_connected()
            active = (
                getattr(self, "_cache_backup_thread", None) is not None
                or getattr(self, "_cache_backup_dialog", None) is not None
            )
            self._offline_cache_backup_action.setEnabled(connected and not active)

    def _on_file_offline_cache_backup(self) -> None:
        if not hasattr(self, "_remote_manager") or not self._remote_manager.is_connected():
            QMessageBox.information(
                self, "Offline Cache Backup", "Connect to a remote-control server first (Server menu)."
            )
            return
        if getattr(self, "_cache_backup_dialog", None) is not None:
            self._status.showMessage("Offline cache backup is already open.")
            return
        self._cache_backup_cancel_event = threading.Event()
        self._cache_backup_progress_lock = threading.Lock()
        self._cache_backup_progress: dict[str, Any] = {
            "started": False,
            "running": False,
            "total": 0,
            "completed": 0,
            "uploaded": 0,
            "skipped": 0,
            "detail": "Click Start to begin.",
        }
        self._cache_backup_dialog = OfflineCacheBackupDialog(
            self._cache_backup_progress_snapshot,
            self._start_offline_cache_backup,
            self._cancel_offline_cache_backup,
            self,
        )
        self._cache_backup_dialog.finished.connect(self._on_cache_backup_dialog_closed)
        self._update_offline_cache_backup_action_enabled()
        self._cache_backup_dialog.show()

    def _cache_backup_progress_snapshot(self) -> dict[str, Any]:
        with self._cache_backup_progress_lock:
            return dict(self._cache_backup_progress)

    def _update_cache_backup_progress(self, **changes: Any) -> None:
        with self._cache_backup_progress_lock:
            self._cache_backup_progress.update(changes)

    def _start_offline_cache_backup(self) -> None:
        if getattr(self, "_cache_backup_thread", None) is not None:
            return
        self._update_cache_backup_progress(started=True, running=True, detail="Checking remote cache...")
        self._status.showMessage("Offline cache backup started...")

        def _worker() -> None:
            try:
                result = self._perform_offline_cache_backup(
                    self._update_cache_backup_progress,
                    self._cache_backup_cancel_event,
                )
            except Exception as exc:  # noqa: BLE001 - surface any failure to the user
                self._cache_backup_finished.emit(None, exc)
                return
            self._cache_backup_finished.emit(result, None)

        self._cache_backup_thread = threading.Thread(target=_worker, name="jatd-cache-backup", daemon=True)
        self._cache_backup_thread.start()

    def _cancel_offline_cache_backup(self) -> None:
        self._cache_backup_cancel_event.set()
        self._update_cache_backup_progress(detail="Cancelling after the current file finishes...")

    def _on_cache_backup_finished(self, result: dict[str, Any] | None, exc: Exception | None) -> None:
        self._cache_backup_thread = None
        self._update_cache_backup_progress(running=False)
        self._update_offline_cache_backup_action_enabled()
        dialog = getattr(self, "_cache_backup_dialog", None)
        if dialog is not None:
            dialog.finish()
        if exc is not None:
            QMessageBox.warning(self, "Offline Cache Backup Failed", str(exc))
            return
        if result is None:
            return
        if result.get("cancelled"):
            self._status.showMessage("Offline cache backup cancelled.")
            return
        now = time.time()
        self._cache_backup_last_epoch = now
        self._settings.setValue("server/lastCacheBackupEpoch", now)
        summary = (
            f"{result['uploaded']} uploaded, {result['skipped']} already up to date "
            f"({result['total']} jingle(s) cached total)."
        )
        self._status.showMessage(f"Offline cache backup complete: {summary}")
        QMessageBox.information(self, "Offline Cache Backup Complete", summary)

    def _on_cache_backup_dialog_closed(self) -> None:
        self._cache_backup_dialog = None
        self._update_offline_cache_backup_action_enabled()

    def _perform_offline_cache_backup(
        self,
        report_progress: Callable[..., None],
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        import urllib.error
        import urllib.parse
        import urllib.request

        def _raise_with_detail(exc: "urllib.error.HTTPError", context: str) -> None:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = ""
            raise RuntimeError(f"{context} (HTTP {exc.code}): {detail or exc.reason}") from exc

        base_url = _remote_server.relay_http_base_url(self._server_address)
        headers = {"X-Device-Token": self._server_device_token}

        status_request = urllib.request.Request(f"{base_url}/agent/cache/status", headers=headers, method="GET")
        try:
            with urllib.request.urlopen(status_request, timeout=30) as response:
                existing_files: dict[str, int] = json.loads(response.read()).get("files", {})
        except urllib.error.HTTPError as exc:
            _raise_with_detail(exc, "Could not read existing cache status")

        records = [record for record in self._records if record.path.exists()]
        report_progress(total=len(records), completed=0, detail="Comparing local files with the remote cache...")
        manifest_items: list[dict[str, Any]] = []
        uploaded = 0
        skipped = 0
        completed = 0
        for record in records:
            if cancel_event.is_set():
                return {"uploaded": uploaded, "skipped": skipped, "total": len(records), "cancelled": True}
            relpath = _remote_server.cache_relpath_for_path(str(record.path))
            local_size = record.path.stat().st_size
            if existing_files.get(relpath) == local_size:
                skipped += 1
                detail = f"Up to date: {record.name}"
            else:
                report_progress(detail=f"Uploading: {record.name}")
                data = record.path.read_bytes()
                url = f"{base_url}/agent/cache/file?relpath={urllib.parse.quote(relpath)}"
                request = urllib.request.Request(url, data=data, headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(request, timeout=30) as response:
                        confirmed_bytes = json.loads(response.read()).get("bytes")
                except urllib.error.HTTPError as exc:
                    _raise_with_detail(exc, f"Upload failed for '{record.name}'")
                if confirmed_bytes != len(data):
                    raise RuntimeError(
                        f"Upload for '{record.name}' was incomplete ({confirmed_bytes} of {len(data)} bytes received) - try again."
                    )
                uploaded += 1
                detail = f"Uploaded: {record.name}"
            manifest_items.append(
                {
                    "name": record.name,
                    "path": str(record.path),
                    "categories": list(record.categories),
                    "duration_seconds": record.duration_seconds,
                    "size_bytes": local_size,
                }
            )
            completed += 1
            report_progress(completed=completed, uploaded=uploaded, skipped=skipped, detail=detail)
        if cancel_event.is_set():
            return {"uploaded": uploaded, "skipped": skipped, "total": len(records), "cancelled": True}
        report_progress(detail="Verifying remote cache files...")
        try:
            with urllib.request.urlopen(status_request, timeout=30) as response:
                verified_files: dict[str, int] = json.loads(response.read()).get("files", {})
        except urllib.error.HTTPError as exc:
            _raise_with_detail(exc, "Could not verify remote cache status")
        incomplete = [
            item["name"]
            for item in manifest_items
            if verified_files.get(_remote_server.cache_relpath_for_path(item["path"])) != item["size_bytes"]
        ]
        if incomplete:
            raise RuntimeError(f"Remote cache verification failed for {len(incomplete)} jingle(s): {', '.join(incomplete[:3])}")
        report_progress(detail="Updating cached library index...")
        manifest_request = urllib.request.Request(
            f"{base_url}/agent/cache/manifest",
            data=json.dumps({"items": manifest_items}).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(manifest_request, timeout=30):
                pass
        except urllib.error.HTTPError as exc:
            _raise_with_detail(exc, "Manifest upload failed")
        return {"uploaded": uploaded, "skipped": skipped, "total": len(records), "cancelled": False}


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
