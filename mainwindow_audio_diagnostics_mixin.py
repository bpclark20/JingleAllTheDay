from __future__ import annotations

from typing import Any

from app_helpers import _has_qt_multimedia, QMediaDevices
from broadcast_routing import (
    broadcast_route_warnings,
    format_audio_device_label as _format_audio_device_label,
    is_virtual_audio_device_name as _is_virtual_audio_device_name,
    virtual_device_guide,
)
from dialogs import AudioDiagnosticsDialog
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
            **self._virtual_device_assistant_snapshot(),
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
        return broadcast_route_warnings(
            self._output_device, self._broadcast_output_device, self._mixer_enabled
        )

    def _virtual_device_assistant_snapshot(self) -> dict[str, Any]:
        guide = virtual_device_guide()
        if guide is None:
            return {
                "virtual_device_assistant_title": "Virtual Device Assistant",
                "virtual_device_assistant": [
                    "No virtual device setup assistant is available for this platform yet."
                ],
                "virtual_device_workflow_notes": [],
            }
        lines = list(guide.assistant_lines)
        if self._audio_diagnostics_virtual_candidates():
            lines.append("Existing virtual-looking devices are already visible below in the diagnostics report.")
        return {
            "virtual_device_assistant_title": guide.title,
            "virtual_device_assistant": lines,
            "virtual_device_workflow_notes": list(guide.workflow_notes),
        }

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
