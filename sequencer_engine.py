"""Sequencer playback engine for timeline-based audio triggering."""

from __future__ import annotations

import math
import threading
import time
import wave
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

import numpy as np

from sequencer_model import Sequence


class PlaybackState(Enum):
    """Playback state machine."""

    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


@dataclass
class PlaybackMetrics:
    """Metrics during sequencer playback."""

    state: PlaybackState
    current_beat: float
    current_seconds: float
    bpm: float


class SequencerEngine:
    """Manages sequencer playback with BPM-based timeline."""

    def __init__(self, sample_pad_audio_engine=None) -> None:
        self._state = PlaybackState.STOPPED
        self._current_beat = 0.0
        self._bpm = 120.0
        self._start_time = 0.0
        self._pause_time = 0.0
        self._sample_pad_engine = sample_pad_audio_engine
        self._playback_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._metrics_callback: Callable[[PlaybackMetrics], None] | None = None
        self._active_voices: dict[str, dict] = {}  # track_index:trigger_index -> voice info
        self._metronome_enabled = False
        self._metronome_voice_id = "_metronome"
        self._sequence: Sequence | None = None
        self._triggered_events: set[tuple[int, int]] = set()
        self._last_processed_beat = 0.0
        self._sequencer_pad_offset = 10_000
        self._source_duration_cache: dict[str, float | None] = {}
        self._lock = threading.RLock()

    def set_sequence(self, sequence: Sequence | None) -> None:
        """Set sequence used for playback scheduling."""
        with self._lock:
            self._sequence = sequence
            self._triggered_events.clear()
            self._last_processed_beat = self._current_beat

    def set_bpm(self, bpm: float) -> None:
        """Set BPM (beats per minute)."""
        with self._lock:
            self._bpm = max(1.0, min(300.0, float(bpm)))

    def get_bpm(self) -> float:
        """Get current BPM."""
        return self._bpm

    def get_state(self) -> PlaybackState:
        """Get current playback state."""
        return self._state

    def get_current_beat(self) -> float:
        """Get current beat position."""
        return self._current_beat

    def get_current_seconds(self) -> float:
        """Get current playback time in seconds."""
        return self._beat_to_seconds(self._current_beat)

    def play(self) -> None:
        """Start playback from current position."""
        with self._lock:
            if self._state == PlaybackState.PLAYING:
                return
            if self._state == PlaybackState.PAUSED:
                # Resume from pause
                elapsed_pause = time.time() - self._pause_time
                self._start_time += elapsed_pause
            else:
                # Start from current beat
                self._start_time = time.time() - self._beat_to_seconds(self._current_beat)
                self._triggered_events.clear()
            self._last_processed_beat = self._current_beat

            self._state = PlaybackState.PLAYING
            self._stop_event.clear()

            # Start playback thread
            self._playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
            self._playback_thread.start()

    def pause(self) -> None:
        """Pause playback."""
        with self._lock:
            if self._state != PlaybackState.PLAYING:
                return
            self._state = PlaybackState.PAUSED
            self._pause_time = time.time()
            self._stop_playback_loop()
            self._stop_all_voices()

    def stop(self) -> None:
        """Stop playback and reset to beat 0."""
        with self._lock:
            self._state = PlaybackState.STOPPED
            self._current_beat = 0.0
            self._last_processed_beat = 0.0
            self._triggered_events.clear()
            self._stop_playback_loop()
            self._stop_all_voices()

    def seek_to_beat(self, beat: float) -> None:
        """Seek to a specific beat position."""
        with self._lock:
            self._current_beat = max(0.0, float(beat))
            self._last_processed_beat = self._current_beat
            self._triggered_events = {
                key
                for key in self._triggered_events
                if self._event_beat_position(*key) < self._current_beat
            }
            if self._state == PlaybackState.PLAYING:
                self._start_time = time.time() - self._beat_to_seconds(self._current_beat)
            self._stop_all_voices()  # Stop all active voices when seeking

    def set_metronome_enabled(self, enabled: bool) -> None:
        """Enable/disable metronome click."""
        self._metronome_enabled = enabled

    def is_metronome_enabled(self) -> bool:
        """Check if metronome is enabled."""
        return self._metronome_enabled

    def sequencer_pad_offset(self) -> int:
        """Return pad index offset used for sequencer-triggered voices."""
        return self._sequencer_pad_offset

    def get_metrics(self) -> PlaybackMetrics:
        """Get current playback metrics."""
        with self._lock:
            return PlaybackMetrics(
                state=self._state,
                current_beat=self._current_beat,
                current_seconds=self._beat_to_seconds(self._current_beat),
                bpm=self._bpm,
            )

    def set_metrics_callback(self, callback: Callable[[PlaybackMetrics], None]) -> None:
        """Set callback for periodic metrics updates."""
        self._metrics_callback = callback

    def update_track_trigger(
        self,
        track_index: int,
        trigger_index: int,
        beat_position: float,
        duration_beats: float,
    ) -> None:
        """Update a trigger's position and duration (for UI editing)."""
        # This is called from UI thread; just store for next playback loop to check
        # In a real implementation, you might want to handle live editing
        pass

    # --- Private methods ---

    def _playback_loop(self) -> None:
        """Main playback loop (runs in separate thread)."""
        try:
            last_metric_time = time.time()

            while not self._stop_event.is_set():
                with self._lock:
                    if self._state != PlaybackState.PLAYING:
                        break

                    # Update current beat based on elapsed time
                    elapsed = time.time() - self._start_time
                    previous_beat = self._current_beat
                    self._current_beat = self._seconds_to_beat(elapsed)

                    self._process_timeline_events(previous_beat, self._current_beat)
                    self._stop_finished_voices(self._current_beat)
                    self._last_processed_beat = self._current_beat

                    sequence_duration = self._sequence.get_duration_beats() if self._sequence else 0.0
                    if sequence_duration > 0 and self._current_beat >= sequence_duration:
                        self._state = PlaybackState.STOPPED
                        self._current_beat = 0.0
                        self._last_processed_beat = 0.0
                        self._triggered_events.clear()
                        self._stop_all_voices()
                        break

                now = time.time()
                if now - last_metric_time > 0.05:  # Update metrics every 50ms
                    if self._metrics_callback:
                        self._metrics_callback(self.get_metrics())
                    last_metric_time = now

                time.sleep(0.01)  # 10ms tick

        finally:
            self._stop_playback_loop()

    def _stop_playback_loop(self) -> None:
        """Signal playback loop to stop."""
        self._stop_event.set()
        if self._playback_thread and self._playback_thread != threading.current_thread():
            self._playback_thread.join(timeout=1.0)

    def _stop_all_voices(self) -> None:
        """Stop all active voice playback."""
        if not self._sample_pad_engine:
            return
        with self._lock:
            for voice_info in list(self._active_voices.values()):
                try:
                    pad_index = int(voice_info.get("pad_index", -1))
                    if pad_index >= 0:
                        self._sample_pad_engine.stop(pad_index=pad_index)
                except Exception:
                    pass
            self._active_voices.clear()

    def _process_timeline_events(self, start_beat: float, end_beat: float) -> None:
        """Trigger events that start between start_beat and end_beat."""
        if not self._sample_pad_engine or not self._sequence:
            return
        if end_beat < start_beat:
            return

        sequence = self._sequence
        has_solo = any(track.solo for track in sequence.tracks)

        for track_index, track in enumerate(sequence.tracks):
            if not track.source_path:
                continue

            if has_solo:
                if not track.solo or track.mute:
                    continue
            elif track.mute:
                continue

            pad_index = self._sequencer_pad_offset + track_index
            try:
                self._sample_pad_engine.set_pad_mix(
                    pad_index=pad_index,
                    volume_percent=track.volume_percent,
                    pan_percent=track.pan_percent,
                    muted=track.mute,
                    solo=track.solo,
                )
            except Exception:
                pass

            for trigger_index, trigger in enumerate(track.triggers):
                trigger_start = max(0.0, float(trigger.beat_position))
                if not (start_beat <= trigger_start <= end_beat):
                    continue

                event_key = (track_index, trigger_index)
                if event_key in self._triggered_events:
                    continue

                trigger_duration_beats = max(0.01, float(trigger.duration_beats))
                trigger_end_beat = trigger_start + trigger_duration_beats
                duration_seconds = self._beat_to_seconds(trigger_duration_beats)
                source_duration_seconds = self._source_duration_seconds(track.source_path)
                should_loop = (
                    source_duration_seconds is not None
                    and source_duration_seconds > 0.0
                    and duration_seconds > source_duration_seconds + 1e-6
                )

                # One pad index is used per track, so replace any pending end-marker
                # tied to the same pad when retriggering.
                stale_keys = [
                    key
                    for key, voice in self._active_voices.items()
                    if int(voice.get("pad_index", -1)) == pad_index
                ]
                for stale_key in stale_keys:
                    self._active_voices.pop(stale_key, None)

                try:
                    self._sample_pad_engine.trigger(
                        path=track.source_path,
                        volume=1.0,
                        clip_start_seconds=0.0,
                        clip_stop_seconds=0.0 if should_loop else duration_seconds,
                        loop=should_loop,
                        pad_index=pad_index,
                    )
                except Exception:
                    continue

                self._active_voices[f"{track_index}:{trigger_index}"] = {
                    "track_index": track_index,
                    "trigger_index": trigger_index,
                    "pad_index": pad_index,
                    "triggered_at_beat": trigger_start,
                    "end_beat": trigger_end_beat,
                    "looping": should_loop,
                }
                self._triggered_events.add(event_key)

    def _stop_finished_voices(self, current_beat: float) -> None:
        """Stop voices whose trigger boundary has been reached."""
        if not self._sample_pad_engine:
            return

        to_stop: list[tuple[str, int]] = []
        for voice_key, voice_info in self._active_voices.items():
            end_beat = float(voice_info.get("end_beat", -1.0))
            if end_beat >= 0.0 and current_beat >= end_beat:
                pad_index = int(voice_info.get("pad_index", -1))
                if pad_index >= 0:
                    to_stop.append((voice_key, pad_index))

        for voice_key, pad_index in to_stop:
            try:
                self._sample_pad_engine.stop(pad_index=pad_index)
            except Exception:
                pass
            self._active_voices.pop(voice_key, None)

    def _source_duration_seconds(self, source_path: str) -> float | None:
        """Resolve source duration (seconds) with a small in-memory cache."""
        path_key = (source_path or "").strip()
        if not path_key:
            return None
        if path_key in self._source_duration_cache:
            return self._source_duration_cache[path_key]

        duration: float | None = None
        path_obj = Path(path_key)
        if path_obj.exists() and path_obj.is_file():
            try:
                import soundfile as sf  # type: ignore

                info = sf.info(str(path_obj))
                if info.samplerate > 0:
                    duration = float(info.frames) / float(info.samplerate)
            except Exception:
                duration = None

            if duration is None and path_obj.suffix.lower() in {".wav", ".wave"}:
                try:
                    with wave.open(str(path_obj), "rb") as wav_file:
                        frames = wav_file.getnframes()
                        sample_rate = wav_file.getframerate()
                        if sample_rate > 0:
                            duration = float(frames) / float(sample_rate)
                except Exception:
                    duration = None

        self._source_duration_cache[path_key] = duration
        return duration

    def _event_beat_position(self, track_index: int, trigger_index: int) -> float:
        """Return beat position for a trigger key, if it still exists."""
        sequence = self._sequence
        if not sequence:
            return 0.0
        if track_index < 0 or track_index >= len(sequence.tracks):
            return 0.0
        triggers = sequence.tracks[track_index].triggers
        if trigger_index < 0 or trigger_index >= len(triggers):
            return 0.0
        return float(triggers[trigger_index].beat_position)

    def _beat_to_seconds(self, beat: float) -> float:
        """Convert beat position to seconds."""
        # seconds = beat / (bpm / 60)
        if self._bpm <= 0:
            return 0.0
        return beat / (self._bpm / 60.0)

    def _seconds_to_beat(self, seconds: float) -> float:
        """Convert seconds to beat position."""
        # beat = seconds * (bpm / 60)
        return seconds * (self._bpm / 60.0)


class MetronomeGenerator:
    """Generates metronome click samples."""

    @staticmethod
    def generate_click(
        duration_seconds: float,
        frequency_hz: float = 1000.0,
        sample_rate: int = 44100,
    ) -> np.ndarray:
        """Generate a simple metronome click (sine wave pulse)."""
        samples = int(duration_seconds * sample_rate)
        t = np.linspace(0, duration_seconds, samples)

        # Generate sine wave
        waveform = np.sin(2 * np.pi * frequency_hz * t)

        # Apply envelope (fade in/out to avoid clicks)
        envelope_samples = max(1, int(sample_rate * 0.01))  # 10ms envelope
        envelope = np.ones(samples)
        envelope[:envelope_samples] = np.linspace(0, 1, envelope_samples)
        envelope[-envelope_samples:] = np.linspace(1, 0, envelope_samples)
        waveform *= envelope

        # Normalize to 16-bit range
        waveform = np.clip(waveform * 0.3, -1.0, 1.0)  # 30% volume
        return waveform.astype(np.float32)


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
