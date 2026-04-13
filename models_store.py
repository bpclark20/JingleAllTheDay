from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from app_helpers import merge_tags as _merge_tags
from app_helpers import normalize_tags as _normalize_tags


@dataclass
class JingleRecord:
    path: Path
    categories: list[str]
    size_bytes: int = 0
    duration_seconds: float = 0.0
    clip_start_seconds: float = 0.0
    clip_stop_seconds: float = 0.0

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def folder(self) -> str:
        parent = self.path.parent.name
        return parent if parent else str(self.path.parent)


class LibraryStore:
    def __init__(self, json_path: Path) -> None:
        self._json_path = json_path
        self._entries: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self._json_path.exists():
            self._entries = {}
            return
        try:
            payload = json.loads(self._json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._entries = {}
            return

        self._entries = self._entries_from_payload(payload)

    @staticmethod
    def _entries_from_payload(payload: object) -> dict[str, dict[str, Any]]:
        raw_items = payload.get("items", {}) if isinstance(payload, dict) else {}
        entries: dict[str, dict[str, Any]] = {}
        if isinstance(raw_items, dict):
            for path_key, info in raw_items.items():
                if not isinstance(path_key, str) or not isinstance(info, dict):
                    continue

                # Backward-compatible read: old shape used single category/subcategory strings.
                category_raw = info.get("categories")
                if category_raw is None:
                    category_raw = info.get("category", "")

                subcategory_raw = info.get("subcategories")
                if subcategory_raw is None:
                    subcategory_raw = info.get("subcategory", "")

                categories = _merge_tags(
                    _normalize_tags(category_raw),
                    _normalize_tags(subcategory_raw),
                )

                entry: dict[str, Any] = {
                    "categories": categories,
                }

                size_raw = info.get("size_bytes")
                duration_raw = info.get("duration_seconds")
                mtime_raw = info.get("mtime_ns")
                try:
                    if size_raw is not None:
                        entry["size_bytes"] = max(0, int(size_raw))
                except (TypeError, ValueError):
                    pass
                try:
                    if duration_raw is not None:
                        entry["duration_seconds"] = max(0.0, float(duration_raw))
                except (TypeError, ValueError):
                    pass
                try:
                    if mtime_raw is not None:
                        entry["mtime_ns"] = max(0, int(mtime_raw))
                except (TypeError, ValueError):
                    pass

                clip_start_raw = info.get("clip_start_seconds")
                clip_stop_raw = info.get("clip_stop_seconds")
                try:
                    if clip_start_raw is not None:
                        entry["clip_start_seconds"] = max(0.0, float(clip_start_raw))
                except (TypeError, ValueError):
                    pass
                try:
                    if clip_stop_raw is not None:
                        entry["clip_stop_seconds"] = max(0.0, float(clip_stop_raw))
                except (TypeError, ValueError):
                    pass

                entries[path_key] = entry
        return entries

    def save(self) -> None:
        self._json_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 5, "items": self._entries}
        self._json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def export_to(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 5, "items": self._entries}
        destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def import_from(self, source: Path) -> None:
        payload = json.loads(source.read_text(encoding="utf-8"))
        self._entries = self._entries_from_payload(payload)

    def get(self, path: Path) -> list[str]:
        info = self._entries.get(str(path), {})
        categories = _normalize_tags(info.get("categories", []))
        return categories

    def set(self, path: Path, categories: list[str]) -> None:
        key = str(path)
        entry = dict(self._entries.get(key, {}))
        entry["categories"] = _normalize_tags(categories)
        self._entries[key] = entry

    def remove(self, path: Path) -> None:
        self._entries.pop(str(path), None)

    def rename(self, source: Path, destination: Path) -> None:
        source_key = str(source)
        destination_key = str(destination)
        entry = dict(self._entries.get(source_key, {}))
        if source_key in self._entries:
            self._entries.pop(source_key, None)
        if entry:
            self._entries[destination_key] = entry

    def get_media_cache(self, path: Path) -> tuple[int, float, int] | None:
        info = self._entries.get(str(path), {})
        try:
            size_bytes = int(info.get("size_bytes"))
            duration_seconds = float(info.get("duration_seconds"))
            mtime_ns = int(info.get("mtime_ns"))
        except (TypeError, ValueError):
            return None

        if size_bytes < 0 or duration_seconds < 0.0 or mtime_ns < 0:
            return None
        return size_bytes, duration_seconds, mtime_ns

    def set_media_cache(self, path: Path, size_bytes: int, duration_seconds: float, mtime_ns: int) -> None:
        key = str(path)
        entry = dict(self._entries.get(key, {}))
        entry["size_bytes"] = max(0, int(size_bytes))
        entry["duration_seconds"] = max(0.0, float(duration_seconds))
        entry["mtime_ns"] = max(0, int(mtime_ns))
        self._entries[key] = entry

    def get_clip_points(self, path: Path, duration_seconds: float) -> tuple[float, float]:
        info = self._entries.get(str(path), {})
        try:
            start = float(info.get("clip_start_seconds", 0.0))
        except (TypeError, ValueError):
            start = 0.0

        fallback_stop = max(0.0, float(duration_seconds))
        try:
            stop = float(info.get("clip_stop_seconds", fallback_stop))
        except (TypeError, ValueError):
            stop = fallback_stop

        if start < 0.0:
            start = 0.0
        if stop < 0.0:
            stop = 0.0

        if duration_seconds > 0.0:
            start = min(start, duration_seconds)
            stop = min(stop, duration_seconds)
            if stop <= start:
                start = 0.0
                stop = duration_seconds
        else:
            if stop < start:
                start, stop = stop, start

        return start, stop

    def set_clip_points(self, path: Path, start_seconds: float, stop_seconds: float) -> None:
        key = str(path)
        entry = dict(self._entries.get(key, {}))

        duration_seconds = 0.0
        try:
            duration_seconds = max(0.0, float(entry.get("duration_seconds", 0.0)))
        except (TypeError, ValueError):
            duration_seconds = 0.0

        start = max(0.0, float(start_seconds))
        stop = max(0.0, float(stop_seconds))

        if duration_seconds > 0.0:
            start = min(start, duration_seconds)
            stop = min(stop, duration_seconds)
            if stop <= start:
                start = 0.0
                stop = duration_seconds
            is_default = abs(start - 0.0) < 0.0005 and abs(stop - duration_seconds) < 0.0005
        else:
            if stop < start:
                start, stop = stop, start
            is_default = abs(start - 0.0) < 0.0005 and abs(stop - 0.0) < 0.0005

        if is_default:
            entry.pop("clip_start_seconds", None)
            entry.pop("clip_stop_seconds", None)
        else:
            entry["clip_start_seconds"] = round(start, 4)
            entry["clip_stop_seconds"] = round(stop, 4)

        self._entries[key] = entry

    def iter_entries(self) -> Iterator[tuple[str, dict[str, Any]]]:
        for path_key, info in self._entries.items():
            if isinstance(path_key, str) and isinstance(info, dict):
                yield path_key, info

    def sync_with_files(self, files: list[Path]) -> None:
        keep = {str(path) for path in files}
        self._entries = {k: v for k, v in self._entries.items() if k in keep}
        for path in files:
            key = str(path)
            if key not in self._entries:
                self._entries[key] = {"categories": []}


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
