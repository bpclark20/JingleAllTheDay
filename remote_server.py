"""LAN remote-control server for JingleAllTheDay.

Runs a FastAPI/Uvicorn server in a background thread so a phone or any other
device on the same LAN can browse the library and mirror/drive the main
window's playback (search, play/pause/stop, loop mode, Live/Preview).

Thread-safety model
--------------------
Uvicorn owns its own asyncio event loop on a background thread. Control
endpoints are declared as plain ``def`` (Starlette runs them in a worker
threadpool), and they reach into Qt via `RemoteServerBridge`, a `QObject`
that lives on the Qt main thread. Emitting its signal with
`Qt.ConnectionType.BlockingQueuedConnection` blocks the calling worker
thread until the slot has run to completion on the main thread, so no Qt
object is ever touched off the main thread.

Status/library reads are lock-protected snapshots (`RemoteServerState`,
`RemoteDiagnostics`) that the main thread keeps up to date; the async side
only ever reads them, so no cross-thread Qt calls are needed for polling.
"""

from __future__ import annotations

import collections
import threading
import time
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from fastapi import Header as _Header
from fastapi import Request, WebSocket
from pydantic import BaseModel


def _pin_header() -> Any:
    """Fresh `Header()` default per route param (FastAPI defaults must not be shared)."""
    return _Header(default=None)



class RemoteCommand:
    """A single cross-thread request: action name + kwargs, with a result slot."""

    def __init__(self, action: str, **kwargs: Any) -> None:
        self.action = action
        self.kwargs = kwargs
        self.result: dict[str, Any] = {"ok": False, "error": "not_handled"}
        self.done = threading.Event()


class RemoteServerBridge(QObject):
    """Lives on the Qt main thread; marshals remote commands into MainWindow calls."""

    _command_ready = pyqtSignal(object)

    def __init__(self, main_window: Any) -> None:
        super().__init__()
        self._main_window = main_window
        # PyQt6 stubs only declare the single-arg connect() overload; the two-arg
        # form (slot, connection type) is valid at runtime and required here.
        self._command_ready.connect(self._handle_command, Qt.ConnectionType.BlockingQueuedConnection)  # pyright: ignore[reportCallIssue]

    def execute(self, action: str, timeout: float = 5.0, **kwargs: Any) -> dict[str, Any]:
        """Called from any thread; blocks until the main thread has handled it."""
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
                    bool(cmd.kwargs.get("is_live_mode", True)),
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
        except Exception as exc:  # noqa: BLE001 - surface any failure back to the HTTP caller
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


class RemoteDiagnostics:
    """Thread-safe connected-client registry and recent request log for the Diagnostics dialog."""

    def __init__(self, max_log_entries: int = 200) -> None:
        self._lock = threading.Lock()
        self._clients: dict[str, dict[str, Any]] = {}
        self._log: collections.deque[dict[str, Any]] = collections.deque(maxlen=max_log_entries)

    def client_connected(self, client_id: str, ip: str) -> None:
        with self._lock:
            self._clients[client_id] = {"ip": ip, "connected_at": time.time()}

    def client_disconnected(self, client_id: str) -> None:
        with self._lock:
            self._clients.pop(client_id, None)

    def log_request(self, ip: str, action: str, target: str, ok: bool, detail: str = "") -> None:
        with self._lock:
            self._log.appendleft(
                {
                    "time": time.time(),
                    "ip": ip,
                    "action": action,
                    "target": target,
                    "ok": ok,
                    "detail": detail,
                }
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "clients": list(self._clients.items()),
                "log": list(self._log),
            }


def build_app(
    bridge: RemoteServerBridge,
    state: RemoteServerState,
    diagnostics: RemoteDiagnostics,
    pin_provider: Callable[[], str],
    admin_pin_provider: Callable[[], str],
    library_provider: Callable[..., dict[str, Any]],
    audio_path_provider: Callable[[str], Path | None],
    static_dir: Path | None,
):
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="JingleAllTheDay Remote", docs_url=None, redoc_url=None)
    api = _RemoteApi(bridge, state, diagnostics, pin_provider, admin_pin_provider, library_provider, audio_path_provider)
    api.register_routes(app)

    if static_dir is not None and static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="webclient")

    return app


# NOTE: route handlers below are defined as plain module-level methods (not nested
# functions) because FastAPI's signature introspection uses `get_type_hints()` against
# each function's module globals; combined with `from __future__ import annotations`,
# closures defined *inside* a factory function cannot resolve their own local classes
# (e.g. request-body models) and silently misroute parameters.
class PlayRequest(BaseModel):
    path: str
    loop_mode: str = "off"
    live: bool = True


class LoopModeRequest(BaseModel):
    loop_mode: str


class OutputModeRequest(BaseModel):
    live: bool


class LoginRequest(BaseModel):
    pin: str


class _RemoteApi:
    def __init__(
        self,
        bridge: RemoteServerBridge,
        state: RemoteServerState,
        diagnostics: RemoteDiagnostics,
        pin_provider: Callable[[], str],
        admin_pin_provider: Callable[[], str],
        library_provider: Callable[..., dict[str, Any]],
        audio_path_provider: Callable[[str], Path | None],
    ) -> None:
        self._bridge = bridge
        self._state = state
        self._diagnostics = diagnostics
        self._pin_provider = pin_provider
        self._admin_pin_provider = admin_pin_provider
        self._library_provider = library_provider
        self._audio_path_provider = audio_path_provider

    def register_routes(self, app) -> None:  # type: ignore[no-untyped-def]
        from fastapi import HTTPException, Request
        from fastapi.responses import JSONResponse

        app.get("/api/status")(self.get_status)
        app.get("/api/library")(self.get_library)
        app.get("/api/audio")(self.get_audio)
        app.post("/api/login")(self.login)
        app.post("/api/playback/play")(self.play)
        app.post("/api/playback/pause")(self.pause)
        app.post("/api/playback/stop")(self.stop)
        app.post("/api/playback/mode")(self.set_mode)
        app.post("/api/playback/output")(self.set_output)
        app.websocket("/ws/status")(self.ws_status)
        app.exception_handler(HTTPException)(self._http_exception_handler)

    def _role_for_pin(self, pin: str | None) -> str | None:
        """Resolve a submitted PIN to a role. Admin PIN blank means no one gets admin."""
        candidate = (pin or "").strip()
        if not candidate:
            return None
        admin_pin = self._admin_pin_provider().strip()
        if admin_pin and candidate == admin_pin:
            return "admin"
        guest_pin = self._pin_provider().strip()
        if guest_pin and candidate == guest_pin:
            return "user"
        return None

    def _resolve_role(self, x_remote_pin: str | None) -> str:
        from fastapi import HTTPException

        if not self._pin_provider().strip() and not self._admin_pin_provider().strip():
            raise HTTPException(status_code=403, detail="Set a PIN in Options to enable remote control.")
        role = self._role_for_pin(x_remote_pin)
        if role is None:
            raise HTTPException(status_code=403, detail="Invalid or missing PIN.")
        return role

    @staticmethod
    def _client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def get_status(self) -> dict[str, Any]:
        return self._state.get()

    def get_library(
        self,
        search: str = "",
        scope: str = "all",
        category: str = "",
        category_mode: str = "any",
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        return self._library_provider(
            search=search,
            scope=scope,
            category=category,
            category_mode=category_mode,
            limit=limit,
            offset=offset,
        )

    def login(self, body: LoginRequest) -> dict[str, Any]:
        role = self._role_for_pin(body.pin)
        if role is None:
            return {"ok": False, "error": "Invalid PIN."}
        return {"ok": True, "role": role}

    def get_audio(self, path: str, request: Request, x_remote_pin: str | None = _pin_header()) -> Any:
        import mimetypes

        from fastapi import HTTPException
        from fastapi.responses import FileResponse

        ip = self._client_ip(request)
        try:
            self._resolve_role(x_remote_pin)
        except HTTPException as exc:
            self._diagnostics.log_request(ip, "audio", path, False, str(exc.detail))
            raise
        resolved = self._audio_path_provider(path)
        if resolved is None:
            self._diagnostics.log_request(ip, "audio", path, False, "not_found")
            raise HTTPException(status_code=404, detail="Jingle not found.")
        self._diagnostics.log_request(ip, "audio", path, True, "")
        media_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        return FileResponse(str(resolved), media_type=media_type)

    def play(self, body: PlayRequest, request: Request, x_remote_pin: str | None = _pin_header()) -> dict[str, Any]:
        from fastapi import HTTPException

        ip = self._client_ip(request)
        try:
            role = self._resolve_role(x_remote_pin)
        except HTTPException as exc:
            self._diagnostics.log_request(ip, "play", body.path, False, str(exc.detail))
            raise
        # Guests can never flip the host's real Live/Preview routing via Play,
        # even if they craft the request directly - always defer to host state.
        effective_live = body.live if role == "admin" else bool(self._state.get().get("is_live_mode", True))
        result = self._bridge.execute("play", path=body.path, loop_mode=body.loop_mode, is_live_mode=effective_live)
        self._diagnostics.log_request(ip, "play", body.path, bool(result.get("ok")), str(result.get("error", "")))
        return result

    def pause(self, request: Request, x_remote_pin: str | None = _pin_header()) -> dict[str, Any]:
        from fastapi import HTTPException

        ip = self._client_ip(request)
        try:
            self._resolve_role(x_remote_pin)
        except HTTPException as exc:
            self._diagnostics.log_request(ip, "pause", "", False, str(exc.detail))
            raise
        result = self._bridge.execute("toggle_pause")
        self._diagnostics.log_request(ip, "pause", "", bool(result.get("ok")), "")
        return result

    def stop(self, request: Request, x_remote_pin: str | None = _pin_header()) -> dict[str, Any]:
        from fastapi import HTTPException

        ip = self._client_ip(request)
        try:
            self._resolve_role(x_remote_pin)
        except HTTPException as exc:
            self._diagnostics.log_request(ip, "stop", "", False, str(exc.detail))
            raise
        result = self._bridge.execute("stop")
        self._diagnostics.log_request(ip, "stop", "", bool(result.get("ok")), "")
        return result

    def set_mode(self, body: LoopModeRequest, request: Request, x_remote_pin: str | None = _pin_header()) -> dict[str, Any]:
        from fastapi import HTTPException

        ip = self._client_ip(request)
        try:
            role = self._resolve_role(x_remote_pin)
        except HTTPException as exc:
            self._diagnostics.log_request(ip, "loop_mode", body.loop_mode, False, str(exc.detail))
            raise
        if role != "admin" and not bool(self._state.get().get("is_live_mode", True)):
            # Guests only mirror the host's loop mode while actually riding the host's Live playback.
            error = "Cannot change the Host's playback mode while the Host is in Preview."
            self._diagnostics.log_request(ip, "loop_mode", body.loop_mode, False, error)
            return {"ok": False, "error": error, **self._state.get()}
        result = self._bridge.execute("set_loop_mode", loop_mode=body.loop_mode)
        self._diagnostics.log_request(ip, "loop_mode", body.loop_mode, bool(result.get("ok")), "")
        return result

    def set_output(self, body: OutputModeRequest, request: Request, x_remote_pin: str | None = _pin_header()) -> dict[str, Any]:
        from fastapi import HTTPException

        ip = self._client_ip(request)
        target = "live" if body.live else "preview"
        try:
            role = self._resolve_role(x_remote_pin)
        except HTTPException as exc:
            self._diagnostics.log_request(ip, "output_mode", target, False, str(exc.detail))
            raise
        if role != "admin":
            # Guests never touch the host's real device routing - only gate their own local toggle.
            snapshot = self._state.get()
            host_is_live = bool(snapshot.get("is_live_mode", True))
            if body.live and not host_is_live:
                error = "Cannot switch to Live - the Host hasn't engaged Live mode yet."
                self._diagnostics.log_request(ip, "output_mode", target, False, error)
                return {"ok": False, "error": error, **snapshot}
            self._diagnostics.log_request(ip, "output_mode", target, True, "")
            return {"ok": True, **snapshot}
        result = self._bridge.execute("set_live_mode", is_live_mode=body.live)
        self._diagnostics.log_request(ip, "output_mode", target, bool(result.get("ok")), "")
        return result

    async def ws_status(self, websocket: WebSocket) -> None:
        import asyncio

        await websocket.accept()
        client_id = f"{id(websocket)}"
        ip = websocket.client.host if websocket.client else "unknown"
        self._diagnostics.client_connected(client_id, ip)
        try:
            while True:
                await websocket.send_json(self._state.get())
                await asyncio.sleep(0.25)
        except Exception:
            pass
        finally:
            self._diagnostics.client_disconnected(client_id)

    @staticmethod
    def _http_exception_handler(_request: Request, exc: Any) -> Any:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.detail})


class RemoteServerManager:
    """Owns the background uvicorn thread and exposes start/stop/restart."""

    def __init__(
        self,
        bridge: RemoteServerBridge,
        state: RemoteServerState,
        diagnostics: RemoteDiagnostics,
        pin_provider: Callable[[], str],
        admin_pin_provider: Callable[[], str],
        library_provider: Callable[..., dict[str, Any]],
        audio_path_provider: Callable[[str], Path | None],
        static_dir: Path | None,
    ) -> None:
        self._bridge = bridge
        self._state = state
        self._diagnostics = diagnostics
        self._pin_provider = pin_provider
        self._admin_pin_provider = admin_pin_provider
        self._library_provider = library_provider
        self._audio_path_provider = audio_path_provider
        self._static_dir = static_dir
        self._server: Any | None = None
        self._thread: threading.Thread | None = None
        self._port: int = 0
        self._last_error: str = ""

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def port(self) -> int:
        return self._port

    def last_error(self) -> str:
        return self._last_error

    def start(self, port: int) -> bool:
        if self.is_running():
            return True
        try:
            import uvicorn
        except ModuleNotFoundError as exc:
            self._last_error = f"uvicorn is not installed: {exc}"
            return False

        app = build_app(
            self._bridge,
            self._state,
            self._diagnostics,
            self._pin_provider,
            self._admin_pin_provider,
            self._library_provider,
            self._audio_path_provider,
            self._static_dir,
        )
        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=port,
            loop="asyncio",
            http="h11",
            ws="websockets",
            log_level="warning",
        )
        # uvicorn.Server.capture_signals() auto-skips signal registration off the
        # main thread, so no extra configuration is needed to run it here.
        server = uvicorn.Server(config)

        ready = threading.Event()
        error: list[BaseException] = []

        def _run() -> None:
            try:
                import asyncio

                asyncio.run(_serve_with_ready_signal(server, ready))
            except BaseException as exc:  # noqa: BLE001
                error.append(exc)
                ready.set()

        self._server = server
        self._thread = threading.Thread(target=_run, name="jatd-remote-server", daemon=True)
        self._thread.start()
        ready.wait(5.0)
        if error:
            self._last_error = str(error[0])
            self._thread = None
            self._server = None
            return False
        self._port = port
        self._last_error = ""
        return True

    def stop(self, timeout: float = 5.0) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None
        self._server = None
        self._port = 0

    def restart(self, port: int) -> bool:
        self.stop()
        return self.start(port)


async def _serve_with_ready_signal(server: Any, ready: threading.Event) -> None:
    import asyncio

    async def _signal_when_started() -> None:
        while not server.started:
            await asyncio.sleep(0.01)
        ready.set()

    await asyncio.gather(server.serve(), _signal_when_started())
