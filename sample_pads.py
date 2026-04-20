import json
import math
from pathlib import Path
from typing import Any, cast

from PyQt6 import QtCore, QtGui, QtWidgets

from app_helpers import chip_palette_for_tag_seed as _chip_palette_for_tag_seed
from app_helpers import coerce_volume_percent as _coerce_volume_percent

_PAD_MODES = ("one_shot", "loop", "release")
_PAD_MODE_LABELS = {"one_shot": "One Shot", "loop": "Loop", "release": "Release"}


class SamplePad(QtWidgets.QWidget):
    def __init__(
        self,
        board_index: int,
        slot_index: int,
        pad_index: int,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.board_index = board_index
        self.slot_index = slot_index
        self.pad_index = pad_index
        self.jingle = None
        self.is_muted = False
        self.is_solo = False
        self.volume_percent = 100
        self.pad_mode: str = "one_shot"
        self.is_playing = False
        self._is_held = False
        self.init_ui()

    def init_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout()
        self.sample_btn = QtWidgets.QPushButton(f"Pad {self.slot_index + 1}")
        self.sample_btn.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.sample_btn.setMinimumHeight(64)
        self.sample_btn.pressed.connect(self.on_sample_pressed)
        self.sample_btn.released.connect(self.on_sample_released)
        layout.addWidget(self.sample_btn)

        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.on_stop_clicked)
        layout.addWidget(self.stop_btn)

        btn_row = QtWidgets.QHBoxLayout()
        self.mute_btn = QtWidgets.QPushButton("M")
        self.mute_btn.setCheckable(True)
        self.mute_btn.clicked.connect(self.on_mute_clicked)
        btn_row.addWidget(self.mute_btn)

        self.solo_btn = QtWidgets.QPushButton("S")
        self.solo_btn.setCheckable(True)
        self.solo_btn.clicked.connect(self.on_solo_clicked)
        btn_row.addWidget(self.solo_btn)

        self.mode_btn = QtWidgets.QPushButton("One Shot")
        self.mode_btn.clicked.connect(self.on_mode_clicked)
        btn_row.addWidget(self.mode_btn)

        layout.addLayout(btn_row)
        self.setLayout(layout)

    def _parent_window(self) -> Any:
        parent_window: Any = self.parent()
        while parent_window and not hasattr(parent_window, "parent_main_window"):
            parent_window = parent_window.parent()
        return parent_window

    def _sync_playing_state_from_main_window(self) -> None:
        parent_window = self._parent_window()
        if not parent_window or not hasattr(parent_window, "parent_main_window"):
            self.is_playing = False
            return

        main_window: Any = parent_window.parent_main_window
        if not main_window:
            self.is_playing = False
            return

        if hasattr(main_window, "is_sample_pad_playing"):
            try:
                self.is_playing = bool(main_window.is_sample_pad_playing(self.pad_index))
            except Exception:
                self.is_playing = False
            self._refresh_stop_button_state()
            return

        if getattr(main_window, "_current_sample_pad_index", -1) != self.pad_index:
            self.is_playing = False
            self._refresh_stop_button_state()
            return

        player = getattr(main_window, "_player", None)
        if player is None or not hasattr(player, "playbackState"):
            self.is_playing = False
            self._refresh_stop_button_state()
            return

        try:
            state = player.playbackState()
        except Exception:
            self.is_playing = False
            self._refresh_stop_button_state()
            return

        state_type = type(state)
        playing_state = getattr(state_type, "PlayingState", None)
        paused_state = getattr(state_type, "PausedState", None)
        self.is_playing = state in (playing_state, paused_state)
        self._refresh_stop_button_state()

    def refresh_playback_state_from_main_window(self) -> None:
        self._sync_playing_state_from_main_window()

    def _refresh_stop_button_state(self) -> None:
        self.stop_btn.setEnabled(bool(self.is_playing))

    def on_sample_pressed(self) -> None:
        self._sync_playing_state_from_main_window()
        self._is_held = True
        if self.pad_mode == "one_shot":
            if self.is_playing:
                self.restart_sample()
            else:
                self.play_sample()
        elif self.pad_mode == "loop":
            if self.is_playing:
                self.stop_sample()
            else:
                self.play_sample()
        else:  # release
            self.play_sample()

    def on_sample_released(self) -> None:
        if not self._is_held:
            return
        if self.pad_mode == "release":
            self.stop_sample()
        else:
            self._is_held = False

    def play_sample(self) -> None:
        self.is_playing = True
        self._refresh_stop_button_state()
        self._push_mix_state_to_main_window()
        parent_window = self._parent_window()
        if parent_window and hasattr(parent_window, "parent_main_window"):
            main_window: Any = parent_window.parent_main_window
            if main_window and hasattr(main_window, "play_sample_pad_jingle"):
                main_window.play_sample_pad_jingle(
                    self.jingle,
                    parent_window.is_live_mode,
                    pad_mode=self.pad_mode,
                    pad_index=self.pad_index,
                    pad_volume_percent=self.volume_percent,
                    pad_is_muted=self.is_muted,
                    pad_is_solo=self.is_solo,
                )

    def restart_sample(self) -> None:
        self.play_sample()

    def stop_sample(self) -> None:
        self._is_held = False
        self.is_playing = False
        self._refresh_stop_button_state()
        parent_window = self._parent_window()
        if parent_window and hasattr(parent_window, "parent_main_window"):
            main_window: Any = parent_window.parent_main_window
            if main_window and hasattr(main_window, "stop_sample_pad_jingle"):
                main_window.stop_sample_pad_jingle(self.pad_index)

    def on_stop_clicked(self) -> None:
        if not self.is_playing:
            return
        self.stop_sample()

    def force_stopped(self) -> None:
        self._is_held = False
        self.is_playing = False
        self._refresh_stop_button_state()

    def on_mute_clicked(self) -> None:
        self.is_muted = self.mute_btn.isChecked()
        self._push_mix_state_to_main_window()
        self._emit_pad_state_changed()

    def on_solo_clicked(self) -> None:
        self.is_solo = self.solo_btn.isChecked()
        self._push_mix_state_to_main_window()
        self._emit_pad_state_changed()

    def set_muted(self, muted: bool, *, notify: bool = True) -> None:
        self.is_muted = bool(muted)
        self.mute_btn.blockSignals(True)
        self.mute_btn.setChecked(self.is_muted)
        self.mute_btn.blockSignals(False)
        if notify:
            self._push_mix_state_to_main_window()
            self._emit_pad_state_changed()

    def set_solo(self, solo: bool, *, notify: bool = True) -> None:
        self.is_solo = bool(solo)
        self.solo_btn.blockSignals(True)
        self.solo_btn.setChecked(self.is_solo)
        self.solo_btn.blockSignals(False)
        if notify:
            self._push_mix_state_to_main_window()
            self._emit_pad_state_changed()

    def set_volume_percent(self, value: int, *, notify: bool = True) -> None:
        self.volume_percent = _coerce_volume_percent(value)
        if notify:
            self._push_mix_state_to_main_window()
            self._emit_pad_state_changed()

    def _push_mix_state_to_main_window(self) -> None:
        parent_window = self._parent_window()
        if parent_window and hasattr(parent_window, "sync_pad_mix_to_main_window"):
            parent_window.sync_pad_mix_to_main_window(self)

    def _emit_pad_state_changed(self) -> None:
        parent_window = self._parent_window()
        if parent_window and hasattr(parent_window, "padStateChanged"):
            if not getattr(parent_window, "_suppress_pad_state_changed", False):
                parent_window.padStateChanged.emit()

    def on_mode_clicked(self) -> None:
        current_index = _PAD_MODES.index(self.pad_mode)
        self.pad_mode = _PAD_MODES[(current_index + 1) % len(_PAD_MODES)]
        self.mode_btn.setText(_PAD_MODE_LABELS[self.pad_mode])
        self._emit_pad_state_changed()

    def assign_jingle(self, jingle: dict[str, Any] | None) -> None:
        self.jingle = jingle
        if isinstance(jingle, dict) and "name" in jingle:
            self.sample_btn.setText(str(jingle["name"]))
        else:
            self.sample_btn.setText(f"Pad {self.slot_index + 1}")

        parent_window = self._parent_window()
        if (
            isinstance(jingle, dict)
            and parent_window
            and hasattr(parent_window, "preload_pad_jingle")
            and not getattr(parent_window, "_suppress_pad_state_changed", False)
        ):
            try:
                parent_window.preload_pad_jingle(jingle)
            except Exception:
                pass

        self._emit_pad_state_changed()


class SamplePadMixerDialog(QtWidgets.QDialog):
    def __init__(self, pads_window: "SamplePadsWindow") -> None:
        super().__init__(None)
        self._pads_window = pads_window
        self.setWindowTitle("Sample Pad Mixer")
        self.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowFlag(QtCore.Qt.WindowType.Tool, False)
        self.setWindowFlag(QtCore.Qt.WindowType.Window, True)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setModal(False)
        self.resize(1480, 420)

        self._name_labels: list[QtWidgets.QLabel] = []
        self._volume_sliders: list[QtWidgets.QSlider] = []
        self._value_labels: list[QtWidgets.QLabel] = []
        self._mute_buttons: list[QtWidgets.QPushButton] = []
        self._solo_buttons: list[QtWidgets.QPushButton] = []
        self._meter_bars: list[QtWidgets.QProgressBar] = []
        self._db_scale_widgets: list[QtWidgets.QWidget] = []
        self._strip_lane_widgets: list[QtWidgets.QWidget] = []
        self._channel_boxes: list[QtWidgets.QGroupBox] = []
        self._strip_height: int = 160
        self._strip_width: int = 84
        self._base_strip_width: int = 84
        self._max_strip_width: int = 104
        self._strip_spacing: int = 8
        self._strip_to_channel_ratio: float | None = None

        root = QtWidgets.QVBoxLayout(self)
        header_row = QtWidgets.QHBoxLayout()
        self._board_label = QtWidgets.QLabel()
        header_row.addWidget(self._board_label)
        header_row.addStretch()

        root.addLayout(header_row)

        self._channels_scroll = QtWidgets.QScrollArea()
        self._channels_scroll.setWidgetResizable(True)
        self._channels_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._channels_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        channels_container = QtWidgets.QWidget()
        channels_container.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        channels_row = QtWidgets.QHBoxLayout(channels_container)
        channels_row.setContentsMargins(0, 0, 0, 0)
        channels_row.setSpacing(self._strip_spacing)

        for slot_index in range(self._pads_window._num_pads):
            channel = QtWidgets.QGroupBox()
            channel.setFixedWidth(self._strip_width)
            channel.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Fixed,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
            channel_layout = QtWidgets.QVBoxLayout(channel)
            channel_layout.setContentsMargins(6, 6, 6, 6)
            channel_layout.setSpacing(4)

            pad_label = QtWidgets.QLabel(f"Pad {slot_index + 1}")
            pad_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            pad_label.setStyleSheet("color: #b0bec5;")
            channel_layout.addWidget(pad_label)

            name_label = QtWidgets.QLabel(f"Pad {slot_index + 1}")
            name_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            name_label.setWordWrap(True)
            name_label.setMinimumHeight(30)
            name_label.setMaximumHeight(30)
            channel_layout.addWidget(name_label)

            strip_lane_widget = QtWidgets.QWidget()
            strip_lane_widget.setFixedHeight(self._strip_height)
            strip_row = QtWidgets.QHBoxLayout(strip_lane_widget)
            strip_row.setContentsMargins(0, 0, 0, 0)
            strip_row.setSpacing(4)

            meter_bar = QtWidgets.QProgressBar()
            meter_bar.setRange(0, 100)
            meter_bar.setValue(0)
            meter_bar.setTextVisible(False)
            meter_bar.setOrientation(QtCore.Qt.Orientation.Vertical)
            meter_bar.setFixedHeight(self._strip_height)
            meter_bar.setFixedWidth(12)
            meter_bar.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Fixed,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
            meter_bar.setStyleSheet(self._meter_stylesheet("#26a69a"))
            strip_row.addWidget(meter_bar, 0, QtCore.Qt.AlignmentFlag.AlignTop)

            db_scale_widget = QtWidgets.QWidget()
            db_scale_widget.setFixedHeight(self._strip_height)
            db_ticks = QtWidgets.QVBoxLayout(db_scale_widget)
            db_ticks.setContentsMargins(0, 0, 0, 0)
            db_ticks.setSpacing(0)
            for label in ("0", "-6", "-12", "-24", "-inf"):
                tick = QtWidgets.QLabel(label)
                tick.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
                tick.setStyleSheet("color: #90a4ae; font-size: 9px;")
                db_ticks.addWidget(tick)
                if label != "-inf":
                    db_ticks.addStretch()
            strip_row.addWidget(db_scale_widget, 0, QtCore.Qt.AlignmentFlag.AlignTop)

            volume_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Vertical)
            volume_slider.setRange(0, 100)
            volume_slider.setValue(100)
            volume_slider.setPageStep(5)
            volume_slider.setFixedHeight(self._strip_height)
            volume_slider.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Fixed,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
            volume_slider.valueChanged.connect(
                lambda value, idx=slot_index: self._on_volume_changed(idx, value)
            )
            strip_row.addWidget(volume_slider, 0, QtCore.Qt.AlignmentFlag.AlignTop)
            channel_layout.addWidget(strip_lane_widget, 0, QtCore.Qt.AlignmentFlag.AlignTop)

            value_label = QtWidgets.QLabel("100%")
            value_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            channel_layout.addWidget(value_label)

            buttons_row = QtWidgets.QHBoxLayout()
            buttons_row.setSpacing(4)

            mute_btn = QtWidgets.QPushButton("M")
            mute_btn.setCheckable(True)
            mute_btn.toggled.connect(lambda checked, idx=slot_index: self._on_mute_toggled(idx, checked))
            mute_btn.setFixedWidth(38)
            buttons_row.addWidget(mute_btn)

            solo_btn = QtWidgets.QPushButton("S")
            solo_btn.setCheckable(True)
            solo_btn.toggled.connect(lambda checked, idx=slot_index: self._on_solo_toggled(idx, checked))
            solo_btn.setFixedWidth(38)
            buttons_row.addWidget(solo_btn)

            channel_layout.addLayout(buttons_row)

            self._name_labels.append(name_label)
            self._volume_sliders.append(volume_slider)
            self._value_labels.append(value_label)
            self._mute_buttons.append(mute_btn)
            self._solo_buttons.append(solo_btn)
            self._meter_bars.append(meter_bar)
            self._db_scale_widgets.append(db_scale_widget)
            self._strip_lane_widgets.append(strip_lane_widget)
            self._channel_boxes.append(channel)

            channels_row.addWidget(channel)

        channels_row.addStretch()
        self._channels_scroll.setWidget(channels_container)

        root.addWidget(self._channels_scroll)

        self._meter_timer = QtCore.QTimer(self)
        self._meter_timer.setInterval(40)
        self._meter_timer.timeout.connect(self._refresh_meters)
        self._meter_timer.start()

        self.refresh_from_pads()
        self._apply_responsive_strip_geometry()

    @staticmethod
    def _meter_stylesheet(color: str) -> str:
        return (
            "QProgressBar {"
            " border: 1px solid #2f3a40;"
            " border-radius: 2px;"
            " background: #11161a;"
            "}"
            f"QProgressBar::chunk {{ background: {color}; }}"
        )

    @staticmethod
    def _volume_percent_to_db_text(value: int) -> str:
        if value <= 0:
            return "-inf dB"
        db = 20.0 * math.log10(max(0.001, float(value) / 100.0))
        return f"{db:.1f} dB"

    def _apply_responsive_strip_geometry(self) -> None:
        viewport = self._channels_scroll.viewport()
        if viewport is None:
            return

        visible_width = max(320, viewport.width())
        visible_height = max(220, viewport.height())
        pads_count = max(1, len(self._channel_boxes))
        # Keep strips narrow by default. Only expand when the viewport can
        # display all strips at once.
        narrow_total_width = self._ideal_mixer_content_width(self._base_strip_width)
        if visible_width >= narrow_total_width:
            computed_width = min(
                self._max_strip_width,
                int((visible_width - (pads_count - 1) * self._strip_spacing) / pads_count),
            )
        else:
            computed_width = self._base_strip_width

        channel_height = max(220, visible_height - 2)
        # Keep enough room for pad/name labels plus value + M/S buttons.
        reserved_top_bottom = 116

        if self._strip_to_channel_ratio is None:
            computed_height = max(132, channel_height - reserved_top_bottom)
            self._strip_to_channel_ratio = computed_height / float(channel_height)
        else:
            computed_height = int(round(channel_height * self._strip_to_channel_ratio))
            computed_height = min(computed_height, channel_height - reserved_top_bottom)
            computed_height = max(132, computed_height)

        if computed_width == self._strip_width and computed_height == self._strip_height:
            return

        self._strip_width = computed_width
        self._strip_height = computed_height
        for channel in self._channel_boxes:
            channel.setFixedWidth(self._strip_width)
        for meter in self._meter_bars:
            meter.setFixedHeight(self._strip_height)
        for slider in self._volume_sliders:
            slider.setFixedHeight(self._strip_height)
        for db_scale in self._db_scale_widgets:
            db_scale.setFixedHeight(self._strip_height)
        for strip_lane in self._strip_lane_widgets:
            strip_lane.setFixedHeight(self._strip_height)

    def _ideal_mixer_content_width(self, strip_width: int) -> int:
        pads_count = max(1, len(self._channel_boxes))
        return pads_count * strip_width + (pads_count - 1) * self._strip_spacing + 14

    def fit_to_screen_if_possible(self) -> None:
        screen = self.screen()
        if screen is None:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                screen = app.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        if available.width() <= 0 or available.height() <= 0:
            return

        target_height = min(max(420, int(available.height() * 0.72)), available.height() - 64)
        if target_height > 220:
            self.resize(self.width(), target_height)

        full_narrow_width = self._ideal_mixer_content_width(self._base_strip_width)
        if available.width() >= full_narrow_width + 36:
            desired_width = min(
                available.width() - 24,
                self._ideal_mixer_content_width(self._max_strip_width),
            )
            self.resize(desired_width, self.height())

    def refresh_from_pads(self) -> None:
        board_index = self._pads_window.active_board_index
        board = self._pads_window.board_pads(board_index)
        self._board_label.setText(f"Board {board_index + 1} mixer")
        for slot_index, pad in enumerate(board):
            if isinstance(pad.jingle, dict):
                name = str(pad.jingle.get("name", "")).strip() or f"Pad {slot_index + 1}"
            else:
                name = f"Pad {slot_index + 1}"
            self._name_labels[slot_index].setText(name)

            self._volume_sliders[slot_index].blockSignals(True)
            self._volume_sliders[slot_index].setValue(pad.volume_percent)
            self._volume_sliders[slot_index].blockSignals(False)
            self._value_labels[slot_index].setText(f"{pad.volume_percent}%")
            self._volume_sliders[slot_index].setToolTip(self._volume_percent_to_db_text(pad.volume_percent))

            self._mute_buttons[slot_index].blockSignals(True)
            self._mute_buttons[slot_index].setChecked(pad.is_muted)
            self._mute_buttons[slot_index].blockSignals(False)

            self._solo_buttons[slot_index].blockSignals(True)
            self._solo_buttons[slot_index].setChecked(pad.is_solo)
            self._solo_buttons[slot_index].blockSignals(False)

    def _on_volume_changed(self, slot_index: int, value: int) -> None:
        board = self._pads_window.board_pads(self._pads_window.active_board_index)
        if slot_index < 0 or slot_index >= len(board):
            return
        pad = board[slot_index]
        pad.set_volume_percent(value, notify=True)
        self._value_labels[slot_index].setText(f"{pad.volume_percent}%")
        self._volume_sliders[slot_index].setToolTip(self._volume_percent_to_db_text(pad.volume_percent))

    def _on_mute_toggled(self, slot_index: int, checked: bool) -> None:
        board = self._pads_window.board_pads(self._pads_window.active_board_index)
        if slot_index < 0 or slot_index >= len(board):
            return
        board[slot_index].set_muted(checked, notify=True)

    def _on_solo_toggled(self, slot_index: int, checked: bool) -> None:
        board = self._pads_window.board_pads(self._pads_window.active_board_index)
        if slot_index < 0 or slot_index >= len(board):
            return
        board[slot_index].set_solo(checked, notify=True)

    def _refresh_meters(self) -> None:
        levels = self._pads_window.sample_pad_meter_levels()
        board = self._pads_window.board_pads(self._pads_window.active_board_index)
        for slot_index, pad in enumerate(board):
            level = max(0.0, min(1.0, float(levels.get(pad.pad_index, 0.0))))
            meter_value = int(round(level * 100.0))
            self._meter_bars[slot_index].setValue(meter_value)
            if meter_value >= 90:
                color = "#ef5350"
            elif meter_value >= 70:
                color = "#ffd54f"
            else:
                color = "#26a69a"
            self._meter_bars[slot_index].setStyleSheet(self._meter_stylesheet(color))

    def showEvent(self, event: QtCore.QEvent | None) -> None:
        self._meter_timer.start()
        self.refresh_from_pads()
        self.fit_to_screen_if_possible()
        self._apply_responsive_strip_geometry()
        QtCore.QTimer.singleShot(0, self._apply_responsive_strip_geometry)
        super().showEvent(event)

    def resizeEvent(self, event: QtGui.QResizeEvent | None) -> None:
        self._apply_responsive_strip_geometry()
        super().resizeEvent(event)

    def closeEvent(self, event: QtCore.QEvent | None) -> None:
        self._meter_timer.stop()
        super().closeEvent(event)


class SamplePadsWindow(QtWidgets.QDialog):
    layoutLoaded = QtCore.pyqtSignal(str)
    layoutSaved = QtCore.pyqtSignal(str)
    modeChanged = QtCore.pyqtSignal(bool)
    globalHotkeysToggled = QtCore.pyqtSignal(bool)
    altModifierToggled = QtCore.pyqtSignal(bool)
    boardSwitchCtrlModifierToggled = QtCore.pyqtSignal(bool)
    padStateChanged = QtCore.pyqtSignal()
    activeBoardChanged = QtCore.pyqtSignal(int)

    def __init__(
        self,
        num_pads: int = 20,
        num_boards: int = 5,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(None)
        self.setWindowTitle("Sample Pads")
        self.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowFlag(QtCore.Qt.WindowType.Tool, False)
        self.setWindowFlag(QtCore.Qt.WindowType.Window, True)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowMinimizeButtonHint, True)

        self._num_pads = max(1, int(num_pads))
        self._num_boards = max(1, int(num_boards))
        self._boards: list[list[SamplePad]] = []
        self._board_buttons: list[QtWidgets.QPushButton] = []
        self._active_board_index = 0

        self.is_live_mode: bool = True
        self.parent_main_window: QtWidgets.QWidget | None = parent
        self._current_layout_path = ""
        self._suppress_pad_state_changed: bool = False
        self._mixer_dialog: SamplePadMixerDialog | None = None
        self._playback_state_timer = QtCore.QTimer(self)
        self._playback_state_timer.setInterval(120)
        self._playback_state_timer.timeout.connect(self._refresh_pad_playback_states)
        self._playback_state_timer.start()
        self._init_ui()
        self.padStateChanged.connect(self._on_pad_state_changed)

    @property
    def pads(self) -> list[SamplePad]:
        return self._boards[self._active_board_index]

    @property
    def board_count(self) -> int:
        return self._num_boards

    @property
    def active_board_index(self) -> int:
        return self._active_board_index

    def board_pads(self, board_index: int) -> list[SamplePad]:
        if 0 <= board_index < self._num_boards:
            return self._boards[board_index]
        return []

    def _board_pill_style(self, board_index: int, selected: bool) -> str:
        seed = str((board_index % 9) + 1)
        bg, border, hover = _chip_palette_for_tag_seed(seed)
        if selected:
            return (
                "QPushButton {"
                f" border: 1px solid {border};"
                " border-radius: 10px;"
                " padding: 3px 10px;"
                f" background: {bg};"
                " color: #ffffff;"
                " font-weight: bold;"
                "}"
                f"QPushButton:hover {{ background: {hover}; }}"
            )
        return (
            "QPushButton {"
            " border: 1px solid #9ea7b3;"
            " border-radius: 10px;"
            " padding: 3px 10px;"
            " background: #eceff1;"
            " color: #263238;"
            "}"
            "QPushButton:hover { background: #dfe5ea; }"
        )

    def _refresh_board_pills(self) -> None:
        for idx, btn in enumerate(self._board_buttons):
            selected = idx == self._active_board_index
            btn.blockSignals(True)
            btn.setChecked(selected)
            btn.blockSignals(False)
            btn.setStyleSheet(self._board_pill_style(idx, selected))

    def _on_board_pill_clicked(self, board_index: int) -> None:
        if self.set_active_board(board_index):
            self.activeBoardChanged.emit(board_index)
            if not self._suppress_pad_state_changed:
                self.padStateChanged.emit()

    def set_active_board(self, board_index: int) -> bool:
        if board_index < 0 or board_index >= self._num_boards:
            return False
        if board_index == self._active_board_index:
            return False
        self._active_board_index = board_index
        self._boards_stack.setCurrentIndex(board_index)
        self._refresh_board_pills()
        if self._mixer_dialog is not None:
            self._mixer_dialog.refresh_from_pads()
        return True

    def _init_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        controls_row = QtWidgets.QHBoxLayout()

        self.mode_btn = QtWidgets.QPushButton("Mode: Live")
        self.mode_btn.setCheckable(True)
        self.mode_btn.setChecked(True)
        self.mode_btn.clicked.connect(self.toggle_mode)
        controls_row.addWidget(self.mode_btn)

        self.mode_volume_label = QtWidgets.QLabel("Live Vol")
        controls_row.addWidget(self.mode_volume_label)

        self.mode_volume_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.mode_volume_slider.setRange(0, 100)
        self.mode_volume_slider.setPageStep(5)
        self.mode_volume_slider.setFixedWidth(120)
        self.mode_volume_slider.valueChanged.connect(self._on_mode_volume_changed)
        controls_row.addWidget(self.mode_volume_slider)

        self.mode_volume_value = QtWidgets.QLabel("100%")
        controls_row.addWidget(self.mode_volume_value)

        self.global_hotkeys_checkbox = QtWidgets.QCheckBox("Global Hotkeys")
        self.global_hotkeys_checkbox.setToolTip(
            "Trigger pads 1-10 with keys 1-9 and 0; pads 11-20 with Ctrl+1-9 and Ctrl+0. Works even when this app is not focused."
        )
        self.global_hotkeys_checkbox.toggled.connect(self.globalHotkeysToggled.emit)
        controls_row.addWidget(self.global_hotkeys_checkbox)

        self.alt_modifier_checkbox = QtWidgets.QCheckBox("Use Alt modifier")
        self.alt_modifier_checkbox.setToolTip(
            "Use Alt+1-0 instead of bare 1-0 to avoid conflicts with games and other apps."
        )
        self.alt_modifier_checkbox.setEnabled(False)
        self.alt_modifier_checkbox.toggled.connect(self.altModifierToggled.emit)
        controls_row.addWidget(self.alt_modifier_checkbox)

        self.board_switch_ctrl_checkbox = QtWidgets.QCheckBox("Require Ctrl for board hotkeys")
        self.board_switch_ctrl_checkbox.setToolTip(
            "When off, switch boards with Shift+1-5. When on, require Ctrl+Shift+1-5 instead."
        )
        self.board_switch_ctrl_checkbox.toggled.connect(
            self.boardSwitchCtrlModifierToggled.emit
        )
        controls_row.addWidget(self.board_switch_ctrl_checkbox)

        self.mixer_btn = QtWidgets.QPushButton("Mixer...")
        self.mixer_btn.setToolTip("Open per-pad mixer controls with live metering.")
        self.mixer_btn.clicked.connect(self._on_mixer_clicked)
        controls_row.addWidget(self.mixer_btn)

        self.stop_all_btn = QtWidgets.QPushButton("Stop All Playback")
        self.stop_all_btn.setToolTip("Stop all currently-playing sample pads.")
        self.stop_all_btn.clicked.connect(self._on_stop_all_clicked)
        controls_row.addWidget(self.stop_all_btn)

        controls_row.addStretch()

        boards_label = QtWidgets.QLabel("Boards")
        controls_row.addWidget(boards_label)

        for board_index in range(self._num_boards):
            btn = QtWidgets.QPushButton(str(board_index + 1))
            btn.setCheckable(True)
            btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked=False, idx=board_index: self._on_board_pill_clicked(idx)
            )
            self._board_buttons.append(btn)
            controls_row.addWidget(btn)

        layout.addLayout(controls_row)

        self._boards_stack = QtWidgets.QStackedWidget()
        cols = 4
        for board_index in range(self._num_boards):
            board_page = QtWidgets.QWidget(self)
            grid = QtWidgets.QGridLayout(board_page)
            board_pads: list[SamplePad] = []
            for slot_index in range(self._num_pads):
                absolute_index = board_index * self._num_pads + slot_index
                pad = SamplePad(board_index, slot_index, absolute_index, board_page)
                board_pads.append(pad)
                row, col = divmod(slot_index, cols)
                grid.addWidget(pad, row, col)
            self._boards.append(board_pads)
            self._boards_stack.addWidget(board_page)
        layout.addWidget(self._boards_stack)
        self._refresh_board_pills()
        self.sync_all_pad_mix_to_main_window()

        layout_row = QtWidgets.QHBoxLayout()
        self.save_layout_btn = QtWidgets.QPushButton("Save Layout...")
        self.save_layout_btn.clicked.connect(self._on_save_layout_clicked)
        layout_row.addWidget(self.save_layout_btn)

        self.load_layout_btn = QtWidgets.QPushButton("Load Layout...")
        self.load_layout_btn.clicked.connect(self._on_load_layout_clicked)
        layout_row.addWidget(self.load_layout_btn)

        self.current_layout_label = QtWidgets.QLabel("Layout: (none)")
        self.current_layout_label.setWordWrap(True)
        layout_row.addWidget(self.current_layout_label, 1)

        layout.addLayout(layout_row)
        self.refresh_mode_volume_controls()

    def toggle_mode(self) -> None:
        self.is_live_mode = self.mode_btn.isChecked()
        self.mode_btn.setText("Mode: Live" if self.is_live_mode else "Mode: Preview")
        self.refresh_mode_volume_controls()
        self.modeChanged.emit(self.is_live_mode)
        if not self._suppress_pad_state_changed:
            self.padStateChanged.emit()

    def _active_mode_volume_from_main_window(self) -> int:
        main_window = self.parent_main_window
        if main_window is not None and hasattr(main_window, "sample_pad_mode_volume_percent"):
            try:
                value = main_window.sample_pad_mode_volume_percent(self.is_live_mode)
                return _coerce_volume_percent(value)
            except Exception:
                pass
        return 100

    def refresh_mode_volume_controls(self) -> None:
        mode_text = "Live Vol" if self.is_live_mode else "Preview Vol"
        self.mode_volume_label.setText(mode_text)
        value = self._active_mode_volume_from_main_window()
        self.mode_volume_slider.blockSignals(True)
        self.mode_volume_slider.setValue(value)
        self.mode_volume_slider.blockSignals(False)
        self.mode_volume_value.setText(f"{value}%")

    def _on_mode_volume_changed(self, value: int) -> None:
        percent = _coerce_volume_percent(value)
        self.mode_volume_value.setText(f"{percent}%")
        main_window = self.parent_main_window
        if main_window is not None and hasattr(main_window, "set_sample_pad_mode_volume_percent"):
            try:
                main_window.set_sample_pad_mode_volume_percent(self.is_live_mode, percent)
            except Exception:
                pass

    def _on_stop_all_clicked(self) -> None:
        main_window = self.parent_main_window
        if main_window is not None and hasattr(main_window, "stop_all_sample_pad_playback"):
            try:
                main_window.stop_all_sample_pad_playback()
            except Exception:
                pass
        for board in self._boards:
            for pad in board:
                pad.force_stopped()

    def _refresh_pad_playback_states(self) -> None:
        for board in self._boards:
            for pad in board:
                pad.refresh_playback_state_from_main_window()

    def preload_pad_jingle(self, jingle: dict[str, Any]) -> None:
        main_window = self.parent_main_window
        if main_window is None or not hasattr(main_window, "preload_sample_pad_jingle"):
            return
        try:
            main_window.preload_sample_pad_jingle(jingle)
        except Exception:
            pass

    def _on_mixer_clicked(self) -> None:
        dialog = self._ensure_mixer_dialog()
        dialog.refresh_from_pads()
        dialog.fit_to_screen_if_possible()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_pad_state_changed(self) -> None:
        if self._mixer_dialog is not None:
            self._mixer_dialog.refresh_from_pads()

    def _ensure_mixer_dialog(self) -> SamplePadMixerDialog:
        if self._mixer_dialog is None:
            self._mixer_dialog = SamplePadMixerDialog(self)
        return self._mixer_dialog

    def closeEvent(self, event: QtGui.QCloseEvent | None) -> None:
        if self._mixer_dialog is not None:
            try:
                self._mixer_dialog.close()
            except Exception:
                pass
        super().closeEvent(event)

    def sync_pad_mix_to_main_window(self, pad: SamplePad) -> None:
        main_window = self.parent_main_window
        if main_window is None:
            return
        if not hasattr(main_window, "set_sample_pad_mix"):
            return
        try:
            main_window.set_sample_pad_mix(
                pad.pad_index,
                pad.volume_percent,
                pad.is_muted,
                pad.is_solo,
            )
        except Exception:
            return

    def sync_all_pad_mix_to_main_window(self) -> None:
        for board in self._boards:
            for pad in board:
                self.sync_pad_mix_to_main_window(pad)

    def sample_pad_meter_levels(self) -> dict[int, float]:
        main_window = self.parent_main_window
        if main_window is None or not hasattr(main_window, "sample_pad_meter_levels"):
            return {}
        try:
            result = main_window.sample_pad_meter_levels()
            if isinstance(result, dict):
                return cast(dict[int, float], result)
        except Exception:
            pass
        return {}

    def assign_jingle_to_pad(self, pad_index: int, jingle: dict[str, Any] | None) -> None:
        self.assign_jingle_to_board_pad(self._active_board_index, pad_index, jingle)

    def assign_jingle_to_board_pad(
        self,
        board_index: int,
        pad_index: int,
        jingle: dict[str, Any] | None,
    ) -> None:
        if board_index < 0 or board_index >= self._num_boards:
            return
        board = self._boards[board_index]
        if 0 <= pad_index < len(board):
            board[pad_index].assign_jingle(jingle)

    def trigger_pad(self, pad_index: int) -> bool:
        board = self._boards[self._active_board_index]
        if pad_index < 0 or pad_index >= len(board):
            return False
        board[pad_index].on_sample_pressed()
        return True

    def release_pad(self, pad_index: int) -> bool:
        board = self._boards[self._active_board_index]
        if pad_index < 0 or pad_index >= len(board):
            return False
        board[pad_index].on_sample_released()
        return True

    def current_layout_path(self) -> str:
        return self._current_layout_path

    def set_current_layout_path(self, file_path: str) -> None:
        self._current_layout_path = file_path.strip()
        if self._current_layout_path:
            self.current_layout_label.setText(f"Layout: {self._current_layout_path}")
        else:
            self.current_layout_label.setText("Layout: (none)")

    def set_global_hotkeys_available(self, available: bool, reason: str = "") -> None:
        self.global_hotkeys_checkbox.setEnabled(available)
        if available:
            self.global_hotkeys_checkbox.setToolTip(
                "Trigger pads 1-10 with keys 1-9 and 0; pads 11-20 with Ctrl+1-9 and Ctrl+0. Works even when this app is not focused."
            )
        else:
            self.global_hotkeys_checkbox.setToolTip(reason or "Global hotkeys are unavailable.")
            self.alt_modifier_checkbox.setEnabled(False)

    def set_global_hotkeys_enabled(self, enabled: bool) -> None:
        self.global_hotkeys_checkbox.blockSignals(True)
        self.global_hotkeys_checkbox.setChecked(enabled)
        self.global_hotkeys_checkbox.blockSignals(False)
        if not enabled:
            self.alt_modifier_checkbox.setEnabled(False)
        elif self.global_hotkeys_checkbox.isEnabled():
            self.alt_modifier_checkbox.setEnabled(True)

    def set_alt_modifier_enabled(self, enabled: bool) -> None:
        self.alt_modifier_checkbox.blockSignals(True)
        self.alt_modifier_checkbox.setChecked(enabled)
        self.alt_modifier_checkbox.blockSignals(False)

    def set_board_switch_ctrl_modifier_enabled(self, enabled: bool) -> None:
        self.board_switch_ctrl_checkbox.blockSignals(True)
        self.board_switch_ctrl_checkbox.setChecked(enabled)
        self.board_switch_ctrl_checkbox.blockSignals(False)

    def layout_payload(self) -> dict[str, Any]:
        boards_payload: list[dict[str, Any]] = []
        for board_index, board in enumerate(self._boards):
            pads_payload: list[dict[str, Any]] = []
            for pad in board:
                jingle_payload: dict[str, Any] | None = None
                if isinstance(pad.jingle, dict):
                    name_value = str(pad.jingle.get("name", "")).strip()
                    path_value = str(pad.jingle.get("path", "")).strip()
                    if path_value:
                        jingle_payload = {
                            "name": name_value or Path(path_value).name,
                            "path": path_value,
                        }
                        clip_start_value = pad.jingle.get("clip_start_seconds")
                        clip_stop_value = pad.jingle.get("clip_stop_seconds")
                        clip_profile_index_value = pad.jingle.get("clip_profile_index")
                        if isinstance(clip_start_value, (int, float)):
                            jingle_payload["clip_start_seconds"] = float(clip_start_value)
                        if isinstance(clip_stop_value, (int, float)):
                            jingle_payload["clip_stop_seconds"] = float(clip_stop_value)
                        if isinstance(clip_profile_index_value, int):
                            jingle_payload["clip_profile_index"] = int(clip_profile_index_value)
                pads_payload.append(
                    {
                        "index": pad.slot_index,
                        "jingle": jingle_payload,
                        "mode": pad.pad_mode,
                        "volumePercent": pad.volume_percent,
                        "isMuted": bool(pad.is_muted),
                        "isSolo": bool(pad.is_solo),
                    }
                )
            boards_payload.append({"index": board_index, "pads": pads_payload})

        return {
            "version": 2,
            "isLiveMode": bool(self.is_live_mode),
            "activeBoard": self._active_board_index,
            "boards": boards_payload,
        }

    def _reset_all_boards(self) -> None:
        for board in self._boards:
            for pad in board:
                pad.assign_jingle(None)
                pad.pad_mode = "one_shot"
                pad.mode_btn.setText(_PAD_MODE_LABELS[pad.pad_mode])
                pad.set_volume_percent(100, notify=False)
                pad.set_muted(False, notify=False)
                pad.set_solo(False, notify=False)

    def _apply_board_pads_payload(self, board_index: int, pads_payload_any: list[Any]) -> int:
        if board_index < 0 or board_index >= self._num_boards:
            return 0
        board = self._boards[board_index]
        assigned = 0
        for entry in pads_payload_any:
            if not isinstance(entry, dict):
                continue
            entry_dict = cast(dict[str, Any], entry)
            index_raw = entry_dict.get("index")
            if not isinstance(index_raw, int):
                continue
            if index_raw < 0 or index_raw >= len(board):
                continue

            pad = board[index_raw]
            jingle_value = entry_dict.get("jingle")
            if not isinstance(jingle_value, dict):
                pad.assign_jingle(None)
            else:
                jingle_dict = cast(dict[str, Any], jingle_value)
                jingle_path = str(jingle_dict.get("path", "")).strip()
                if not jingle_path:
                    pad.assign_jingle(None)
                else:
                    jingle_name = str(jingle_dict.get("name", "")).strip() or Path(jingle_path).name
                    restored_jingle: dict[str, Any] = {"name": jingle_name, "path": jingle_path}
                    clip_start_value = jingle_dict.get("clip_start_seconds")
                    clip_stop_value = jingle_dict.get("clip_stop_seconds")
                    clip_profile_index_value = jingle_dict.get("clip_profile_index")
                    if isinstance(clip_start_value, (int, float)):
                        restored_jingle["clip_start_seconds"] = float(clip_start_value)
                    if isinstance(clip_stop_value, (int, float)):
                        restored_jingle["clip_stop_seconds"] = float(clip_stop_value)
                    if isinstance(clip_profile_index_value, int):
                        restored_jingle["clip_profile_index"] = int(clip_profile_index_value)
                    pad.assign_jingle(restored_jingle)
                    assigned += 1

            mode_value = str(entry_dict.get("mode", "one_shot")).strip()
            if mode_value not in _PAD_MODES:
                mode_value = "one_shot"
            pad.pad_mode = mode_value
            pad.mode_btn.setText(_PAD_MODE_LABELS[mode_value])

            volume_raw = entry_dict.get("volumePercent", 100)
            muted_raw = entry_dict.get("isMuted", False)
            solo_raw = entry_dict.get("isSolo", False)
            pad.set_volume_percent(_coerce_volume_percent(volume_raw), notify=False)
            pad.set_muted(bool(muted_raw), notify=False)
            pad.set_solo(bool(solo_raw), notify=False)

        return assigned

    def apply_layout_payload(self, payload: dict[str, Any]) -> int:
        assigned = 0
        self._suppress_pad_state_changed = True
        try:
            self._reset_all_boards()

            boards_payload = payload.get("boards")
            if isinstance(boards_payload, list):
                for board_entry in cast(list[Any], boards_payload):
                    if not isinstance(board_entry, dict):
                        continue
                    board_dict = cast(dict[str, Any], board_entry)
                    board_index_raw = board_dict.get("index")
                    pads_payload = board_dict.get("pads")
                    if not isinstance(board_index_raw, int) or not isinstance(pads_payload, list):
                        continue
                    assigned += self._apply_board_pads_payload(
                        board_index_raw,
                        cast(list[Any], pads_payload),
                    )
            else:
                # Backward compatibility for v1 payloads with a single "pads" list.
                pads_payload = payload.get("pads")
                if not isinstance(pads_payload, list):
                    raise ValueError("Layout file is missing a valid 'boards' or 'pads' list.")
                assigned += self._apply_board_pads_payload(0, cast(list[Any], pads_payload))

            if "isLiveMode" in payload:
                is_live_mode = bool(payload.get("isLiveMode"))
                self.mode_btn.blockSignals(True)
                self.mode_btn.setChecked(is_live_mode)
                self.mode_btn.blockSignals(False)
                self.is_live_mode = is_live_mode
                self.mode_btn.setText("Mode: Live" if self.is_live_mode else "Mode: Preview")

            active_board = int(payload.get("activeBoard", 0))
            if active_board < 0 or active_board >= self._num_boards:
                active_board = 0
            changed = self.set_active_board(active_board)
            if changed:
                self.activeBoardChanged.emit(active_board)
        finally:
            self._suppress_pad_state_changed = False

        self.sync_all_pad_mix_to_main_window()
        if self._mixer_dialog is not None:
            self._mixer_dialog.refresh_from_pads()
        self.padStateChanged.emit()
        return assigned

    def save_layout_to_path(self, file_path: str) -> None:
        resolved = Path(file_path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        payload = self.layout_payload()
        resolved.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.set_current_layout_path(str(resolved))
        self.layoutSaved.emit(str(resolved))

    def load_layout_from_path(self, file_path: str, *, show_errors: bool = True) -> bool:
        resolved = Path(file_path)
        try:
            raw = resolved.read_text(encoding="utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Layout root must be a JSON object.")
            self.apply_layout_payload(cast(dict[str, Any], payload))
        except Exception as exc:
            if show_errors:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Load Sample Pad Layout",
                    f"Could not load layout file:\n{resolved}\n\n{exc}",
                )
            return False

        self.set_current_layout_path(str(resolved))
        self.layoutLoaded.emit(str(resolved))
        return True

    def _on_save_layout_clicked(self) -> None:
        default_name = self._current_layout_path or str(Path.home() / "sample-pad-layout.json")
        selected, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Sample Pad Layout",
            default_name,
            "Sample Pad Layout (*.json);;All Files (*)",
        )
        if not selected:
            return
        if not selected.lower().endswith(".json"):
            selected = f"{selected}.json"
        try:
            self.save_layout_to_path(selected)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self,
                "Save Sample Pad Layout",
                f"Could not save layout file:\n{selected}\n\n{exc}",
            )

    def _on_load_layout_clicked(self) -> None:
        default_name = self._current_layout_path or str(Path.home())
        selected, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Load Sample Pad Layout",
            default_name,
            "Sample Pad Layout (*.json);;All Files (*)",
        )
        if not selected:
            return
        self.load_layout_from_path(selected, show_errors=True)
