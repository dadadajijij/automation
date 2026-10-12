"""Small, opt-in-safe runtime audit payload for gateway usage reports."""

from __future__ import annotations

import os
import threading
from typing import Any, Optional

_MAX_EVENTS = 64
_task_skill_events: dict[str, list[dict[str, Any]]] = {}
_task_lock = threading.Lock()


def _text(value: Any, limit: int = 160) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def reset(agent: Any) -> None:
    agent._gateway_audit_events = []
    agent._gateway_skill_audit = []


def _skill_text(value: Any, limit: int = 240) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def _append_skill_event(event: dict[str, Any], task_id: str | None = None) -> None:
    """Best-effort side-channel for gateways running an older usage writer."""
    if task_id:
        with _task_lock:
            bucket = _task_skill_events.setdefault(str(task_id), [])
            if len(bucket) < _MAX_EVENTS:
                bucket.append(event)
    path = os.environ.get("HERMES_GATEWAY_SKILL_AUDIT_FILE", "").strip()
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            import json
            handle.write(json.dumps(event, ensure_ascii=True) + "\n")
            handle.flush()
    except Exception:
        pass


def record_skill(
    agent: Any,
    skill_id: str,
    status: str,
    *,
    task_id: str | None = None,
    duration_ms: float | None = None,
    error_type: str | None = None,
    reason: str | None = None,
    summary: str | None = None,
) -> None:
    skill_id = _skill_text(skill_id, 160)
    if not skill_id:
        return
    event: dict[str, Any] = {"skill_id": skill_id, "status": _skill_text(status, 40) or "unknown"}
    if duration_ms is not None:
        event["duration_ms"] = max(0.0, round(float(duration_ms), 1))
    for key, value in (("error_type", error_type), ("reason", reason), ("summary", summary)):
        cleaned = _skill_text(value)
        if cleaned:
            event[key] = cleaned
    events = getattr(agent, "_gateway_skill_audit", None)
    if not isinstance(events, list):
        events = []
        agent._gateway_skill_audit = events
    if len(events) >= _MAX_EVENTS:
        return
    events.append(event)
    _append_skill_event(event, task_id=task_id)


def record_task_skill(
    task_id: str | None,
    skill_id: str,
    status: str,
    **kwargs: Any,
) -> None:
    """Record Skill loading that happens before the AIAgent exists."""
    event: dict[str, Any] = {"skill_id": _skill_text(skill_id, 160), "status": _skill_text(status, 40) or "unknown"}
    event = {key: value for key, value in event.items() if value}
    for key in ("duration_ms", "error_type", "reason", "summary"):
        value = kwargs.get(key)
        if key == "duration_ms" and isinstance(value, (int, float)):
            event[key] = max(0.0, round(float(value), 1))
        elif isinstance(value, str) and value.strip():
            event[key] = value.strip()[:240]
    _append_skill_event(event, task_id=task_id)


def record(
    agent: Any,
    kind: str,
    *,
    task: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    input_mode: str | None = None,
    from_provider: str | None = None,
    from_model: str | None = None,
    reason: str | None = None,
) -> None:
    events = getattr(agent, "_gateway_audit_events", None)
    if not isinstance(events, list) or len(events) >= _MAX_EVENTS:
        return
    event = {"kind": kind}
    for key, value in (
        ("task", task),
        ("provider", provider),
        ("model", model),
        ("input_mode", input_mode),
        ("from_provider", from_provider),
        ("from_model", from_model),
        ("reason", reason),
    ):
        cleaned = _text(value)
        if cleaned:
            event[key] = cleaned
    if events and events[-1] == event:
        return
    events.append(event)


def record_runtime(
    main_runtime: Any,
    *,
    task: str | None,
    provider: str | None,
    model: str | None,
) -> None:
    if not isinstance(main_runtime, dict):
        return
    events = main_runtime.get("gateway_audit_events")
    if not isinstance(events, list) or len(events) >= _MAX_EVENTS:
        return
    event = {"kind": "auxiliary", "task": _text(task) or "auxiliary"}
    for key, value in (("provider", provider), ("model", model)):
        cleaned = _text(value)
        if cleaned:
            event[key] = cleaned
    if events and events[-1] == event:
        return
    events.append(event)


def build(agent: Any) -> dict[str, Any]:
    context_window = getattr(getattr(agent, "context_compressor", None), "context_length", None)
    skills = list(getattr(agent, "_gateway_skill_audit", []) or [])[:_MAX_EVENTS]
    task_id = getattr(agent, "session_id", None)
    if task_id:
        with _task_lock:
            skills.extend(_task_skill_events.pop(str(task_id), []))
    result: dict[str, Any] = {
        "version": 1,
        "main": {
            "provider": _text(getattr(agent, "provider", None)) or "unknown",
            "model": _text(getattr(agent, "model", None)) or "unknown",
        },
        "events": list(getattr(agent, "_gateway_audit_events", []) or [])[:_MAX_EVENTS],
        "skills": skills[:_MAX_EVENTS],
    }
    if isinstance(context_window, int) and context_window > 0:
        result["context_window"] = context_window
    return result
