from __future__ import annotations

import shutil
import sys
from typing import Any

from app_helpers import _has_qt_multimedia, QMediaDevices
from dialogs import (
    AudioDiagnosticsDialog,
    format_audio_device_label as _format_audio_device_label,
    is_virtual_audio_device_name as _is_virtual_audio_device_name,
)
import sample_pad_audio_engine as _sp_engine_mod


class MainWindowAudioDiagnosticsMixin:
    def _current_mixer_status_text(self) -> str:
        if not _sp_engine_mod.is_available():
            return "Backend unavailable"
        if not self._mixer_enabled:
            return "Off"

        try:
            input_name = self._sp_engine.input_device_name().strip()
            output_name = self._sp_engine.output_device_name().strip()
            meter_left, meter_right = self._sp_engine.input_meter_levels()
        except Exception:
            return "State unavailable"

        if not input_name:
            return "Waiting for microphone"

        meter_peak = max(float(meter_left), float(meter_right))
        activity = "Active" if meter_peak >= 0.01 else "Idle"
        level_percent = int(round(meter_peak * 100.0))
        if output_name:
            return f"{activity} {level_percent:02d}% | Mic {input_name} -> {output_name}"
        return f"{activity} {level_percent:02d}% | Mic {input_name} -> No output route"

    def _collect_qt_audio_device_names(self, *, inputs: bool) -> list[str]:
        if not _has_qt_multimedia:
            return []
        names: list[str] = []
        try:
            devices = QMediaDevices.audioInputs() if inputs else QMediaDevices.audioOutputs()
            for device in devices:
                name = device.description().strip()
                if name and name not in names:
                    names.append(name)
        except Exception:
            return []
        return names

    def _collect_portaudio_device_names(self, *, inputs: bool) -> list[str]:
        channel_key = "max_input_channels" if inputs else "max_output_channels"
        names: list[str] = []
        for dev in _sp_engine_mod.list_audio_devices(channel_key=channel_key):
            name = str(dev.get("name", "")).strip()
            if not name:
                continue
            channels = int(dev.get(channel_key, 0) or 0)
            samplerate = int(round(float(dev.get("default_samplerate", 0.0) or 0.0)))
            names.append(f"{name} | ch={channels} | {samplerate} Hz")
        return names

    def _audio_diagnostics_snapshot(self) -> dict[str, Any]:
        qt_outputs = self._collect_qt_audio_device_names(inputs=False)
        qt_inputs = self._collect_qt_audio_device_names(inputs=True)
        portaudio_outputs = self._collect_portaudio_device_names(inputs=False)
        portaudio_inputs = self._collect_portaudio_device_names(inputs=True)
        virtual_candidates = self._audio_diagnostics_virtual_candidates()

        qt_output_device = ""
        if self._audio_output is not None and _has_qt_multimedia:
            try:
                qt_output_device = self._audio_output.device().description().strip()
            except Exception:
                qt_output_device = ""

        return {
            "mixer_status": self._current_mixer_status_text(),
            "active_output_route": self._resolved_mixer_output_device(),
            "live_output_setting": self._output_device,
            "preview_output_setting": self._preview_output_device,
            "broadcast_output_setting": self._broadcast_output_device,
            "microphone_setting": self._microphone_input_device,
            "qt_output_device": qt_output_device,
            "portaudio_output_device": self._sp_engine.output_device_name(),
            "portaudio_input_device": self._sp_engine.input_device_name(),
            "qt_output_devices": [_format_audio_device_label(name) for name in qt_outputs],
            "qt_input_devices": [_format_audio_device_label(name) for name in qt_inputs],
            "portaudio_output_devices": portaudio_outputs,
            "portaudio_input_devices": portaudio_inputs,
            "virtual_candidates": virtual_candidates,
            "warnings": self._broadcast_route_warnings(),
            "linux_assistant": self._linux_virtual_sink_assistant(),
        }

    def _on_help_audio_diagnostics(self) -> None:
        if self._audio_diagnostics_dialog is None:
            self._audio_diagnostics_dialog = AudioDiagnosticsDialog(
                snapshot_provider=self._audio_diagnostics_snapshot,
                parent=self,
            )
        self._audio_diagnostics_dialog.refresh_report()
        self._audio_diagnostics_dialog.show()
        self._audio_diagnostics_dialog.raise_()
        self._audio_diagnostics_dialog.activateWindow()

    def _broadcast_route_warnings(self) -> list[str]:
        warnings: list[str] = []
        live_device = self._output_device.strip()
        broadcast_device = self._broadcast_output_device.strip()
        if not self._mixer_enabled:
            warnings.append(
                "Mixer mode is off, so microphone audio will not be sent to the Broadcast Device until mixer mode is enabled."
            )
        if not broadcast_device:
            if live_device and not _is_virtual_audio_device_name(live_device):
                warnings.append(
                    "No Broadcast Device is selected. Microphone broadcast and mirrored main-window playback will follow the Live Device, which may not be suitable for Discord/OBS style capture unless it is a virtual sink."
                )
        else:
            if not _is_virtual_audio_device_name(broadcast_device):
                warnings.append(
                    "Broadcast Device does not look like a virtual or loopback device. Physical playback devices usually do not behave like isolated broadcast feeds."
                )
            if self._normalize_device_key(broadcast_device) == self._normalize_device_key(live_device):
                warnings.append(
                    "Broadcast Device matches the Live Device, so local monitoring and broadcast routing are not isolated from each other."
                )
        warnings.append(
            "Main library jingles are mirrored to the Broadcast Device. Sample-pad jingles continue to follow the current Live/Preview monitor route, and live-mode sample pads are also duplicated to the Broadcast Device when it differs from the monitor route."
        )
        return warnings

    def _linux_virtual_sink_assistant(self) -> list[str]:
        if not sys.platform.startswith("linux"):
            return ["Linux assistant is only available on Linux systems."]
        lines: list[str] = []
        if shutil.which("pactl"):
            lines.extend(
                [
                    "Create both the broadcast sink and a Discord-friendly source:",
                    'pactl load-module module-null-sink sink_name=JingleBroadcast sink_properties=device.description="Jingle Broadcast"',
                    'pactl load-module module-remap-source master=JingleBroadcast.monitor source_name=JingleMic source_properties=device.description="Jingle Mic"',
                    "List sinks and sources after creating them:",
                    "pactl list short sinks",
                    "pactl list short sources",
                    "In Discord, select Jingle Mic as the input device. In Audacity, Jingle Mic or JingleBroadcast.monitor should work.",
                    "If Discord was already open, fully quit and reopen it after creating the new source so it refreshes its device list.",
                    "Delete the devices later with the Copy Delete Broadcast Devices button in Audio Diagnostics.",
                ]
            )
        else:
            lines.append("`pactl` was not found. Install PulseAudio/PipeWire user tools to create and inspect null sinks.")
        virtual_candidates = self._audio_diagnostics_virtual_candidates()
        if virtual_candidates:
            lines.append("Existing virtual-looking devices are already visible below in the diagnostics report.")
        return lines

    def _audio_diagnostics_virtual_candidates(self) -> list[str]:
        qt_outputs = self._collect_qt_audio_device_names(inputs=False)
        qt_inputs = self._collect_qt_audio_device_names(inputs=True)
        portaudio_outputs = self._collect_portaudio_device_names(inputs=False)
        portaudio_inputs = self._collect_portaudio_device_names(inputs=True)

        virtual_candidates: list[str] = []
        seen_virtuals: set[str] = set()
        for name in qt_outputs + qt_inputs:
            if not _is_virtual_audio_device_name(name):
                continue
            display = f"Qt: {_format_audio_device_label(name)}"
            if display not in seen_virtuals:
                seen_virtuals.add(display)
                virtual_candidates.append(display)
        for item in portaudio_outputs + portaudio_inputs:
            raw_name = item.split(" | ", 1)[0].strip()
            if not _is_virtual_audio_device_name(raw_name):
                continue
            display = f"PortAudio: {_format_audio_device_label(raw_name)}"
            if display not in seen_virtuals:
                seen_virtuals.add(display)
                virtual_candidates.append(display)
        return virtual_candidates


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
