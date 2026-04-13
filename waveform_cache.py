from __future__ import annotations

import hashlib
import math
import subprocess
import wave
from pathlib import Path
from typing import Callable

_WAVEFORM_CACHE: dict[tuple[str, int, int, int], list[float]] = {}


def load_waveform_peaks(path: Path, bucket_count: int = 900, cache_dir: Path | None = None) -> list[float]:
    peaks, _ = load_waveform_peaks_with_meta(path, bucket_count=bucket_count, cache_dir=cache_dir)
    return peaks


def has_persisted_waveform_preview(path: Path, cache_dir: Path, bucket_count: int = 900) -> bool:
    """Return True when a valid persisted waveform preview exists on disk."""
    cache_key = _build_cache_key(path, bucket_count)
    if cache_key is None:
        return False
    return _read_persisted_peaks(cache_dir, cache_key) is not None


def load_waveform_peaks_with_meta(
    path: Path,
    bucket_count: int = 900,
    cache_dir: Path | None = None,
) -> tuple[list[float], str]:
    """Return waveform peaks plus source marker: memory|disk|computed."""
    if bucket_count <= 0:
        return [], "computed"

    cache_key = _build_cache_key(path, bucket_count)
    if cache_key is not None:
        cached = _WAVEFORM_CACHE.get(cache_key)
        if cached is not None:
            return list(cached), "memory"

    if cache_key is not None and cache_dir is not None:
        disk_peaks = _read_persisted_peaks(cache_dir, cache_key)
        if disk_peaks is not None:
            _WAVEFORM_CACHE[cache_key] = list(disk_peaks)
            return list(disk_peaks), "disk"

    peaks: list[float] = []
    suffix = path.suffix.lower()
    if suffix in {".wav", ".wave"}:
        peaks = _load_wav_waveform_peaks(path, bucket_count)

    if not peaks:
        peaks = _load_ffmpeg_waveform_peaks(path, bucket_count)

    if cache_key is not None:
        if len(_WAVEFORM_CACHE) >= 192:
            _WAVEFORM_CACHE.pop(next(iter(_WAVEFORM_CACHE)))
        _WAVEFORM_CACHE[cache_key] = list(peaks)
        if cache_dir is not None and peaks:
            _write_persisted_peaks(cache_dir, cache_key, peaks)

    return peaks, "computed"


def build_waveform_previews(
    paths: list[Path],
    cache_dir: Path,
    bucket_count: int = 900,
    progress_callback: Callable[[int, int, Path, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[int, int, int]:
    unique_paths: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique_paths.append(path)

    total = len(unique_paths)
    cached_count = 0
    computed_count = 0

    for idx, path in enumerate(unique_paths, start=1):
        if should_cancel is not None and should_cancel():
            break
        _peaks, source = load_waveform_peaks_with_meta(path, bucket_count=bucket_count, cache_dir=cache_dir)
        if source in {"memory", "disk"}:
            cached_count += 1
        else:
            computed_count += 1
        if progress_callback is not None:
            progress_callback(idx, total, path, source)

    return total, computed_count, cached_count


def _build_cache_key(path: Path, bucket_count: int) -> tuple[str, int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path), int(stat.st_mtime_ns), int(stat.st_size), int(bucket_count))


def _cache_file_for_key(cache_dir: Path, cache_key: tuple[str, int, int, int]) -> Path:
    digest = hashlib.sha1("|".join(map(str, cache_key)).encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.wfp"


def _encode_peaks(peaks: list[float]) -> bytes:
    count = min(65535, len(peaks))
    quantized = bytearray(count)
    for idx in range(count):
        value = max(0.0, min(1.0, float(peaks[idx])))
        quantized[idx] = int(round(value * 255.0))
    return b"WFP1" + count.to_bytes(2, byteorder="little", signed=False) + bytes(quantized)


def _decode_peaks(payload: bytes) -> list[float] | None:
    if len(payload) < 6 or payload[:4] != b"WFP1":
        return None
    count = int.from_bytes(payload[4:6], byteorder="little", signed=False)
    raw = payload[6 : 6 + count]
    if len(raw) != count:
        return None
    return [byte / 255.0 for byte in raw]


def _read_persisted_peaks(
    cache_dir: Path,
    cache_key: tuple[str, int, int, int],
) -> list[float] | None:
    cache_file = _cache_file_for_key(cache_dir, cache_key)
    try:
        payload = cache_file.read_bytes()
    except OSError:
        return None
    return _decode_peaks(payload)


def _write_persisted_peaks(
    cache_dir: Path,
    cache_key: tuple[str, int, int, int],
    peaks: list[float],
) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    cache_file = _cache_file_for_key(cache_dir, cache_key)
    tmp_file = cache_file.with_suffix(".tmp")
    payload = _encode_peaks(peaks)
    try:
        tmp_file.write_bytes(payload)
        tmp_file.replace(cache_file)
    except OSError:
        try:
            if tmp_file.exists():
                tmp_file.unlink()
        except OSError:
            pass


def _load_wav_waveform_peaks(path: Path, bucket_count: int) -> list[float]:
    try:
        with wave.open(str(path), "rb") as wav_file:
            frame_count = wav_file.getnframes()
            channel_count = max(1, wav_file.getnchannels())
            sample_width = wav_file.getsampwidth()
            raw = wav_file.readframes(frame_count)
    except (wave.Error, OSError):
        return []

    if frame_count <= 0 or sample_width not in {1, 2, 4}:
        return []

    amplitudes: list[float] = []
    step = sample_width * channel_count
    max_value = float((1 << (8 * sample_width - 1)) - 1)
    for frame_idx in range(frame_count):
        offset = frame_idx * step
        sample_bytes = raw[offset : offset + sample_width]
        if len(sample_bytes) != sample_width:
            break
        if sample_width == 1:
            sample = int.from_bytes(sample_bytes, byteorder="little", signed=False) - 128
        else:
            sample = int.from_bytes(sample_bytes, byteorder="little", signed=True)
        amplitudes.append(min(1.0, abs(sample) / max_value if max_value else 0.0))

    return _reduce_to_peaks(amplitudes, bucket_count)


def _load_ffmpeg_waveform_peaks(path: Path, bucket_count: int) -> list[float]:
    ffmpeg = shutil_which("ffmpeg")
    if not ffmpeg:
        return []

    cmd = [
        ffmpeg,
        "-v",
        "error",
        "-i",
        str(path),
        "-ac",
        "1",
        "-ar",
        "3000",
        "-f",
        "s16le",
        "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=8.0, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0 or not result.stdout:
        return []

    return _reduce_pcm16_bytes_to_peaks(result.stdout, bucket_count)


def _reduce_pcm16_bytes_to_peaks(raw_pcm: bytes, bucket_count: int) -> list[float]:
    sample_count = len(raw_pcm) // 2
    if sample_count <= 0 or bucket_count <= 0:
        return []

    if sample_count <= bucket_count:
        out: list[float] = []
        for i in range(sample_count):
            value = int.from_bytes(raw_pcm[i * 2 : i * 2 + 2], byteorder="little", signed=True)
            out.append(min(1.0, abs(value) / 32767.0))
        return out

    peaks = [0.0] * bucket_count
    for i in range(sample_count):
        bucket = min(bucket_count - 1, int(i * bucket_count / sample_count))
        value = int.from_bytes(raw_pcm[i * 2 : i * 2 + 2], byteorder="little", signed=True)
        amplitude = min(1.0, abs(value) / 32767.0)
        if amplitude > peaks[bucket]:
            peaks[bucket] = amplitude
    return peaks


def _reduce_to_peaks(amplitudes: list[float], bucket_count: int) -> list[float]:
    if not amplitudes:
        return []

    count = len(amplitudes)
    if count <= bucket_count:
        return amplitudes

    peaks: list[float] = []
    for bucket in range(bucket_count):
        start = int(math.floor(bucket * count / bucket_count))
        end = int(math.floor((bucket + 1) * count / bucket_count))
        if end <= start:
            end = min(count, start + 1)
        window = amplitudes[start:end]
        peaks.append(max(window) if window else 0.0)
    return peaks


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)
