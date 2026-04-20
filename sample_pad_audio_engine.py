"""
SamplePadAudioEngine
====================
Low-latency, click-free sample pad playback using sounddevice + soundfile.

The engine keeps a PortAudio output stream open continuously on the chosen
device so there is no pipeline teardown/rebuild on retrigger. Multiple pads
can play simultaneously; each active pad maps to one voice, and retriggering
the same pad replaces only that pad's voice.

Loop and release modes are supported per voice.

Thread-safety: all public methods are called from the Qt main thread.  The
PortAudio callback runs on a private audio thread.  All shared state that the
callback reads is protected by a threading.Lock.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
import shutil
import subprocess
from typing import Optional

import numpy as np

try:
    import sounddevice as sd
    import soundfile as sf
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def is_available() -> bool:
    return _AVAILABLE


def _get_ffmpeg_path() -> str | None:
    global _FFMPEG_PATH, _FFMPEG_CHECKED
    if _FFMPEG_CHECKED:
        return _FFMPEG_PATH
    _FFMPEG_CHECKED = True
    _FFMPEG_PATH = shutil.which("ffmpeg")
    return _FFMPEG_PATH


# ---------------------------------------------------------------------------
# Fade length used when a retrigger or stop interrupts an active voice.
# 2 ms at the output sample rate is imperceptible as silence but long enough
# to prevent an aliasing click.
_FADE_SAMPLES = 88  # ~2 ms @ 44100 Hz; scaled per stream at open time
_LOW_LATENCY_BLOCKSIZE = 128
_FALLBACK_BLOCKSIZE = 256
_MIX_HEADROOM = 0.92
_STREAMING_MIN_SECONDS = 120.0
_FFMPEG_PATH: str | None = None
_FFMPEG_CHECKED = False


class _Voice:
    __slots__ = (
        "voice_id",
        "pad_index",
        "pcm",
        "pos",
        "clip_start",
        "clip_end",
        "loop",
        "volume",
        "fade_out_remaining",
        "fade_out_total",
    )

    def __init__(
        self,
        voice_id: int,
        pad_index: int,
        pcm: np.ndarray,
        clip_start: int,
        clip_end: int,
        loop: bool,
        volume: float,
    ) -> None:
        self.voice_id = voice_id
        self.pad_index = pad_index
        self.pcm = pcm
        self.pos = clip_start
        self.clip_start = clip_start
        self.clip_end = clip_end
        self.loop = loop
        self.volume = float(max(0.0, volume))
        self.fade_out_remaining = 0
        self.fade_out_total = _FADE_SAMPLES


class _StreamingVoice:
    __slots__ = (
        "voice_id",
        "pad_index",
        "stream",
        "ffmpeg_process",
        "ffmpeg_args",
        "clip_start",
        "clip_end",
        "loop",
        "volume",
        "pos",
        "queue",
        "queue_frames",
        "queue_lock",
        "max_buffer_frames",
        "start_buffer_frames",
        "buffer_started",
        "eof",
        "stop_event",
        "fade_out_remaining",
        "fade_out_total",
    )

    def __init__(
        self,
        voice_id: int,
        pad_index: int,
        stream: "sf.SoundFile | None",
        clip_start: int,
        clip_end: int,
        loop: bool,
        volume: float,
    ) -> None:
        self.voice_id = voice_id
        self.pad_index = pad_index
        self.stream = stream
        self.ffmpeg_process: subprocess.Popen[bytes] | None = None
        self.ffmpeg_args: list[str] | None = None
        self.clip_start = clip_start
        self.clip_end = clip_end
        self.loop = loop
        self.volume = float(max(0.0, volume))
        self.pos = clip_start
        self.queue: deque[np.ndarray] = deque()
        self.queue_frames = 0
        self.queue_lock = threading.Lock()
        self.max_buffer_frames = 0
        self.start_buffer_frames = 0
        self.buffer_started = False
        self.eof = False
        self.stop_event = threading.Event()
        self.fade_out_remaining = 0
        self.fade_out_total = _FADE_SAMPLES


class SamplePadAudioEngine:
    """Click-free polyphonic sample pad audio engine backed by PortAudio."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream: Optional["sd.OutputStream"] = None
        self._stream_device: str = ""
        self._stream_samplerate: int = 0
        self._stream_channels: int = 2
        self._stream_blocksize: int = _LOW_LATENCY_BLOCKSIZE

        # PCM cache: keyed by (str(path), clip_start_seconds, clip_stop_seconds)
        # Stores already-decoded, resampled, channel-matched float32 numpy arrays
        # so that trigger() never touches the disk on the hot path.
        # Access from any thread is protected by _cache_lock (separate from _lock
        # to avoid deadlocking with the PortAudio callback).
        self._cache: dict[tuple, np.ndarray] = {}
        self._cache_lock = threading.Lock()
        self._cache_inflight: set[tuple] = set()

        # Polyphonic voice state (read by callback, written by main thread)
        self._voices: dict[int, _Voice | _StreamingVoice] = {}
        self._pad_to_voice: dict[int, int] = {}
        self._next_voice_id: int = 1
        self._pad_mix_settings: dict[int, tuple[float, bool, bool]] = {}
        self._pad_meter_levels: dict[int, float] = {}
        self._pending_pad_triggers: dict[int, tuple[tuple, float, bool]] = {}
        self._streaming_min_seconds: float = _STREAMING_MIN_SECONDS

    # ------------------------------------------------------------------
    # Public API (called from Qt main thread)
    # ------------------------------------------------------------------

    def set_device(
        self,
        device_name: str,
        samplerate: int = 44100,
        channels: int = 2,
        blocksize: int = _LOW_LATENCY_BLOCKSIZE,
    ) -> None:
        """Open (or reopen) the PortAudio stream on *device_name*.

        Safe to call while audio is playing; playback continues uninterrupted
        if the device hasn't changed.
        """
        if not _AVAILABLE:
            return
        device_name = device_name.strip()
        blocksize = max(64, int(blocksize))
        if (
            self._stream is not None
            and self._stream.active
            and self._stream_device == device_name
            and self._stream_samplerate == samplerate
            and self._stream_channels == channels
            and self._stream_blocksize == blocksize
        ):
            return  # already open on the right device — nothing to do

        self._close_stream()
        # Reopening the stream starts from a clean playback state.
        with self._lock:
            for voice in self._voices.values():
                self._close_voice_stream(voice)
            self._voices.clear()
            self._pad_to_voice.clear()
            self._pad_meter_levels.clear()
        device_index = self._find_device(device_name)
        self._stream_samplerate = samplerate
        self._stream_channels = channels
        self._stream_device = device_name
        self._stream_blocksize = blocksize
        try:
            # Favor low latency for keyboard-triggered pads.  A smaller blocksize
            # and explicit low-latency request significantly reduce key-to-audio
            # delay on most devices.
            try:
                self._stream = sd.OutputStream(
                    device=device_index,
                    samplerate=samplerate,
                    channels=channels,
                    dtype="float32",
                    blocksize=blocksize,
                    latency="low",
                    callback=self._callback,
                    prime_output_buffers_using_stream_callback=True,
                )
            except Exception:
                # Some devices/drivers reject very aggressive stream params.
                # Fall back to a still-low-latency profile that is more broadly
                # compatible.
                self._stream = sd.OutputStream(
                    device=device_index,
                    samplerate=samplerate,
                    channels=channels,
                    dtype="float32",
                    blocksize=max(_FALLBACK_BLOCKSIZE, blocksize),
                    latency="low",
                    callback=self._callback,
                )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise RuntimeError(f"SamplePadAudioEngine: could not open device '{device_name}': {exc}") from exc

    def preload(
        self,
        path: str | Path,
        samplerate: int | None = None,
        channels: int | None = None,
        clip_start_seconds: float = 0.0,
        clip_stop_seconds: float = 0.0,
    ) -> None:
        """Decode and cache *path* so that the next trigger() call is instantaneous.

        Uses the stream's current samplerate/channels if not specified.
        Safe to call from a background thread.
        """
        if not _AVAILABLE:
            return
        sr = samplerate or self._stream_samplerate or 44100
        ch = channels or self._stream_channels or 2
        cache_key = (str(path), clip_start_seconds, clip_stop_seconds, sr, ch)

        if self._should_skip_preload(path, clip_start_seconds, clip_stop_seconds):
            return

        with self._cache_lock:
            if cache_key in self._cache:
                return  # already cached
            if cache_key in self._cache_inflight:
                return
            self._cache_inflight.add(cache_key)
        try:
            pcm = self._load_pcm(path, sr, ch)
        except Exception:
            with self._cache_lock:
                self._cache_inflight.discard(cache_key)
            return
        with self._cache_lock:
            self._cache[cache_key] = pcm
            self._cache_inflight.discard(cache_key)

    def clear_cache(self) -> None:
        """Evict all preloaded PCM data."""
        with self._cache_lock:
            self._cache.clear()

    def trigger(
        self,
        path: str | Path,
        volume: float = 1.0,
        clip_start_seconds: float = 0.0,
        clip_stop_seconds: float = 0.0,
        loop: bool = False,
        pad_index: int = -1,
    ) -> None:
        """Start (or retrigger) playback of *path* as an independent voice."""
        if not _AVAILABLE or self._stream is None:
            return

        stream_sr = self._stream_samplerate
        target_ch = self._stream_channels
        cache_key = (str(path), clip_start_seconds, clip_stop_seconds, stream_sr, target_ch)

        # Fast path: use preloaded PCM if available
        with self._cache_lock:
            pcm = self._cache.get(cache_key)

        if pcm is None:
            if self._try_start_streaming_voice(
                path=path,
                stream_sr=stream_sr,
                clip_start_seconds=clip_start_seconds,
                clip_stop_seconds=clip_stop_seconds,
                loop=loop,
                volume=volume,
                pad_index=pad_index,
            ):
                return

            # Cache miss: do not block UI thread on long decode/resample. Queue
            # a background preload and remember the latest trigger for this pad.
            # When preload finishes, that pending trigger is started automatically.
            if pad_index >= 0:
                with self._lock:
                    self._pending_pad_triggers[pad_index] = (cache_key, float(volume), bool(loop))

            should_start_worker = False
            with self._cache_lock:
                if cache_key in self._cache:
                    pcm = self._cache.get(cache_key)
                elif cache_key not in self._cache_inflight:
                    self._cache_inflight.add(cache_key)
                    should_start_worker = True

            if pcm is None:
                if should_start_worker:
                    worker = threading.Thread(
                        target=self._preload_and_fulfill_pending,
                        args=(path, cache_key),
                        daemon=True,
                    )
                    worker.start()
                return

        self._start_voice(
            pcm,
            stream_sr,
            clip_start_seconds,
            clip_stop_seconds,
            loop,
            volume,
            pad_index,
        )

    def stop(self, pad_index: int | None = None) -> None:
        """Fade out and stop one pad voice or all voices."""
        if not _AVAILABLE:
            return
        with self._lock:
            fade_samples = max(1, int((self._stream_samplerate or 44100) * 0.002))
            if pad_index is None:
                self._pending_pad_triggers.clear()
                for voice in self._voices.values():
                    self._begin_voice_fade(voice, fade_samples)
                return

            self._pending_pad_triggers.pop(pad_index, None)

            voice_id = self._pad_to_voice.get(pad_index)
            if voice_id is None:
                return
            voice = self._voices.get(voice_id)
            if voice is None:
                self._pad_to_voice.pop(pad_index, None)
                return
            self._begin_voice_fade(voice, fade_samples)

    def set_pad_mix(self, pad_index: int, volume_percent: int, muted: bool, solo: bool) -> None:
        """Set per-pad mixer settings used for current and future voices."""
        if pad_index < 0:
            return
        volume = max(0.0, min(1.0, float(volume_percent) / 100.0))
        with self._lock:
            self._pad_mix_settings[pad_index] = (volume, bool(muted), bool(solo))

    def set_streaming_min_seconds(self, seconds: float) -> None:
        """Set minimum duration for using streaming on cache-miss trigger."""
        try:
            parsed = float(seconds)
        except (TypeError, ValueError):
            parsed = _STREAMING_MIN_SECONDS
        self._streaming_min_seconds = max(0.0, min(3600.0, parsed))

    def meter_levels(self) -> dict[int, float]:
        """Return normalized post-fader meter levels keyed by pad index."""
        with self._lock:
            return dict(self._pad_meter_levels)

    def is_pad_playing(self, pad_index: int) -> bool:
        """Return True when *pad_index* currently has an active voice."""
        if pad_index < 0:
            return False
        with self._lock:
            if pad_index in self._pending_pad_triggers:
                return True
            voice_id = self._pad_to_voice.get(pad_index)
            if voice_id is None:
                return False
            return voice_id in self._voices

    def active_pad_indices(self) -> set[int]:
        """Return active pad indices currently owned by voices."""
        with self._lock:
            active = {pad for pad, voice_id in self._pad_to_voice.items() if voice_id in self._voices}
            active.update(self._pending_pad_triggers.keys())
            return active

    def close(self) -> None:
        """Stop playback and close the PortAudio stream."""
        with self._lock:
            for voice in self._voices.values():
                self._close_voice_stream(voice)
            self._voices.clear()
            self._pad_to_voice.clear()
            self._pad_meter_levels.clear()
        self._close_stream()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_pcm(self, path: str | Path, samplerate: int, channels: int) -> np.ndarray:
        """Read *path*, resample to *samplerate*, and mix to *channels*. Returns float32 (N, channels)."""
        pcm, sr = sf.read(str(path), dtype="float32", always_2d=True)
        # Channel match
        file_ch = pcm.shape[1]
        if file_ch < channels:
            reps = -(-channels // file_ch)
            pcm = np.tile(pcm, (1, reps))[:, :channels]
        elif file_ch > channels:
            pcm = pcm[:, :channels]
        # Resample
        if sr != samplerate:
            pcm = _resample(pcm, sr, samplerate)
        return pcm

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop(ignore_errors=True)
                self._stream.close(ignore_errors=True)
            except Exception:
                pass
            self._stream = None

    @staticmethod
    def _close_voice_stream(voice: _Voice | _StreamingVoice) -> None:
        if isinstance(voice, _StreamingVoice):
            voice.stop_event.set()
            proc = getattr(voice, "ffmpeg_process", None)
            if proc is not None:
                try:
                    if proc.stdout is not None:
                        proc.stdout.close()
                except Exception:
                    pass
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=0.2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                voice.ffmpeg_process = None
        stream = getattr(voice, "stream", None)
        if stream is None:
            return
        try:
            stream.close()
        except Exception:
            pass

    @staticmethod
    def _channel_match_chunk(chunk: np.ndarray, channels: int) -> np.ndarray:
        file_ch = chunk.shape[1]
        if file_ch < channels:
            reps = -(-channels // max(1, file_ch))
            return np.tile(chunk, (1, reps))[:, :channels]
        if file_ch > channels:
            return chunk[:, :channels]
        return chunk

    def _start_voice(
        self,
        pcm: np.ndarray,
        stream_sr: int,
        clip_start_seconds: float,
        clip_stop_seconds: float,
        loop: bool,
        volume: float,
        pad_index: int,
    ) -> None:
        n_frames = pcm.shape[0]
        start_frame = max(0, int(clip_start_seconds * stream_sr))
        stop_frame = int(clip_stop_seconds * stream_sr) if clip_stop_seconds > 0 else n_frames
        stop_frame = min(stop_frame, n_frames)
        if stop_frame <= start_frame:
            start_frame = 0
            stop_frame = n_frames

        with self._lock:
            fade_samples = max(1, int(stream_sr * 0.002))
            if pad_index >= 0:
                old_voice_id = self._pad_to_voice.get(pad_index)
                old_voice = self._voices.get(old_voice_id) if old_voice_id is not None else None
                if old_voice is not None:
                    self._begin_voice_fade(old_voice, fade_samples)

            voice_id = self._next_voice_id
            self._next_voice_id += 1
            new_voice = _Voice(
                voice_id=voice_id,
                pad_index=pad_index,
                pcm=pcm,
                clip_start=start_frame,
                clip_end=stop_frame,
                loop=loop,
                volume=volume,
            )
            self._voices[voice_id] = new_voice
            if pad_index >= 0:
                self._pad_to_voice[pad_index] = voice_id

    def _start_streaming_voice(
        self,
        stream: "sf.SoundFile",
        stream_sr: int,
        clip_start_seconds: float,
        clip_stop_seconds: float,
        loop: bool,
        volume: float,
        pad_index: int,
    ) -> bool:
        n_frames = int(stream.frames)
        start_frame = max(0, int(clip_start_seconds * stream_sr))
        stop_frame = int(clip_stop_seconds * stream_sr) if clip_stop_seconds > 0 else n_frames
        stop_frame = min(stop_frame, n_frames)
        if stop_frame <= start_frame:
            start_frame = 0
            stop_frame = n_frames

        try:
            stream.seek(start_frame)
        except Exception:
            try:
                stream.close()
            except Exception:
                pass
            return False

        with self._lock:
            fade_samples = max(1, int(stream_sr * 0.002))
            if pad_index >= 0:
                old_voice_id = self._pad_to_voice.get(pad_index)
                old_voice = self._voices.get(old_voice_id) if old_voice_id is not None else None
                if old_voice is not None:
                    self._begin_voice_fade(old_voice, fade_samples)

            voice_id = self._next_voice_id
            self._next_voice_id += 1
            new_voice = _StreamingVoice(
                voice_id=voice_id,
                pad_index=pad_index,
                stream=stream,
                clip_start=start_frame,
                clip_end=stop_frame,
                loop=loop,
                volume=volume,
            )
            new_voice.max_buffer_frames = max(16384, int(stream_sr * 6.0))
            new_voice.start_buffer_frames = max(4096, int(stream_sr * 0.35))
            self._voices[voice_id] = new_voice
            if pad_index >= 0:
                self._pad_to_voice[pad_index] = voice_id

        worker = threading.Thread(
            target=self._streaming_worker,
            args=(new_voice, self._stream_channels),
            daemon=True,
        )
        worker.start()
        return True

    def _streaming_worker(self, voice: _StreamingVoice, target_channels: int) -> None:
        chunk_frames = max(1024, int(self._stream_blocksize) * 8)
        bytes_per_frame = max(1, 4 * target_channels)
        while not voice.stop_event.is_set():
            with voice.queue_lock:
                buffered = voice.queue_frames
            if buffered >= voice.max_buffer_frames:
                time.sleep(0.004)
                continue

            chunk: np.ndarray
            if voice.stream is not None:
                try:
                    current_pos = int(voice.stream.tell())
                except Exception:
                    voice.eof = True
                    break

                available = voice.clip_end - current_pos
                if available <= 0:
                    if voice.loop:
                        try:
                            voice.stream.seek(voice.clip_start)
                        except Exception:
                            voice.eof = True
                            break
                        continue
                    voice.eof = True
                    break

                space = max(0, voice.max_buffer_frames - buffered)
                to_read = min(chunk_frames, available, space)
                if to_read <= 0:
                    time.sleep(0.004)
                    continue

                try:
                    raw = voice.stream.read(to_read, dtype="float32", always_2d=True)
                except Exception:
                    voice.eof = True
                    break

                if raw is None or raw.size == 0:
                    if voice.loop:
                        try:
                            voice.stream.seek(voice.clip_start)
                        except Exception:
                            voice.eof = True
                            break
                        continue
                    voice.eof = True
                    break

                chunk = self._channel_match_chunk(raw, target_channels)
            else:
                proc = voice.ffmpeg_process
                if proc is None or proc.stdout is None:
                    voice.eof = True
                    break
                space = max(0, voice.max_buffer_frames - buffered)
                to_read = min(chunk_frames, space)
                if to_read <= 0:
                    time.sleep(0.004)
                    continue
                try:
                    raw_bytes = proc.stdout.read(to_read * bytes_per_frame)
                except Exception:
                    raw_bytes = b""
                if not raw_bytes:
                    if voice.loop and self._restart_ffmpeg_process(voice):
                        continue
                    voice.eof = True
                    break
                if len(raw_bytes) < bytes_per_frame:
                    if voice.loop and self._restart_ffmpeg_process(voice):
                        continue
                    voice.eof = True
                    break
                usable_bytes = len(raw_bytes) - (len(raw_bytes) % bytes_per_frame)
                if usable_bytes <= 0:
                    continue
                raw = np.frombuffer(raw_bytes[:usable_bytes], dtype=np.float32)
                if raw.size <= 0:
                    continue
                frames_read = raw.size // target_channels
                if frames_read <= 0:
                    continue
                chunk = raw[: frames_read * target_channels].reshape(frames_read, target_channels)

            if chunk.shape[0] <= 0:
                continue

            with voice.queue_lock:
                voice.queue.append(chunk)
                voice.queue_frames += int(chunk.shape[0])

    def _should_skip_preload(
        self,
        path: str | Path,
        clip_start_seconds: float,
        clip_stop_seconds: float,
    ) -> bool:
        suffix = Path(path).suffix.lower()
        if suffix in {".wav", ".wave", ".aiff", ".aif", ".flac"}:
            return False
        try:
            info = sf.info(str(path))
            duration = float(info.frames) / float(info.samplerate or 1)
        except Exception:
            return False

        if clip_stop_seconds > 0.0 and clip_stop_seconds > clip_start_seconds:
            duration = max(0.0, float(clip_stop_seconds - clip_start_seconds))

        # Long compressed files should stream; avoid heavy full decode preload.
        return duration >= self._streaming_min_seconds

    @staticmethod
    def _restart_ffmpeg_process(voice: _StreamingVoice) -> bool:
        args = voice.ffmpeg_args
        if not args:
            return False
        old_proc = voice.ffmpeg_process
        if old_proc is not None:
            try:
                if old_proc.stdout is not None:
                    old_proc.stdout.close()
            except Exception:
                pass
            try:
                old_proc.terminate()
            except Exception:
                pass
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            voice.ffmpeg_process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
            return True
        except Exception:
            voice.ffmpeg_process = None
            return False

    @staticmethod
    def _pop_stream_chunk(voice: _StreamingVoice, frames: int, channels: int) -> np.ndarray:
        if frames <= 0:
            return np.zeros((0, channels), dtype=np.float32)

        out_parts: list[np.ndarray] = []
        remaining = frames
        with voice.queue_lock:
            while remaining > 0 and voice.queue:
                head = voice.queue[0]
                head_frames = int(head.shape[0])
                if head_frames <= remaining:
                    out_parts.append(head)
                    voice.queue.popleft()
                    voice.queue_frames -= head_frames
                    remaining -= head_frames
                    continue
                out_parts.append(head[:remaining])
                voice.queue[0] = head[remaining:]
                voice.queue_frames -= remaining
                remaining = 0

        if not out_parts:
            return np.zeros((0, channels), dtype=np.float32)
        if len(out_parts) == 1:
            return out_parts[0]
        return np.vstack(out_parts).astype(np.float32, copy=False)

    def _try_start_streaming_voice(
        self,
        path: str | Path,
        stream_sr: int,
        clip_start_seconds: float,
        clip_stop_seconds: float,
        loop: bool,
        volume: float,
        pad_index: int,
    ) -> bool:
        if not _AVAILABLE:
            return False
        try:
            info = sf.info(str(path))
        except Exception:
            return False

        duration_seconds = float(info.frames) / float(info.samplerate or 1)
        if duration_seconds < self._streaming_min_seconds:
            return False

        # Best case: stream directly with soundfile when samplerates already match.
        if int(info.samplerate) == int(stream_sr):
            try:
                stream = sf.SoundFile(str(path), mode="r")
            except Exception:
                stream = None
            if stream is not None and int(stream.samplerate) == int(stream_sr):
                return self._start_streaming_voice(
                    stream,
                    stream_sr,
                    clip_start_seconds,
                    clip_stop_seconds,
                    loop,
                    volume,
                    pad_index,
                )
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

        # Fallback for compressed/non-matching sources: ffmpeg pipe streaming
        # (decode + resample to target format without large temp files).
        return self._try_start_ffmpeg_streaming_voice(
            path=path,
            stream_sr=stream_sr,
            clip_start_seconds=clip_start_seconds,
            clip_stop_seconds=clip_stop_seconds,
            duration_seconds=duration_seconds,
            loop=loop,
            volume=volume,
            pad_index=pad_index,
        )

    def _try_start_ffmpeg_streaming_voice(
        self,
        path: str | Path,
        stream_sr: int,
        clip_start_seconds: float,
        clip_stop_seconds: float,
        duration_seconds: float,
        loop: bool,
        volume: float,
        pad_index: int,
    ) -> bool:
        ffmpeg = _get_ffmpeg_path()
        if not ffmpeg:
            return False

        clip_start = max(0.0, float(clip_start_seconds))
        clip_duration = 0.0
        if clip_stop_seconds > 0.0 and clip_stop_seconds > clip_start:
            clip_duration = float(clip_stop_seconds - clip_start)
        elif duration_seconds > clip_start:
            clip_duration = float(duration_seconds - clip_start)
        if clip_duration <= 0.0:
            return False

        ffmpeg_args = [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-hide_banner",
            "-ss",
            f"{clip_start:.6f}",
            "-i",
            str(path),
            "-t",
            f"{clip_duration:.6f}",
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-ac",
            str(self._stream_channels),
            "-ar",
            str(stream_sr),
            "pipe:1",
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen(
                ffmpeg_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
        except Exception:
            return False

        clip_frames = max(1, int(round(clip_duration * float(stream_sr))))
        with self._lock:
            fade_samples = max(1, int(stream_sr * 0.002))
            if pad_index >= 0:
                old_voice_id = self._pad_to_voice.get(pad_index)
                old_voice = self._voices.get(old_voice_id) if old_voice_id is not None else None
                if old_voice is not None:
                    self._begin_voice_fade(old_voice, fade_samples)

            voice_id = self._next_voice_id
            self._next_voice_id += 1
            voice = _StreamingVoice(
                voice_id=voice_id,
                pad_index=pad_index,
                stream=None,
                clip_start=0,
                clip_end=clip_frames,
                loop=loop,
                volume=volume,
            )
            voice.ffmpeg_process = proc
            voice.ffmpeg_args = ffmpeg_args
            voice.max_buffer_frames = max(16384, int(stream_sr * 6.0))
            voice.start_buffer_frames = max(4096, int(stream_sr * 0.35))
            self._voices[voice_id] = voice
            if pad_index >= 0:
                self._pad_to_voice[pad_index] = voice_id

        worker = threading.Thread(
            target=self._streaming_worker,
            args=(voice, self._stream_channels),
            daemon=True,
        )
        worker.start()
        return True

    def _preload_and_fulfill_pending(self, path: str | Path, cache_key: tuple) -> None:
        _, clip_start_seconds, clip_stop_seconds, samplerate, channels = cache_key
        try:
            pcm = self._load_pcm(path, int(samplerate), int(channels))
        except Exception:
            with self._cache_lock:
                self._cache_inflight.discard(cache_key)
            return

        with self._cache_lock:
            self._cache[cache_key] = pcm
            self._cache_inflight.discard(cache_key)

        # Collect pads still waiting for this exact cache key.
        pending: list[tuple[int, float, bool]] = []
        with self._lock:
            for pad_index, (pending_key, pending_volume, pending_loop) in list(self._pending_pad_triggers.items()):
                if pending_key == cache_key:
                    pending.append((pad_index, pending_volume, pending_loop))
                    self._pending_pad_triggers.pop(pad_index, None)

        # Start each pending trigger only if stream format still matches.
        for pad_index, pending_volume, pending_loop in pending:
            if self._stream is None:
                continue
            if self._stream_samplerate != int(samplerate) or self._stream_channels != int(channels):
                continue
            self._start_voice(
                pcm,
                int(samplerate),
                float(clip_start_seconds),
                float(clip_stop_seconds),
                pending_loop,
                pending_volume,
                pad_index,
            )

    @staticmethod
    def _find_device(name: str) -> Optional[int]:
        """Return the sounddevice output device index for *name*, or None."""
        if not name:
            return None
        name_cf = name.casefold()
        try:
            devices = sd.query_devices()
        except Exception:
            return None
        for idx, dev in enumerate(devices):
            if dev.get("max_output_channels", 0) > 0:
                if dev.get("name", "").casefold() == name_cf:
                    return idx
        # Partial match fallback
        for idx, dev in enumerate(devices):
            if dev.get("max_output_channels", 0) > 0:
                if name_cf in dev.get("name", "").casefold():
                    return idx
        return None

    @staticmethod
    def _begin_voice_fade(voice: _Voice | _StreamingVoice, fade_samples: int) -> None:
        fade_samples = max(1, int(fade_samples))
        if voice.fade_out_remaining > 0:
            voice.fade_out_remaining = min(voice.fade_out_remaining, fade_samples)
            voice.fade_out_total = max(voice.fade_out_total, voice.fade_out_remaining)
            return
        voice.fade_out_remaining = fade_samples
        voice.fade_out_total = fade_samples

    # ------------------------------------------------------------------
    # PortAudio callback (audio thread)
    # ------------------------------------------------------------------

    def _callback(
        self,
        outdata: np.ndarray,
        frames: int,
        _time,
        _status,
    ) -> None:
        with self._lock:
            if self._pad_meter_levels:
                stale_pads: list[int] = []
                for pad_index, level in self._pad_meter_levels.items():
                    decayed = level * 0.82
                    if decayed < 0.001:
                        stale_pads.append(pad_index)
                    else:
                        self._pad_meter_levels[pad_index] = decayed
                for pad_index in stale_pads:
                    self._pad_meter_levels.pop(pad_index, None)

            if not self._voices:
                outdata[:] = 0
                return

            solo_active = any(solo for _gain, _muted, solo in self._pad_mix_settings.values())
            out = np.zeros((frames, outdata.shape[1]), dtype=np.float32)
            finished_voice_ids: list[int] = []

            for voice_id, voice in list(self._voices.items()):
                pad_gain, pad_muted, pad_solo = self._pad_mix_settings.get(
                    voice.pad_index,
                    (1.0, False, False),
                )
                gate_open = not pad_muted and (not solo_active or pad_solo)
                effective_gain = voice.volume * pad_gain if gate_open else 0.0
                written = 0
                pos = voice.pos
                while written < frames:
                    remain = frames - written
                    if isinstance(voice, _Voice):
                        chunk = _read_frames(
                            voice.pcm,
                            pos,
                            remain,
                            voice.clip_start,
                            voice.clip_end,
                        )
                    else:
                        if not voice.buffer_started:
                            with voice.queue_lock:
                                buffered = voice.queue_frames
                            if buffered < voice.start_buffer_frames and not voice.eof:
                                # Keep initial output silent until a small prebuffer
                                # is ready to avoid startup decode underrun artifacts.
                                break
                            voice.buffer_started = True
                        chunk = self._pop_stream_chunk(voice, remain, outdata.shape[1])
                    n = chunk.shape[0]
                    if n <= 0:
                        if isinstance(voice, _StreamingVoice):
                            if voice.eof:
                                finished_voice_ids.append(voice_id)
                            # No buffered audio yet; keep voice alive and output silence.
                            break
                        if voice.loop:
                            pos = voice.clip_start
                            continue
                        finished_voice_ids.append(voice_id)
                        break

                    if voice.fade_out_remaining > 0:
                        n = min(n, voice.fade_out_remaining)
                        fade_start = voice.fade_out_total - voice.fade_out_remaining
                        ramp = np.linspace(
                            1.0 - (fade_start / voice.fade_out_total),
                            1.0 - ((fade_start + n) / voice.fade_out_total),
                            n,
                            dtype=np.float32,
                        )[:, np.newaxis]
                        if effective_gain > 0.0:
                            scaled = chunk[:n] * ramp * effective_gain
                            out[written : written + n] += scaled
                            if voice.pad_index >= 0:
                                peak = float(np.max(np.abs(scaled)))
                                if peak > self._pad_meter_levels.get(voice.pad_index, 0.0):
                                    self._pad_meter_levels[voice.pad_index] = peak
                        voice.fade_out_remaining -= n
                        pos += n
                        written += n
                        if voice.fade_out_remaining <= 0:
                            finished_voice_ids.append(voice_id)
                            break
                        continue

                    if effective_gain > 0.0:
                        scaled = chunk * effective_gain
                        out[written : written + n] += scaled
                        if voice.pad_index >= 0:
                            peak = float(np.max(np.abs(scaled)))
                            if peak > self._pad_meter_levels.get(voice.pad_index, 0.0):
                                self._pad_meter_levels[voice.pad_index] = peak
                    pos += n
                    written += n

                    if pos >= voice.clip_end:
                        if voice.loop:
                            pos = voice.clip_start
                        else:
                            finished_voice_ids.append(voice_id)
                            break

                voice.pos = pos

            if finished_voice_ids:
                for voice_id in set(finished_voice_ids):
                    voice = self._voices.pop(voice_id, None)
                    if voice is None:
                        continue
                    self._close_voice_stream(voice)
                    if voice.pad_index >= 0 and self._pad_to_voice.get(voice.pad_index) == voice_id:
                        self._pad_to_voice.pop(voice.pad_index, None)

        peak = float(np.max(np.abs(out))) if out.size else 0.0
        if peak > 1.0:
            out *= (_MIX_HEADROOM / peak)
        outdata[:] = out


# ---------------------------------------------------------------------------
# Helpers (module-level, called from both threads)
# ---------------------------------------------------------------------------

def _read_frames(
    pcm: np.ndarray,
    pos: int,
    n: int,
    clip_start: int,
    clip_end: int,
) -> np.ndarray:
    """Read up to *n* frames from *pcm* starting at *pos*, clamped to [clip_start, clip_end)."""
    available = clip_end - pos
    if available <= 0:
        return np.zeros((0, pcm.shape[1]), dtype=np.float32)
    n = min(n, available)
    return pcm[pos: pos + n]


def _resample(pcm: np.ndarray, from_sr: int, to_sr: int) -> np.ndarray:
    """Resample *pcm* from *from_sr* to *to_sr* using linear interpolation."""
    if from_sr == to_sr:
        return pcm
    try:
        import resampy
        return resampy.resample(pcm, from_sr, to_sr, axis=0).astype(np.float32)
    except ImportError:
        pass
    # Fallback: linear interpolation (adequate for typical audio files)
    n_in = pcm.shape[0]
    n_out = int(round(n_in * to_sr / from_sr))
    x_in = np.linspace(0, n_in - 1, n_in)
    x_out = np.linspace(0, n_in - 1, n_out)
    result = np.zeros((n_out, pcm.shape[1]), dtype=np.float32)
    for ch in range(pcm.shape[1]):
        result[:, ch] = np.interp(x_out, x_in, pcm[:, ch])
    return result
