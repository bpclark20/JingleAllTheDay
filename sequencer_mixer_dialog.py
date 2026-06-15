"""Mixer dialog for sequencer tracks."""

from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from mixer_strip_builder import create_channel_strip
from mixer_model import MixerState
from sample_pads import _DbScaleWidget, _PeakMeterWidget


class SequencerMixerDialog(QtWidgets.QDialog):
    """Non-modal mixer for sequencer track channels."""

    mixerChanged = QtCore.pyqtSignal()

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        meter_levels_provider=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sequencer Mixer")
        self.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowFlag(QtCore.Qt.WindowType.Tool, False)
        self.setWindowFlag(QtCore.Qt.WindowType.Window, True)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(1000, 420)

        self._sequence = None
        self._mixer_state = MixerState("Sequencer")
        self._meter_levels_provider = meter_levels_provider

        self._name_labels: list[QtWidgets.QLabel] = []
        self._volume_sliders: list[QtWidgets.QSlider] = []
        self._pan_sliders: list[QtWidgets.QSlider] = []
        self._mute_buttons: list[QtWidgets.QPushButton] = []
        self._solo_buttons: list[QtWidgets.QPushButton] = []
        self._meter_bars: list[_PeakMeterWidget] = []
        self._db_scale_widgets: list[_DbScaleWidget] = []
        self._strip_lane_widgets: list[QtWidgets.QWidget] = []
        self._channel_boxes: list[QtWidgets.QWidget] = []
        self._strip_height: int = 220
        self._strip_width: int = 84
        self._base_strip_width: int = 84
        self._max_strip_width: int = 106
        self._strip_spacing: int = 8
        self._top_group_height: int = 30
        self._pan_group_height: int = 22
        self._pan_to_buttons_gap: int = 4
        self._bottom_group_height: int = 26
        self._top_to_strip_gap: int = 6
        self._strip_to_bottom_gap: int = 8

        root = QtWidgets.QVBoxLayout(self)
        self._header_label = QtWidgets.QLabel("Sequencer Mixer")
        root.addWidget(self._header_label)

        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        root.addWidget(self._scroll)

        self._channels_container = QtWidgets.QWidget()
        self._channels_row = QtWidgets.QHBoxLayout(self._channels_container)
        self._channels_row.setContentsMargins(0, 0, 0, 0)
        self._channels_row.setSpacing(self._strip_spacing)

        self._scroll.setWidget(self._channels_container)

        self._meter_timer = QtCore.QTimer(self)
        self._meter_timer.setInterval(40)
        self._meter_timer.timeout.connect(self._refresh_meters)
        self._meter_timer.start()

    def set_sequence(self, sequence) -> None:
        """Bind dialog to sequence object and rebuild channels."""
        self._sequence = sequence
        self._mixer_state = MixerState.from_sequencer_sequence(sequence)
        self._rebuild_channels()

    def refresh_from_sequence(self) -> None:
        """Refresh sliders/buttons from current sequence values."""
        if self._sequence is None:
            return
        self._mixer_state = MixerState.from_sequencer_sequence(self._sequence)
        channel_count = len(self._mixer_state.channels)

        if channel_count != len(self._volume_sliders):
            self._rebuild_channels()
            return

        for idx, channel in enumerate(self._mixer_state.channels):
            self._name_labels[idx].setText(channel.name)

            self._volume_sliders[idx].blockSignals(True)
            self._volume_sliders[idx].setValue(channel.volume_percent)
            self._volume_sliders[idx].blockSignals(False)

            self._pan_sliders[idx].blockSignals(True)
            self._pan_sliders[idx].setValue(channel.pan_percent)
            self._pan_sliders[idx].blockSignals(False)

            self._mute_buttons[idx].blockSignals(True)
            self._mute_buttons[idx].setChecked(channel.muted)
            self._mute_buttons[idx].blockSignals(False)

            self._solo_buttons[idx].blockSignals(True)
            self._solo_buttons[idx].setChecked(channel.solo)
            self._solo_buttons[idx].blockSignals(False)

    def _rebuild_channels(self) -> None:
        while self._channels_row.count():
            item = self._channels_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._name_labels.clear()
        self._volume_sliders.clear()
        self._pan_sliders.clear()
        self._mute_buttons.clear()
        self._solo_buttons.clear()
        self._meter_bars.clear()
        self._db_scale_widgets.clear()
        self._strip_lane_widgets.clear()
        self._channel_boxes.clear()

        for idx, channel in enumerate(self._mixer_state.channels):
            meter = _PeakMeterWidget(self._strip_height, "#26a69a")
            db_scale_widget = _DbScaleWidget(self._strip_height)

            strip = create_channel_strip(
                parent_layout=self._channels_row,
                index_text=f"Tr {idx + 1}",
                name_text=channel.name,
                channel_width=self._strip_width,
                strip_height=self._strip_height,
                top_to_strip_gap=self._top_to_strip_gap,
                strip_to_bottom_gap=self._strip_to_bottom_gap,
                pan_slider_width=max(34, self._strip_width - 30),
                meter_widget=meter,
                db_scale_widget=db_scale_widget,
                volume_value=channel.volume_percent,
                pan_value=channel.pan_percent,
                muted=channel.muted,
                solo=channel.solo,
                on_volume_changed=lambda value, channel_index=idx: self._on_volume_changed(channel_index, value),
                on_volume_dragged=None,
                on_pan_changed=lambda value, channel_index=idx: self._on_pan_changed(channel_index, value),
                on_pan_dragged=None,
                on_mute_toggled=lambda checked, channel_index=idx: self._on_mute_toggled(channel_index, checked),
                on_solo_toggled=lambda checked, channel_index=idx: self._on_solo_toggled(channel_index, checked),
            )
            strip.channel_box.setObjectName("mixerChannel")
            strip.channel_box.setStyleSheet(
                "#mixerChannel {"
                " border: 1px solid #3a464d;"
                " border-radius: 4px;"
                " background: #161c21;"
                "}"
            )
            strip.name_label.setWordWrap(False)

            self._name_labels.append(strip.name_label)
            self._volume_sliders.append(strip.volume_slider)
            self._pan_sliders.append(strip.pan_slider)
            self._mute_buttons.append(strip.mute_button)
            self._solo_buttons.append(strip.solo_button)
            self._meter_bars.append(meter)
            self._db_scale_widgets.append(db_scale_widget)
            self._strip_lane_widgets.append(strip.strip_lane)
            self._channel_boxes.append(strip.channel_box)

        self._channels_row.addStretch()
        self._apply_responsive_strip_geometry()

    def _apply_state(self) -> None:
        if self._sequence is None:
            return
        self._mixer_state.apply_to_sequencer_sequence(self._sequence)
        self.mixerChanged.emit()

    def _on_volume_changed(self, channel_index: int, value: int) -> None:
        if 0 <= channel_index < len(self._mixer_state.channels):
            self._mixer_state.channels[channel_index].volume_percent = int(value)
            self._apply_state()

    def _on_pan_changed(self, channel_index: int, value: int) -> None:
        if 0 <= channel_index < len(self._mixer_state.channels):
            self._mixer_state.channels[channel_index].pan_percent = int(value)
            self._apply_state()

    def _on_mute_toggled(self, channel_index: int, checked: bool) -> None:
        if 0 <= channel_index < len(self._mixer_state.channels):
            self._mixer_state.channels[channel_index].muted = bool(checked)
            self._apply_state()

    def _on_solo_toggled(self, channel_index: int, checked: bool) -> None:
        if 0 <= channel_index < len(self._mixer_state.channels):
            self._mixer_state.channels[channel_index].solo = bool(checked)
            self._apply_state()

    def _refresh_meters(self) -> None:
        if not self.isVisible():
            return
        levels_map = {}
        if callable(self._meter_levels_provider):
            try:
                provided = self._meter_levels_provider()
                if isinstance(provided, dict):
                    levels_map = provided
            except Exception:
                levels_map = {}

        for idx, meter in enumerate(self._meter_bars):
            level = max(0.0, min(1.0, float(levels_map.get(idx, 0.0))))
            meter.set_level(level)
            meter_value = int(round(level * 100.0))
            if meter_value >= 90:
                color = "#ef5350"
            elif meter_value >= 70:
                color = "#ffd54f"
            else:
                color = "#26a69a"
            meter.set_bar_color(color)

    def _apply_responsive_strip_geometry(self) -> None:
        viewport = self._scroll.viewport()
        if viewport is None:
            return

        visible_width = max(320, viewport.width())
        visible_height = max(260, viewport.height())
        channels_count = max(1, len(self._channel_boxes))
        narrow_total_width = self._ideal_mixer_content_width(self._base_strip_width)
        if visible_width >= narrow_total_width:
            computed_width = min(
                self._max_strip_width,
                int((visible_width - (channels_count - 1) * self._strip_spacing) / channels_count),
            )
        else:
            computed_width = self._base_strip_width

        channel_height = max(240, visible_height - 2)
        reserved_top_bottom = (
            8
            + self._top_group_height
            + self._top_to_strip_gap
            + self._strip_to_bottom_gap
            + self._pan_group_height
            + self._pan_to_buttons_gap
            + self._bottom_group_height
        )
        computed_height = channel_height - reserved_top_bottom
        computed_height = max(140, computed_height)

        if computed_width == self._strip_width and computed_height == self._strip_height:
            return

        self._strip_width = computed_width
        self._strip_height = computed_height
        for channel in self._channel_boxes:
            channel.setFixedWidth(self._strip_width)
        for meter in self._meter_bars:
            meter.set_strip_height(self._strip_height)
        for slider in self._volume_sliders:
            slider.setFixedHeight(self._strip_height)
        pan_slider_width = max(34, self._strip_width - 30)
        for pan_slider in self._pan_sliders:
            pan_slider.setFixedWidth(pan_slider_width)
        for db_scale in self._db_scale_widgets:
            db_scale.set_strip_height(self._strip_height)
        for strip_lane in self._strip_lane_widgets:
            strip_lane.setFixedHeight(self._strip_height)

    def _ideal_mixer_content_width(self, strip_width: int) -> int:
        channels_count = max(1, len(self._channel_boxes))
        return channels_count * strip_width + (channels_count - 1) * self._strip_spacing + 32

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

        target_height = min(max(460, int(available.height() * 0.78)), available.height() - 56)
        if target_height > 240:
            self.resize(self.width(), target_height)

        full_narrow_width = self._ideal_mixer_content_width(self._base_strip_width)
        if available.width() >= full_narrow_width + 36:
            desired_width = min(
                available.width() - 24,
                self._ideal_mixer_content_width(self._max_strip_width),
            )
            self.resize(desired_width, self.height())

    def resizeEvent(self, event) -> None:
        self._apply_responsive_strip_geometry()
        super().resizeEvent(event)

    def closeEvent(self, event) -> None:
        self._meter_timer.stop()
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        self._meter_timer.start()
        self.fit_to_screen_if_possible()
        self._apply_responsive_strip_geometry()
        QtCore.QTimer.singleShot(0, self._apply_responsive_strip_geometry)
        super().showEvent(event)


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
