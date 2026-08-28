"""FastAPI app: browser-facing web/API routes, the agent WebSocket endpoint,
and static webclient hosting. Bind this to 127.0.0.1 only; Caddy terminates
TLS and reverse-proxies the public domain to this port.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import cache, config, db, security
from .agent_manager import AgentAudioError, AgentCommandTimeout, AgentManager, AgentNotConnected

app = FastAPI(title="jingleserver")
agent_manager = AgentManager()

_SAFE_RELPATH = re.compile(r"^[A-Za-z0-9_.\-\/]+$")


@app.on_event("startup")
def _on_startup() -> None:
    db.init_db()


# --- auth --------------------------------------------------------------------

class LoginBody(BaseModel):
    username: str
    password: str


def get_current_user(jsid: str | None = Cookie(default=None)):
    if not jsid:
        raise HTTPException(status_code=401, detail="not_authenticated")
    user = db.get_session_user(jsid)
    if user is None:
        raise HTTPException(status_code=401, detail="not_authenticated")
    return user


def require_admin(user=Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="admin_required")
    return user


@app.post("/api/login")
def login(body: LoginBody, response: Response) -> dict[str, Any]:
    user = db.get_user_by_username(body.username)
    if user is None or not security.verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid_credentials")
    session_id = security.generate_token()
    db.create_session(session_id, user["id"], config.SESSION_TTL_HOURS)
    response.set_cookie(
        config.SESSION_COOKIE_NAME,
        session_id,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=config.SESSION_TTL_HOURS * 3600,
    )
    return {"ok": True, "username": user["username"], "role": user["role"]}


@app.post("/api/logout")
def logout(response: Response, jsid: str | None = Cookie(default=None)) -> dict[str, Any]:
    if jsid:
        db.delete_session(jsid)
    response.delete_cookie(config.SESSION_COOKIE_NAME)
    return {"ok": True}


@app.get("/api/session")
def session_info(jsid: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = db.get_session_user(jsid) if jsid else None
    if user is None:
        return {"authenticated": False}
    return {"authenticated": True, "username": user["username"], "role": user["role"]}


# --- status / library (browser-facing) ---------------------------------------

@app.get("/api/status")
async def get_status(user=Depends(get_current_user)) -> dict[str, Any]:
    if not agent_manager.is_connected():
        return {"agent_connected": False}
    status = agent_manager.last_status() or {}
    return {"agent_connected": True, **status}


@app.get("/api/library")
async def get_library(
    user=Depends(get_current_user),
    search: str = "",
    scope: str = "all",
    category: str = "",
    category_mode: str = "any",
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    if agent_manager.is_connected():
        try:
            data = await agent_manager.send_command(
                "get_library",
                {
                    "search": search,
                    "scope": scope,
                    "category": category,
                    "category_mode": category_mode,
                    "limit": limit,
                    "offset": offset,
                },
            )
            return {"agent_connected": True, **data}
        except (AgentNotConnected, AgentCommandTimeout):
            pass
    manifest = cache.read_manifest()
    if manifest is None:
        return {"agent_connected": False, "offline": True, "items": [], "message": "no jingle machine currently connected"}
    return {"agent_connected": False, "offline": True, **manifest}


@app.get("/api/audio")
async def get_audio(user=Depends(get_current_user), path: str = "", cached_relpath: str = ""):
    """Serve jingle audio for browser preview, streaming progressively instead of buffering the whole file.

    Offline items are served straight from disk (Range/seek supported natively by FileResponse).
    Online items are pulled from the connected agent chunk-by-chunk and written to disk as they
    arrive, so only the first preview of a given file pays the agent round-trip.
    """
    import mimetypes

    if cached_relpath:
        resolved = cache.resolve_cached_audio_path(cached_relpath)
        if resolved is None:
            raise HTTPException(status_code=404, detail="not_cached")
        media_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        return FileResponse(str(resolved), media_type=media_type)

    if not path:
        raise HTTPException(status_code=400, detail="missing_path_or_cached_relpath")

    live_relpath = cache.live_cache_relpath(path)
    already_cached = cache.resolve_cached_audio_path(live_relpath)
    if already_cached is not None:
        media_type = mimetypes.guess_type(str(already_cached))[0] or "application/octet-stream"
        return FileResponse(str(already_cached), media_type=media_type)

    if not agent_manager.is_connected():
        raise HTTPException(status_code=409, detail="agent_not_connected")
    try:
        stream = await agent_manager.request_audio(path)
    except AgentNotConnected:
        raise HTTPException(status_code=409, detail="agent_not_connected")
    except AgentAudioError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    async def _tee_to_cache_and_client():
        dest = cache.audio_dir() / live_relpath
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest.with_name(dest.name + ".part")
        wrote_ok = False
        try:
            with open(tmp_path, "wb") as tmp_file:
                async for chunk in stream.chunks:
                    tmp_file.write(chunk)
                    yield chunk
            wrote_ok = True
        finally:
            if wrote_ok:
                tmp_path.replace(dest)
            else:
                tmp_path.unlink(missing_ok=True)

    return StreamingResponse(_tee_to_cache_and_client(), media_type=stream.content_type)


# --- playback control (browser-facing, relayed to the connected agent) -------

class PlayBody(BaseModel):
    path: str
    loop_mode: str = "off"
    live: bool = True


class ModeBody(BaseModel):
    loop_mode: str


class OutputBody(BaseModel):
    live: bool


def _agent_error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, AgentNotConnected):
        return JSONResponse(status_code=409, content={"ok": False, "error": "agent_not_connected"})
    return JSONResponse(status_code=504, content={"ok": False, "error": "agent_timeout"})


@app.post("/api/playback/play")
async def playback_play(body: PlayBody, user=Depends(get_current_user)):
    kwargs = body.model_dump()
    if user["role"] != "admin":
        # Guests can never force the host's real Live/Preview routing - always defer
        # to whatever the host is actually doing right now (mirrors legacy PIN-based behavior).
        last_status = agent_manager.last_status() or {}
        kwargs["live"] = bool(last_status.get("is_live_mode", True))
    try:
        return await agent_manager.send_command("play", kwargs)
    except (AgentNotConnected, AgentCommandTimeout) as exc:
        return _agent_error_response(exc)


@app.post("/api/playback/pause")
async def playback_pause(user=Depends(get_current_user)):
    try:
        return await agent_manager.send_command("toggle_pause", {})
    except (AgentNotConnected, AgentCommandTimeout) as exc:
        return _agent_error_response(exc)


@app.post("/api/playback/stop")
async def playback_stop(user=Depends(get_current_user)):
    try:
        return await agent_manager.send_command("stop", {})
    except (AgentNotConnected, AgentCommandTimeout) as exc:
        return _agent_error_response(exc)


@app.post("/api/playback/mode")
async def playback_mode(body: ModeBody, user=Depends(get_current_user)):
    if user["role"] != "admin" and not bool((agent_manager.last_status() or {}).get("is_live_mode", True)):
        # Guests only mirror the host's loop mode while actually riding the host's Live playback.
        return JSONResponse(
            status_code=403,
            content={"ok": False, "error": "Cannot change the Host's playback mode while the Host is in Preview."},
        )
    try:
        return await agent_manager.send_command("set_loop_mode", {"loop_mode": body.loop_mode})
    except (AgentNotConnected, AgentCommandTimeout) as exc:
        return _agent_error_response(exc)


@app.post("/api/playback/output")
async def playback_output(body: OutputBody, user=Depends(get_current_user)):
    if user["role"] != "admin":
        # Guests never touch the host's real device routing - only gate their own local toggle.
        snapshot = agent_manager.last_status() or {}
        host_is_live = bool(snapshot.get("is_live_mode", True))
        if body.live and not host_is_live:
            return JSONResponse(
                status_code=403,
                content={"ok": False, "error": "Cannot switch to Live - the Host hasn't engaged Live mode yet.", **snapshot},
            )
        return {"ok": True, **snapshot}
    try:
        return await agent_manager.send_command("set_live_mode", {"is_live_mode": body.live})
    except (AgentNotConnected, AgentCommandTimeout) as exc:
        return _agent_error_response(exc)


# --- status websocket (browser-facing) ---------------------------------------

@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket) -> None:
    jsid = websocket.cookies.get(config.SESSION_COOKIE_NAME)
    if not jsid or db.get_session_user(jsid) is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    try:
        import asyncio

        while True:
            if agent_manager.is_connected():
                payload = {"agent_connected": True, **(agent_manager.last_status() or {})}
            else:
                payload = {"agent_connected": False}
            await websocket.send_json(payload)
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return


# --- agent (desktop app) connection ------------------------------------------

@app.websocket("/agent/connect")
async def agent_connect(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        hello = await websocket.receive_json()
    except Exception:
        await websocket.close(code=4400)
        return
    token = hello.get("token", "")
    label = hello.get("label", "jingle-machine")
    device = db.get_device_by_token_hash(security.hash_token(token)) if token else None
    if device is None:
        await websocket.close(code=4401)
        return
    db.touch_device(device["id"])
    await agent_manager.attach(websocket, label=device["label"] or label)
    # Explicit ack so the desktop app's "Test Connection" button can confirm
    # acceptance without guessing from silence.
    await websocket.send_json({"type": "ack", "ok": True})
    try:
        while True:
            # Audio chunks arrive as binary frames; everything else (status pushes,
            # command replies, audio_start/end/error control messages) is JSON text.
            raw = await websocket.receive()
            if raw.get("bytes") is not None:
                agent_manager.handle_incoming_bytes(raw["bytes"])
            elif raw.get("text") is not None:
                agent_manager.handle_incoming(json.loads(raw["text"]))
    except WebSocketDisconnect:
        pass
    finally:
        await agent_manager.detach(websocket)


# --- offline cache upload (desktop app pushes files/manifest here) -----------

def _require_device_token(request: Request) -> None:
    token = request.headers.get("x-device-token", "")
    if not token or db.get_device_by_token_hash(security.hash_token(token)) is None:
        raise HTTPException(status_code=401, detail="invalid_device_token")


@app.post("/agent/cache/manifest")
async def upload_cache_manifest(request: Request) -> dict[str, Any]:
    _require_device_token(request)
    body = await request.json()
    items = body.get("items", [])
    cache.write_manifest(items)
    return {"ok": True, "count": len(items)}


@app.post("/agent/cache/file")
async def upload_cache_file(request: Request, relpath: str) -> dict[str, Any]:
    _require_device_token(request)
    if cache.is_traversal_unsafe(relpath) or Path(relpath).is_absolute() or not _SAFE_RELPATH.match(relpath):
        raise HTTPException(status_code=400, detail="invalid_relpath")
    content = await request.body()
    cache.save_audio_file(relpath, content)
    return {"ok": True, "bytes": len(content)}


# --- static webclient (mounted last so it never shadows /api or /agent) -----

if config.WEBCLIENT_DIR.exists():
    app.mount("/", StaticFiles(directory=str(config.WEBCLIENT_DIR), html=True), name="webclient")
