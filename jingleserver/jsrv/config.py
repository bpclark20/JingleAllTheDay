"""Runtime configuration for jingleserver, sourced entirely from environment variables.

Kept in one place so both the FastAPI app and the CLI agree on where the
SQLite DB, offline jingle cache, and webclient static files live.
"""

from __future__ import annotations

import os
from pathlib import Path

# Root folder for persistent state (SQLite DB + default cache location).
DATA_DIR = Path(os.environ.get("JINGLESERVER_DATA_DIR", "/var/lib/jingleserver")).expanduser()

DB_PATH = Path(os.environ.get("JINGLESERVER_DB_PATH", str(DATA_DIR / "jingleserver.db"))).expanduser()

# Folder where offline-cached jingle audio files + library_cache.json are stored.
CACHE_DIR = Path(os.environ.get("JINGLESERVER_CACHE_DIR", str(DATA_DIR / "cache"))).expanduser()

# Static webclient files (index.html/app.js/styles.css) to serve at "/".
WEBCLIENT_DIR = Path(
    os.environ.get("JINGLESERVER_WEBCLIENT_DIR", str(Path(__file__).resolve().parent.parent.parent / "webclient"))
).expanduser()

HOST = os.environ.get("JINGLESERVER_HOST", "127.0.0.1")
PORT = int(os.environ.get("JINGLESERVER_PORT", "47030"))

SESSION_COOKIE_NAME = "jsid"
SESSION_TTL_HOURS = int(os.environ.get("JINGLESERVER_SESSION_TTL_HOURS", "12"))

# How long to wait for the connected desktop agent to answer a relayed command.
AGENT_COMMAND_TIMEOUT_SECONDS = float(os.environ.get("JINGLESERVER_AGENT_TIMEOUT", "6.0"))


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
