"""Broadcast-route knowledge shared by the Options and Audio Diagnostics UIs.

The in-app mixer (microphone + jingles) runs in the PortAudio engine on every
platform; what differs per platform is how a user creates the virtual device
that carries that mix into Discord/OBS. Platform-specific guidance lives in
``virtual_device_guide()`` so another platform can be added in one place.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field

import sample_pad_audio_engine as _sp_engine_mod

_VIRTUAL_AUDIO_KEYWORDS = (
    "broadcast",
    "blackhole",
    "cable",
    "dante",
    "jack",
    "loopback",
    "monitor",
    "null",
    "sink",
    "soundflower",
    "vb-audio",
    "virtual",
    "voicemeeter",
)


def is_virtual_audio_device_name(name: str) -> bool:
    normalized = name.strip().casefold()
    if not normalized:
        return False
    if any(keyword in normalized for keyword in _VIRTUAL_AUDIO_KEYWORDS):
        return True

    # On Linux, Qt friendly labels for null sinks can be shortened or cleaned up
    # compared with the PortAudio device names. If a matching PortAudio output has
    # a corresponding .monitor input, treat it as a virtual sink.
    if sys.platform.startswith("linux") and _sp_engine_mod.is_available():
        try:
            devices = _sp_engine_mod.list_audio_devices()
        except Exception:
            devices = []
        candidate_names = {
            str(dev.get("name", "")).strip().casefold()
            for dev in devices
            if str(dev.get("name", "")).strip()
        }
        for candidate in candidate_names:
            if normalized not in candidate and candidate not in normalized:
                continue
            if f"{candidate}.monitor" in candidate_names:
                return True
            if candidate.endswith("broadcast") or candidate.endswith("sink"):
                return True
    return False


def format_audio_device_label(name: str) -> str:
    if is_virtual_audio_device_name(name):
        return f"[Virtual] {name}"
    return name


def broadcast_route_warnings(live_device: str, broadcast_device: str, mixer_enabled: bool) -> list[str]:
    live_device = live_device.strip()
    broadcast_device = broadcast_device.strip()
    warnings: list[str] = []
    if not mixer_enabled:
        warnings.append(
            "Mixer mode is off, so microphone audio will not be sent to the Broadcast Device until mixer mode is enabled."
        )
    if not broadcast_device:
        if live_device and not is_virtual_audio_device_name(live_device):
            warnings.append(
                "No Broadcast Device is selected. Microphone broadcast and mirrored main-window playback will follow the Live Device, which may not be suitable for Discord/OBS style capture unless it is a virtual sink."
            )
    else:
        if not is_virtual_audio_device_name(broadcast_device):
            warnings.append(
                "Broadcast Device does not look like a virtual or loopback device. Physical playback devices usually do not behave like isolated broadcast feeds."
            )
        if broadcast_device.casefold() == live_device.casefold():
            warnings.append(
                "Broadcast Device matches the Live Device, so local monitoring and broadcast routing are not isolated from each other."
            )
    warnings.append(
        "Main library jingles are mirrored to the Broadcast Device. Sample-pad jingles continue to follow the current Live/Preview monitor route, and live-mode sample pads are also duplicated to the Broadcast Device when it differs from the monitor route."
    )
    return warnings


@dataclass(frozen=True)
class VirtualDeviceGuide:
    """How to create, inspect and remove broadcast devices on one platform."""

    title: str
    assistant_lines: list[str]
    workflow_notes: list[str] = field(default_factory=list)
    # Shell snippets offered on copy buttons; empty means no button.
    create_commands: str = ""
    delete_commands: str = ""
    list_commands: str = ""


_PACTL_CREATE_COMMANDS = (
    'pactl load-module module-null-sink sink_name=JingleBroadcast sink_properties=device.description="Jingle Broadcast"',
    'pactl load-module module-remap-source master=JingleBroadcast.monitor source_name=JingleMic source_properties=device.description="Jingle Mic"',
)
_PACTL_DELETE_COMMANDS = (
    "for id in $(pactl list short modules | awk '/module-remap-source/ && /source_name=JingleMic/ {print $1}'); do pactl unload-module \"$id\"; done",
    "for id in $(pactl list short modules | awk '/module-null-sink/ && /sink_name=JingleBroadcast/ {print $1}'); do pactl unload-module \"$id\"; done",
)
_PACTL_LIST_COMMANDS = (
    "pactl list short sinks",
    "pactl list short sources",
)


def _linux_guide() -> VirtualDeviceGuide:
    if shutil.which("pactl"):
        assistant_lines = [
            "Create both the broadcast sink and a Discord-friendly source:",
            *_PACTL_CREATE_COMMANDS,
            "List sinks and sources after creating them:",
            *_PACTL_LIST_COMMANDS,
            "In Discord, select Jingle Mic as the input device. In Audacity, Jingle Mic or JingleBroadcast.monitor should work.",
            "If Discord was already open, fully quit and reopen it after creating the new source so it refreshes its device list.",
            "Delete the devices later with the Copy Delete Broadcast Devices button in Audio Diagnostics.",
        ]
    else:
        assistant_lines = [
            "`pactl` was not found. Install PulseAudio/PipeWire user tools to create and inspect null sinks."
        ]
    return VirtualDeviceGuide(
        title="Linux Virtual Sink Assistant",
        assistant_lines=assistant_lines,
        workflow_notes=[
            "On Linux, create both the sink and the Jingle Mic remapped source, then select Jingle Mic in Discord or other chat apps.",
            "Audacity can usually record from either Jingle Mic or the raw JingleBroadcast.monitor source.",
        ],
        create_commands="\n".join(_PACTL_CREATE_COMMANDS),
        delete_commands="\n".join(_PACTL_DELETE_COMMANDS),
        list_commands="\n".join(_PACTL_LIST_COMMANDS),
    )


def virtual_device_guide() -> VirtualDeviceGuide | None:
    """Return setup guidance for this platform, or None if there is none yet."""
    if sys.platform.startswith("linux"):
        return _linux_guide()
    return None


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
