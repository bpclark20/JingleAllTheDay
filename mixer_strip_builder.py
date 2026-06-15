"""Shared mixer strip UI builder used by sample pads and sequencer mixers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt6 import QtCore, QtWidgets


@dataclass
class ChannelStripWidgets:
    """Widget references for a created mixer strip."""

    channel_box: QtWidgets.QWidget
    name_label: QtWidgets.QLabel
    strip_lane: QtWidgets.QWidget
    volume_slider: QtWidgets.QSlider
    pan_slider: QtWidgets.QSlider
    mute_button: QtWidgets.QPushButton
    solo_button: QtWidgets.QPushButton


def create_channel_strip(
    *,
    parent_layout: QtWidgets.QHBoxLayout,
    index_text: str,
    name_text: str,
    channel_width: int,
    strip_height: int,
    top_to_strip_gap: int,
    strip_to_bottom_gap: int,
    pan_slider_width: int,
    meter_widget: QtWidgets.QWidget | None,
    db_scale_widget: QtWidgets.QWidget | None,
    volume_value: int,
    pan_value: int,
    muted: bool,
    solo: bool,
    on_volume_changed: Callable[[int], None],
    on_volume_dragged: Callable[[int], None] | None,
    on_pan_changed: Callable[[int], None],
    on_pan_dragged: Callable[[int], None] | None,
    on_mute_toggled: Callable[[bool], None],
    on_solo_toggled: Callable[[bool], None],
    channel_object_name: str = "mixerChannel",
    channel_stylesheet: str | None = None,
) -> ChannelStripWidgets:
    """Create a single channel strip and add it to the parent row layout."""
    channel = QtWidgets.QFrame()
    channel.setObjectName(channel_object_name)
    if channel_stylesheet:
        channel.setStyleSheet(channel_stylesheet)
    channel.setFixedWidth(channel_width)
    channel.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Fixed,
        QtWidgets.QSizePolicy.Policy.Expanding,
    )

    channel_layout = QtWidgets.QVBoxLayout(channel)
    channel_layout.setContentsMargins(4, 4, 4, 4)
    channel_layout.setSpacing(0)

    index_label = QtWidgets.QLabel(index_text)
    index_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    index_label.setStyleSheet("color: #b0bec5;")
    index_label.setMinimumHeight(14)
    index_label.setMaximumHeight(14)
    channel_layout.addWidget(index_label)

    name_label = QtWidgets.QLabel(name_text)
    name_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    name_label.setWordWrap(False)
    name_label.setMinimumHeight(16)
    name_label.setMaximumHeight(16)
    channel_layout.addWidget(name_label)
    channel_layout.addSpacing(top_to_strip_gap)

    strip_lane = QtWidgets.QWidget()
    strip_lane.setFixedHeight(strip_height)
    strip_row = QtWidgets.QHBoxLayout(strip_lane)
    strip_row.setContentsMargins(0, 0, 0, 0)
    strip_row.setSpacing(4)

    if meter_widget is not None:
        strip_row.addWidget(meter_widget, 0, QtCore.Qt.AlignmentFlag.AlignTop)
    if db_scale_widget is not None:
        strip_row.addWidget(db_scale_widget, 0, QtCore.Qt.AlignmentFlag.AlignTop)

    volume_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Vertical)
    volume_slider.setRange(0, 100)
    volume_slider.setValue(int(volume_value))
    volume_slider.setPageStep(5)
    volume_slider.setFixedHeight(strip_height)
    volume_slider.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Fixed,
        QtWidgets.QSizePolicy.Policy.Expanding,
    )
    volume_slider.valueChanged.connect(on_volume_changed)
    if on_volume_dragged is not None:
        volume_slider.sliderMoved.connect(on_volume_dragged)
    strip_row.addWidget(volume_slider, 0, QtCore.Qt.AlignmentFlag.AlignTop)

    channel_layout.addWidget(strip_lane, 0, QtCore.Qt.AlignmentFlag.AlignTop)
    channel_layout.addSpacing(strip_to_bottom_gap)

    pan_row = QtWidgets.QHBoxLayout()
    pan_row.setSpacing(4)

    pan_left_label = QtWidgets.QLabel("L")
    pan_left_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    pan_left_label.setStyleSheet("color: #90a4ae;")
    pan_left_label.setFixedWidth(10)
    pan_row.addWidget(pan_left_label)

    pan_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
    pan_slider.setRange(-100, 100)
    pan_slider.setValue(int(pan_value))
    pan_slider.setPageStep(10)
    pan_slider.setFixedWidth(max(34, int(pan_slider_width)))
    pan_slider.valueChanged.connect(on_pan_changed)
    if on_pan_dragged is not None:
        pan_slider.sliderMoved.connect(on_pan_dragged)
    pan_row.addWidget(pan_slider)

    pan_right_label = QtWidgets.QLabel("R")
    pan_right_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    pan_right_label.setStyleSheet("color: #90a4ae;")
    pan_right_label.setFixedWidth(10)
    pan_row.addWidget(pan_right_label)

    channel_layout.addLayout(pan_row)
    channel_layout.addSpacing(4)

    buttons_row = QtWidgets.QHBoxLayout()
    buttons_row.setSpacing(4)

    mute_btn = QtWidgets.QPushButton("M")
    mute_btn.setCheckable(True)
    mute_btn.setChecked(bool(muted))
    mute_btn.toggled.connect(on_mute_toggled)
    mute_btn.setFixedWidth(34)
    buttons_row.addWidget(mute_btn)

    solo_btn = QtWidgets.QPushButton("S")
    solo_btn.setCheckable(True)
    solo_btn.setChecked(bool(solo))
    solo_btn.toggled.connect(on_solo_toggled)
    solo_btn.setFixedWidth(34)
    buttons_row.addWidget(solo_btn)

    channel_layout.addLayout(buttons_row)

    parent_layout.addWidget(channel)
    return ChannelStripWidgets(
        channel_box=channel,
        name_label=name_label,
        strip_lane=strip_lane,
        volume_slider=volume_slider,
        pan_slider=pan_slider,
        mute_button=mute_btn,
        solo_button=solo_btn,
    )


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
