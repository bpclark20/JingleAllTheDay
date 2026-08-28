"""Offline jingle cache: a JSON library snapshot plus the cached audio files
themselves, written to `config.CACHE_DIR` by the desktop app's "Offline Cache
Backup" action and read by the webapp when no agent is connected.

Also used as a write-through cache for audio streamed live from a connected
agent (see `web.get_audio`): the first guest to preview a jingle triggers a
progressive relay from the desktop app, and the bytes are saved here as they
stream so later requests can be served directly (with Range/seek support)
without touching the agent again.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from . import config

_MANIFEST_NAME = "library_cache.json"


def manifest_path() -> Path:
    return config.CACHE_DIR / _MANIFEST_NAME


def read_manifest() -> dict[str, Any] | None:
    path = manifest_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_manifest(items: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = {"generated_at": int(time.time()), "items": items}
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path().write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def audio_dir() -> Path:
    path = config.CACHE_DIR / "audio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_audio_file(relpath: str, content: bytes) -> Path:
    """`relpath` must already be sanitized (no `..`, no absolute paths) by the caller."""
    dest = audio_dir() / relpath
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return dest


def live_cache_relpath(path: str) -> str:
    """Stable, path-derived filename for write-through caching of live agent-streamed audio."""
    suffix = Path(path).suffix
    return "live_" + hashlib.sha256(path.encode("utf-8")).hexdigest() + suffix


def is_traversal_unsafe(relpath: str) -> bool:
    """True if any path segment is exactly '..' (a real traversal attempt).

    A plain substring check for '..' is too strict - sanitized filenames can
    legitimately contain a literal '..' (e.g. 'etc.' + '.mp3' -> 'etc..mp3')
    without ever being a path-traversal attempt.
    """
    return any(part == ".." for part in relpath.replace("\\", "/").split("/"))


def resolve_cached_audio_path(relpath: str) -> Path | None:
    """Return the cached file for `relpath` if it exists and stays within the cache's audio dir."""
    if not relpath or is_traversal_unsafe(relpath) or Path(relpath).is_absolute():
        return None
    candidate = (audio_dir() / relpath).resolve()
    if audio_dir().resolve() not in candidate.parents:
        return None
    return candidate if candidate.exists() else None

