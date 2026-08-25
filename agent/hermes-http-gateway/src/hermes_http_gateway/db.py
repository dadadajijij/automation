from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import Settings

_schema_lock = threading.Lock()
_initialized = False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(settings: Settings) -> None:
    global _initialized
    if _initialized:
        return
    with _schema_lock:
        if _initialized:
            return
        with _connect(settings.database_url) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                  user_id TEXT PRIMARY KEY,
                  default_hermes_profile TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                  external_session_id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL,
                  hermes_session_id TEXT,
                  hermes_profile TEXT NOT NULL,
                  title TEXT,
                  status TEXT NOT NULL,
                  last_error TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_conversations_user_updated
                  ON conversations(user_id, updated_at DESC);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_hermes_session
                  ON conversations(hermes_session_id)
                  WHERE hermes_session_id IS NOT NULL;

                CREATE TABLE IF NOT EXISTS messages (
                  id TEXT PRIMARY KEY,
                  external_session_id TEXT NOT NULL,
                  user_id TEXT NOT NULL,
                  role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                  kind TEXT NOT NULL CHECK(kind IN ('chat', 'error')),
                  content TEXT NOT NULL,
                  model TEXT,
                  provider TEXT,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY (external_session_id) REFERENCES conversations(external_session_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
                  ON messages(external_session_id, created_at);

                CREATE VIRTUAL TABLE IF NOT EXISTS session_search USING fts5(
                  user_id UNINDEXED,
                  external_session_id UNINDEXED,
                  message_id UNINDEXED,
                  role UNINDEXED,
                  kind UNINDEXED,
                  created_at UNINDEXED,
                  title,
                  content,
                  tokenize = 'unicode61'
                );

                CREATE TABLE IF NOT EXISTS admin_sessions (
                  session_token TEXT PRIMARY KEY,
                  username TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  expires_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_admin_sessions_expires_at
                  ON admin_sessions(expires_at);
                """
            )
        _initialized = True


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def get_user(settings: Settings, user_id: str) -> dict[str, Any] | None:
    with _connect(settings.database_url) as conn:
        row = conn.execute(
            "SELECT user_id, default_hermes_profile, created_at, updated_at FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return _row_to_dict(row)


def upsert_user(settings: Settings, user_id: str, default_hermes_profile: str) -> dict[str, Any]:
    existing = get_user(settings, user_id)
    now = now_iso()
    with _connect(settings.database_url) as conn:
        if existing is None:
            conn.execute(
                """
                INSERT INTO users (user_id, default_hermes_profile, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, default_hermes_profile, now, now),
            )
        else:
            conn.execute(
                """
                UPDATE users
                SET default_hermes_profile = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (default_hermes_profile, now, user_id),
            )
    return get_user(settings, user_id) or {
        "user_id": user_id,
        "default_hermes_profile": default_hermes_profile,
        "created_at": now,
        "updated_at": now,
    }


def get_or_create_user(settings: Settings, user_id: str) -> dict[str, Any]:
    user = get_user(settings, user_id)
    if user is not None:
        return user
    return upsert_user(settings, user_id, settings.hermes_profile_default)


def set_user_default_profile(settings: Settings, user_id: str, profile: str) -> dict[str, Any]:
    return upsert_user(settings, user_id, profile)


def create_admin_session(
    settings: Settings,
    *,
    username: str,
    ttl_seconds: int,
) -> dict[str, Any]:
    session_token = uuid.uuid4().hex
    now = now_iso()
    expires_at = datetime.now(timezone.utc).timestamp() + ttl_seconds
    expires_iso = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()
    with _connect(settings.database_url) as conn:
        conn.execute(
            """
            INSERT INTO admin_sessions (session_token, username, created_at, updated_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_token, username, now, now, expires_iso),
        )
    return {
        "session_token": session_token,
        "username": username,
        "created_at": now,
        "updated_at": now,
        "expires_at": expires_iso,
    }


def get_admin_session(settings: Settings, session_token: str) -> dict[str, Any] | None:
    now = now_iso()
    with _connect(settings.database_url) as conn:
        row = conn.execute(
            """
            SELECT session_token, username, created_at, updated_at, expires_at
            FROM admin_sessions
            WHERE session_token = ? AND expires_at > ?
            """,
            (session_token, now),
        ).fetchone()
        return _row_to_dict(row)


def touch_admin_session(settings: Settings, session_token: str, ttl_seconds: int) -> dict[str, Any] | None:
    now = now_iso()
    expires_at = datetime.now(timezone.utc).timestamp() + ttl_seconds
    expires_iso = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()
    with _connect(settings.database_url) as conn:
        conn.execute(
            """
            UPDATE admin_sessions
            SET updated_at = ?, expires_at = ?
            WHERE session_token = ?
            """,
            (now, expires_iso, session_token),
        )
    return get_admin_session(settings, session_token)


def delete_admin_session(settings: Settings, session_token: str) -> None:
    with _connect(settings.database_url) as conn:
        conn.execute("DELETE FROM admin_sessions WHERE session_token = ?", (session_token,))


def count_users(settings: Settings) -> int:
    with _connect(settings.database_url) as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()
        return int((row["count"] if row else 0) or 0)


def count_conversations(settings: Settings) -> int:
    with _connect(settings.database_url) as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM conversations").fetchone()
        return int((row["count"] if row else 0) or 0)


def count_messages(settings: Settings) -> int:
    with _connect(settings.database_url) as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM messages").fetchone()
        return int((row["count"] if row else 0) or 0)


def list_users(
    settings: Settings,
    limit: int = 100,
    offset: int = 0,
    query: str | None = None,
) -> list[dict[str, Any]]:
    where = ""
    params: list[Any] = []
    if query:
        where = """
        WHERE u.user_id LIKE ? COLLATE NOCASE
           OR u.default_hermes_profile LIKE ? COLLATE NOCASE
        """
        like = f"%{query.strip()}%"
        params.extend([like, like])
    params.extend([limit, offset])
    with _connect(settings.database_url) as conn:
        rows = conn.execute(
            f"""
            SELECT
              u.user_id,
              u.default_hermes_profile,
              u.created_at,
              u.updated_at,
              (SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.user_id) AS conversation_count,
              (SELECT COUNT(*) FROM messages m WHERE m.user_id = u.user_id) AS message_count
            FROM users u
            {where}
            ORDER BY u.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def list_all_conversations(
    settings: Settings,
    limit: int = 100,
    offset: int = 0,
    query: str | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if query:
        clauses.append(
            """
            (
              c.external_session_id LIKE ? COLLATE NOCASE
              OR c.user_id LIKE ? COLLATE NOCASE
              OR c.title LIKE ? COLLATE NOCASE
              OR c.hermes_profile LIKE ? COLLATE NOCASE
            )
            """
        )
        like = f"%{query.strip()}%"
        params.extend([like, like, like, like])
    if user_id:
        clauses.append("c.user_id = ?")
        params.append(user_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    with _connect(settings.database_url) as conn:
        rows = conn.execute(
            f"""
            SELECT c.external_session_id, c.user_id, c.hermes_session_id, c.hermes_profile,
                   c.title, c.status, c.last_error, c.created_at, c.updated_at,
                   COUNT(m.id) AS message_count
            FROM conversations c
            LEFT JOIN messages m ON m.external_session_id = c.external_session_id
            {where}
            GROUP BY c.external_session_id
            ORDER BY c.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def get_conversation_by_session(settings: Settings, external_session_id: str) -> dict[str, Any] | None:
    with _connect(settings.database_url) as conn:
        row = conn.execute(
            """
            SELECT external_session_id, user_id, hermes_session_id, hermes_profile,
                   title, status, last_error, created_at, updated_at
            FROM conversations
            WHERE external_session_id = ?
            """,
            (external_session_id,),
        ).fetchone()
        return _row_to_dict(row)


def list_messages_by_session(settings: Settings, external_session_id: str) -> list[dict[str, Any]]:
    with _connect(settings.database_url) as conn:
        rows = conn.execute(
            """
            SELECT id, external_session_id, user_id, role, kind, content, model, provider, created_at
            FROM messages
            WHERE external_session_id = ?
            ORDER BY created_at ASC
            """,
            (external_session_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def search_messages_admin(
    settings: Settings,
    *,
    query: str,
    user_id: str | None = None,
    external_session_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    pattern = f"%{query.strip()}%"
    if pattern == "%%":
        return []
    clauses: list[str] = [
        """
        (
          s.title LIKE ? COLLATE NOCASE
          OR s.content LIKE ? COLLATE NOCASE
          OR s.role LIKE ? COLLATE NOCASE
          OR s.kind LIKE ? COLLATE NOCASE
        )
        """
    ]
    params: list[Any] = [pattern, pattern, pattern, pattern]
    if user_id:
        clauses.append("s.user_id = ?")
        params.append(user_id)
    if external_session_id:
        clauses.append("s.external_session_id = ?")
        params.append(external_session_id)
    where = " AND ".join(clauses)
    params.append(limit)
    with _connect(settings.database_url) as conn:
        rows = conn.execute(
            f"""
            SELECT s.user_id, s.external_session_id, c.hermes_session_id, c.hermes_profile,
                   c.title AS conversation_title, s.message_id, s.role, s.kind,
                   s.created_at, s.title, s.content
            FROM session_search s
            JOIN conversations c
              ON c.external_session_id = s.external_session_id
             AND c.user_id = s.user_id
            WHERE {where}
            ORDER BY s.created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def create_conversation(
    settings: Settings,
    *,
    external_session_id: str,
    user_id: str,
    hermes_profile: str,
    title: str | None,
) -> dict[str, Any]:
    now = now_iso()
    with _connect(settings.database_url) as conn:
        conn.execute(
            """
            INSERT INTO conversations (
              external_session_id, user_id, hermes_session_id, hermes_profile,
              title, status, last_error, created_at, updated_at
            )
            VALUES (?, ?, NULL, ?, ?, 'pending', NULL, ?, ?)
            """,
            (external_session_id, user_id, hermes_profile, title, now, now),
        )
        conn.execute(
            """
            INSERT INTO session_search (
              user_id, external_session_id, message_id, role, kind, created_at, title, content
            )
            VALUES (?, ?, NULL, NULL, 'conversation', ?, ?, ?)
            """,
            (user_id, external_session_id, now, title, title or ""),
        )
    return get_conversation(settings, user_id, external_session_id) or {}


def get_conversation(settings: Settings, user_id: str, external_session_id: str) -> dict[str, Any] | None:
    with _connect(settings.database_url) as conn:
        row = conn.execute(
            """
            SELECT external_session_id, user_id, hermes_session_id, hermes_profile,
                   title, status, last_error, created_at, updated_at
            FROM conversations
            WHERE user_id = ? AND external_session_id = ?
            """,
            (user_id, external_session_id),
        ).fetchone()
        return _row_to_dict(row)


def list_conversations(settings: Settings, user_id: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    with _connect(settings.database_url) as conn:
        rows = conn.execute(
            """
            SELECT c.external_session_id, c.user_id, c.hermes_session_id, c.hermes_profile,
                   c.title, c.status, c.last_error, c.created_at, c.updated_at,
                   COUNT(m.id) AS message_count
            FROM conversations c
            LEFT JOIN messages m ON m.external_session_id = c.external_session_id
            WHERE c.user_id = ?
            GROUP BY c.external_session_id
            ORDER BY c.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]


def list_messages(settings: Settings, user_id: str, external_session_id: str) -> list[dict[str, Any]]:
    with _connect(settings.database_url) as conn:
        rows = conn.execute(
            """
            SELECT id, external_session_id, user_id, role, kind, content, model, provider, created_at
            FROM messages
            WHERE user_id = ? AND external_session_id = ?
            ORDER BY created_at ASC
            """,
            (user_id, external_session_id),
        ).fetchall()
        return [dict(row) for row in rows]


def append_message(
    settings: Settings,
    *,
    external_session_id: str,
    user_id: str,
    role: str,
    kind: str,
    content: str,
    title: str | None = None,
    model: str | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    message = {
        "id": str(uuid.uuid4()),
        "external_session_id": external_session_id,
        "user_id": user_id,
        "role": role,
        "kind": kind,
        "content": content,
        "model": model,
        "provider": provider,
        "created_at": now_iso(),
    }
    with _connect(settings.database_url) as conn:
        conn.execute(
            """
            INSERT INTO messages (
              id, external_session_id, user_id, role, kind, content, model, provider, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message["id"],
                message["external_session_id"],
                message["user_id"],
                message["role"],
                message["kind"],
                message["content"],
                message["model"],
                message["provider"],
                message["created_at"],
            ),
        )
        conn.execute(
            """
            UPDATE conversations
            SET updated_at = ?
            WHERE external_session_id = ? AND user_id = ?
            """,
            (message["created_at"], external_session_id, user_id),
        )
        conn.execute(
            """
            INSERT INTO session_search (
              user_id, external_session_id, message_id, role, kind, created_at, title, content
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                external_session_id,
                message["id"],
                role,
                kind,
                message["created_at"],
                title,
                content,
            ),
        )
    return message


def delete_message(settings: Settings, *, external_session_id: str, user_id: str, message_id: str) -> None:
    with _connect(settings.database_url) as conn:
        conn.execute(
            """
            DELETE FROM messages
            WHERE id = ? AND external_session_id = ? AND user_id = ?
            """,
            (message_id, external_session_id, user_id),
        )
        conn.execute(
            """
            DELETE FROM session_search
            WHERE message_id = ? AND external_session_id = ? AND user_id = ?
            """,
            (message_id, external_session_id, user_id),
        )


def update_conversation(
    settings: Settings,
    *,
    external_session_id: str,
    user_id: str,
    hermes_session_id: str | None = None,
    hermes_profile: str | None = None,
    title: str | None = None,
    status: str | None = None,
    last_error: str | None = None,
) -> dict[str, Any] | None:
    now = now_iso()
    updates: list[str] = []
    values: list[Any] = []
    if hermes_session_id is not None:
        updates.append("hermes_session_id = ?")
        values.append(hermes_session_id)
    if hermes_profile is not None:
        updates.append("hermes_profile = ?")
        values.append(hermes_profile)
    if title is not None:
        updates.append("title = ?")
        values.append(title)
    if status is not None:
        updates.append("status = ?")
        values.append(status)
    if last_error is not None:
        updates.append("last_error = ?")
        values.append(last_error)
    updates.append("updated_at = ?")
    values.append(now)
    values.extend([external_session_id, user_id])

    with _connect(settings.database_url) as conn:
        conn.execute(
            f"""
            UPDATE conversations
            SET {", ".join(updates)}
            WHERE external_session_id = ? AND user_id = ?
            """,
            values,
        )
    return get_conversation(settings, user_id, external_session_id)


def search_messages(
    settings: Settings,
    *,
    user_id: str,
    query: str,
    external_session_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    pattern = f"%{query.strip()}%"
    if pattern == "%%":
        return []
    with _connect(settings.database_url) as conn:
        rows = conn.execute(
            """
            SELECT s.user_id, s.external_session_id, c.hermes_session_id, c.hermes_profile,
                   c.title AS conversation_title, s.message_id, s.role, s.kind,
                   s.created_at, s.title, s.content, m.model, m.provider
            FROM session_search s
            JOIN conversations c
              ON c.external_session_id = s.external_session_id
             AND c.user_id = s.user_id
            LEFT JOIN messages m
              ON m.id = s.message_id
            WHERE s.user_id = ?
              AND (? IS NULL OR s.external_session_id = ?)
              AND (
                s.title LIKE ? COLLATE NOCASE
                OR s.content LIKE ? COLLATE NOCASE
                OR s.role LIKE ? COLLATE NOCASE
                OR s.kind LIKE ? COLLATE NOCASE
              )
            ORDER BY s.created_at DESC
            LIMIT ?
            """,
            (user_id, external_session_id, external_session_id, pattern, pattern, pattern, pattern, limit),
        ).fetchall()
        return [dict(row) for row in rows]
