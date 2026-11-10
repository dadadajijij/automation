from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
import uuid
import shutil
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .admin_ui import render_chat_page, render_dashboard_page, render_latency_page, render_login_page
from .attachments import (
    AttachmentError,
    DownloadedFile,
    DownloadedImage,
    attachment_session_dir,
    attachment_request_dir,
    request_workspace,
    write_request_manifest,
    cleanup_old_attachment_dirs,
    download_file,
    download_image,
    store_file,
    store_image,
)
from .config import Settings, load_env_file, load_settings
from .db import (
    append_message,
    create_admin_session,
    count_conversations,
    count_messages,
    count_request_events,
    count_users,
    delete_admin_session,
    delete_message,
    create_conversation,
    create_request_event,
    finish_request_event,
    get_conversation_by_session,
    get_conversation,
    get_admin_session,
    get_or_create_user,
    init_db,
    list_all_conversations,
    list_messages_by_session,
    list_request_events_by_session,
    list_request_stages_by_session,
    list_recent_replaceable_request_events,
    list_user_private_skill_bindings,
    list_users,
    latency_report,
    list_conversations,
    list_messages,
    search_messages,
    search_messages_admin,
    search_admin_context,
    record_request_stage,
    update_user_private_skill_bindings,
    touch_admin_session,
    update_conversation,
)
from .hermes_client import HermesInvocationCancelled, HermesInvocationError, run_first_turn, run_resume_turn
from .skills_catalog import PrivateSkillCatalogError, list_private_skills
from .reports import cleanup_reports, get_report_download, publish_report_answer
from .locks import (
    begin_attachment_download,
    begin_session_turn,
    clear_pending_attachments,
    finish_attachment_download,
    get_pending_attachments,
    get_session_lock,
    is_session_turn_current,
    wait_for_attachment_downloads,
)


SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
DEFAULT_USER_ID = "guest"
DEFAULT_HERMES_PROFILE = "default"
MAX_AUDIT_BODY_BYTES = 64 * 1024
SENSITIVE_URL_QUERY_KEYS = {"key", "api_key", "apikey", "token", "access_token", "signature", "sig", "auth"}


class ChatAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    type: Literal["image", "file"]


class ChatQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="", max_length=20000)
    attachments: list[ChatAttachment] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def require_text_or_attachment(self) -> "ChatQuestion":
        if not self.text.strip() and not self.attachments:
            raise ValueError("question.text is required when question.attachments is empty")
        return self


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: ChatQuestion
    user_id: str | None = Field(default=None, max_length=120)
    session_id: str | None = Field(default=None, max_length=120)


class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    add: list[str] = Field(default_factory=list, max_length=10)
    remove: list[str] = Field(default_factory=list, max_length=10)


class SkillBindingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    add: list[str] = Field(default_factory=list, max_length=10)
    remove: list[str] = Field(default_factory=list, max_length=10)


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=200)


@dataclass(slots=True)
class PreparedAttachments:
    images: list[DownloadedImage]
    files: list[DownloadedFile]

    @property
    def has_attachments(self) -> bool:
        return bool(self.images or self.files)


def _redact_audit_body(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_audit_body(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_audit_body(item) for item in value]
    if isinstance(value, str) and (value.startswith("http://") or value.startswith("https://")):
        parsed = urlsplit(value)
        query = []
        for key, item in parse_qsl(parsed.query, keep_blank_values=True):
            query.append((key, "[REDACTED]" if key.lower() in SENSITIVE_URL_QUERY_KEYS else item))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    return value


def _serialize_audit_body(value: Any) -> str:
    serialized = json.dumps(_redact_audit_body(value), ensure_ascii=False, indent=2)
    if len(serialized.encode("utf-8")) <= MAX_AUDIT_BODY_BYTES:
        return serialized
    preview = serialized.encode("utf-8")[: MAX_AUDIT_BODY_BYTES - 160].decode("utf-8", errors="ignore")
    return json.dumps(
        {
            "_truncated": True,
            "original_size_bytes": len(serialized.encode("utf-8")),
            "body_prefix": preview,
        },
        ensure_ascii=False,
        indent=2,
    )


def _settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise RuntimeError("gateway settings missing")
    return settings


def _require_gateway_key(request: Request, settings: Settings) -> None:
    if not settings.gateway_api_key:
        return
    supplied = (
        request.headers.get("x-gateway-key")
        or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    )
    if supplied != settings.gateway_api_key:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _admin_session_from_request(request: Request, settings: Settings) -> dict[str, Any] | None:
    session_token = request.cookies.get(settings.admin_cookie_name, "").strip()
    if not session_token:
        return None
    session = get_admin_session(settings, session_token)
    if session is None:
        return None
    return touch_admin_session(settings, session_token, settings.admin_session_ttl_seconds) or session


def _require_admin_session(request: Request, settings: Settings) -> dict[str, Any]:
    session = _admin_session_from_request(request, settings)
    if session is None:
        raise HTTPException(status_code=401, detail="Admin login required")
    return session


def _admin_csrf_token(session_token: str) -> str:
    return hmac.new(
        session_token.encode("utf-8"),
        b"hermes-http-gateway/admin-csrf/v1",
        hashlib.sha256,
    ).hexdigest()


def _require_admin_csrf(request: Request, session: dict[str, Any]) -> None:
    supplied = request.headers.get("x-csrf-token", "")
    expected = _admin_csrf_token(str(session["session_token"]))
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def _public_path(settings: Settings, path: str) -> str:
    return f"{settings.public_base_path}{path}"


def _set_admin_cookie(response: JSONResponse | HTMLResponse | RedirectResponse, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.admin_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.admin_cookie_secure,
        max_age=settings.admin_session_ttl_seconds,
        path="/",
    )


def _clear_admin_cookie(response: JSONResponse | HTMLResponse | RedirectResponse, settings: Settings) -> None:
    response.delete_cookie(key=settings.admin_cookie_name, path="/")


def _validate_admin_login(settings: Settings, username: str, password: str) -> None:
    if not settings.admin_password:
        raise HTTPException(status_code=503, detail="Admin password is not configured")
    if username.strip() != settings.admin_username or password != settings.admin_password:
        raise HTTPException(status_code=401, detail="Invalid username or password")


def _resolve_user_id(value: str | None = None) -> str:
    user_id = (value or "").strip()
    return user_id or DEFAULT_USER_ID


def _validate_session_id(session_id: str) -> str:
    value = session_id.strip()
    if not value or not SESSION_ID_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail="session_id must match ^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$",
        )
    return value


def _conversation_title(question: str) -> str:
    title = " ".join(question.strip().split())
    if not title:
        return "附件请求"
    if len(title) <= 80:
        return title
    return title[:77].rstrip() + "..."


SUPERSEDED_PROMPT_MERGE_WINDOW_SECONDS = 2.0


def _is_file_only_prompt(prompt: str) -> bool:
    value = prompt.strip()
    return not value or value.startswith("[文件]")


def _merge_recent_superseded_prompts(
    settings: Settings,
    *,
    user_id: str,
    session_id: str,
    current_prompt: str,
    request_created_at: str,
) -> tuple[str, list[str]]:
    if not _is_file_only_prompt(current_prompt):
        return current_prompt, []
    try:
        current_time = datetime.fromisoformat(request_created_at)
    except ValueError:
        return current_prompt, []
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    since = (current_time - timedelta(seconds=SUPERSEDED_PROMPT_MERGE_WINDOW_SECONDS)).isoformat()
    events = list_recent_replaceable_request_events(
        settings,
        user_id=user_id,
        external_session_id=session_id,
        before=request_created_at,
        since=since,
    )
    prompts: list[str] = []
    request_ids: list[str] = []
    for event in events:
        prompt = str(event.get("prompt") or "").strip()
        if not prompt or _is_file_only_prompt(prompt) or prompt in prompts:
            continue
        prompts.append(prompt)
        request_ids.append(str(event["id"]))
    return ("\n\n".join(prompts) if prompts else current_prompt), request_ids


def _conversation_payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": row["external_session_id"],
        "user_id": row["user_id"],
        "hermes_session_id": row["hermes_session_id"],
        "hermes_profile": row["hermes_profile"],
        "title": row["title"],
        "status": row["status"],
        "last_error": row["last_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "message_count": row.get("message_count"),
    }


def _public_conversation_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = _conversation_payload(row)
    payload.pop("hermes_profile", None)
    return payload


def _message_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": "message",
        "id": row["id"],
        "session_id": row["external_session_id"],
        "user_id": row["user_id"],
        "role": row["role"],
        "kind": row["kind"],
        "content": row["content"],
        "model": row["model"],
        "provider": row["provider"],
        "created_at": row["created_at"],
    }


def _request_event_payload(
    row: dict[str, Any],
    stages: list[dict[str, Any]] | None = None,
    timing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_audit = row.get("runtime_audit")
    if isinstance(runtime_audit, dict) and "private_skills" in runtime_audit:
        runtime_audit = dict(runtime_audit)
        runtime_audit["skills"] = runtime_audit.pop("private_skills")
    if timing is None and isinstance(runtime_audit, dict):
        candidate = runtime_audit.get("timing")
        if isinstance(candidate, dict):
            timing = candidate
    return {
        "event_type": "request",
        "id": row["id"],
        "session_id": row["external_session_id"],
        "user_id": row["user_id"],
        "prompt": row["prompt"],
        "status": row["status"],
        "http_status": row["http_status"],
        "error_detail": row["error_detail"],
        "model": row["model"],
        "provider": row["provider"],
        "request_body": row.get("request_body"),
        "runtime_audit": runtime_audit,
        "timing": timing,
        "stages": stages or [],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }


_HERMES_FILE_MUTATION_FOOTER = re.compile(
    r"(?:^|\n)⚠️ File-mutation verifier:.*\Z",
    re.DOTALL,
)


def _strip_internal_runtime_diagnostics(answer: str) -> tuple[str, dict[str, Any] | None]:
    """Keep Hermes developer diagnostics out of customer-visible HTTP replies."""
    if not isinstance(answer, str):
        return answer, None
    match = _HERMES_FILE_MUTATION_FOOTER.search(answer)
    if match is None:
        return answer, None
    return answer[: match.start()].rstrip(), {
        "kind": "internal_runtime_diagnostic",
        "source": "hermes_file_mutation_verifier",
        "customer_visible": False,
        "summary": "Hermes reported a file mutation failure; diagnostic hidden from customer response",
    }


def _runtime_audit_from_usage(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the safe, structured runtime audit emitted by Hermes."""
    if not isinstance(usage, dict):
        return None
    audit = usage.get("gateway_audit")
    if not isinstance(audit, dict):
        return None
    events = audit.get("events")
    if not isinstance(events, list):
        events = []
    safe_events = [item for item in events[:64] if isinstance(item, dict)]
    main = audit.get("main") if isinstance(audit.get("main"), dict) else {}
    result: dict[str, Any] = {"version": audit.get("version", 1), "events": safe_events}
    skills = audit.get("skills")
    if isinstance(skills, list):
        result["skills"] = [item for item in skills[:64] if isinstance(item, dict)]
    if main:
        result["main"] = main
    context_window = audit.get("context_window")
    if isinstance(context_window, int) and context_window > 0:
        result["context_window"] = context_window
    timing = audit.get("timing")
    if isinstance(timing, dict):
        result["timing"] = timing
    return result


def _hermes_state_db_candidates(settings: Settings) -> list[Path]:
    candidates: list[Path] = []
    configured_home = os.environ.get("HERMES_HOME", "").strip()
    if configured_home:
        candidates.append(Path(configured_home) / "state.db")
    candidates.extend(
        [
            Path.home() / ".hermes" / "state.db",
            settings.hermes_workdir / ".hermes" / "state.db",
        ]
    )
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _hermes_runtime_breakdown(
    settings: Settings,
    hermes_session_id: str | None,
    *,
    window_start: str | None = None,
    window_end: str | None = None,
) -> dict[str, Any] | None:
    """Derive coarse model/tool timing from Hermes' persisted message clock.

    Hermes writes an assistant message after each model call and tool messages
    after tool execution.  The gap before an assistant message is therefore
    model decision time; the interval from an assistant tool call to its tool
    results is tool time.  This is intentionally best effort because older
    Hermes sessions may not have a readable state database.
    """
    if not hermes_session_id:
        return None
    db_path = next((path for path in _hermes_state_db_candidates(settings) if path.exists()), None)
    if db_path is None:
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT role, timestamp, tool_calls FROM messages WHERE session_id = ? ORDER BY timestamp, id",
            (hermes_session_id,),
        ).fetchall()
        conn.close()
    except (OSError, sqlite3.Error):
        return None
    if not rows:
        return None

    try:
        lower = datetime.fromisoformat(window_start).timestamp() - 5 if window_start else None
        upper = datetime.fromisoformat(window_end).timestamp() + 5 if window_end else None
    except (TypeError, ValueError, OverflowError):
        lower = upper = None
    if lower is not None or upper is not None:
        rows = [
            row for row in rows
            if (lower is None or float(row["timestamp"]) >= lower)
            and (upper is None or float(row["timestamp"]) <= upper)
        ]
    if not rows:
        return None

    model_ms = 0.0
    tool_ms = 0.0
    model_calls = 0
    tool_calls = 0
    segments: list[dict[str, Any]] = []
    previous_timestamp: float | None = None
    index = 0
    while index < len(rows):
        row = rows[index]
        timestamp = float(row["timestamp"])
        if row["role"] == "assistant":
            try:
                calls = json.loads(row["tool_calls"] or "[]")
            except (TypeError, json.JSONDecodeError):
                calls = []
            if not isinstance(calls, list):
                calls = []
            model_calls += 1
            tool_calls += len(calls)
            if previous_timestamp is not None:
                duration = max(0.0, (timestamp - previous_timestamp) * 1000.0)
                model_ms += duration
                if duration:
                    segments.append({
                        "kind": "model",
                        "start_ts": previous_timestamp,
                        "end_ts": timestamp,
                        "duration_ms": round(duration, 1),
                        "detail": "模型调用",
                    })
            if calls:
                tool_end = timestamp
                cursor = index + 1
                while cursor < len(rows) and rows[cursor]["role"] == "tool":
                    tool_end = max(tool_end, float(rows[cursor]["timestamp"]))
                    cursor += 1
                duration = max(0.0, (tool_end - timestamp) * 1000.0)
                tool_ms += duration
                if duration:
                    segments.append({
                        "kind": "tool",
                        "start_ts": timestamp,
                        "end_ts": tool_end,
                        "duration_ms": round(duration, 1),
                        "detail": f"{len(calls)} 次工具调用",
                    })
                previous_timestamp = tool_end
                index = cursor - 1
            else:
                previous_timestamp = timestamp
        elif previous_timestamp is None:
            previous_timestamp = timestamp
        index += 1

    measured_ms = model_ms + tool_ms
    return {
        "version": 1,
        "model_wait_ms": round(model_ms, 1),
        "tool_execution_ms": round(tool_ms, 1),
        "cli_overhead_ms": None,
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "source": "Hermes state.db message timestamps",
        "hermes_session_id": hermes_session_id,
        "measured_ms": round(measured_ms, 1),
        "segments": segments[:256],
    }


def _timing_stages(timing: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not timing:
        return []
    stages: list[dict[str, Any]] = []
    model_ms = float(timing.get("model_wait_ms") or 0)
    tool_ms = float(timing.get("tool_execution_ms") or 0)
    overhead = timing.get("cli_overhead_ms")
    if model_ms:
        stages.append({"stage": "模型决策等待", "duration_ms": model_ms,
                       "detail": f"{timing.get('model_calls', 0)} 次模型调用",
                       "nested": True})
    if tool_ms:
        stages.append({"stage": "Hermes 工具执行", "duration_ms": tool_ms,
                       "detail": f"{timing.get('tool_calls', 0)} 次工具调用",
                       "nested": True})
    if overhead is not None and float(overhead) > 0:
        stages.append({"stage": "Hermes CLI 初始化/收尾", "duration_ms": float(overhead),
                       "detail": "总运行时间扣除模型与工具时间",
                       "nested": True})
    return stages


def _request_timing(
    settings: Settings,
    hermes_session_id: str | None,
    stages: list[dict[str, Any]],
    *,
    window_start: str | None = None,
    window_end: str | None = None,
    fallback_timing: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    timing = _hermes_runtime_breakdown(
        settings,
        hermes_session_id,
        window_start=window_start,
        window_end=window_end,
    )
    if not timing:
        timing = dict(fallback_timing) if isinstance(fallback_timing, dict) else None
    if not timing:
        return None, stages
    if timing.get("cli_overhead_ms") is None:
        total_ms = sum(
            float(item.get("duration_ms") or 0)
            for item in stages
            if item.get("stage") == "Hermes CLI 运行"
        )
        overhead = max(0.0, total_ms - float(timing.get("measured_ms") or 0))
        timing["cli_overhead_ms"] = round(overhead, 1)
    timeline: list[dict[str, Any]] = []
    try:
        request_start = datetime.fromisoformat(window_start).timestamp() if window_start else None
    except (TypeError, ValueError, OverflowError):
        request_start = None
    hermes_start = hermes_end = None
    for item in stages:
        try:
            end_ts = datetime.fromisoformat(str(item["created_at"])).timestamp()
            duration_ms = float(item.get("duration_ms") or 0)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        start_ts = end_ts - duration_ms / 1000.0
        if item.get("stage") == "Hermes CLI 运行":
            hermes_start, hermes_end = start_ts, end_ts
        elif request_start is not None and duration_ms > 0:
            timeline.append({
                "kind": "gateway",
                "label": str(item.get("stage") or "网关阶段"),
                "start_ms": round(max(0.0, (start_ts - request_start) * 1000.0), 1),
                "duration_ms": round(duration_ms, 1),
                "detail": item.get("detail"),
            })
    if request_start is not None and hermes_start is not None and hermes_end is not None:
        cursor = hermes_start
        for item in timing.get("segments", []):
            start_ts = max(hermes_start, float(item.get("start_ts") or hermes_start))
            end_ts = min(hermes_end, float(item.get("end_ts") or hermes_end))
            if end_ts <= start_ts:
                continue
            if start_ts > cursor:
                timeline.append({
                    "kind": "cli",
                    "label": "CLI 初始化/收尾",
                    "start_ms": round(max(0.0, (cursor - request_start) * 1000.0), 1),
                    "duration_ms": round((start_ts - cursor) * 1000.0, 1),
                    "detail": "消息时间戳未覆盖的 CLI 区间",
                })
            kind = str(item.get("kind") or "model")
            timeline.append({
                "kind": kind,
                "label": "模型等待" if kind == "model" else "工具执行",
                "start_ms": round(max(0.0, (start_ts - request_start) * 1000.0), 1),
                "duration_ms": round((end_ts - start_ts) * 1000.0, 1),
                "detail": item.get("detail"),
            })
            cursor = max(cursor, end_ts)
        if cursor < hermes_end:
            timeline.append({
                "kind": "cli",
                "label": "CLI 初始化/收尾",
                "start_ms": round(max(0.0, (cursor - request_start) * 1000.0), 1),
                "duration_ms": round((hermes_end - cursor) * 1000.0, 1),
                "detail": "消息时间戳未覆盖的 CLI 区间",
            })
    if timeline:
        timing["timeline"] = sorted(timeline, key=lambda item: item["start_ms"])
        timing["timeline_total_ms"] = round(max(
            item["start_ms"] + item["duration_ms"] for item in timeline
        ), 1)
    expanded: list[dict[str, Any]] = []
    inserted = False
    for stage in stages:
        expanded.append(stage)
        if stage.get("stage") == "Hermes CLI 运行":
            expanded.extend(_timing_stages(timing))
            inserted = True
    if not inserted:
        expanded.extend(_timing_stages(timing))
    return timing, expanded


def _runtime_audit_with_private_skills(
    usage: dict[str, Any] | None,
    private_skills: list[dict[str, Any]],
    skill_audit: dict[str, Any] | None = None,
    executed_skills: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    audit = _runtime_audit_from_usage(usage) or {}
    usage_skill_audit = None
    if isinstance(audit.get("skills"), list):
        usage_skill_audit = {"version": audit.get("version", 1), "skills": audit["skills"]}
    normalized = _normalize_skill_audit(skill_audit or usage_skill_audit, private_skills)
    if private_skills:
        audit["skills"] = private_skills
    if executed_skills:
        if normalized is None:
            normalized = {"version": 1, "telemetry": "gateway_detected", "skills": []}
        existing = {
            str(item.get("id")) for item in normalized.get("skills", [])
            if isinstance(item, dict) and item.get("id")
        }
        for item in executed_skills:
            skill_id = str(item.get("id") or "").strip()
            if skill_id and skill_id not in existing:
                normalized.setdefault("skills", []).append(item)
                existing.add(skill_id)
    if normalized is not None:
        actual = [
            item for item in normalized.get("skills", [])
            if isinstance(item, dict)
            and str(item.get("status") or "").lower() in {"running", "succeeded", "failed", "timeout", "partial"}
        ]
        if actual:
            audit["executed_skills"] = actual[:128]
    if normalized is not None:
        audit["skill_audit"] = normalized
    return audit or None


def _detect_executed_skills(
    settings: Settings,
    hermes_session_id: str | None,
    *,
    window_start: str | None = None,
    window_end: str | None = None,
) -> list[dict[str, Any]]:
    """Detect auxiliary Skills invoked by Hermes tools in this request.

    Hermes' native Skill audit reports selected Skills, but shell based helper
    Skills (such as Bailian RAG) appear as terminal commands. Inspect the
    persisted tool calls for the request window so the audit reflects actual
    execution as well as selection.
    """
    if not hermes_session_id:
        return []
    db_path = next((path for path in _hermes_state_db_candidates(settings) if path.exists()), None)
    if db_path is None:
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT role, content, tool_calls, tool_call_id, timestamp "
            "FROM messages WHERE session_id = ? ORDER BY timestamp, id",
            (hermes_session_id,),
        ).fetchall()
        conn.close()
    except (OSError, sqlite3.Error):
        return []
    try:
        lower = datetime.fromisoformat(window_start).timestamp() - 5 if window_start else None
        upper = datetime.fromisoformat(window_end).timestamp() + 5 if window_end else None
    except (TypeError, ValueError, OverflowError):
        lower = upper = None
    if lower is not None or upper is not None:
        rows = [
            row for row in rows
            if (lower is None or float(row["timestamp"]) >= lower)
            and (upper is None or float(row["timestamp"]) <= upper)
        ]
    tool_result_ids = {str(row["tool_call_id"]) for row in rows if row["role"] == "tool" and row["tool_call_id"]}
    detected: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["role"] != "assistant" or not row["tool_calls"]:
            continue
        try:
            calls = json.loads(row["tool_calls"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function") if isinstance(call.get("function"), dict) else call
            name = str(function.get("name") or "")
            arguments = str(function.get("arguments") or "")
            command = f"{name} {arguments}"
            skill_id = None
            if "bailian_search_all.py" in command or "agora-bailian-rag" in command:
                skill_id = "agora-bailian-rag"
            else:
                match = re.search(r"/skills/([A-Za-z0-9._-]+)(?:/|\b)", command)
                if match:
                    skill_id = match.group(1)
            if not skill_id:
                continue
            call_id = str(call.get("id") or call.get("call_id") or "")
            detected[skill_id] = {
                "id": skill_id,
                "status": "succeeded" if call_id in tool_result_ids else "running",
                "source": "hermes_tool_call",
                "invocation_count": 1,
            }
    return list(detected.values())


_SKILL_STATUSES = {"selected", "running", "succeeded", "failed", "timeout", "skipped", "unavailable", "unknown", "partial"}


def _normalize_skill_audit(
    payload: dict[str, Any] | None,
    private_skills: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Keep a small, safe Skill execution summary for request auditing.

    Hermes versions may return either ``skills`` or event-style records. A
    missing producer signal is represented as ``unknown`` rather than inferred
    from the overall Hermes exit code.
    """
    if not isinstance(payload, dict):
        if not private_skills:
            return None
        return {
            "version": 1,
            "telemetry": "missing",
            "skills": [
                {
                    "id": item.get("id"),
                    "status": "unknown",
                    "source": item.get("source", "user_binding"),
                    "reason": "Hermes did not emit Skill audit telemetry",
                }
                for item in private_skills
            ],
        }

    raw_items = payload.get("skills")
    if not isinstance(raw_items, list):
        raw_items = payload.get("events")
    if not isinstance(raw_items, list):
        raw_items = []
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items[:128]:
        if not isinstance(raw, dict):
            continue
        skill_id = raw.get("skill_id") or raw.get("id") or raw.get("name") or raw.get("task")
        if not isinstance(skill_id, str) or not skill_id.strip():
            continue
        skill_id = skill_id.strip()[:160]
        status = str(raw.get("status") or "unknown").strip().lower()
        if status not in _SKILL_STATUSES:
            status = "unknown"
        item: dict[str, Any] = {"id": skill_id, "status": status}
        for key in ("duration_ms", "invocation_count", "error_type", "reason", "summary", "services"):
            value = raw.get(key)
            if key == "services" and isinstance(value, dict):
                item[key] = {
                    str(name)[:80]: str(state)[:40]
                    for name, state in list(value.items())[:16]
                }
            elif key in {"duration_ms", "invocation_count"} and isinstance(value, (int, float)):
                item[key] = max(0, value)
            elif isinstance(value, str) and value.strip():
                item[key] = value.strip()[:240]
        skills.append(item)
        seen.add(skill_id)

    for private in private_skills:
        skill_id = str(private.get("id") or "").strip()
        if skill_id and skill_id not in seen:
            skills.append({
                "id": skill_id,
                "status": "unknown",
                "source": private.get("source", "user_binding"),
                "reason": "Skill was selected but emitted no execution result",
            })
    if not skills and not private_skills:
        return None
    return {
        "version": payload.get("version", 1),
        "telemetry": "received",
        "skills": skills[:128],
    }


def _effective_gateway_skill_ids(
    bindings: list[dict[str, Any]], default_skill: str
) -> tuple[list[str], str]:
    """Select explicit user bindings, or the gateway default when absent."""
    selected = [str(item.get("id") or "").strip() for item in bindings]
    selected = list(dict.fromkeys(item for item in selected if item))
    if selected:
        return selected, "user_binding"
    return [default_skill], "gateway_default"


def _actual_runtime(usage: dict[str, Any] | None, fallback_model: str | None, fallback_provider: str | None) -> tuple[str | None, str | None]:
    if not isinstance(usage, dict):
        return fallback_model, fallback_provider
    model = usage.get("model")
    provider = usage.get("provider")
    return (
        model.strip() if isinstance(model, str) and model.strip() else fallback_model,
        provider.strip() if isinstance(provider, str) and provider.strip() else fallback_provider,
    )


def _search_message_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["message_id"],
        "session_id": row["external_session_id"],
        "user_id": row["user_id"],
        "role": row["role"],
        "kind": row["kind"],
        "content": row["content"],
        "model": row["model"],
        "provider": row["provider"],
        "created_at": row["created_at"],
    }


def _prepare_image_attachments(
    settings: Settings,
    attachments: list[ChatAttachment],
    target_dir: Path,
) -> list[DownloadedImage]:
    image_attachments = [item for item in attachments if item.type == "image"]
    if len(image_attachments) > settings.attachment_max_images:
        raise HTTPException(
            status_code=400,
            detail=f"only {settings.attachment_max_images} image attachment(s) are supported",
        )

    downloaded: list[DownloadedImage] = []
    for item in image_attachments:
        try:
            downloaded.append(download_image(settings, url=item.url, target_dir=target_dir))
        except AttachmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return downloaded


def _prepare_file_attachments(
    settings: Settings,
    attachments: list[ChatAttachment],
    target_dir: Path,
) -> list[DownloadedFile]:
    file_attachments = [item for item in attachments if item.type == "file"]

    downloaded: list[DownloadedFile] = []
    for item in file_attachments:
        try:
            downloaded.append(download_file(settings, url=item.url, target_dir=target_dir))
        except AttachmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return downloaded


async def _read_uploaded_file(upload: Any, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = await upload.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"attachment exceeds max size of {max_bytes} bytes",
                )
            chunks.append(chunk)
    finally:
        close = getattr(upload, "close", None)
        if close is not None:
            await close()
    return b"".join(chunks)


def _is_image_upload(filename: str, content_type: str) -> bool:
    if content_type.lower().startswith("image/"):
        return True
    return Path(filename).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}


async def _prepare_uploaded_attachments(
    settings: Settings,
    uploads: list[Any],
    target_dir: Path,
) -> PreparedAttachments:
    if len(uploads) > 8:
        raise HTTPException(status_code=400, detail="only 8 attachment(s) are supported")
    declared_image_count = sum(
        _is_image_upload(
            str(getattr(upload, "filename", "") or "attachment.bin"),
            str(getattr(upload, "content_type", "") or "application/octet-stream"),
        )
        for upload in uploads
    )
    if declared_image_count > settings.attachment_max_images:
        raise HTTPException(
            status_code=400,
            detail=f"only {settings.attachment_max_images} image attachment(s) are supported",
        )

    images: list[DownloadedImage] = []
    files: list[DownloadedFile] = []
    for upload in uploads:
        filename = str(getattr(upload, "filename", "") or "attachment.bin")
        content_type = str(getattr(upload, "content_type", "") or "application/octet-stream")
        data = await _read_uploaded_file(upload, settings.attachment_max_bytes)
        source = f"upload:{filename}"
        try:
            if _is_image_upload(filename, content_type):
                images.append(
                    store_image(
                        data=data,
                        source=source,
                        declared_content_type=content_type,
                        target_dir=target_dir,
                    )
                )
            else:
                files.append(
                    store_file(
                        data=data,
                        source=source,
                        declared_content_type=content_type,
                        target_dir=target_dir,
                    )
                )
        except AttachmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return PreparedAttachments(images=images, files=files)


def _prompt_with_attachments(
    prompt: str,
    images: list[DownloadedImage],
    files: list[DownloadedFile],
) -> str:
    if not images and not files:
        return prompt

    sections: list[str] = []
    if images:
        if len(images) == 1:
            sections.append("本轮请求包含 1 张图片附件，请结合随请求传入的图片进行回答。")
        else:
            sections.append(f"本轮请求包含 {len(images)} 张图片附件，请结合随请求传入的图片进行回答。")
    if files:
        file_refs = "\n".join(
            "- 文件名：{name}\n  路径：{path}\n  大小：{size} bytes\n  类型：{mime}".format(
                name=item.local_path.name,
                path=item.local_path,
                size=(
                    getattr(item, "size_bytes", None)
                    if getattr(item, "size_bytes", None) is not None
                    else (item.local_path.stat().st_size if item.local_path.exists() else 0)
                ),
                mime=getattr(item, "mime_type", "application/octet-stream"),
            )
            for item in files
        )
        sections.append(
            "大型或普通文件请使用 Hermes 文件/终端工具按需读取，不要一次性加载全文。\n"
            "先搜索与用户问题相关的关键词和时间范围，再分段读取相关上下文；"
            "只能读取本轮附件目录中的文件。\n\n"
            f"附件文件：\n{file_refs}"
        )
    prefix = prompt.rstrip()
    if not prefix:
        return "\n\n".join(sections)
    return f"{prefix}\n\n" + "\n\n".join(sections)


def _dedupe_downloaded(items: list[Any]) -> list[Any]:
    seen: set[str] = set()
    deduped: list[Any] = []
    for item in items:
        key = str(item.local_path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _attachment_root_for_prompt(
    settings: Settings,
    external_session_id: str,
    prompt: str,
    images: list[DownloadedImage],
    files: list[DownloadedFile],
) -> Path | None:
    paths = [item.local_path for item in images] + [item.local_path for item in files]
    if paths:
        parents = {path.parent for path in paths}
        if len(parents) == 1:
            return next(iter(parents))
        return settings.attachment_storage_root
    return None


def _ensure_turn_current(run_key: str, run_generation: int) -> None:
    if not is_session_turn_current(run_key, run_generation):
        raise HermesInvocationCancelled("request superseded by a newer request")


def _ensure_conversation(
    settings: Settings,
    *,
    user_id: str | None,
    session_id: str | None,
    title: str | None,
) -> tuple[str, dict[str, Any]]:
    requested_user_id = _resolve_user_id(user_id)
    user_id_supplied = bool((user_id or "").strip())
    external_session_id = _validate_session_id(session_id) if session_id else str(uuid.uuid4())

    existing = get_conversation_by_session(settings, external_session_id) if session_id else None
    if existing:
        if user_id_supplied and existing["user_id"] != requested_user_id:
            raise HTTPException(
                status_code=409,
                detail="session already belongs to a different user",
            )
        return existing["user_id"], existing

    get_or_create_user(settings, requested_user_id)
    return (
        requested_user_id,
        create_conversation(
            settings,
            external_session_id=external_session_id,
            user_id=requested_user_id,
            hermes_profile=DEFAULT_HERMES_PROFILE,
            title=title,
        ),
    )


async def _run_chat_turn(
    settings: Settings,
    *,
    user_id: str | None,
    session_id: str | None,
    request: ChatRequest,
    prepared_attachments: PreparedAttachments | None = None,
    request_body: str | None = None,
) -> dict[str, Any]:
    question_text = request.question.text
    attachments = request.question.attachments
    effective_model = settings.default_model
    effective_provider = settings.default_provider
    if effective_provider and not effective_model:
        raise HTTPException(
            status_code=400,
            detail="DEFAULT_PROVIDER requires DEFAULT_MODEL",
        )
    user_id, conversation = _ensure_conversation(
        settings,
        user_id=user_id,
        session_id=session_id or request.session_id,
        title=_conversation_title(question_text),
    )

    if conversation["hermes_profile"] != DEFAULT_HERMES_PROFILE:
        conversation = update_conversation(
            settings,
            external_session_id=conversation["external_session_id"],
            user_id=user_id,
            hermes_profile=DEFAULT_HERMES_PROFILE,
        ) or conversation

    external_session_id = conversation["external_session_id"]
    request_event = create_request_event(
        settings,
        external_session_id=external_session_id,
        user_id=user_id,
        prompt=question_text,
        model=effective_model,
        provider=effective_provider,
        request_body=request_body,
    )
    workspace = request_workspace(settings, request_event["id"])
    merged_question_text, merged_request_ids = _merge_recent_superseded_prompts(
        settings,
        user_id=user_id,
        session_id=external_session_id,
        current_prompt=question_text,
        request_created_at=request_event["created_at"],
    )

    private_skills: list[dict[str, Any]] = []
    private_catalog_error: str | None = None
    private_catalog_started_at = time.perf_counter()
    bindings = list_user_private_skill_bindings(settings, user_id)
    if not bindings:
        selected_ids, selection_mode = _effective_gateway_skill_ids(
            [], settings.gateway_default_skill
        )
        private_skills = [
            {"id": skill_id, "version": "", "source": selection_mode}
            for skill_id in selected_ids
        ]
    else:
        try:
            catalog = await asyncio.to_thread(list_private_skills, settings)
            catalog_by_id = {str(item["id"]): item for item in catalog if item.get("enabled")}
            for binding in bindings:
                skill = catalog_by_id.get(str(binding["skill_id"]))
                if skill is None:
                    continue
                private_skills.append(
                    {
                        "id": skill["id"],
                        "version": skill.get("version") or "",
                        "source": "user_binding",
                    }
                )
        except PrivateSkillCatalogError as exc:
            private_catalog_error = str(exc)

    def finish_request(
        status: str,
        *,
        http_status: int | None = None,
        error_detail: str | None = None,
        runtime_audit: dict[str, Any] | None = None,
    ) -> None:
        finish_request_event(
            settings,
            event_id=request_event["id"],
            status=status,
            http_status=http_status,
            error_detail=error_detail,
            runtime_audit=runtime_audit,
        )

    def record_stage(stage: str, started_at: float, detail: str | None = None) -> None:
        record_request_stage(
            settings,
            request_event_id=request_event["id"],
            stage=stage,
            duration_ms=(time.perf_counter() - started_at) * 1000,
            detail=detail,
        )

    if private_catalog_error:
        record_stage("解析 Skills", private_catalog_started_at, private_catalog_error)
    else:
        record_stage(
            "解析 Skills",
            private_catalog_started_at,
            ", ".join(item["id"] for item in private_skills) or "无绑定",
        )

    run_key = f"{user_id}:{external_session_id}"
    if prepared_attachments is None:
        queue_started_at = time.perf_counter()
        await asyncio.to_thread(wait_for_attachment_downloads, run_key)
        record_stage("等待前序附件下载", queue_started_at)
    run_generation = begin_session_turn(run_key)
    lock = get_session_lock(run_key)

    try:
        _ensure_turn_current(run_key, run_generation)
    except HermesInvocationCancelled as exc:
        finish_request("superseded", http_status=409, error_detail=str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if prepared_attachments is None and attachments:
        begin_attachment_download(run_key)
        download_started_at = time.perf_counter()
        try:
            target_dir = workspace["input"]
            downloaded_images = await asyncio.to_thread(
                _prepare_image_attachments,
                settings,
                attachments,
                target_dir,
            )
            downloaded_files = await asyncio.to_thread(
                _prepare_file_attachments,
                settings,
                attachments,
                target_dir,
            )
        except HTTPException as exc:
            record_stage("附件下载", download_started_at, "失败")
            finish_request("failed", http_status=exc.status_code, error_detail=str(exc.detail))
            finish_attachment_download(run_key)
            raise
        except Exception as exc:
            record_stage("附件下载", download_started_at, "失败")
            finish_request("failed", http_status=500, error_detail=str(exc))
            finish_attachment_download(run_key)
            raise
        else:
            record_stage("附件下载", download_started_at)
            finish_attachment_download(
                run_key,
                images=downloaded_images,
                files=downloaded_files,
            )
            write_request_manifest(
                workspace,
                request_id=request_event["id"],
                session_id=external_session_id,
                user_id=user_id,
                files=[*downloaded_images, *downloaded_files],
            )
    elif prepared_attachments is None:
        cleanup_old_attachment_dirs(settings)
    try:
        _ensure_turn_current(run_key, run_generation)
    except HermesInvocationCancelled as exc:
        finish_request("superseded", http_status=409, error_detail=str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    user_message_id: str | None = None
    lock_wait_started_at = time.perf_counter()
    async with lock:
        record_stage("等待会话锁", lock_wait_started_at)
        try:
            _ensure_turn_current(run_key, run_generation)
        except HermesInvocationCancelled as exc:
            finish_request("superseded", http_status=409, error_detail=str(exc))
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        conversation = get_conversation(settings, user_id, external_session_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Session not found")

        if prepared_attachments is None:
            pending_images, pending_files = get_pending_attachments(run_key)
            turn_images = _dedupe_downloaded(pending_images)
            turn_files = _dedupe_downloaded(pending_files)
        else:
            turn_images = _dedupe_downloaded(prepared_attachments.images)
            turn_files = _dedupe_downloaded(prepared_attachments.files)
        if turn_images or turn_files:
            write_request_manifest(
                workspace,
                request_id=request_event["id"],
                session_id=external_session_id,
                user_id=user_id,
                files=[*turn_images, *turn_files],
            )
        hermes_prompt = _prompt_with_attachments(merged_question_text, turn_images, turn_files)
        image_paths = [item.local_path for item in turn_images]
        attachment_root = _attachment_root_for_prompt(
            settings,
            external_session_id,
            hermes_prompt,
            turn_images,
            turn_files,
        )

        user_message = append_message(
            settings,
            external_session_id=external_session_id,
            user_id=user_id,
            role="user",
            kind="chat",
            content=merged_question_text,
            title=conversation["title"],
        )
        user_message_id = user_message["id"]
        update_conversation(
            settings,
            external_session_id=external_session_id,
            user_id=user_id,
            status="running",
            last_error=None,
        )

        try:
            hermes_started_at = time.perf_counter()
            hermes_skill_kwargs = (
                {"private_skill_ids": [item["id"] for item in private_skills]}
                if private_skills
                else {}
            )
            if conversation["hermes_session_id"]:
                result = await asyncio.to_thread(
                    run_resume_turn,
                    settings,
                    prompt=hermes_prompt,
                    hermes_session_id=conversation["hermes_session_id"],
                    profile=conversation["hermes_profile"],
                    model=effective_model,
                    provider=effective_provider,
                    image_paths=image_paths,
                    attachment_root=attachment_root,
                    run_key=run_key,
                    run_generation=run_generation,
                    **hermes_skill_kwargs,
                )
            else:
                result = await asyncio.to_thread(
                    run_first_turn,
                    settings,
                    prompt=hermes_prompt,
                    profile=conversation["hermes_profile"],
                    model=effective_model,
                    provider=effective_provider,
                    image_paths=image_paths,
                    attachment_root=attachment_root,
                    run_key=run_key,
                    run_generation=run_generation,
                    **hermes_skill_kwargs,
                )
            _ensure_turn_current(run_key, run_generation)
            customer_answer, runtime_diagnostic = _strip_internal_runtime_diagnostics(result.answer)
            timing, _ = _request_timing(
                settings,
                result.session_id or conversation.get("hermes_session_id"),
                [{"stage": "Hermes CLI 运行", "duration_ms": (time.perf_counter() - hermes_started_at) * 1000}],
            )
            if timing is not None and isinstance(result.usage, dict):
                gateway_audit = result.usage.setdefault("gateway_audit", {})
                if isinstance(gateway_audit, dict):
                    gateway_audit["timing"] = timing
            if runtime_diagnostic and isinstance(result.usage, dict):
                gateway_audit = result.usage.setdefault("gateway_audit", {})
                if isinstance(gateway_audit, dict):
                    events = gateway_audit.setdefault("events", [])
                    if isinstance(events, list):
                        events.append(runtime_diagnostic)
            published_answer = await asyncio.to_thread(
                publish_report_answer,
                settings,
                customer_answer,
                user_id=user_id,
                session_id=external_session_id,
                request_id=request_event["id"],
            )
            _ensure_turn_current(run_key, run_generation)
        except HermesInvocationCancelled as exc:
            record_stage("Hermes CLI 运行", hermes_started_at, "被后续请求中断")
            if user_message_id:
                delete_message(
                    settings,
                    external_session_id=external_session_id,
                    user_id=user_id,
                    message_id=user_message_id,
                )
            finish_request("superseded", http_status=409, error_detail=str(exc))
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except HermesInvocationError as exc:
            record_stage("Hermes CLI 运行", hermes_started_at, str(exc))
            error_usage = exc.usage if isinstance(exc.usage, dict) else {}
            error_session_id = error_usage.get("session_id") if isinstance(error_usage.get("session_id"), str) else conversation.get("hermes_session_id")
            executed_skills = _detect_executed_skills(
                settings,
                error_session_id,
                window_start=request_event["created_at"],
                window_end=datetime.now(timezone.utc).isoformat(),
            )
            append_message(
                settings,
                external_session_id=external_session_id,
                user_id=user_id,
                role="assistant",
                kind="error",
                content=str(exc),
                title=conversation["title"],
                model=effective_model,
                provider=effective_provider,
            )
            update_conversation(
                settings,
                external_session_id=external_session_id,
                user_id=user_id,
                status="error",
                last_error=str(exc),
            )
            finish_request(
                "failed",
                http_status=502,
                error_detail=str(exc),
                runtime_audit=_runtime_audit_with_private_skills(
                    exc.usage, private_skills, exc.skill_audit, executed_skills
                ),
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "Hermes invocation failed",
                    "error": str(exc),
                    "stdout": exc.stdout,
                    "stderr": exc.stderr,
                    "returncode": exc.returncode,
                    "session_id": external_session_id,
                },
            ) from exc

        record_stage("Hermes CLI 运行", hermes_started_at)
        persistence_started_at = time.perf_counter()
        if not conversation["hermes_session_id"]:
            if not result.session_id:
                detail = "Hermes did not return a session_id"
                finish_request("failed", http_status=502, error_detail=detail)
                raise HTTPException(
                    status_code=502,
                    detail={
                        "message": detail,
                        "session_id": external_session_id,
                    },
                )
            conversation = update_conversation(
                settings,
                external_session_id=external_session_id,
                user_id=user_id,
                hermes_session_id=result.session_id,
                status="open",
                last_error=None,
            ) or conversation
        else:
            conversation = update_conversation(
                settings,
                external_session_id=external_session_id,
                user_id=user_id,
                hermes_session_id=result.session_id or conversation["hermes_session_id"],
                status="open",
                last_error=None,
            ) or conversation

        actual_model, actual_provider = _actual_runtime(
            result.usage, effective_model, effective_provider
        )
        executed_skills = _detect_executed_skills(
            settings,
            result.session_id or conversation.get("hermes_session_id"),
            window_start=request_event["created_at"],
            window_end=datetime.now(timezone.utc).isoformat(),
        )
        append_message(
            settings,
            external_session_id=external_session_id,
            user_id=user_id,
            role="assistant",
            kind="chat",
            content=published_answer,
            title=conversation["title"],
            model=actual_model,
            provider=actual_provider,
        )
        if prepared_attachments is None:
            clear_pending_attachments(run_key, images=turn_images, files=turn_files)
        record_stage("保存结果", persistence_started_at)
        finish_request(
            "completed",
            http_status=200,
            runtime_audit=_runtime_audit_with_private_skills(
                result.usage, private_skills, result.skill_audit, executed_skills
            ),
        )

        response = {
            "session_id": external_session_id,
            "hermes_session_id": conversation["hermes_session_id"] or result.session_id,
            "user_id": user_id,
            "answer": published_answer,
        }
        return response


async def _admin_chat_request(request: Request, settings: Settings) -> dict[str, Any]:
    session = _require_admin_session(request, settings)
    _require_admin_csrf(request, session)
    try:
        form = await request.form()
    except AssertionError as exc:
        raise HTTPException(
            status_code=503,
            detail="multipart support is unavailable; install python-multipart",
        ) from exc

    text = str(form.get("text", ""))
    user_id = str(form.get("user_id", "")).strip() or "admin-playground"
    session_id = str(form.get("session_id", "")).strip() or str(uuid.uuid4())
    uploads = [item for item in form.getlist("files") if hasattr(item, "read")]

    external_session_id = _validate_session_id(session_id)
    target_dir = request_workspace(settings, f"upload-{uuid.uuid4()}")["input"]
    prepared = await _prepare_uploaded_attachments(settings, uploads, target_dir)
    try:
        # Prepared uploads are already local, so this marker is never resolved as a URL.
        question_attachments = (
            [ChatAttachment(url="upload://local", type="file")]
            if prepared.has_attachments
            else []
        )
        chat_request = ChatRequest(
            question=ChatQuestion(text=text, attachments=question_attachments),
            user_id=user_id,
            session_id=session_id,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    return await _run_chat_turn(
        settings,
        user_id=chat_request.user_id,
        session_id=chat_request.session_id,
        request=chat_request,
        prepared_attachments=prepared,
        request_body=_serialize_audit_body(
            {
                "text": text,
                "user_id": user_id,
                "session_id": session_id,
                "files": [
                    {
                        "type": "image",
                        "filename": item.source_url.removeprefix("upload:"),
                        "mime_type": item.mime_type,
                        "size_bytes": item.size_bytes,
                        "sha256": item.sha256,
                    }
                    for item in prepared.images
                ]
                + [
                    {
                        "type": "file",
                        "filename": item.source_url.removeprefix("upload:"),
                        "mime_type": item.mime_type,
                        "size_bytes": item.size_bytes,
                        "sha256": item.sha256,
                    }
                    for item in prepared.files
                ],
            }
        ),
    )


async def _report_cleanup_loop(settings: Settings) -> None:
    while True:
        await asyncio.to_thread(cleanup_reports, settings)
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_env_file()
    settings = load_settings()
    init_db(settings)
    get_or_create_user(settings, DEFAULT_USER_ID)
    app.state.settings = settings
    report_cleanup_task = asyncio.create_task(_report_cleanup_loop(settings))
    try:
        yield
    finally:
        report_cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await report_cleanup_task


app = FastAPI(title="Hermes HTTP Gateway", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health(request: Request):
    settings = _settings(request)
    hermes_exists = bool(settings.hermes_bin) and (
        shutil.which(settings.hermes_bin) is not None or settings.hermes_bin.startswith("/")
    )
    return {
        "ok": True,
        "database": str(settings.database_url),
        "hermes_bin": settings.hermes_bin,
        "hermes_workdir": str(settings.hermes_workdir),
        "hermes_available": hermes_exists,
    }


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    settings = _settings(request)
    session = _admin_session_from_request(request, settings)
    if session is not None:
        return RedirectResponse(_public_path(settings, "/admin"), status_code=302)
    return HTMLResponse(
        render_login_page(
            username_default=settings.admin_username,
            password_configured=bool(settings.admin_password),
            public_base_path=settings.public_base_path,
        )
    )


@app.post("/admin/login")
def admin_login(request: Request, body: AdminLoginRequest):
    settings = _settings(request)
    _validate_admin_login(settings, body.username, body.password)
    session = create_admin_session(
        settings,
        username=settings.admin_username,
        ttl_seconds=settings.admin_session_ttl_seconds,
    )
    response = JSONResponse(
        {"ok": True, "redirect": _public_path(settings, "/admin"), "username": session["username"]}
    )
    _set_admin_cookie(response, settings, session["session_token"])
    return response


@app.get("/admin/logout")
def admin_logout(request: Request):
    settings = _settings(request)
    session_token = request.cookies.get(settings.admin_cookie_name, "").strip()
    if session_token:
        delete_admin_session(settings, session_token)
    response = RedirectResponse(_public_path(settings, "/admin/login"), status_code=302)
    _clear_admin_cookie(response, settings)
    return response


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    settings = _settings(request)
    session = _admin_session_from_request(request, settings)
    if session is None:
        return RedirectResponse(_public_path(settings, "/admin/login"), status_code=302)
    return HTMLResponse(
        render_dashboard_page(
            username=session["username"],
            csrf_token=_admin_csrf_token(str(session["session_token"])),
            public_base_path=settings.public_base_path,
        )
    )


@app.get("/admin/latency", response_class=HTMLResponse)
def admin_latency_page(request: Request):
    settings = _settings(request)
    session = _admin_session_from_request(request, settings)
    if session is None:
        return RedirectResponse(_public_path(settings, "/admin/login"), status_code=302)
    return HTMLResponse(render_latency_page(
        username=str(session["username"]),
        public_base_path=settings.public_base_path,
    ))


@app.get("/admin/chat", response_class=HTMLResponse)
def admin_chat_page(request: Request):
    settings = _settings(request)
    session = _admin_session_from_request(request, settings)
    if session is None:
        return RedirectResponse(_public_path(settings, "/admin/login"), status_code=302)
    return HTMLResponse(
        render_chat_page(
            username=session["username"],
            csrf_token=_admin_csrf_token(str(session["session_token"])),
            public_base_path=settings.public_base_path,
        )
    )


@app.post("/admin/v1/chat")
async def admin_chat(request: Request):
    settings = _settings(request)
    return await _admin_chat_request(request, settings)


@app.get("/admin/v1/dashboard")
def admin_dashboard(
    request: Request,
    user_query: str | None = None,
    user_limit: int = 100,
    user_offset: int = 0,
):
    settings = _settings(request)
    _require_admin_session(request, settings)
    return {
        "summary": {
            "users": count_users(settings),
            "conversations": count_conversations(settings),
            "messages": count_messages(settings),
            "requests": count_request_events(settings),
        },
        "users": list_users(
            settings,
            limit=user_limit,
            offset=user_offset,
            query=user_query,
        ),
        "user_pagination": {"limit": user_limit, "offset": user_offset},
    }


@app.get("/admin/v1/latency-report")
def admin_latency_report(request: Request):
    settings = _settings(request)
    _require_admin_session(request, settings)
    return latency_report(settings)


def _private_skill_catalog_or_503(settings: Settings) -> list[dict[str, Any]]:
    try:
        return list_private_skills(settings)
    except PrivateSkillCatalogError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/admin/v1/capabilities")
def capabilities(request: Request):
    settings = _settings(request)
    _require_admin_session(request, settings)
    if "scope" in request.query_params:
        raise HTTPException(status_code=422, detail="scope is no longer supported")
    return {
        "profile": DEFAULT_HERMES_PROFILE,
        "skills": _private_skill_catalog_or_503(settings),
    }


def _admin_private_skills_binding_payload(settings: Settings, user_id: str) -> dict[str, Any]:
    bound_skill_ids = [item["skill_id"] for item in list_user_private_skill_bindings(settings, user_id)]
    return {
        "user_id": user_id,
        "bound_skill_ids": bound_skill_ids,
    }


def _update_private_skill_bindings(
    settings: Settings,
    *,
    user_id: str,
    add: list[str],
    remove: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    add_ids = {str(skill_id).strip() for skill_id in add if str(skill_id).strip()}
    remove_ids = {str(skill_id).strip() for skill_id in remove if str(skill_id).strip()}
    overlap = sorted(add_ids & remove_ids)
    if overlap:
        raise HTTPException(status_code=422, detail={"conflicting_skill_ids": overlap})
    catalog = _private_skill_catalog_or_503(settings)
    enabled_ids = {str(item["id"]) for item in catalog if item.get("enabled")}
    invalid_ids = sorted(add_ids - enabled_ids)
    if invalid_ids:
        raise HTTPException(status_code=422, detail={"invalid_skill_ids": invalid_ids})
    try:
        bindings = update_user_private_skill_bindings(
            settings, user_id=user_id, add=add_ids, remove=remove_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return bindings, catalog


@app.get("/admin/v1/users/{user_id}")
def admin_user(request: Request, user_id: str):
    settings = _settings(request)
    _require_admin_session(request, settings)
    resolved_user_id = _resolve_user_id(user_id)
    user = get_or_create_user(settings, resolved_user_id)
    return {
        "user": user,
        "skills": _admin_private_skills_binding_payload(settings, resolved_user_id),
    }


@app.patch("/admin/v1/users/{user_id}")
def update_admin_user(
    request: Request,
    user_id: str,
    body: AdminUserUpdate,
):
    settings = _settings(request)
    session = _require_admin_session(request, settings)
    _require_admin_csrf(request, session)
    resolved_user_id = _resolve_user_id(user_id)
    user = get_or_create_user(settings, resolved_user_id)
    bindings, _ = _update_private_skill_bindings(
        settings,
        user_id=resolved_user_id,
        add=body.add,
        remove=body.remove,
    )
    return {
        "user": user,
        "skills": {
            "bound_skill_ids": [item["skill_id"] for item in bindings],
        },
    }


@app.get("/admin/v1/browse")
def admin_browse(
    request: Request,
    keyword: str = "",
    user_query: str = "",
    user_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    settings = _settings(request)
    _require_admin_session(request, settings)
    if not 1 <= limit <= 500 or offset < 0:
        raise HTTPException(status_code=422, detail="Invalid pagination")
    return search_admin_context(settings, query=keyword, user_query=user_query,
                                user_id=user_id, limit=limit, offset=offset)


@app.get("/admin/v1/conversations")
def admin_conversations(
    request: Request,
    query: str | None = None,
    user_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    settings = _settings(request)
    _require_admin_session(request, settings)
    return {
        "conversations": list_all_conversations(
            settings,
            limit=limit,
            offset=offset,
            query=query,
            user_id=user_id,
        )
    }


@app.get("/admin/v1/conversations/{session_id}/timeline")
def admin_conversation_timeline(request: Request, session_id: str):
    settings = _settings(request)
    _require_admin_session(request, settings)
    conversation_id = _validate_session_id(session_id)
    conversation = get_conversation_by_session(settings, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = [
        _message_payload(row) for row in list_messages_by_session(settings, conversation_id)
    ]
    stages_by_request = list_request_stages_by_session(settings, conversation_id)
    requests = []
    for row in list_request_events_by_session(settings, conversation_id):
        stages = stages_by_request.get(row["id"], [])
        stored_timing = None
        if isinstance(row.get("runtime_audit"), dict):
            candidate = row["runtime_audit"].get("timing")
            if isinstance(candidate, dict):
                stored_timing = candidate
        timing, stages = _request_timing(
            settings,
            conversation.get("hermes_session_id"),
            stages,
            window_start=row.get("created_at"),
            window_end=row.get("completed_at"),
            fallback_timing=stored_timing,
        )
        requests.append(_request_event_payload(row, stages, timing))
    timeline = sorted(
        [*messages, *requests],
        key=lambda item: (item["created_at"], 0 if item["event_type"] == "request" else 1),
    )
    return {
        "conversation": _conversation_payload(conversation),
        "messages": messages,
        "requests": requests,
        "timeline": timeline,
    }


@app.get("/admin/v1/search")
def admin_search(
    request: Request,
    keyword: str,
    user_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
):
    settings = _settings(request)
    _require_admin_session(request, settings)
    if "q" in getattr(request, "query_params", {}):
        raise HTTPException(status_code=422, detail="q is no longer supported; use keyword")
    return {
        "results": search_messages_admin(
            settings,
            query=keyword,
            user_id=user_id,
            external_session_id=_validate_session_id(session_id) if session_id else None,
            limit=limit,
        )
    }


def _report_response(request: Request, download_token: str, filename: str):
    try:
        path, filename = get_report_download(
            _settings(request), download_token, expected_filename=filename
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Report not found or expired") from exc
    return FileResponse(
        path,
        media_type="text/html",
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        },
    )


@app.get("/v1/reports/{filename}/{download_token}")
def report_page(request: Request, filename: str, download_token: str):
    return _report_response(request, download_token, filename)


@app.get("/v1/capabilities")
def public_capabilities(request: Request):
    settings = _settings(request)
    _require_gateway_key(request, settings)
    if "scope" in request.query_params:
        raise HTTPException(status_code=422, detail="scope is no longer supported")
    return {
        "skills": _private_skill_catalog_or_503(settings),
    }


def _public_private_skill_binding_payload(
    settings: Settings, user_id: str
) -> dict[str, Any]:
    catalog = _private_skill_catalog_or_503(settings)
    bound_skill_ids = [
        item["skill_id"] for item in list_user_private_skill_bindings(settings, user_id)
    ]
    available_ids = {str(item["id"]) for item in catalog if item.get("enabled")}
    return {
        "user_id": user_id,
        "bound_skill_ids": bound_skill_ids,
        "active_skill_ids": [skill_id for skill_id in bound_skill_ids if skill_id in available_ids],
        "unavailable_skill_ids": [skill_id for skill_id in bound_skill_ids if skill_id not in available_ids],
    }


@app.get("/v1/users/{user_id}/skills")
def public_user_private_skills(request: Request, user_id: str):
    settings = _settings(request)
    _require_gateway_key(request, settings)
    resolved_user_id = _resolve_user_id(user_id)
    get_or_create_user(settings, resolved_user_id)
    return _public_private_skill_binding_payload(settings, resolved_user_id)


@app.patch("/v1/users/{user_id}/skills")
def update_public_user_private_skills(
    request: Request,
    user_id: str,
    body: SkillBindingsUpdate,
):
    settings = _settings(request)
    _require_gateway_key(request, settings)
    resolved_user_id = _resolve_user_id(user_id)
    get_or_create_user(settings, resolved_user_id)
    _update_private_skill_bindings(
        settings,
        user_id=resolved_user_id,
        add=body.add,
        remove=body.remove,
    )
    return _public_private_skill_binding_payload(settings, resolved_user_id)


@app.get("/v1/sessions")
def sessions(request: Request, user_id: str | None = None, limit: int = 50, offset: int = 0):
    settings = _settings(request)
    _require_gateway_key(request, settings)
    resolved_user_id = _resolve_user_id(user_id)
    return {
        "sessions": [
            _public_conversation_payload(row)
            for row in list_conversations(settings, resolved_user_id, limit=limit, offset=offset)
        ]
    }


@app.get("/v1/messages")
def messages(
    request: Request,
    user_id: str | None = None,
    keyword: str | None = None,
    session_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    settings = _settings(request)
    _require_gateway_key(request, settings)
    if "q" in getattr(request, "query_params", {}):
        raise HTTPException(status_code=422, detail="q is no longer supported; use keyword")
    resolved_user_id = _resolve_user_id(user_id)
    query = (keyword or "").strip()
    if not session_id and not query:
        raise HTTPException(status_code=422, detail="keyword is required when session_id is not provided")

    conversation = None
    conversation_id = None
    if session_id:
        conversation_id = _validate_session_id(session_id)
        conversation = get_conversation(settings, resolved_user_id, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Session not found")

    if query:
        result_messages = [
            _search_message_payload(row)
            for row in search_messages(
                settings,
                user_id=resolved_user_id,
                query=query,
                external_session_id=conversation_id,
                limit=limit,
                offset=offset,
            )
        ]
    else:
        result_messages = [
            _message_payload(row)
            for row in list_messages(
                settings,
                resolved_user_id,
                conversation["external_session_id"],
                limit=limit,
                offset=offset,
            )
        ]
    return {
        "conversation": _public_conversation_payload(conversation) if conversation else None,
        "messages": result_messages,
        "limit": limit,
        "offset": offset,
    }


@app.post("/v1/chat")
async def chat(request: Request, body: ChatRequest):
    settings = _settings(request)
    _require_gateway_key(request, settings)
    return await _run_chat_turn(
        settings,
        user_id=body.user_id,
        session_id=body.session_id,
        request=body,
        request_body=_serialize_audit_body(body.model_dump(mode="json", exclude_none=True)),
    )


def main() -> None:
    load_env_file()
    settings = load_settings()
    uvicorn.run(
        "hermes_http_gateway.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
