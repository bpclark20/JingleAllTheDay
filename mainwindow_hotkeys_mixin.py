from __future__ import annotations

import ctypes
import os
from typing import Any, Callable

from PyQt6.QtCore import QAbstractNativeEventFilter, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from app_helpers import (
    _has_pynput,
    _has_windows_native_hotkeys,
    _MOD_ALT,
    _MOD_CONTROL,
    _MOD_NOREPEAT,
    _MOD_SHIFT,
    _pynput_keyboard,
    _WM_HOTKEY,
)


def _is_wayland_session() -> bool:
    session_type = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if session_type == "wayland":
        return True
    return bool(os.environ.get("WAYLAND_DISPLAY", "").strip())


class _WindowsHotkeyEventFilter(QAbstractNativeEventFilter):
    def __init__(
        self,
        id_to_pad: dict[int, int],
        on_pad_hotkey: Callable[[int], None],
    ) -> None:
        super().__init__()
        self._id_to_pad = id_to_pad
        self._on_pad_hotkey = on_pad_hotkey

    def nativeEventFilter(self, event_type: Any, message: Any) -> tuple[bool, int]:
        if not _has_windows_native_hotkeys:
            return False, 0
        event_name = bytes(event_type).decode("utf-8", errors="ignore")
        if event_name not in ("windows_generic_MSG", "windows_dispatcher_MSG"):
            return False, 0
        try:
            msg = ctypes.wintypes.MSG.from_address(int(message))
        except Exception:
            return False, 0
        if int(msg.message) != _WM_HOTKEY:
            return False, 0

        hotkey_id = int(msg.wParam)
        pad_index = self._id_to_pad.get(hotkey_id)
        if pad_index is None:
            return False, 0

        self._on_pad_hotkey(pad_index)
        return True, 0


class MainWindowHotkeysMixin:
    def _on_sample_pads_global_hotkeys_toggled(self, enabled: bool) -> None:
        self._set_sample_pad_global_hotkeys(enabled)

    def _on_sample_pads_alt_modifier_toggled(self, enabled: bool) -> None:
        self._sample_pad_alt_modifier = enabled
        self._settings.setValue(
            "samplePads/globalHotkeysAltModifier",
            "true" if enabled else "false",
        )
        # Restart the active listener so it picks up the new modifier
        if self._sample_pad_hotkey_backend != "none":
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self._stop_sample_pad_global_hotkeys()
                self._start_sample_pad_global_hotkeys()
            finally:
                QApplication.restoreOverrideCursor()

    def _on_sample_pads_board_switch_ctrl_modifier_toggled(self, enabled: bool) -> None:
        self._sample_pad_board_switch_requires_ctrl = enabled
        self._settings.setValue(
            "samplePads/boardSwitchRequiresCtrl",
            "true" if enabled else "false",
        )
        if self._sample_pad_hotkey_backend != "none":
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self._stop_sample_pad_global_hotkeys()
                self._start_sample_pad_global_hotkeys()
            finally:
                QApplication.restoreOverrideCursor()

    def _set_sample_pad_global_hotkeys(self, enabled: bool) -> None:
        target_enabled = bool(enabled)
        global_hotkeys_available = _has_windows_native_hotkeys or _has_pynput
        if target_enabled and not global_hotkeys_available:
            target_enabled = False
            message = "Global sample pad hotkeys unavailable on this system."
            if _is_wayland_session():
                message += " On Wayland, desktop security rules may block global keyboard hooks."
            self._status.showMessage(message)

        if target_enabled:
            self._start_sample_pad_global_hotkeys()
        else:
            self._stop_sample_pad_global_hotkeys()

        self._sample_pad_global_hotkeys_enabled = target_enabled
        self._settings.setValue(
            "samplePads/globalHotkeysEnabled",
            "true" if target_enabled else "false",
        )

        if self._sample_pads_window is not None:
            self._sample_pads_window.set_global_hotkeys_enabled(target_enabled)

    def _start_sample_pad_global_hotkeys(self) -> None:
        if self._sample_pad_hotkey_backend != "none":
            return

        # Prefer non-exclusive listener first so number keys still type normally
        # in other applications while global pad triggers are enabled.
        if _has_pynput and _pynput_keyboard is not None:
            if self._start_pynput_hotkeys():
                return

        # Fallback to Windows native registration only when pynput is unavailable.
        if _has_windows_native_hotkeys and self._start_windows_native_hotkeys():
            self._status.showMessage(
                "Global hotkeys running in Windows-native fallback mode; number keys may be captured system-wide."
            )
            return

        self._sample_pad_global_hotkeys_enabled = False
        self._settings.setValue("samplePads/globalHotkeysEnabled", "false")
        if self._sample_pads_window is not None:
            self._sample_pads_window.set_global_hotkeys_enabled(False)
        message = "Could not start global hotkeys listener."
        if _is_wayland_session():
            message += " On Wayland, try X11 or ensure the listener backend is permitted."
        self._status.showMessage(message)

    def _start_pynput_hotkeys(self) -> bool:
        if not _has_pynput or _pynput_keyboard is None:
            return False

        use_alt = self._sample_pad_alt_modifier
        board_switch_requires_ctrl = self._sample_pad_board_switch_requires_ctrl
        alt_keys = (
            _pynput_keyboard.Key.alt,
            _pynput_keyboard.Key.alt_l,
            _pynput_keyboard.Key.alt_r,
            _pynput_keyboard.Key.alt_gr,
        )
        ctrl_keys = (
            _pynput_keyboard.Key.ctrl,
            _pynput_keyboard.Key.ctrl_l,
            _pynput_keyboard.Key.ctrl_r,
        )
        shift_keys = (
            _pynput_keyboard.Key.shift,
            _pynput_keyboard.Key.shift_l,
            _pynput_keyboard.Key.shift_r,
        )
        _alt_held = [False]
        _ctrl_held = [False]
        _shift_held = [False]
        _keys_held: set[str] = set()
        # char → pad index for bare/alt keys (pads 0-9)
        _char_to_pad = {'1': 0, '2': 1, '3': 2, '4': 3, '5': 4,
                        '6': 5, '7': 6, '8': 7, '9': 8, '0': 9}
        _vk_to_board = {49: 0, 50: 1, 51: 2, 52: 3, 53: 4}
        # char → pad index for Ctrl+key (pads 10-15)
        # VK codes for digit keys: '1'=49...'9'=57, '0'=48
        # When Ctrl is held pynput sets key.char=None for digits, so we use key.vk
        _vk_to_ctrl_pad = {49: 10, 50: 11, 51: 12, 52: 13, 53: 14,
                           54: 15, 55: 16, 56: 17, 57: 18, 48: 19}

        def _on_press(key: Any) -> None:
            # Track modifier keys
            if key in ctrl_keys:
                _ctrl_held[0] = True
                return
            if key in shift_keys:
                _shift_held[0] = True
                return
            if use_alt:
                if key in alt_keys:
                    _alt_held[0] = True
                    return
            board_combo_active = (
                _shift_held[0] and _ctrl_held[0]
                if board_switch_requires_ctrl
                else _shift_held[0] and not _ctrl_held[0]
            )
            if board_combo_active:
                try:
                    vk = key.vk
                except AttributeError:
                    return
                board_index = _vk_to_board.get(vk)
                if board_index is not None:
                    held_key = f'board+{vk}'
                    if held_key not in _keys_held:
                        _keys_held.add(held_key)
                        self._sample_pad_board_switch_requested.emit(board_index)
                return
            # Ctrl+digit → pads 10-19: use VK codes because key.char is None when Ctrl held
            if _ctrl_held[0]:
                try:
                    vk = key.vk
                except AttributeError:
                    return
                pad_index = _vk_to_ctrl_pad.get(vk)
                if pad_index is not None:
                    held_key = f'ctrl+{vk}'
                    if held_key not in _keys_held:
                        _keys_held.add(held_key)
                        self._sample_pad_hotkey_requested.emit(pad_index)
                return
            # Normal pads 0-9 with optional alt modifier
            if use_alt and not _alt_held[0]:
                return
            try:
                char = key.char
            except AttributeError:
                return
            if char is None or char not in _char_to_pad:
                return
            # Suppress auto-repeat: only emit if this key wasn't already held
            if char in _keys_held:
                return
            _keys_held.add(char)
            self._sample_pad_hotkey_requested.emit(_char_to_pad[char])

        def _on_release(key: Any) -> None:
            # Ctrl released: emit releases for all held ctrl+vk pads
            if key in ctrl_keys:
                _ctrl_held[0] = False
                to_release = [k for k in list(_keys_held) if k.startswith('ctrl+')]
                for held_key in to_release:
                    _keys_held.discard(held_key)
                    vk = int(held_key[5:])
                    pad_index = _vk_to_ctrl_pad.get(vk)
                    if pad_index is not None:
                        self._sample_pad_release_requested.emit(pad_index)
                for held_key in [k for k in list(_keys_held) if k.startswith('board+')]:
                    _keys_held.discard(held_key)
                return
            if key in shift_keys:
                _shift_held[0] = False
                for held_key in [k for k in list(_keys_held) if k.startswith('board+')]:
                    _keys_held.discard(held_key)
                return
            if use_alt and key in alt_keys:
                _alt_held[0] = False
                return
            board_combo_active = (
                _shift_held[0] and _ctrl_held[0]
                if board_switch_requires_ctrl
                else _shift_held[0] and not _ctrl_held[0]
            )
            if board_combo_active:
                try:
                    vk = key.vk
                except AttributeError:
                    return
                _keys_held.discard(f'board+{vk}')
                return
            # Check for ctrl+digit release via VK (digit released while Ctrl still held)
            try:
                vk = key.vk
            except AttributeError:
                vk = None
            if vk is not None:
                held_key = f'ctrl+{vk}'
                if held_key in _keys_held:
                    _keys_held.discard(held_key)
                    pad_index = _vk_to_ctrl_pad.get(vk)
                    if pad_index is not None:
                        self._sample_pad_release_requested.emit(pad_index)
                    return
            # Normal pad release
            try:
                char = key.char
            except AttributeError:
                return
            if char is None or char not in _char_to_pad:
                return
            _keys_held.discard(char)
            self._sample_pad_release_requested.emit(_char_to_pad[char])

        try:
            self._sample_pad_listener = _pynput_keyboard.Listener(
                on_press=_on_press,
                on_release=_on_release,
            )
            self._sample_pad_listener.daemon = True
            self._sample_pad_listener.start()
            self._sample_pad_hotkey_backend = "pynput"
            return True
        except Exception:
            self._sample_pad_listener = None
            self._sample_pad_hotkey_backend = "none"
            return False

    def _start_windows_native_hotkeys(self) -> bool:
        if not _has_windows_native_hotkeys:
            return False

        app = QApplication.instance()
        if app is None:
            return False

        user32 = ctypes.windll.user32
        registered_ids: list[int] = []
        id_to_pad: dict[int, int] = {}
        mod_flags = _MOD_NOREPEAT | (_MOD_ALT if self._sample_pad_alt_modifier else 0)
        ctrl_mod_flags = _MOD_NOREPEAT | _MOD_CONTROL
        board_mod_flags = _MOD_NOREPEAT | _MOD_SHIFT
        if self._sample_pad_board_switch_requires_ctrl:
            board_mod_flags |= _MOD_CONTROL

        # Pads 0-9: keys 1-9 and 0 (with optional alt modifier)
        pad_key_specs = [
            (5000, int(Qt.Key.Key_1), 0), (5001, int(Qt.Key.Key_2), 1),
            (5002, int(Qt.Key.Key_3), 2), (5003, int(Qt.Key.Key_4), 3),
            (5004, int(Qt.Key.Key_5), 4), (5005, int(Qt.Key.Key_6), 5),
            (5006, int(Qt.Key.Key_7), 6), (5007, int(Qt.Key.Key_8), 7),
            (5008, int(Qt.Key.Key_9), 8), (5009, int(Qt.Key.Key_0), 9),
        ]
        # Pads 10-15: Ctrl+1-6 (always, regardless of alt modifier)
        ctrl_key_specs = [
            (5010, int(Qt.Key.Key_1), 10), (5011, int(Qt.Key.Key_2), 11),
            (5012, int(Qt.Key.Key_3), 12), (5013, int(Qt.Key.Key_4), 13),
            (5014, int(Qt.Key.Key_5), 14), (5015, int(Qt.Key.Key_6), 15),
            (5016, int(Qt.Key.Key_7), 16), (5017, int(Qt.Key.Key_8), 17),
            (5018, int(Qt.Key.Key_9), 18), (5019, int(Qt.Key.Key_0), 19),
        ]
        board_key_specs = [
            (5020, int(Qt.Key.Key_1), 100), (5021, int(Qt.Key.Key_2), 101),
            (5022, int(Qt.Key.Key_3), 102), (5023, int(Qt.Key.Key_4), 103),
            (5024, int(Qt.Key.Key_5), 104),
        ]

        for hotkey_id, vk_code, pad_idx in pad_key_specs:
            if not user32.RegisterHotKey(None, hotkey_id, mod_flags, vk_code):
                for existing_id in registered_ids:
                    user32.UnregisterHotKey(None, existing_id)
                return False
            registered_ids.append(hotkey_id)
            id_to_pad[hotkey_id] = pad_idx

        for hotkey_id, vk_code, pad_idx in ctrl_key_specs:
            if not user32.RegisterHotKey(None, hotkey_id, ctrl_mod_flags, vk_code):
                for existing_id in registered_ids:
                    user32.UnregisterHotKey(None, existing_id)
                return False
            registered_ids.append(hotkey_id)
            id_to_pad[hotkey_id] = pad_idx

        for hotkey_id, vk_code, board_marker in board_key_specs:
            if not user32.RegisterHotKey(None, hotkey_id, board_mod_flags, vk_code):
                for existing_id in registered_ids:
                    user32.UnregisterHotKey(None, existing_id)
                return False
            registered_ids.append(hotkey_id)
            id_to_pad[hotkey_id] = board_marker

        self._sample_pad_hotkey_id_to_pad = id_to_pad
        self._sample_pad_win_filter = _WindowsHotkeyEventFilter(
            id_to_pad=self._sample_pad_hotkey_id_to_pad,
            on_pad_hotkey=lambda pad_index: self._sample_pad_board_switch_requested.emit(pad_index - 100)
            if pad_index >= 100
            else self._sample_pad_hotkey_requested.emit(pad_index),
        )
        app.installNativeEventFilter(self._sample_pad_win_filter)
        self._sample_pad_hotkey_backend = "windows-native"
        return True

    def _stop_sample_pad_global_hotkeys(self) -> None:
        if self._sample_pad_hotkey_backend == "windows-native":
            app = QApplication.instance()
            if app is not None and self._sample_pad_win_filter is not None:
                try:
                    app.removeNativeEventFilter(self._sample_pad_win_filter)
                except Exception:
                    pass
            if _has_windows_native_hotkeys:
                user32 = ctypes.windll.user32
                for hotkey_id in list(self._sample_pad_hotkey_id_to_pad.keys()):
                    try:
                        user32.UnregisterHotKey(None, hotkey_id)
                    except Exception:
                        pass
            self._sample_pad_hotkey_id_to_pad = {}
            self._sample_pad_win_filter = None
            self._sample_pad_hotkey_backend = "none"
            return

        if self._sample_pad_listener is None:
            self._sample_pad_hotkey_backend = "none"
            return
        try:
            self._sample_pad_listener.stop()
        except Exception:
            pass
        self._sample_pad_listener = None
        self._sample_pad_hotkey_backend = "none"

    def _on_sample_pad_hotkey_requested(self, pad_index: int) -> None:
        self._trigger_sample_pad(pad_index)

    def _on_sample_pad_board_switch_requested(self, board_index: int) -> None:
        self._switch_sample_pad_board(board_index)

    def _on_sample_pad_hotkey_released(self, pad_index: int) -> None:
        if self._sample_pads_window is not None:
            self._sample_pads_window.release_pad(pad_index)

    def _handle_sample_pad_key_event(self, event: QKeyEvent) -> bool:
        # Ignore auto-repeat to avoid re-triggering while a key is held.
        if event.isAutoRepeat():
            return False

        # When a global listener is active it handles triggers from everywhere
        # (including when the app has focus), so skip local handling to avoid
        # double-triggering the same pad.
        if self._sample_pad_hotkey_backend in ("windows-native", "pynput"):
            return False

        modifiers = event.modifiers()
        ctrl_held = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift_held = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)

        board_combo_active = (
            shift_held and ctrl_held
            if self._sample_pad_board_switch_requires_ctrl
            else shift_held and not ctrl_held
        )
        if board_combo_active:
            return False

        # Ctrl+1-0 → pads 10-19 (always, regardless of alt modifier setting)
        if ctrl_held:
            ctrl_key_to_pad = {
                int(Qt.Key.Key_1): 10,
                int(Qt.Key.Key_2): 11,
                int(Qt.Key.Key_3): 12,
                int(Qt.Key.Key_4): 13,
                int(Qt.Key.Key_5): 14,
                int(Qt.Key.Key_6): 15,
                int(Qt.Key.Key_7): 16,
                int(Qt.Key.Key_8): 17,
                int(Qt.Key.Key_9): 18,
                int(Qt.Key.Key_0): 19,
            }
            pad_index = ctrl_key_to_pad.get(int(event.key()))
            if pad_index is not None:
                return self._trigger_sample_pad(pad_index)
            return False

        # Determine which modifier (if any) is required for pads 0-9.
        use_alt = self._sample_pad_alt_modifier
        if use_alt:
            required = Qt.KeyboardModifier.AltModifier
            if modifiers & ~Qt.KeyboardModifier.KeypadModifier != required:
                return False
        else:
            if modifiers not in (
                Qt.KeyboardModifier.NoModifier,
                Qt.KeyboardModifier.KeypadModifier,
            ):
                return False
            # Don't steal bare number keys from text input widgets.
            focus_widget = QApplication.focusWidget()
            if focus_widget is not None and (
                focus_widget.inherits("QLineEdit")
                or focus_widget.inherits("QTextEdit")
                or focus_widget.inherits("QPlainTextEdit")
            ):
                return False

        key_to_pad = {
            int(Qt.Key.Key_1): 0,
            int(Qt.Key.Key_2): 1,
            int(Qt.Key.Key_3): 2,
            int(Qt.Key.Key_4): 3,
            int(Qt.Key.Key_5): 4,
            int(Qt.Key.Key_6): 5,
            int(Qt.Key.Key_7): 6,
            int(Qt.Key.Key_8): 7,
            int(Qt.Key.Key_9): 8,
            int(Qt.Key.Key_0): 9,
        }
        pad_index = key_to_pad.get(int(event.key()))
        if pad_index is None:
            return False
        return self._trigger_sample_pad(pad_index)


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
