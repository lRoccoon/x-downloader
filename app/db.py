import sqlite3
import threading
import time
import uuid
from typing import Optional

from . import config

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_FILE, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id          TEXT PRIMARY KEY,
                url         TEXT NOT NULL,
                title       TEXT,
                format_id   TEXT,
                resolution  TEXT,
                status      TEXT NOT NULL,        -- queued | downloading | finished | error
                progress    REAL DEFAULT 0,       -- 0..100
                speed       TEXT,
                eta         TEXT,
                filename    TEXT,
                filepath    TEXT,
                filesize    INTEGER,
                error       TEXT,
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL
            )
            """
        )
        _conn.commit()
    return _conn


def create_task(url: str, title: str, format_id: str, resolution: str) -> str:
    task_id = uuid.uuid4().hex[:12]
    now = time.time()
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO tasks (id, url, title, format_id, resolution, status, "
            "progress, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (task_id, url, title, format_id, resolution, "queued", 0, now, now),
        )
        conn.commit()
    return task_id


def update_task(task_id: str, only_if_status: str | None = None, **fields) -> None:
    """only_if_status: 仅当任务当前处于该状态时才更新，用于并发写入方
    （如进度轮询线程）避免覆盖已落定的终态/暂停态。"""
    if not fields:
        return
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    sql = f"UPDATE tasks SET {cols} WHERE id = ?"
    values = list(fields.values()) + [task_id]
    if only_if_status:
        sql += " AND status = ?"
        values.append(only_if_status)
    with _lock:
        conn = _connect()
        conn.execute(sql, values)
        conn.commit()


def get_task(task_id: str) -> Optional[dict]:
    with _lock:
        conn = _connect()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def list_tasks(limit: int = 100) -> list:
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def delete_task(task_id: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()


def clear_tasks() -> None:
    with _lock:
        conn = _connect()
        conn.execute("DELETE FROM tasks")
        conn.commit()
