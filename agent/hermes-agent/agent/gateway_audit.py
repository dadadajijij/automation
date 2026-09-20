"""Small, opt-in-safe runtime audit payload for gateway usage reports."""

from __future__ import annotations

from typing import Any, Optional

_MAX_EVENTS = 64


def _text(value: Any, limit: int = 160) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def reset(agent: Any) -> None:
    agent._gateway_audit_events = []


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
    result: dict[str, Any] = {
        "version": 1,
        "main": {
            "provider": _text(getattr(agent, "provider", None)) or "unknown",
            "model": _text(getattr(agent, "model", None)) or "unknown",
        },
        "events": list(getattr(agent, "_gateway_audit_events", []) or [])[:_MAX_EVENTS],
    }
    if isinstance(context_window, int) and context_window > 0:
        result["context_window"] = context_window
    return result
