from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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

                CREATE TABLE IF NOT EXISTS request_events (
                  id TEXT PRIMARY KEY,
                  external_session_id TEXT NOT NULL,
                  user_id TEXT NOT NULL,
                  prompt TEXT NOT NULL,
                  status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed', 'superseded')),
                  http_status INTEGER,
                  error_detail TEXT,
                  model TEXT,
                  provider TEXT,
                  request_body TEXT,
                  runtime_audit TEXT,
                  created_at TEXT NOT NULL,
                  completed_at TEXT,
                  FOREIGN KEY (external_session_id) REFERENCES conversations(external_session_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_request_events_conversation_created
                  ON request_events(external_session_id, created_at);

                CREATE TABLE IF NOT EXISTS request_stage_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  request_event_id TEXT NOT NULL,
                  stage TEXT NOT NULL,
                  duration_ms REAL NOT NULL,
                  detail TEXT,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY (request_event_id) REFERENCES request_events(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_request_stage_events_request_created
                  ON request_stage_events(request_event_id, created_at, id);

                CREATE TABLE IF NOT EXISTS user_private_skill_bindings (
                  user_id TEXT NOT NULL,
                  skill_id TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY (user_id, skill_id),
                  FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_user_private_skill_bindings_user
                  ON user_private_skill_bindings(user_id, skill_id);

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
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(request_events)").fetchall()
            }
            if "request_body" not in columns:
                conn.execute("ALTER TABLE request_events ADD COLUMN request_body TEXT")
            if "runtime_audit" not in columns:
                conn.execute("ALTER TABLE request_events ADD COLUMN runtime_audit TEXT")
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


def list_user_private_skill_bindings(settings: Settings, user_id: str) -> list[dict[str, Any]]:
    with _connect(settings.database_url) as conn:
        rows = conn.execute(
            """
            SELECT user_id, skill_id, created_at, updated_at
            FROM user_private_skill_bindings
            WHERE user_id = ?
            ORDER BY skill_id ASC
            """,
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def replace_user_private_skill_bindings(
    settings: Settings,
    *,
    user_id: str,
    skill_ids: Iterable[str],
) -> list[dict[str, Any]]:
    normalized = sorted({str(skill_id).strip() for skill_id in skill_ids if str(skill_id).strip()})
    now = now_iso()
    with _connect(settings.database_url) as conn:
        conn.execute(
            "DELETE FROM user_private_skill_bindings WHERE user_id = ?",
            (user_id,),
        )
        conn.executemany(
            """
            INSERT INTO user_private_skill_bindings (user_id, skill_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            [(user_id, skill_id, now, now) for skill_id in normalized],
        )
    return list_user_private_skill_bindings(settings, user_id)


def update_user_private_skill_bindings(
    settings: Settings,
    *,
    user_id: str,
    add: Iterable[str],
    remove: Iterable[str],
) -> list[dict[str, Any]]:
    add_ids, remove_ids = set(add), set(remove)
    with _connect(settings.database_url) as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = {
            row["skill_id"] for row in conn.execute(
                "SELECT skill_id FROM user_private_skill_bindings WHERE user_id = ?", (user_id,)
            )
        }
        next_ids = (current | add_ids) - remove_ids
        if len(next_ids) > 10:
            raise ValueError("A user can have at most 10 skills")
        conn.executemany(
            "DELETE FROM user_private_skill_bindings WHERE user_id = ? AND skill_id = ?",
            [(user_id, skill_id) for skill_id in current - next_ids],
        )
        now = now_iso()
        conn.executemany(
            "INSERT INTO user_private_skill_bindings (user_id, skill_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
            [(user_id, skill_id, now, now) for skill_id in next_ids - current],
        )
        rows = conn.execute(
            "SELECT user_id, skill_id, created_at, updated_at FROM user_private_skill_bindings WHERE user_id = ? ORDER BY skill_id",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


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


def count_request_events(settings: Settings) -> int:
    with _connect(settings.database_url) as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM request_events").fetchone()
        return int((row["count"] if row else 0) or 0)


def latency_report(settings: Settings, *, now: datetime | None = None) -> dict[str, Any]:
    """Build rolling latency aggregates from completed gateway requests.

    The report intentionally uses UTC database timestamps and the recorded
    ``Hermes CLI 运行`` stage when available.  This is the closest structured
    approximation to Agent processing time until response-ready log events are
    persisted separately.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    end = now.astimezone(timezone.utc)
    start = end - timedelta(hours=168)
    trend_start = end - timedelta(days=28)
    with _connect(settings.database_url) as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.user_id, r.created_at, r.completed_at, r.status,
                   COALESCE((SELECT SUM(s.duration_ms) FROM request_stage_events s
                             WHERE s.request_event_id = r.id AND s.stage = 'Hermes CLI 运行'),
                            (julianday(r.completed_at) - julianday(r.created_at)) * 86400000.0) AS agent_ms,
                   (SELECT COUNT(*) FROM request_stage_events s WHERE s.request_event_id = r.id
                      AND s.stage = 'Hermes CLI 运行') AS stage_matches
            FROM request_events r
            WHERE r.status = 'completed' AND r.completed_at IS NOT NULL
              AND r.created_at >= ? AND r.created_at < ?
            ORDER BY r.created_at ASC
            """,
            (trend_start.isoformat(), end.isoformat()),
        ).fetchall()

    def parse(value: str) -> datetime:
        return datetime.fromisoformat(value).astimezone(timezone.utc)

    def values_for(left: datetime, right: datetime) -> list[dict[str, Any]]:
        return [dict(row) | {"created": parse(row["created_at"])} for row in rows
                if left <= parse(row["created_at"]) < right]

    def percentile(values: list[float], fraction: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        position = (len(ordered) - 1) * fraction
        lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    def duration_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
        values = [max(0.0, float(item["agent_ms"] or 0)) for item in items]
        return {
            "count": len(values),
            "average_ms": round(sum(values) / len(values), 1) if values else None,
            "median_ms": round(percentile(values, 0.5), 1) if values else None,
            "p90_ms": round(percentile(values, 0.9), 1) if values else None,
            "p95_ms": round(percentile(values, 0.95), 1) if values else None,
            "max_ms": round(max(values), 1) if values else None,
        }

    recent = values_for(start, end)
    distribution = []
    for label, low, high in [("≤30秒", 0, 30000), ("31–60秒", 30000, 60000),
                             ("1–2分钟", 60000, 120000), ("2–5分钟", 120000, 300000),
                             ("5–10分钟", 300000, 600000), (">10分钟", 600000, None)]:
        def in_bucket(item: dict[str, Any]) -> bool:
            value = float(item["agent_ms"] or 0)
            return (value <= high if high is not None else value > low) and (not low or value > low)
        count = sum(1 for item in recent if in_bucket(item))
        distribution.append({"label": label, "count": count,
                             "percentage": round(count / len(recent) * 100, 1) if recent else 0})

    users: dict[str, list[dict[str, Any]]] = {}
    for item in recent:
        users.setdefault(str(item["user_id"]), []).append(item)
    user_rows = []
    for user_id, items in sorted(users.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        summary = duration_summary(items)
        user_rows.append({"user_id": user_id, **summary})

    daily: dict[str, list[dict[str, Any]]] = {}
    for item in recent:
        daily.setdefault(item["created"].date().isoformat(), []).append(item)
    daily_rows = [{"date": day, **duration_summary(items)} for day, items in sorted(daily.items())]
    trends = []
    for index in range(4):
        left = end - timedelta(days=28 - index * 7)
        items = values_for(left, left + timedelta(days=7))
        trends.append({"period": {"start": left.isoformat(), "end": (left + timedelta(days=7)).isoformat()}, **duration_summary(items)})
    matched = sum(1 for item in recent if int(item["stage_matches"] or 0) > 0)
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat(), "timezone": "UTC"},
        "metric": "Hermes CLI 运行 stage; fallback to completed_at-created_at",
        "core": duration_summary(recent),
        "distribution": distribution,
        "users": user_rows,
        "daily": daily_rows,
        "trends": trends,
        "api_calls": None,
        "pairing": {"matched": matched, "total": len(recent), "unmatched": len(recent) - matched},
    }


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
                   title, status, last_error, created_at, updated_at,
                   (
                     SELECT COUNT(*)
                     FROM messages m
                     WHERE m.external_session_id = conversations.external_session_id
                   ) AS message_count
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


def create_request_event(
    settings: Settings,
    *,
    external_session_id: str,
    user_id: str,
    prompt: str,
    model: str | None = None,
    provider: str | None = None,
    request_body: str | None = None,
) -> dict[str, Any]:
    event = {
        "id": str(uuid.uuid4()),
        "external_session_id": external_session_id,
        "user_id": user_id,
        "prompt": prompt,
        "status": "running",
        "http_status": None,
        "error_detail": None,
        "model": model,
        "provider": provider,
        "request_body": request_body,
        "created_at": now_iso(),
        "completed_at": None,
    }
    with _connect(settings.database_url) as conn:
        conn.execute(
            """
            INSERT INTO request_events (
              id, external_session_id, user_id, prompt, status, http_status,
              error_detail, model, provider, request_body, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["id"],
                event["external_session_id"],
                event["user_id"],
                event["prompt"],
                event["status"],
                event["http_status"],
                event["error_detail"],
                event["model"],
                event["provider"],
                event["request_body"],
                event["created_at"],
                event["completed_at"],
            ),
        )
        conn.execute(
            """
            UPDATE conversations SET updated_at = ?
            WHERE external_session_id = ? AND user_id = ?
            """,
            (event["created_at"], external_session_id, user_id),
        )
    return event


def finish_request_event(
    settings: Settings,
    *,
    event_id: str,
    status: str,
    http_status: int | None = None,
    error_detail: str | None = None,
    runtime_audit: dict[str, Any] | None = None,
) -> None:
    if status not in {"completed", "failed", "superseded"}:
        raise ValueError(f"invalid request event status: {status}")
    completed_at = now_iso()
    with _connect(settings.database_url) as conn:
        conn.execute(
            """
            UPDATE request_events
            SET status = ?, http_status = ?, error_detail = ?, runtime_audit = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                status,
                http_status,
                error_detail,
                json.dumps(runtime_audit, ensure_ascii=True) if runtime_audit else None,
                completed_at,
                event_id,
            ),
        )


def list_recent_replaceable_request_events(
    settings: Settings,
    *,
    user_id: str,
    external_session_id: str,
    before: str,
    since: str,
) -> list[dict[str, Any]]:
    """Return recent requests whose text may be inherited by a file-only turn."""
    with _connect(settings.database_url) as conn:
        rows = conn.execute(
            """
            SELECT id, prompt, status, http_status, created_at
            FROM request_events
            WHERE user_id = ?
              AND external_session_id = ?
              AND created_at >= ?
              AND created_at < ?
              AND (status = 'superseded' OR (status = 'running' AND http_status IS NULL))
            ORDER BY created_at ASC
            """,
            (user_id, external_session_id, since, before),
        ).fetchall()
    return [dict(row) for row in rows]


def record_request_stage(
    settings: Settings,
    *,
    request_event_id: str,
    stage: str,
    duration_ms: float,
    detail: str | None = None,
) -> dict[str, Any]:
    event = {
        "request_event_id": request_event_id,
        "stage": stage,
        "duration_ms": round(max(0.0, duration_ms), 1),
        "detail": detail,
        "created_at": now_iso(),
    }
    with _connect(settings.database_url) as conn:
        cursor = conn.execute(
            """
            INSERT INTO request_stage_events (
              request_event_id, stage, duration_ms, detail, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                event["request_event_id"],
                event["stage"],
                event["duration_ms"],
                event["detail"],
                event["created_at"],
            ),
        )
    event["id"] = int(cursor.lastrowid)
    return event


def list_request_stages_by_session(
    settings: Settings, external_session_id: str
) -> dict[str, list[dict[str, Any]]]:
    with _connect(settings.database_url) as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.request_event_id, s.stage, s.duration_ms, s.detail, s.created_at
            FROM request_stage_events s
            JOIN request_events r ON r.id = s.request_event_id
            WHERE r.external_session_id = ?
            ORDER BY s.created_at ASC, s.id ASC
            """,
            (external_session_id,),
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        grouped.setdefault(item["request_event_id"], []).append(item)
    return grouped


def list_request_events_by_session(
    settings: Settings, external_session_id: str
) -> list[dict[str, Any]]:
    with _connect(settings.database_url) as conn:
        rows = conn.execute(
            """
            SELECT id, external_session_id, user_id, prompt, status, http_status,
                   error_detail, model, provider, request_body, runtime_audit, created_at, completed_at
            FROM request_events
            WHERE external_session_id = ?
            ORDER BY created_at ASC
            """,
            (external_session_id,),
        ).fetchall()
        events = [dict(row) for row in rows]
    for event in events:
        raw_audit = event.get("runtime_audit")
        try:
            event["runtime_audit"] = json.loads(raw_audit) if raw_audit else None
        except (TypeError, json.JSONDecodeError):
            event["runtime_audit"] = None
    return events


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


def search_admin_context(
    settings: Settings,
    *,
    query: str = "",
    user_query: str = "",
    user_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Build linked admin lists from the complete match set before pagination."""
    def like_pattern(value: str) -> str:
        return "%" + value.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"

    clauses: list[str] = []
    params: list[Any] = []
    if user_query.strip():
        clauses.append("(u.user_id LIKE ? ESCAPE '!' COLLATE NOCASE OR u.default_hermes_profile LIKE ? ESCAPE '!' COLLATE NOCASE)")
        params.extend([like_pattern(user_query)] * 2)
    if query.strip():
        clauses.append("""(c.title LIKE ? ESCAPE '!' COLLATE NOCASE OR c.external_session_id LIKE ? ESCAPE '!' COLLATE NOCASE
            OR EXISTS (SELECT 1 FROM session_search s
                WHERE s.external_session_id = c.external_session_id AND s.user_id = c.user_id
                AND (s.content LIKE ? ESCAPE '!' COLLATE NOCASE OR s.title LIKE ? ESCAPE '!' COLLATE NOCASE
                     OR s.role LIKE ? ESCAPE '!' COLLATE NOCASE OR s.kind LIKE ? ESCAPE '!' COLLATE NOCASE)))""")
        params.extend([like_pattern(query)] * 6)
    # LEFT JOIN retains users with no conversations for user-only searches.
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with _connect(settings.database_url) as conn:
        users = conn.execute(
            f"""SELECT u.user_id, u.default_hermes_profile, u.updated_at,
                COUNT(c.external_session_id) AS conversation_count,
                COALESCE(SUM((SELECT COUNT(*) FROM messages m
                    WHERE m.external_session_id = c.external_session_id)), 0) AS message_count
                FROM users u LEFT JOIN conversations c ON c.user_id = u.user_id
                {where} GROUP BY u.user_id ORDER BY u.updated_at DESC, u.user_id ASC""",
            params,
        ).fetchall()
        users = [dict(row) for row in users]
        ids = {row["user_id"] for row in users}
        scoped_user = user_id if user_id in ids else None
        if user_query.strip() and not scoped_user and users:
            scoped_user = users[0]["user_id"]
        if scoped_user:
            clauses.append("c.user_id = ?")
            params.append(scoped_user)
        clauses.append("c.external_session_id IS NOT NULL")
        where = "WHERE " + " AND ".join(clauses)
        total = conn.execute(
            f"SELECT COUNT(*) FROM conversations c JOIN users u ON u.user_id = c.user_id {where}", params
        ).fetchone()[0]
        pattern = like_pattern(query)
        conversations = conn.execute(
            f"""SELECT c.*,
                (SELECT COUNT(*) FROM messages m WHERE m.external_session_id = c.external_session_id) AS message_count,
                (SELECT COUNT(*) FROM session_search s WHERE s.external_session_id = c.external_session_id
                    AND s.message_id IS NOT NULL
                    AND (s.content LIKE ? ESCAPE '!' COLLATE NOCASE
                         OR s.role LIKE ? ESCAPE '!' COLLATE NOCASE OR s.kind LIKE ? ESCAPE '!' COLLATE NOCASE)) AS match_count,
                (SELECT substr(s.content, max(1, instr(lower(s.content), lower(?)) - 50), 180)
                    FROM session_search s WHERE s.external_session_id = c.external_session_id
                    AND s.message_id IS NOT NULL AND s.content LIKE ? ESCAPE '!' COLLATE NOCASE
                    ORDER BY s.created_at DESC LIMIT 1) AS match_preview
                FROM conversations c JOIN users u ON u.user_id = c.user_id
                {where} ORDER BY c.updated_at DESC, c.external_session_id ASC LIMIT ? OFFSET ?""",
            [pattern] * 3 + [query.strip(), pattern] + params + [limit, offset],
        ).fetchall()
    return {"users": users, "conversations": [dict(row) for row in conversations],
            "scoped_user": scoped_user, "total_conversations": total,
            "pagination": {"limit": limit, "offset": offset, "has_more": offset + limit < total}}


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


def list_messages(
    settings: Settings,
    user_id: str,
    external_session_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    query = """
        SELECT id, external_session_id, user_id, role, kind, content, model, provider, created_at
        FROM messages
        WHERE user_id = ? AND external_session_id = ?
        ORDER BY created_at ASC
    """
    values: list[Any] = [user_id, external_session_id]
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        values.extend((limit, offset))
    with _connect(settings.database_url) as conn:
        rows = conn.execute(query, values).fetchall()
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
    offset: int = 0,
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
            LIMIT ? OFFSET ?
            """,
            (
                user_id,
                external_session_id,
                external_session_id,
                pattern,
                pattern,
                pattern,
                pattern,
                limit,
                offset,
            ),
        ).fetchall()
        return [dict(row) for row in rows]
