"""SQLite persistence for jingleserver: users, sessions, and agent devices.

Uses stdlib sqlite3 directly (no ORM) since the schema is small and both the
FastAPI app and the CLI need to open the same file independently, including
while the service is stopped.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'user')),
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT UNIQUE NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER
);
"""


def init_db(db_path: Path | None = None) -> None:
    config.ensure_dirs()
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def _connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path or config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# --- users -----------------------------------------------------------------

def create_user(username: str, password_hash: str, role: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (username, password_hash, role, int(time.time())),
        )


def get_user_by_username(username: str) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def get_user_by_id(user_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def list_users() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute("SELECT * FROM users ORDER BY username").fetchall()


def delete_user(username: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        return cur.rowcount > 0


def set_role(username: str, role: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
        return cur.rowcount > 0


def set_password(username: str, password_hash: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?", (password_hash, username)
        )
        return cur.rowcount > 0


# --- sessions ----------------------------------------------------------------

def create_session(session_id: str, user_id: int, ttl_hours: int) -> None:
    now = int(time.time())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (session_id, user_id, now, now + ttl_hours * 3600),
        )


def get_session_user(session_id: str) -> sqlite3.Row | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id "
            "WHERE sessions.id = ? AND sessions.expires_at > ?",
            (session_id, int(time.time())),
        ).fetchone()
        return row


def delete_session(session_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def delete_sessions_for_user(user_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


# --- devices -----------------------------------------------------------------

def create_device(label: str, token_hash: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO devices (label, token_hash, created_at) VALUES (?, ?, ?)",
            (label, token_hash, int(time.time())),
        )


def get_device_by_token_hash(token_hash: str) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM devices WHERE token_hash = ?", (token_hash,)).fetchone()


def touch_device(device_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (int(time.time()), device_id))


def list_devices() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute("SELECT * FROM devices ORDER BY label").fetchall()


def delete_device(label: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM devices WHERE label = ?", (label,))
        return cur.rowcount > 0
