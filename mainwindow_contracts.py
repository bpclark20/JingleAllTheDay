from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from models_store import JingleRecord, LibraryStore


class StatusBarLike(Protocol):
    def showMessage(self, message: str) -> None: ...


class LibraryWatcherLike(Protocol):
    def directories(self) -> list[str]: ...

    def files(self) -> list[str]: ...

    def removePaths(self, paths: list[str]) -> Any: ...

    def addPaths(self, paths: list[str]) -> Any: ...


class TimerLike(Protocol):
    def start(self, msec: int) -> None: ...

    def stop(self) -> None: ...


class LineEditLike(Protocol):
    def text(self) -> str: ...

    def clear(self) -> None: ...

    def setText(self, text: str) -> None: ...

    def setPlaceholderText(self, text: str) -> None: ...


class ComboBoxLike(Protocol):
    def currentData(self) -> Any: ...


class LayoutItemLike(Protocol):
    def widget(self) -> Any: ...


class ChipsLayoutLike(Protocol):
    def count(self) -> int: ...

    def takeAt(self, index: int) -> LayoutItemLike: ...

    def addWidget(self, widget: Any) -> None: ...

    def addStretch(self) -> None: ...


class ButtonLike(Protocol):
    def setEnabled(self, enabled: bool) -> None: ...


class TableIndexLike(Protocol):
    def row(self) -> int: ...


class TableLike(Protocol):
    def selectedIndexes(self) -> list[TableIndexLike]: ...


class MediaPlayerLike(Protocol):
    def playbackState(self) -> Any: ...


class MainWindowLibraryHost(Protocol):
    _app_data_dir: Path
    _samples_dir: Path | None
    _watch_library_changes: bool
    _is_rescanning: bool
    _auto_folder_tags: bool
    _auto_generate_waveforms: bool
    _records: list[JingleRecord]
    _visible_indices: list[int]
    _store: LibraryStore
    _library_watcher: LibraryWatcherLike
    _watch_rescan_timer: TimerLike
    _status: StatusBarLike
    _search_edit: LineEditLike
    _search_scope_combo: ComboBoxLike
    _category_filter_edit: LineEditLike
    _category_filter_mode: ComboBoxLike
    _clear_filters_btn: ButtonLike
    _chips_layout: ChipsLayoutLike

    def _selected_record_indices(self) -> list[int]: ...

    def _rebuild_table(self) -> None: ...

    def _apply_folder_titles_to_records(self, records: list[JingleRecord], preserve_existing: bool) -> int: ...


class MainWindowToolsHost(Protocol):
    _app_data_dir: Path
    _records: list[JingleRecord]
    _visible_indices: list[int]
    _samples_dir: Path | None
    _auto_folder_tags: bool
    _auto_generate_waveforms: bool
    _store: LibraryStore
    _status: StatusBarLike
    _watch_library_changes: bool
    _watch_rescan_timer: TimerLike
    _library_watcher: LibraryWatcherLike
    _player: MediaPlayerLike | None
    _table: TableLike

    def _save_auto_folder_tags(self) -> None: ...

    def _save_auto_generate_waveforms(self) -> None: ...

    def _save_watch_library_changes(self) -> None: ...

    def _refresh_library_watcher_paths(self, files: list[Path]) -> None: ...

    def _prompt_folder_update_mode(self) -> bool | None: ...

    def _apply_folder_titles_to_records(self, records: list[JingleRecord], preserve_existing: bool) -> int: ...

    def _apply_filters(self) -> None: ...

    def _scan_audio_files(self, root: Path) -> list[Path]: ...

    def _rescan_library(self) -> None: ...
