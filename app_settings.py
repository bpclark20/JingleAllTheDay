"""Typed access to the persisted QSettings values.

QSettings (INI format) hands values back as strings, so every read needs
coercion and a fallback for missing or corrupt entries. The helpers here keep
that in one place, and ``OptionsSettings`` is the single definition of the
values edited in the Options dialog: their keys, defaults and valid ranges.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QSettings

from app_helpers import (
    _coerce_sample_pad_blocksize,
    _coerce_sample_pad_streaming_min_seconds,
    coerce_recent_window_days,
    coerce_volume_percent,
)


def read_bool(settings: QSettings, key: str, default: bool) -> bool:
    raw = settings.value(key, None)
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower() if raw is not None else ""
    if text == "true":
        return True
    if text == "false":
        return False
    return default


def write_bool(settings: QSettings, key: str, value: bool) -> None:
    settings.setValue(key, "true" if value else "false")


def read_int(
    settings: QSettings,
    key: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Read an int; missing, unparsable or out-of-range values give ``default``."""
    try:
        value = int(settings.value(key, default))
    except (TypeError, ValueError):
        return default
    if minimum is not None and value < minimum:
        return default
    if maximum is not None and value > maximum:
        return default
    return value


def read_float(settings: QSettings, key: str, default: float) -> float:
    try:
        return float(settings.value(key, default))
    except (TypeError, ValueError):
        return default


def read_str(settings: QSettings, key: str, default: str = "") -> str:
    raw = settings.value(key, default)
    return str(raw).strip() if raw is not None else default


def _clamp_microphone_gain_percent(value: Any) -> int:
    try:
        return max(0, min(200, int(value)))
    except (TypeError, ValueError):
        return 100


@dataclass
class OptionsSettings:
    output_device: str = ""
    preview_output_device: str = ""
    broadcast_output_device: str = ""
    mixer_enabled: bool = False
    microphone_input_device: str = ""
    # PortAudio input device index; -1 means "use the default input".
    recording_input_device: int = -1
    microphone_gain_percent: int = 100
    live_volume_percent: int = 100
    preview_volume_percent: int = 100
    recent_window_days: int = 14
    sample_pad_blocksize: int = 128
    sample_pad_streaming_min_seconds: int = 120
    server_enabled: bool = True
    server_address: str = ""
    server_device_token: str = ""
    cache_backup_reminder_days: int = 7

    @classmethod
    def load(cls, settings: QSettings) -> OptionsSettings:
        defaults = cls()
        recording_input_device = read_int(
            settings, "options/recordingInputDevice", -1, minimum=0
        )
        return cls(
            output_device=read_str(settings, "options/outputDevice"),
            preview_output_device=read_str(settings, "options/previewOutputDevice"),
            broadcast_output_device=read_str(settings, "options/broadcastOutputDevice"),
            mixer_enabled=read_bool(settings, "options/mixerEnabled", defaults.mixer_enabled),
            microphone_input_device=read_str(settings, "options/microphoneInputDevice"),
            recording_input_device=recording_input_device,
            microphone_gain_percent=_clamp_microphone_gain_percent(
                settings.value("options/microphoneGainPercent", defaults.microphone_gain_percent)
            ),
            live_volume_percent=coerce_volume_percent(
                settings.value("options/liveVolumePercent", defaults.live_volume_percent)
            ),
            preview_volume_percent=coerce_volume_percent(
                settings.value("options/previewVolumePercent", defaults.preview_volume_percent)
            ),
            recent_window_days=coerce_recent_window_days(
                settings.value("options/recentWindowDays", defaults.recent_window_days)
            ),
            sample_pad_blocksize=_coerce_sample_pad_blocksize(
                settings.value("options/samplePadBlocksize", defaults.sample_pad_blocksize)
            ),
            sample_pad_streaming_min_seconds=_coerce_sample_pad_streaming_min_seconds(
                settings.value(
                    "options/samplePadStreamingMinSeconds",
                    defaults.sample_pad_streaming_min_seconds,
                )
            ),
            server_enabled=read_bool(settings, "server/enabled", defaults.server_enabled),
            server_address=read_str(settings, "server/address"),
            server_device_token=read_str(settings, "server/deviceToken"),
            cache_backup_reminder_days=read_int(
                settings,
                "server/cacheBackupReminderDays",
                defaults.cache_backup_reminder_days,
                minimum=1,
            ),
        )

    def save(self, settings: QSettings) -> None:
        settings.setValue("options/outputDevice", self.output_device)
        settings.setValue("options/previewOutputDevice", self.preview_output_device)
        settings.setValue("options/broadcastOutputDevice", self.broadcast_output_device)
        write_bool(settings, "options/mixerEnabled", self.mixer_enabled)
        settings.setValue("options/microphoneInputDevice", self.microphone_input_device)
        settings.setValue("options/recordingInputDevice", self.recording_input_device)
        settings.setValue("options/microphoneGainPercent", self.microphone_gain_percent)
        settings.setValue("options/liveVolumePercent", self.live_volume_percent)
        settings.setValue("options/previewVolumePercent", self.preview_volume_percent)
        settings.setValue("options/recentWindowDays", self.recent_window_days)
        settings.setValue("options/samplePadBlocksize", self.sample_pad_blocksize)
        settings.setValue(
            "options/samplePadStreamingMinSeconds", self.sample_pad_streaming_min_seconds
        )
        write_bool(settings, "server/enabled", self.server_enabled)
        settings.setValue("server/address", self.server_address)
        settings.setValue("server/deviceToken", self.server_device_token)
        settings.setValue("server/cacheBackupReminderDays", self.cache_backup_reminder_days)


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
