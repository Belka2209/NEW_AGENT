from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import settings

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                due_at TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_session(title: str = "Новый чат") -> dict[str, Any]:
    session_id = str(uuid.uuid4())
    stamp = _now()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, title, stamp, stamp),
        )
    return {"id": session_id, "title": title, "created_at": stamp, "updated_at": stamp}


def list_sessions() -> list[dict[str, Any]]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def get_session(session_id: str) -> dict[str, Any] | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    return dict(row) if row else None


def delete_session(session_id: str) -> bool:
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return cur.rowcount > 0


def rename_session(session_id: str, title: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), session_id),
        )


def touch_session(session_id: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (_now(), session_id),
        )


def add_message(session_id: str, message: dict[str, Any]) -> dict[str, Any]:
    stamp = _now()
    content = message.get("content") or ""
    if isinstance(content, list):
        content = json.dumps(content, ensure_ascii=False)
    with _lock, _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO messages (session_id, role, content, raw_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session_id,
                message["role"],
                content,
                json.dumps(message, ensure_ascii=False),
                stamp,
            ),
        )
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (stamp, session_id),
        )
        msg_id = cur.lastrowid
    return {
        "id": msg_id,
        "session_id": session_id,
        "role": message["role"],
        "content": content,
        "raw": message,
        "created_at": stamp,
    }


def list_messages(session_id: str) -> list[dict[str, Any]]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, session_id, role, content, raw_json, created_at
            FROM messages WHERE session_id = ? ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["raw"] = json.loads(item.pop("raw_json"))
        result.append(item)
    return result


def llm_history(session_id: str) -> list[dict[str, Any]]:
    return [item["raw"] for item in list_messages(session_id)]


def add_memory(text: str) -> dict[str, Any]:
    stamp = _now()
    with _lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO memories (text, created_at) VALUES (?, ?)",
            (text.strip(), stamp),
        )
        return {"id": cur.lastrowid, "text": text.strip(), "created_at": stamp}


def list_memories() -> list[dict[str, Any]]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT id, text, created_at FROM memories ORDER BY id ASC"
        ).fetchall()
    return [dict(row) for row in rows]


def delete_memory(memory_id: int) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return cur.rowcount > 0


def add_reminder(text: str, due_at: str) -> dict[str, Any]:
    stamp = _now()
    with _lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (text, due_at, done, created_at) VALUES (?, ?, 0, ?)",
            (text.strip(), due_at, stamp),
        )
        return {"id": cur.lastrowid, "text": text.strip(), "due_at": due_at}


def list_reminders(include_done: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT id, text, due_at, done, created_at FROM reminders"
    if not include_done:
        sql += " WHERE done = 0"
    sql += " ORDER BY due_at ASC"
    with _lock, _connect() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(row) for row in rows]


def complete_reminder(reminder_id: int) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE reminders SET done = 1 WHERE id = ?",
            (reminder_id,),
        )
        return cur.rowcount > 0
