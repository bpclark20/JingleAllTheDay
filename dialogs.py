from __future__ import annotations

from pathlib import Path

from app_helpers import coerce_volume_percent as _coerce_volume_percent
from app_helpers import format_duration_hms as _format_duration_hms
from app_helpers import format_size_label as _format_size_label
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QKeySequenceEdit,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

_has_qt_multimedia = False
try:
    from PyQt6.QtMultimedia import QMediaDevices

    _has_qt_multimedia = True
except ModuleNotFoundError:
    pass


class OptionsDialog(QDialog):
    def __init__(
        self,
        live_output_device: str,
        preview_output_device: str,
        live_volume_percent: int,
        preview_volume_percent: int,
        samples_dir: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)

        folder_row = QHBoxLayout()
        folder_label = QLabel("Samples Folder")
        folder_label.setFixedWidth(120)
        folder_row.addWidget(folder_label)
        self._folder_edit = QLineEdit(str(samples_dir) if samples_dir else "")
        self._folder_edit.setReadOnly(True)
        self._folder_edit.setPlaceholderText("No folder selected")
        folder_row.addWidget(self._folder_edit)
        folder_browse_btn = QPushButton("Browse")
        folder_browse_btn.clicked.connect(self._on_browse_folder)
        folder_row.addWidget(folder_browse_btn)
        root.addLayout(folder_row)

        live_row = QHBoxLayout()
        live_label = QLabel("Live Device")
        live_label.setFixedWidth(100)
        live_row.addWidget(live_label)

        self._live_device_combo = QComboBox()
        self._live_device_combo.setMinimumWidth(340)
        self._live_device_combo.setToolTip("Audio output used in Live mode.")
        live_row.addWidget(self._live_device_combo)

        root.addLayout(live_row)

        preview_row = QHBoxLayout()
        preview_label = QLabel("Preview Device")
        preview_label.setFixedWidth(100)
        preview_row.addWidget(preview_label)

        self._preview_device_combo = QComboBox()
        self._preview_device_combo.setMinimumWidth(340)
        self._preview_device_combo.setToolTip("Audio output used in Preview mode.")
        preview_row.addWidget(self._preview_device_combo)

        root.addLayout(preview_row)

        live_volume_row = QHBoxLayout()
        live_volume_label = QLabel("Live Volume")
        live_volume_label.setFixedWidth(100)
        live_volume_row.addWidget(live_volume_label)

        self._live_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._live_volume_slider.setRange(0, 100)
        self._live_volume_slider.setPageStep(5)
        self._live_volume_slider.setValue(_coerce_volume_percent(live_volume_percent))
        self._live_volume_slider.setToolTip("Playback volume used in Live mode.")
        live_volume_row.addWidget(self._live_volume_slider)

        self._live_volume_value_label = QLabel()
        self._live_volume_value_label.setFixedWidth(44)
        live_volume_row.addWidget(self._live_volume_value_label)

        root.addLayout(live_volume_row)

        preview_volume_row = QHBoxLayout()
        preview_volume_label = QLabel("Preview Volume")
        preview_volume_label.setFixedWidth(100)
        preview_volume_row.addWidget(preview_volume_label)

        self._preview_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._preview_volume_slider.setRange(0, 100)
        self._preview_volume_slider.setPageStep(5)
        self._preview_volume_slider.setValue(_coerce_volume_percent(preview_volume_percent))
        self._preview_volume_slider.setToolTip("Playback volume used in Preview mode.")
        preview_volume_row.addWidget(self._preview_volume_slider)

        self._preview_volume_value_label = QLabel()
        self._preview_volume_value_label.setFixedWidth(44)
        preview_volume_row.addWidget(self._preview_volume_value_label)

        root.addLayout(preview_volume_row)

        refresh_btn = QPushButton("Refresh Devices")
        refresh_btn.clicked.connect(self._on_refresh_clicked)
        refresh_row = QHBoxLayout()
        refresh_row.addWidget(refresh_btn)
        refresh_row.addStretch()

        root.addLayout(refresh_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._populate_devices(live_output_device, preview_output_device)
        self._live_volume_slider.valueChanged.connect(self._sync_volume_labels)
        self._preview_volume_slider.valueChanged.connect(self._sync_volume_labels)
        self._sync_volume_labels()

    def _populate_devices(self, live_selected: str, preview_selected: str) -> None:
        self._populate_device_combo(self._live_device_combo, live_selected)
        self._populate_device_combo(self._preview_device_combo, preview_selected)

    def _populate_device_combo(self, combo: QComboBox, selected_device: str) -> None:
        combo.blockSignals(True)
        combo.clear()

        default_name = ""
        if _has_qt_multimedia:
            try:
                default_name = QMediaDevices.defaultAudioOutput().description().strip()
                seen: set[str] = set()
                for device in QMediaDevices.audioOutputs():
                    name = device.description().strip()
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    combo.addItem(name, name)
            except Exception:
                pass

        default_label = "System Default"
        if default_name:
            default_label = f"System Default ({default_name})"
        combo.insertItem(0, default_label, "")

        target = selected_device.strip()
        if target:
            idx = combo.findData(target)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.addItem(f"{target} (Unavailable)", target)
                combo.setCurrentIndex(combo.count() - 1)
        else:
            combo.setCurrentIndex(0)

        combo.blockSignals(False)

    def _on_refresh_clicked(self) -> None:
        live_current = self._live_device_combo.currentData()
        preview_current = self._preview_device_combo.currentData()
        live_selected = str(live_current).strip() if live_current is not None else ""
        preview_selected = str(preview_current).strip() if preview_current is not None else ""
        self._populate_devices(live_selected, preview_selected)

    def selected_devices(self) -> tuple[str, str]:
        live = self._live_device_combo.currentData()
        preview = self._preview_device_combo.currentData()
        live_value = str(live).strip() if live is not None else ""
        preview_value = str(preview).strip() if preview is not None else ""
        return live_value, preview_value

    def selected_volumes(self) -> tuple[int, int]:
        return (
            _coerce_volume_percent(self._live_volume_slider.value()),
            _coerce_volume_percent(self._preview_volume_slider.value()),
        )

    def selected_folder(self) -> Path | None:
        text = self._folder_edit.text().strip()
        if not text:
            return None
        p = Path(text)
        return p if p.exists() and p.is_dir() else None

    def _on_browse_folder(self) -> None:
        current = self._folder_edit.text().strip()
        start = current if current else str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Choose Samples Folder", start)
        if not selected:
            return
        path = Path(selected)
        if path.exists() and path.is_dir():
            self._folder_edit.setText(str(path))

    def _sync_volume_labels(self) -> None:
        self._live_volume_value_label.setText(f"{self._live_volume_slider.value()}%")
        self._preview_volume_value_label.setText(f"{self._preview_volume_slider.value()}%")


class KeyboardShortcutsDialog(QDialog):
    def __init__(
        self,
        current_shortcuts: dict[str, str],
        default_shortcuts: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Keyboard Shortcuts")
        self.resize(520, 280)

        root = QVBoxLayout(self)

        help_label = QLabel("Set shortcut keys. Leave a field empty to disable that shortcut.")
        help_label.setWordWrap(True)
        root.addWidget(help_label)

        grid = QGridLayout()
        root.addLayout(grid)

        self._shortcut_editors: dict[str, QKeySequenceEdit] = {}
        self._row_labels: dict[str, str] = {}
        rows = [
            ("rename", "Rename"),
            ("delete", "Delete"),
            ("skip_previous", "Skip to Previous (while playing/paused)"),
            ("skip_next", "Skip to Next (while playing/paused)"),
            ("select_up", "Select Previous Row (when stopped)"),
            ("select_down", "Select Next Row (when stopped)"),
        ]
        for row_idx, (key, label_text) in enumerate(rows):
            label = QLabel(label_text)
            grid.addWidget(label, row_idx, 0)

            editor = QKeySequenceEdit()
            editor.setKeySequence(QKeySequence(current_shortcuts.get(key, default_shortcuts.get(key, ""))))
            grid.addWidget(editor, row_idx, 1)
            self._shortcut_editors[key] = editor
            self._row_labels[key] = label_text

            default_label = QLabel(f"Default: {default_shortcuts.get(key, '')}")
            default_label.setStyleSheet("color: #666;")
            grid.addWidget(default_label, row_idx, 2)

        controls_row = QHBoxLayout()
        reset_defaults_btn = QPushButton("Reset to Defaults")
        reset_defaults_btn.clicked.connect(self._reset_to_defaults)
        controls_row.addWidget(reset_defaults_btn)
        controls_row.addStretch()
        root.addLayout(controls_row)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

        self._default_shortcuts = dict(default_shortcuts)

    def _reset_to_defaults(self) -> None:
        for key, editor in self._shortcut_editors.items():
            editor.setKeySequence(QKeySequence(self._default_shortcuts.get(key, "")))

    def selected_shortcuts(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, editor in self._shortcut_editors.items():
            out[key] = editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        return out

    def _find_conflicts(self) -> list[tuple[str, list[str]]]:
        assignments: dict[str, list[str]] = {}
        for key, editor in self._shortcut_editors.items():
            seq_text = editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText).strip()
            if not seq_text:
                continue
            assignments.setdefault(seq_text, []).append(self._row_labels.get(key, key))

        conflicts: list[tuple[str, list[str]]] = []
        for seq_text, labels in assignments.items():
            if len(labels) > 1:
                conflicts.append((seq_text, labels))
        return conflicts

    def _on_accept(self) -> None:
        conflicts = self._find_conflicts()
        if conflicts:
            lines = ["These shortcuts are assigned to multiple actions:", ""]
            for seq_text, labels in conflicts:
                lines.append(f"{seq_text}: {', '.join(labels)}")
            lines.append("")
            lines.append("Please resolve conflicts before saving.")
            QMessageBox.warning(self, "Shortcut Conflict", "\n".join(lines))
            return
        self.accept()


class AboutDialog(QDialog):
    def __init__(
        self,
        *,
        app_name: str,
        app_version: str,
        icon_path: Path,
        library_count: int,
        library_duration_seconds: float,
        library_size_bytes: int,
        revision_log_path: Path | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {app_name}")
        self.setMinimumWidth(620)
        self._app_name = app_name
        self._app_version = app_version
        self._revision_log_path = revision_log_path

        self._icon_base = QPixmap(str(icon_path)) if icon_path.is_file() else QPixmap()

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(16)

        icon_wrap = QWidget(self)
        icon_layout = QVBoxLayout(icon_wrap)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.addStretch(1)

        self._icon_label = QLabel(icon_wrap)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setMinimumSize(1, 1)
        self._icon_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        icon_layout.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignHCenter)
        icon_layout.addStretch(1)

        text_wrap = QWidget(self)
        text_layout = QVBoxLayout(text_wrap)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(10)

        self._title_label = QLabel(self._app_name)
        self._title_label.setStyleSheet("font-size: 20px; font-weight: 700;")
        text_layout.addWidget(self._title_label)

        self._version_label = QLabel(f"Version {self._app_version}")
        self._version_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        intro_layout = QVBoxLayout()
        intro_layout.setContentsMargins(0, 0, 0, 0)
        intro_layout.setSpacing(2)
        intro_layout.addWidget(self._version_label)

        self._body_label = QLabel(
            "\nJingleAllTheDay is a desktop jingle library manager and playback board.\n\n"
            "You can scan and organize jingles, tag tracks by category, search and filter quickly, "
            "and trigger reliable playback in Live or Preview mode with selectable output devices.\n\n"
            "The app is built for fast on-air workflows, with keyboard shortcuts, batch tag tools, "
            "duplicate detection, and import/export of your tag database."
        )
        self._body_label.setWordWrap(True)
        self._body_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._body_label.setMinimumWidth(430)
        intro_layout.addWidget(self._body_label)
        text_layout.addLayout(intro_layout)

        count_label = f"{max(0, int(library_count)):,}"
        duration_label = _format_duration_hms(max(0.0, float(library_duration_seconds)))
        size_label = _format_size_label(max(0, int(library_size_bytes)))

        self._library_summary_label = QLabel(
            "Current Library\n"
            f"Jingles: {count_label}\n"
            f"Total Duration: {duration_label}\n"
            f"Total Size: {size_label}"
        )
        self._library_summary_label.setWordWrap(True)
        self._library_summary_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        text_layout.addWidget(self._library_summary_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self._show_revision_history_btn = QPushButton("Show Revision History")
        self._show_revision_history_btn.setEnabled(
            self._revision_log_path is not None and self._revision_log_path.is_file()
        )
        if not self._show_revision_history_btn.isEnabled():
            self._show_revision_history_btn.setToolTip("rev.log was not found in the runtime application folder.")
        self._show_revision_history_btn.clicked.connect(self._on_show_revision_history)
        button_row.addWidget(self._show_revision_history_btn)
        text_layout.addLayout(button_row)

        root.addWidget(icon_wrap, 1)
        root.addWidget(text_wrap, 3)

        text_height = (
            self._title_label.sizeHint().height()
            + text_layout.spacing()
            + self._version_label.sizeHint().height()
            + text_layout.spacing()
            + self._body_label.sizeHint().height()
            + text_layout.spacing()
            + self._library_summary_label.sizeHint().height()
            + text_layout.spacing()
            + self._show_revision_history_btn.sizeHint().height()
        )
        frame_height = root.contentsMargins().top() + root.contentsMargins().bottom() + 8
        initial_height = max(220, text_height + frame_height)
        self.resize(640, initial_height)
        self._refresh_icon()
        self.setFixedSize(self.size())

    def _on_show_revision_history(self) -> None:
        if self._revision_log_path is None or not self._revision_log_path.is_file():
            QMessageBox.information(
                self,
                "Revision History",
                "rev.log was not found in the runtime application folder.",
            )
            return

        try:
            revision_text = self._revision_log_path.read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Revision History",
                f"Unable to read revision history file.\n\n{exc}",
            )
            return

        dialog = RevisionHistoryDialog(
            revision_text=revision_text,
            app_name=self._app_name,
            app_version=self._app_version,
            parent=self,
        )
        dialog.exec()

    def _refresh_icon(self) -> None:
        if self._icon_base.isNull():
            self._icon_label.setText("No icon")
            return

        max_by_height = max(48, self.height() - 80)
        max_by_width = max(48, self.width() // 4)
        side_cap = max(100, self.height() // 3)
        side = max(48, min(max_by_height, max_by_width, side_cap))
        scaled = self._icon_base.scaled(
            side,
            side,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._icon_label.setText("")
        self._icon_label.setPixmap(scaled)


class RevisionHistoryDialog(QDialog):
    def __init__(
        self,
        *,
        revision_text: str,
        app_name: str,
        app_version: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Revision History")
        self.setMinimumSize(700, 420)

        root = QVBoxLayout(self)
        title = QLabel(f"{app_name} {app_version}")
        title.setStyleSheet("font-weight: 700;")
        root.addWidget(title)

        history_view = QPlainTextEdit(self)
        history_view.setReadOnly(True)
        history_view.setPlainText(revision_text)
        root.addWidget(history_view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch gui.py to start JingleAllTheDay.")
    raise SystemExit(1)
