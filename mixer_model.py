"""Generic mixer data model for sample pads and sequencer tracks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MixerChannel:
    """Represents one mixer strip/channel."""

    channel_id: str
    name: str
    volume_percent: int = 100
    pan_percent: int = 0
    muted: bool = False
    solo: bool = False
    meter_level: float = 0.0


@dataclass
class MixerState:
    """Represents a complete mixer state with multiple channels."""

    mixer_name: str
    channels: list[MixerChannel] = field(default_factory=list)

    def get_channel(self, channel_id: str) -> MixerChannel | None:
        for channel in self.channels:
            if channel.channel_id == channel_id:
                return channel
        return None

    @staticmethod
    def from_sequencer_sequence(sequence: Any) -> MixerState:
        """Build mixer state from a sequencer Sequence-like object."""
        channels: list[MixerChannel] = []
        for index, track in enumerate(getattr(sequence, "tracks", [])):
            channels.append(
                MixerChannel(
                    channel_id=f"track:{index}",
                    name=str(getattr(track, "name", f"Track {index + 1}")),
                    volume_percent=int(getattr(track, "volume_percent", 100)),
                    pan_percent=int(getattr(track, "pan_percent", 0)),
                    muted=bool(getattr(track, "mute", False)),
                    solo=bool(getattr(track, "solo", False)),
                )
            )
        return MixerState(mixer_name="Sequencer", channels=channels)

    def apply_to_sequencer_sequence(self, sequence: Any) -> None:
        """Apply current channel settings back to a sequencer Sequence-like object."""
        tracks = getattr(sequence, "tracks", [])
        for index, channel in enumerate(self.channels):
            if index >= len(tracks):
                break
            track = tracks[index]
            track.volume_percent = max(0, min(100, int(channel.volume_percent)))
            track.pan_percent = max(-100, min(100, int(channel.pan_percent)))
            track.mute = bool(channel.muted)
            track.solo = bool(channel.solo)


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
