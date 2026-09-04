"""FastAPI app: browser-facing web/API routes, the agent WebSocket endpoint,
and static webclient hosting. Bind this to 127.0.0.1 only; Caddy terminates
TLS and reverse-proxies the public domain to this port.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
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
logger = logging.getLogger(__name__)

_SAFE_RELPATH = re.compile(r"^[A-Za-z0-9_.\-\/]+$")
_live_library_paths: dict[str, str] = {}
_preview_transcode_lock = asyncio.Lock()
_LOSSLESS_SUFFIXES = {".aif", ".aiff", ".alac", ".flac", ".pcm", ".wav"}


def _public_library_item(item: dict[str, Any]) -> dict[str, Any]:
    """Remove private desktop paths from the browser-facing library contract."""
    path = item.get("path")
    public_item = {key: value for key, value in item.items() if key not in {"path", "folder"}}
    if isinstance(path, str):
        public_item["cache_id"] = cache.cache_id_for_path(path)
    return public_item


def _public_library_response(data: dict[str, Any]) -> dict[str, Any]:
    response = dict(data)
    items = response.get("items", [])
    response["items"] = [_public_library_item(item) for item in items if isinstance(item, dict)]
    return response


def _offline_library_items(
    items: list[dict[str, Any]],
    search: str,
    scope: str,
    category: str,
    category_mode: str,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    query = search.strip().casefold()
    selected_categories = {value.strip().casefold() for value in category.split(",") if value.strip()}
    filtered: list[dict[str, Any]] = []
    for item in items:
        name = str(item.get("name", ""))
        path = str(item.get("path", ""))
        categories = [str(value) for value in item.get("categories", [])]
        category_keys = {value.casefold() for value in categories}
        if selected_categories:
            if category_mode == "all" and not selected_categories.issubset(category_keys):
                continue
            if category_mode != "all" and selected_categories.isdisjoint(category_keys):
                continue
        if query:
            if scope == "name":
                haystack = name
            elif scope == "tag":
                haystack = " ".join(categories)
            elif scope == "path":
                haystack = path
            else:
                haystack = " ".join([name, *categories, path])
            if query not in haystack.casefold():
                continue
        filtered.append(item)
    start = max(0, offset)
    return (filtered[start:] if limit <= 0 else filtered[start : start + limit], len(filtered))


def _public_status(status: dict[str, Any]) -> dict[str, Any]:
    public_status = {key: value for key, value in status.items() if key != "current_path"}
    path = status.get("current_path")
    if isinstance(path, str) and path:
        public_status["current_cache_id"] = cache.cache_id_for_path(path)
    return public_status


def _remember_live_library_items(data: dict[str, Any]) -> None:
    for item in data.get("items", []):
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if isinstance(path, str):
            _live_library_paths[cache.cache_id_for_path(path)] = path


def _manifest_path_for_cache_id(cache_id: str) -> str:
    item = cache.find_manifest_item(cache_id)
    path = item.get("path") if item is not None else None
    if not isinstance(path, str):
        path = _live_library_paths.get(cache_id)
    if not isinstance(path, str):
        raise HTTPException(status_code=404, detail="jingle_not_found")
    return path


def _transcode_preview(source: Path, destination: Path) -> bool:
    temporary = destination.with_name(destination.stem + ".part" + destination.suffix)
    try:
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-y", "-i", str(source), "-map", "0:a:0",
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(temporary),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if temporary.stat().st_size == 0:
            return False
        temporary.replace(destination)
        return True
    except (FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        logger.warning("Could not create compressed preview for %s: %s", source.name, exc)
        return False
    finally:
        temporary.unlink(missing_ok=True)


async def _guest_preview_file(cache_id: str, source: Path) -> Path:
    if source.suffix.lower() not in _LOSSLESS_SUFFIXES:
        return source
    preview = cache.resolve_cached_audio_path(cache.preview_cache_relpath(cache_id))
    if preview is not None:
        return preview
    async with _preview_transcode_lock:
        preview = cache.resolve_cached_audio_path(cache.preview_cache_relpath(cache_id))
        if preview is not None:
            return preview
        destination = cache.audio_dir() / cache.preview_cache_relpath(cache_id)
        if await asyncio.to_thread(_transcode_preview, source, destination):
            return destination
    return source


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
    return {"agent_connected": True, **_public_status(status)}


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
            _remember_live_library_items(data)
            return {"agent_connected": True, **_public_library_response(data)}
        except (AgentNotConnected, AgentCommandTimeout):
            pass
    manifest = cache.read_manifest()
    if manifest is None:
        return {"agent_connected": False, "offline": True, "items": [], "message": "no jingle machine currently connected"}
    manifest_items = [item for item in manifest.get("items", []) if isinstance(item, dict)]
    items, total = _offline_library_items(manifest_items, search, scope, category, category_mode, limit, offset)
    return {
        "agent_connected": False,
        "offline": True,
        "offline_filter_applied": True,
        "generated_at": manifest.get("generated_at"),
        "items": [_public_library_item(item) for item in items],
        "total": total,
    }


@app.get("/api/audio/{cache_id}")
async def get_audio(cache_id: str, user=Depends(get_current_user)):
    """Serve jingle audio for browser preview, streaming progressively instead of buffering the whole file.

    A cached copy (from a manual Offline Cache Backup, or a previous live preview that got
    written through to disk) is served straight from disk regardless of agent connection -
    this is what lets preview keep working even while the desktop app isn't running.
    Cache misses are pulled from the connected agent chunk-by-chunk and written to disk as
    they arrive, so only the first preview of a given file ever pays the agent round-trip.
    """
    import mimetypes

    path = _manifest_path_for_cache_id(cache_id)

    live_relpath = cache.live_cache_relpath(path)
    already_cached = cache.resolve_cached_audio_path(live_relpath)
    if already_cached is not None:
        served_file = already_cached
        if user["role"] == "user":
            served_file = await _guest_preview_file(cache_id, already_cached)
        media_type = mimetypes.guess_type(str(served_file))[0] or "application/octet-stream"
        return FileResponse(
            str(served_file),
            media_type=media_type,
            headers={
                "X-Jingle-Cache": "hit",
                "X-Jingle-Cache-Bytes": str(served_file.stat().st_size),
                "X-Jingle-Preview-Encoding": "aac" if served_file != already_cached else "original",
            },
        )

    if not agent_manager.is_connected():
        raise HTTPException(status_code=409, detail="agent_not_connected")
    try:
        stream = await agent_manager.request_audio(path)
    except AgentNotConnected:
        raise HTTPException(status_code=409, detail="agent_not_connected")
    except AgentCommandTimeout:
        raise HTTPException(status_code=504, detail="agent_audio_timeout")
    except AgentAudioError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    async def _tee_to_cache_and_client():
        dest = cache.audio_dir() / live_relpath
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest.with_name(dest.name + ".part")
        bytes_written = 0
        wrote_ok = False
        try:
            with open(tmp_path, "wb") as tmp_file:
                async for chunk in stream.chunks:
                    tmp_file.write(chunk)
                    bytes_written += len(chunk)
                    yield chunk
            # Only trust this as a complete, servable cache entry if the agent actually
            # delivered as many bytes as it told us to expect up front - a partial/corrupted
            # transfer that still ends without raising (rather than erroring out) must not be
            # silently promoted into the cache, or every future preview would be truncated too.
            wrote_ok = stream.size > 0 and bytes_written == stream.size
        finally:
            if wrote_ok:
                tmp_path.replace(dest)
            else:
                tmp_path.unlink(missing_ok=True)
                logger.warning(
                    "Incomplete live audio transfer for cache ID %s: received %d of %d bytes",
                    cache_id,
                    bytes_written,
                    stream.size,
                )

    # A known Content-Length (rather than chunked transfer-encoding) is what lets browsers -
    # Safari/iOS in particular - treat this as a normal progressive download instead of
    # buffering the whole response before starting playback.
    headers = {"Content-Length": str(stream.size)} if stream.size else None
    return StreamingResponse(_tee_to_cache_and_client(), media_type=stream.content_type, headers=headers)


@app.head("/api/audio/{cache_id}")
async def get_audio_info(cache_id: str, user=Depends(get_current_user)) -> Response:
    """Report whether browser-local preview can start without streaming the file."""
    path = _manifest_path_for_cache_id(cache_id)
    cached_file = cache.resolve_cached_audio_path(cache.live_cache_relpath(path))
    if cached_file is not None:
        return Response(
            headers={"X-Jingle-Cache": "hit", "X-Jingle-Cache-Bytes": str(cached_file.stat().st_size)},
        )
    if not agent_manager.is_connected():
        raise HTTPException(status_code=409, detail="agent_not_connected")
    return Response(headers={"X-Jingle-Cache": "miss"})


# --- playback control (browser-facing, relayed to the connected agent) -------

class PlayBody(BaseModel):
    cache_id: str
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
    kwargs["path"] = _manifest_path_for_cache_id(body.cache_id)
    del kwargs["cache_id"]
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
                payload = {"agent_connected": True, **_public_status(agent_manager.last_status() or {})}
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
    missing_or_incomplete = []
    for item in items:
        path = item.get("path") if isinstance(item, dict) else None
        expected_size = item.get("size_bytes") if isinstance(item, dict) else None
        if not isinstance(path, str) or not isinstance(expected_size, int):
            raise HTTPException(status_code=400, detail="invalid_manifest_item")
        cached_file = cache.resolve_cached_audio_path(cache.live_cache_relpath(path))
        if cached_file is None or cached_file.stat().st_size != expected_size:
            missing_or_incomplete.append(item.get("name", path))
    if missing_or_incomplete:
        raise HTTPException(
            status_code=409,
            detail={"error": "cache_incomplete", "items": missing_or_incomplete[:10], "count": len(missing_or_incomplete)},
        )
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


@app.get("/agent/cache/status")
async def cache_status(request: Request) -> dict[str, Any]:
    """Existing cached-file sizes, keyed by relpath, so the desktop app's Offline Cache Backup
    can skip re-uploading files that are already present and unchanged."""
    _require_device_token(request)
    files = {
        str(entry.relative_to(cache.audio_dir())): entry.stat().st_size
        for entry in cache.audio_dir().rglob("*")
        if entry.is_file() and not entry.name.endswith(".part")
    }
    return {"ok": True, "files": files}


# --- static webclient (mounted last so it never shadows /api or /agent) -----

if config.WEBCLIENT_DIR.exists():
    app.mount("/", StaticFiles(directory=str(config.WEBCLIENT_DIR), html=True), name="webclient")
