"""Data models for sequencer/timeline editor."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class TriggerEvent:
    """A trigger event on a track: when to start and for how long."""

    beat_position: float  # Position in beats (can be fractional, e.g., 0.5 for half-beat)
    duration_beats: float  # How long to play (in beats)

    def to_dict(self) -> dict[str, Any]:
        return {"beat_position": self.beat_position, "duration_beats": self.duration_beats}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> TriggerEvent:
        return TriggerEvent(
            beat_position=float(data.get("beat_position", 0.0)),
            duration_beats=float(data.get("duration_beats", 1.0)),
        )


@dataclass
class SequenceTrack:
    """A single track in the sequence (jingle or WAV file)."""

    name: str
    source_path: str  # Path to jingle file or WAV file
    is_jingle: bool  # True if from library, False if arbitrary WAV
    volume_percent: int = 100  # 0-100
    pan_percent: int = 0  # -100 (left) to +100 (right), 0 = center
    mute: bool = False
    solo: bool = False
    triggers: list[TriggerEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_path": self.source_path,
            "is_jingle": self.is_jingle,
            "volume_percent": self.volume_percent,
            "pan_percent": self.pan_percent,
            "mute": self.mute,
            "solo": self.solo,
            "triggers": [t.to_dict() for t in self.triggers],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> SequenceTrack:
        triggers = [TriggerEvent.from_dict(t) for t in data.get("triggers", [])]
        return SequenceTrack(
            name=str(data.get("name", "Untitled")),
            source_path=str(data.get("source_path", "")),
            is_jingle=bool(data.get("is_jingle", False)),
            volume_percent=int(data.get("volume_percent", 100)),
            pan_percent=int(data.get("pan_percent", 0)),
            mute=bool(data.get("mute", False)),
            solo=bool(data.get("solo", False)),
            triggers=triggers,
        )


@dataclass
class Sequence:
    """A complete sequence with tracks, events, and metadata."""

    name: str
    bpm: float = 120.0
    tracks: list[SequenceTrack] = field(default_factory=list)
    creation_timestamp: float = field(default_factory=lambda: __import__("time").time())
    last_modified_timestamp: float = field(default_factory=lambda: __import__("time").time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "bpm": self.bpm,
            "creation_timestamp": self.creation_timestamp,
            "last_modified_timestamp": self.last_modified_timestamp,
            "tracks": [t.to_dict() for t in self.tracks],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Sequence:
        tracks = [SequenceTrack.from_dict(t) for t in data.get("tracks", [])]
        return Sequence(
            name=str(data.get("name", "Untitled Sequence")),
            bpm=float(data.get("bpm", 120.0)),
            creation_timestamp=float(data.get("creation_timestamp", __import__("time").time())),
            last_modified_timestamp=float(data.get("last_modified_timestamp", __import__("time").time())),
            tracks=tracks,
        )

    def add_track(self, track: SequenceTrack) -> None:
        """Add a new track to the sequence."""
        self.tracks.append(track)
        self._update_modified_time()

    def remove_track(self, track_index: int) -> None:
        """Remove a track by index."""
        if 0 <= track_index < len(self.tracks):
            self.tracks.pop(track_index)
            self._update_modified_time()

    def add_trigger(self, track_index: int, trigger: TriggerEvent) -> None:
        """Add a trigger event to a track."""
        if 0 <= track_index < len(self.tracks):
            self.tracks[track_index].triggers.append(trigger)
            self.tracks[track_index].triggers.sort(key=lambda t: t.beat_position)
            self._update_modified_time()

    def remove_trigger(self, track_index: int, trigger_index: int) -> None:
        """Remove a trigger event from a track."""
        if 0 <= track_index < len(self.tracks) and 0 <= trigger_index < len(
            self.tracks[track_index].triggers
        ):
            self.tracks[track_index].triggers.pop(trigger_index)
            self._update_modified_time()

    def move_trigger(self, track_index: int, trigger_index: int, new_beat_position: float) -> None:
        """Move a trigger to a new beat position."""
        if 0 <= track_index < len(self.tracks) and 0 <= trigger_index < len(
            self.tracks[track_index].triggers
        ):
            trigger = self.tracks[track_index].triggers[trigger_index]
            trigger.beat_position = max(0.0, new_beat_position)
            self.tracks[track_index].triggers.sort(key=lambda t: t.beat_position)
            self._update_modified_time()

    def get_duration_beats(self) -> float:
        """Calculate total duration of sequence in beats."""
        max_end = 0.0
        for track in self.tracks:
            for trigger in track.triggers:
                end_beat = trigger.beat_position + trigger.duration_beats
                max_end = max(max_end, end_beat)
        # Add one measure (4 beats) buffer at end
        return max(4.0, max_end + 4.0)

    def _update_modified_time(self) -> None:
        """Update last modified timestamp."""
        import time

        self.last_modified_timestamp = time.time()


class SequenceStore:
    """Persistence layer for sequences."""

    def __init__(self, sequences_dir: Path) -> None:
        self.sequences_dir = Path(sequences_dir)
        self.sequences_dir.mkdir(parents=True, exist_ok=True)

    def save_sequence(self, sequence: Sequence, filename: str | None = None) -> Path:
        """Save sequence to JSON file."""
        if filename is None:
            # Generate filename from sequence name
            import re

            safe_name = re.sub(r"[^\w\s-]", "", sequence.name).strip().replace(" ", "-")
            if not safe_name:
                safe_name = "sequence"
            timestamp = int(sequence.last_modified_timestamp * 1000)
            filename = f"{safe_name}-{timestamp}.jingle-sequence"

        filepath = self.sequences_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "sequence": sequence.to_dict()}, f, indent=2)
        return filepath

    def load_sequence(self, filepath: Path) -> Sequence | None:
        """Load sequence from JSON file."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            seq_data = data.get("sequence", {})
            return Sequence.from_dict(seq_data)
        except (OSError, json.JSONDecodeError):
            return None

    def list_sequences(self) -> list[tuple[Path, str]]:
        """Return list of (filepath, display_name) tuples."""
        sequences = []
        for filepath in sorted(self.sequences_dir.glob("*.jingle-sequence")):
            seq = self.load_sequence(filepath)
            if seq:
                sequences.append((filepath, seq.name))
        return sequences


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
