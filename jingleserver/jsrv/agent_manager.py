"""In-memory registry for the single connected desktop agent WebSocket.

Only one jingle machine is expected to drive the webapp at a time (v1 scope);
this class tracks that one connection, relays commands to it, and caches the
last status/library snapshots it pushes so browser polling never has to wait
on the agent directly.

Audio streaming
----------------
Guest local-preview audio is pulled from the agent in chunks instead of
waiting for the whole file, so playback can start immediately in the
browser instead of after a multi-second/minute download of large files.
Control messages (`audio_start`/`audio_end`/`audio_error`) are JSON text
frames; the chunk payloads themselves are raw WebSocket *binary* frames
prefixed with a 4-byte big-endian request id so multiple concurrent audio
pulls never get their chunks mixed up on the shared agent connection.
"""

from __future__ import annotations

import asyncio
import itertools
import struct
import time
from typing import Any, AsyncIterator

from fastapi import WebSocket

from . import config

_AUDIO_HEADER = struct.Struct(">I")


class AgentNotConnected(Exception):
    pass


class AgentCommandTimeout(Exception):
    pass


class AgentAudioError(Exception):
    pass


class AgentManager:
    def __init__(self) -> None:
        self._socket: WebSocket | None = None
        self._label: str = ""
        self._connected_at: float = 0.0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._request_ids = itertools.count(1)
        self._last_status: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._audio_queues: dict[int, asyncio.Queue[bytes | BaseException | None]] = {}
        self._audio_meta: dict[int, asyncio.Future[dict[str, Any]]] = {}

    def is_connected(self) -> bool:
        return self._socket is not None

    def connection_info(self) -> dict[str, Any]:
        if not self.is_connected():
            return {"connected": False}
        return {"connected": True, "label": self._label, "connected_at": self._connected_at}

    def last_status(self) -> dict[str, Any] | None:
        return self._last_status

    async def attach(self, websocket: WebSocket, label: str) -> None:
        """Replace any previously connected agent with this new connection."""
        async with self._lock:
            previous = self._socket
            self._socket = websocket
            self._label = label
            self._connected_at = time.time()
        if previous is not None and previous is not websocket:
            try:
                await previous.close(code=4000, reason="replaced by new agent connection")
            except Exception:
                pass

    async def detach(self, websocket: WebSocket) -> None:
        async with self._lock:
            if self._socket is websocket:
                self._socket = None
                self._label = ""
                self._last_status = None
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(AgentNotConnected())
                self._pending.clear()
                for queue in self._audio_queues.values():
                    queue.put_nowait(None)
                self._audio_queues.clear()
                for meta_future in self._audio_meta.values():
                    if not meta_future.done():
                        meta_future.set_exception(AgentNotConnected())
                self._audio_meta.clear()

    def handle_incoming(self, message: dict[str, Any]) -> None:
        """Called from the agent's WS receive loop for JSON text frames (pushes, replies, audio control)."""
        msg_type = message.get("type")
        if msg_type == "status":
            self._last_status = message.get("data") or {}
        elif msg_type == "response":
            req_id = message.get("id")
            future = self._pending.pop(req_id, None)
            if future is not None and not future.done():
                future.set_result(message.get("data") or {})
        elif msg_type == "audio_start":
            req_id = message.get("id")
            meta_future = self._audio_meta.get(req_id)
            if meta_future is not None and not meta_future.done():
                meta_future.set_result(
                    {"size": message.get("size", 0), "content_type": message.get("content_type", "application/octet-stream")}
                )
        elif msg_type == "audio_end":
            queue = self._audio_queues.get(message.get("id"))
            if queue is not None:
                queue.put_nowait(None)
        elif msg_type == "audio_error":
            req_id = message.get("id")
            error = AgentAudioError(str(message.get("error", "unknown error")))
            meta_future = self._audio_meta.get(req_id)
            if meta_future is not None and not meta_future.done():
                meta_future.set_exception(error)
            queue = self._audio_queues.get(req_id)
            if queue is not None:
                queue.put_nowait(error)

    def handle_incoming_bytes(self, raw: bytes) -> None:
        """Called from the agent's WS receive loop for binary audio-chunk frames."""
        if len(raw) < _AUDIO_HEADER.size:
            return
        (req_id,) = _AUDIO_HEADER.unpack_from(raw, 0)
        queue = self._audio_queues.get(req_id)
        if queue is not None:
            queue.put_nowait(raw[_AUDIO_HEADER.size :])

    async def send_command(self, action: str, kwargs: dict[str, Any] | None = None,
                            timeout: float = config.AGENT_COMMAND_TIMEOUT_SECONDS) -> dict[str, Any]:
        if self._socket is None:
            raise AgentNotConnected()
        req_id = next(self._request_ids)
        loop = asyncio.get_event_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = future
        try:
            async with self._send_lock:
                await self._socket.send_json({"type": "command", "id": req_id, "action": action, "kwargs": kwargs or {}})
        except Exception as exc:
            self._pending.pop(req_id, None)
            raise AgentNotConnected() from exc
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(req_id, None)
            raise AgentCommandTimeout() from exc

    async def request_audio(self, path: str) -> "AudioStream":
        """Ask the agent to stream a jingle's audio; returns metadata plus a chunk iterator."""
        if self._socket is None:
            raise AgentNotConnected()
        req_id = next(self._request_ids)
        loop = asyncio.get_event_loop()
        meta_future: asyncio.Future[dict[str, Any]] = loop.create_future()
        queue: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue()
        self._audio_meta[req_id] = meta_future
        self._audio_queues[req_id] = queue
        try:
            async with self._send_lock:
                await self._socket.send_json({"type": "command", "id": req_id, "action": "get_audio", "kwargs": {"path": path}})
            meta = await asyncio.wait_for(meta_future, timeout=config.AGENT_AUDIO_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            self._audio_queues.pop(req_id, None)
            self._audio_meta.pop(req_id, None)
            raise AgentCommandTimeout() from exc
        except Exception as exc:
            self._audio_queues.pop(req_id, None)
            self._audio_meta.pop(req_id, None)
            if isinstance(exc, AgentAudioError):
                raise
            raise AgentNotConnected() from exc

        async def _chunks() -> AsyncIterator[bytes]:
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=config.AGENT_AUDIO_TIMEOUT_SECONDS)
                    except asyncio.TimeoutError as exc:
                        raise AgentCommandTimeout() from exc
                    if item is None:
                        return
                    if isinstance(item, BaseException):
                        raise item
                    yield item
            finally:
                self._audio_queues.pop(req_id, None)
                self._audio_meta.pop(req_id, None)

        return AudioStream(size=int(meta.get("size", 0)), content_type=str(meta.get("content_type", "application/octet-stream")), chunks=_chunks())


class AudioStream:
    def __init__(self, size: int, content_type: str, chunks: AsyncIterator[bytes]) -> None:
        self.size = size
        self.content_type = content_type
        self.chunks = chunks


