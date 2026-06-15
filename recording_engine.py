"""Audio recording engine using sounddevice and soundfile."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd
import soundfile as sf


@dataclass
class RecordingConfig:
    """Configuration for recording session."""

    device_id: int | str | None
    sample_rate: int = 44100
    channels: int = 2
    blocksize: int = 2048
    use_wasapi_loopback: bool = False


@dataclass
class RecordingMetrics:
    """Metrics during recording."""

    current_seconds: float
    peak_level: float  # 0.0 to 1.0


class RecordingEngine:
    """Handles audio recording to WAV file with real-time metrics."""

    def __init__(self) -> None:
        self._is_recording = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._metrics_queue: queue.Queue[RecordingMetrics] = queue.Queue(maxsize=1)
        self._error_queue: queue.Queue[str] = queue.Queue(maxsize=1)

    def is_recording(self) -> bool:
        """Return True if currently recording."""
        return self._is_recording

    def get_available_devices(self) -> list[tuple[int, str]]:
        """Return list of (device_id, device_name) tuples for input devices."""
        devices = []
        try:
            device_list = sd.query_devices()
            if isinstance(device_list, dict):
                # Single device
                device_list = [device_list]

            for idx, device in enumerate(device_list):
                if isinstance(device, dict) and device.get("max_input_channels", 0) > 0:
                    name = device.get("name", f"Device {idx}").strip()
                    devices.append((idx, name))
        except Exception:
            pass

        return devices

    def get_wasapi_loopback_devices(self) -> list[tuple[int, str]]:
        """Return list of WASAPI loopback devices (stereo mix)."""
        devices = []
        try:
            device_list = sd.query_devices()
            if isinstance(device_list, dict):
                device_list = [device_list]

            for idx, device in enumerate(device_list):
                if not isinstance(device, dict):
                    continue
                name = device.get("name", "").lower()
                # Look for stereo mix, loopback, or similar indicators
                if any(x in name for x in ["stereo mix", "loopback", "what u hear", "mix"]):
                    if device.get("max_input_channels", 0) > 0:
                        full_name = device.get("name", f"Device {idx}").strip()
                        devices.append((idx, full_name))
        except Exception:
            pass

        return devices

    def start_recording(
        self,
        output_path: Path,
        config: RecordingConfig,
        metrics_callback: Callable[[RecordingMetrics], None] | None = None,
    ) -> str | None:
        """
        Start recording to output_path. Returns error message if failed, None on success.

        Args:
            output_path: Path to write WAV file
            config: Recording configuration
            metrics_callback: Optional callback for real-time metrics
        """
        if self._is_recording:
            return "Recording already in progress"

        if not output_path.parent.exists():
            return f"Output folder does not exist: {output_path.parent}"

        self._stop_event.clear()
        self._is_recording = True
        self._metrics_callback = metrics_callback

        self._thread = threading.Thread(
            target=self._recording_thread,
            args=(output_path, config),
            daemon=False,
        )
        self._thread.start()

        return None

    def stop_recording(self) -> None:
        """Stop recording and wait for thread to finish."""
        if not self._is_recording:
            return

        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

        self._is_recording = False

    def get_latest_error(self) -> str | None:
        """Retrieve latest error from recording thread, if any."""
        try:
            return self._error_queue.get_nowait()
        except queue.Empty:
            return None

    def get_latest_metrics(self) -> RecordingMetrics | None:
        """Retrieve latest metrics from recording thread."""
        try:
            return self._metrics_queue.get_nowait()
        except queue.Empty:
            return None

    def _recording_thread(self, output_path: Path, config: RecordingConfig) -> None:
        """Thread worker for recording audio."""
        try:
            # Normalize device ID
            device_id = config.device_id
            if device_id is None:
                device_id = sd.default.device[0]  # Default input device

            # Prepare audio stream callback
            recorded_frames: list[np.ndarray] = []
            frame_count = 0

            def audio_callback(
                indata: np.ndarray,
                frames: int,
                time_info,
                status: sd.CallbackFlags,
            ) -> None:
                nonlocal frame_count
                if status:
                    # Note status but continue recording
                    pass

                # Copy indata and calculate peak level
                audio_data = indata.copy()
                recorded_frames.append(audio_data)
                frame_count += frames

                # Calculate peak level (across all channels)
                peak = float(np.max(np.abs(audio_data)))

                # Send metrics
                current_seconds = frame_count / config.sample_rate
                metrics = RecordingMetrics(
                    current_seconds=current_seconds,
                    peak_level=peak,
                )

                try:
                    self._metrics_queue.put_nowait(metrics)
                except queue.Full:
                    # Drop old metrics if queue is full
                    try:
                        self._metrics_queue.get_nowait()
                        self._metrics_queue.put_nowait(metrics)
                    except queue.Empty:
                        pass

            # Open input stream
            with sd.InputStream(
                device=device_id,
                samplerate=config.sample_rate,
                channels=config.channels,
                blocksize=config.blocksize,
                callback=audio_callback,
            ):
                # Wait until stop event is set
                self._stop_event.wait()

            # Write recorded audio to file
            if recorded_frames:
                audio_data = np.concatenate(recorded_frames, axis=0)
                sf.write(
                    str(output_path),
                    audio_data,
                    samplerate=config.sample_rate,
                )

        except Exception as e:
            try:
                self._error_queue.put_nowait(str(e))
            except queue.Full:
                pass


# Module-level instance for shared use
_recording_engine = RecordingEngine()


def get_recording_engine() -> RecordingEngine:
    """Get the shared recording engine instance."""
    return _recording_engine
