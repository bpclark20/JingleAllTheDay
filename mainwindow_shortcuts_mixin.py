from __future__ import annotations

from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QKeyEvent, QKeySequence
from PyQt6.QtWidgets import QDialog

from dialogs import KeyboardShortcutsDialog


class MainWindowShortcutsMixin:
    def _load_keyboard_shortcuts(self) -> dict[str, str]:
        shortcuts: dict[str, str] = dict(self._default_keyboard_shortcuts)
        for key, default in self._default_keyboard_shortcuts.items():
            value = str(self._settings.value(f"shortcuts/{key}", default)).strip()
            if value:
                shortcuts[key] = value
            else:
                shortcuts[key] = ""
        return shortcuts

    def _save_keyboard_shortcuts(self) -> None:
        for key, default in self._default_keyboard_shortcuts.items():
            value = self._keyboard_shortcuts.get(key, default).strip()
            self._settings.setValue(f"shortcuts/{key}", value)

    def _shortcut_for(self, key: str) -> QKeySequence:
        value = self._keyboard_shortcuts.get(key, "")
        return QKeySequence(value)

    def _event_matches_shortcut(self, event: QKeyEvent, key: str) -> bool:
        seq = self._shortcut_for(key)
        if seq.isEmpty():
            return False
        event_seq = QKeySequence(event.keyCombination())
        return (
            event_seq.toString(QKeySequence.SequenceFormat.PortableText)
            == seq.toString(QKeySequence.SequenceFormat.PortableText)
        )

    def _apply_keyboard_shortcuts_to_actions(self) -> None:
        if self._rename_action is not None:
            self._rename_action.setShortcut(self._shortcut_for("rename"))
        if self._delete_action is not None:
            self._delete_action.setShortcut(self._shortcut_for("delete"))

    def _on_edit_keyboard_shortcuts(self) -> None:
        dialog = KeyboardShortcutsDialog(
            current_shortcuts=self._keyboard_shortcuts,
            default_shortcuts=self._default_keyboard_shortcuts,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selected = dialog.selected_shortcuts()
        self._keyboard_shortcuts = {
            key: selected.get(key, self._default_keyboard_shortcuts[key]).strip()
            for key in self._default_keyboard_shortcuts
        }
        self._save_keyboard_shortcuts()
        self._apply_keyboard_shortcuts_to_actions()
        self._status.showMessage("Keyboard shortcuts updated.")


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
