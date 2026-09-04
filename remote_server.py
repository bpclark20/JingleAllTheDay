"""Outbound relay client for JingleAllTheDay remote control.

The desktop app no longer hosts an inbound server. Instead it dials OUT to a
jingleserver middleman (see the `jingleserver/` folder) over a persistent
WebSocket at `/agent/connect`, authenticating with a device token issued by
`jingleserver adddevice`. jingleserver relays browser commands to us over
that connection, and we push status snapshots back down it. See
`REMOTE_API.md` for the wire protocol.

Thread-safety model
--------------------
The relay owns its own asyncio event loop on a background thread. Incoming
"command" messages are handled by `RemoteServerBridge` exactly like the old
inbound FastAPI server did: `Bridge.execute()` emits a Qt signal with
`Qt.ConnectionType.BlockingQueuedConnection`, which blocks the relay thread
until the slot has run to completion on the Qt main thread. This lets us
reuse the same `MainWindow.remote_*` surface (`remote_play`,
`remote_get_status`, etc.) the old server used, unchanged.
"""

from __future__ import annotations

import collections
import json
import mimetypes
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QObject, Qt, pyqtSignal

try:
    import websockets
except ModuleNotFoundError:  # pragma: no cover - dependency is in requirements.txt
    websockets = None  # type: ignore[assignment]


class RemoteCommand:
    """A single cross-thread request: action name + kwargs, with a result slot."""

    def __init__(self, action: str, **kwargs: Any) -> None:
        self.action = action
        self.kwargs = kwargs
        self.result: dict[str, Any] = {"ok": False, "error": "not_handled"}
        self.done = threading.Event()


class RemoteServerBridge(QObject):
    """Lives on the Qt main thread; marshals relayed commands into MainWindow calls."""

    _command_ready = pyqtSignal(object)

    def __init__(self, main_window: Any) -> None:
        super().__init__()
        self._main_window = main_window
        # PyQt6 stubs only declare the single-arg connect() overload; the two-arg
        # form (slot, connection type) is valid at runtime and required here.
        self._command_ready.connect(self._handle_command, Qt.ConnectionType.BlockingQueuedConnection)  # pyright: ignore[reportCallIssue]

    def execute(self, action: str, timeout: float = 5.0, **kwargs: Any) -> dict[str, Any]:
        """Called from the relay thread; blocks until the main thread has handled it."""
        cmd = RemoteCommand(action, **kwargs)
        self._command_ready.emit(cmd)
        cmd.done.wait(timeout)
        return cmd.result

    def _handle_command(self, cmd: RemoteCommand) -> None:
        mw = self._main_window
        try:
            if cmd.action == "play":
                cmd.result = mw.remote_play(
                    cmd.kwargs.get("path", ""),
                    cmd.kwargs.get("loop_mode", "off"),
                    bool(cmd.kwargs.get("live", cmd.kwargs.get("is_live_mode", True))),
                )
            elif cmd.action == "toggle_pause":
                cmd.result = {"ok": True, **mw.remote_toggle_pause()}
            elif cmd.action == "stop":
                mw.remote_stop()
                cmd.result = {"ok": True, **mw.remote_get_status()}
            elif cmd.action == "set_loop_mode":
                mw.remote_set_loop_mode(cmd.kwargs.get("loop_mode", "off"))
                cmd.result = {"ok": True, **mw.remote_get_status()}
            elif cmd.action == "set_live_mode":
                applied = mw.remote_set_live_mode(bool(cmd.kwargs.get("is_live_mode", True)))
                cmd.result = {"ok": applied, **mw.remote_get_status()}
            else:
                cmd.result = {"ok": False, "error": f"unknown action: {cmd.action}"}
        except Exception as exc:  # noqa: BLE001 - surface any failure back to jingleserver
            cmd.result = {"ok": False, "error": str(exc)}
        finally:
            cmd.done.set()


class RemoteServerState:
    """Thread-safe now-playing snapshot, refreshed from the main thread's playback timer."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] = {
            "state": "stopped",
            "is_live_mode": True,
            "current_name": "",
            "current_path": "",
            "position_seconds": 0.0,
            "duration_seconds": 0.0,
            "loop_mode": "off",
        }

    def update(self, snapshot: dict[str, Any]) -> None:
        with self._lock:
            self._snapshot = dict(snapshot)

    def get(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._snapshot)


class RemoteRelayDiagnostics:
    """Thread-safe connection status + recent relayed-command log for the Diagnostics dialog."""

    def __init__(self, max_log_entries: int = 200) -> None:
        self._lock = threading.Lock()
        self._connected = False
        self._label = ""
        self._connected_at: float = 0.0
        self._last_error = ""
        self._reconnect_count = 0
        self._log: collections.deque[dict[str, Any]] = collections.deque(maxlen=max_log_entries)

    def set_connected(self, connected: bool, label: str = "", error: str = "") -> None:
        with self._lock:
            was_connected = self._connected
            self._connected = connected
            if connected:
                self._label = label
                self._connected_at = time.time()
                self._last_error = ""
            else:
                if error:
                    self._last_error = error
                if was_connected:
                    self._reconnect_count += 1

    def log_command(self, action: str, target: str, ok: bool, detail: str = "") -> None:
        with self._lock:
            self._log.appendleft(
                {"time": time.time(), "action": action, "target": target, "ok": ok, "detail": detail}
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "connected": self._connected,
                "label": self._label,
                "connected_at": self._connected_at,
                "last_error": self._last_error,
                "reconnect_count": self._reconnect_count,
                "log": list(self._log),
            }


def normalize_relay_uri(address: str) -> str:
    """Turn a bare host[:port] or full ws(s)://... address into a `/agent/connect` URI."""
    candidate = (address or "").strip()
    if not candidate:
        raise ValueError("empty server address")
    if "://" not in candidate:
        candidate = f"wss://{candidate}"
    if not candidate.rstrip("/").endswith("/agent/connect"):
        candidate = candidate.rstrip("/") + "/agent/connect"
    return candidate


def relay_http_base_url(address: str) -> str:
    """Same address the relay connects to, as an http(s) base URL (no path) for cache uploads."""
    candidate = (address or "").strip()
    if not candidate:
        raise ValueError("empty server address")
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    elif candidate.startswith("ws://"):
        candidate = "http://" + candidate[len("ws://") :]
    elif candidate.startswith("wss://"):
        candidate = "https://" + candidate[len("wss://") :]
    return candidate.rstrip("/").removesuffix("/agent/connect")


def cache_relpath_for_path(path: str) -> str:
    """Deterministic cache filename for a jingle's library path.

    Must exactly match jingleserver's `jsrv.cache.live_cache_relpath()` so that a file
    uploaded via Offline Cache Backup lands in the same cache slot jingleserver's own
    write-through live-preview cache would use for that same path - letting `/api/audio`
    find it whether it got there via a manual backup or a prior live preview.
    """
    import hashlib

    suffix = Path(path).suffix
    return "live_" + hashlib.sha256(path.encode("utf-8")).hexdigest() + suffix


def test_connection(address: str, device_token: str, timeout: float = 6.0) -> tuple[bool, str]:
    """Synchronous one-shot connectivity check used by the Options dialog's Test Connection button."""
    if websockets is None:
        return False, "The 'websockets' package is not installed."
    try:
        uri = normalize_relay_uri(address)
    except ValueError as exc:
        return False, str(exc)
    try:
        from websockets.sync.client import connect as sync_connect
    except ImportError:
        return False, "websockets sync client unavailable; upgrade the 'websockets' package."

    start = time.monotonic()
    try:
        with sync_connect(uri, open_timeout=timeout, close_timeout=2) as ws:
            ws.send(json.dumps({"token": device_token, "label": socket.gethostname()}))
            raw = ws.recv(timeout=timeout)
            message = json.loads(raw)
            if not message.get("ok"):
                return False, "Server rejected the connection."
    except TimeoutError:
        return False, "Timed out waiting for the server to respond."
    except Exception as exc:  # noqa: BLE001 - surface any failure to the user
        return False, f"Connection failed: {exc}"
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return True, f"Connected in {elapsed_ms} ms."


class RemoteRelayClient:
    """Owns the background asyncio thread that keeps a connection to jingleserver alive."""

    _MIN_RECONNECT_DELAY = 1.0
    _MAX_RECONNECT_DELAY = 30.0
    _STATUS_PUSH_INTERVAL = 0.25
    _AUDIO_CHUNK_SIZE = 64 * 1024
    _AUDIO_HEADER = struct.Struct(">I")

    def __init__(
        self,
        bridge: RemoteServerBridge,
        state: RemoteServerState,
        diagnostics: RemoteRelayDiagnostics,
        address_provider: Callable[[], str],
        device_token_provider: Callable[[], str],
        library_provider: Callable[..., dict[str, Any]],
        audio_path_provider: Callable[[str], Path | None] | None = None,
    ) -> None:
        self._bridge = bridge
        self._state = state
        self._diagnostics = diagnostics
        self._address_provider = address_provider
        self._device_token_provider = device_token_provider
        self._library_provider = library_provider
        self._audio_path_provider = audio_path_provider
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_connected(self) -> bool:
        return bool(self._diagnostics.snapshot().get("connected"))

    def start(self) -> bool:
        if self.is_running():
            return True
        if websockets is None:
            self._diagnostics.set_connected(False, error="websockets is not installed")
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="jatd-remote-relay", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None
        self._diagnostics.set_connected(False)

    def restart(self) -> bool:
        self.stop()
        return self.start()

    def _run(self) -> None:
        import asyncio

        asyncio.run(self._connect_forever())

    async def _connect_forever(self) -> None:
        import asyncio

        delay = self._MIN_RECONNECT_DELAY
        while not self._stop_event.is_set():
            try:
                uri = normalize_relay_uri(self._address_provider())
            except ValueError as exc:
                self._diagnostics.set_connected(False, error=str(exc))
                await asyncio.sleep(self._MAX_RECONNECT_DELAY)
                continue
            try:
                async with websockets.connect(uri, open_timeout=10) as ws:
                    await ws.send(
                        json.dumps({"token": self._device_token_provider(), "label": socket.gethostname()})
                    )
                    ack_raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    ack = json.loads(ack_raw)
                    if not ack.get("ok"):
                        raise RuntimeError("server rejected device token")
                    self._diagnostics.set_connected(True, label=uri)
                    delay = self._MIN_RECONNECT_DELAY
                    # A single websocket connection cannot have multiple concurrent senders
                    # (status pushes, command replies, and audio-chunk streaming all share it),
                    # so every send() call funnels through this one lock for this connection's lifetime.
                    send_lock = asyncio.Lock()
                    await asyncio.gather(self._recv_loop(ws, send_lock), self._status_push_loop(ws, send_lock))
            except Exception as exc:  # noqa: BLE001 - any failure just triggers a reconnect
                self._diagnostics.set_connected(False, error=str(exc))
            if self._stop_event.is_set():
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._MAX_RECONNECT_DELAY)

    async def _recv_loop(self, ws: Any, send_lock: Any) -> None:
        import asyncio

        async for raw in ws:
            if self._stop_event.is_set():
                return
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if message.get("type") != "command":
                continue
            action = str(message.get("action", ""))
            req_id = message.get("id")
            kwargs = message.get("kwargs") or {}
            if action == "get_audio":
                asyncio.ensure_future(self._stream_audio(ws, send_lock, req_id, str(kwargs.get("path", ""))))
                continue
            result = self._dispatch(message)
            async with send_lock:
                await ws.send(json.dumps({"type": "response", "id": req_id, "data": result}))

    async def _stream_audio(self, ws: Any, send_lock: Any, req_id: Any, path: str) -> None:
        """Stream a jingle's audio to jingleserver in chunks so browser preview starts immediately
        instead of waiting for the whole file (large jingles can exceed 100MB)."""
        resolved = self._audio_path_provider(path) if self._audio_path_provider else None
        if resolved is None or not resolved.exists():
            async with send_lock:
                await ws.send(json.dumps({"type": "audio_error", "id": req_id, "error": "not_found"}))
            return
        try:
            size = resolved.stat().st_size
            content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
            async with send_lock:
                await ws.send(json.dumps({"type": "audio_start", "id": req_id, "size": size, "content_type": content_type}))
            header = self._AUDIO_HEADER.pack(int(req_id))
            with open(resolved, "rb") as audio_file:
                while True:
                    chunk = audio_file.read(self._AUDIO_CHUNK_SIZE)
                    if not chunk:
                        break
                    async with send_lock:
                        await ws.send(header + chunk)
            async with send_lock:
                await ws.send(json.dumps({"type": "audio_end", "id": req_id}))
        except Exception as exc:  # noqa: BLE001 - surface failures back to jingleserver
            try:
                async with send_lock:
                    await ws.send(json.dumps({"type": "audio_error", "id": req_id, "error": str(exc)}))
            except Exception:
                pass

    async def _status_push_loop(self, ws: Any, send_lock: Any) -> None:
        import asyncio

        while not self._stop_event.is_set():
            async with send_lock:
                await ws.send(json.dumps({"type": "status", "data": self._state.get()}))
            await asyncio.sleep(self._STATUS_PUSH_INTERVAL)

    def _dispatch(self, message: dict[str, Any]) -> dict[str, Any]:
        action = str(message.get("action", ""))
        kwargs = message.get("kwargs") or {}
        try:
            if action == "get_library":
                result = self._library_provider(**kwargs)
            else:
                result = self._bridge.execute(action, **kwargs)
        except Exception as exc:  # noqa: BLE001 - surface failures back to jingleserver
            result = {"ok": False, "error": str(exc)}
        self._diagnostics.log_command(action, str(kwargs.get("path", "")), bool(result.get("ok", True)))
        return result
