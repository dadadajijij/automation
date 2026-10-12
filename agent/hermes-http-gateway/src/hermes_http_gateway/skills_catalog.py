from __future__ import annotations

import json
import subprocess
import threading
import time
from typing import Any

from .config import Settings

DEFAULT_PROFILE = "default"
_CACHE_TTL_SECONDS = 45.0
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


class PrivateSkillCatalogError(RuntimeError):
    pass


def _normalize_skill(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    return {
        "id": name,
        "name": name,
        "category": str(raw.get("category") or "").strip(),
        "description": str(raw.get("description") or "").strip(),
        "version": str(raw.get("version") or "").strip(),
        "enabled": bool(raw.get("enabled")),
    }


def list_private_skills(settings: Settings) -> list[dict[str, Any]]:
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(DEFAULT_PROFILE)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return [dict(skill) for skill in cached[1]]

    command = [
        settings.hermes_bin,
        "-p",
        DEFAULT_PROFILE,
        "skills",
        "list",
        "--scope",
        "private",
        "--json",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(settings.hermes_workdir),
            env=None,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except OSError as exc:
        raise PrivateSkillCatalogError(f"Unable to run Hermes skill catalog: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PrivateSkillCatalogError("Hermes private skill catalog timed out") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()
        raise PrivateSkillCatalogError(f"Hermes private skill catalog failed: {detail[:300]}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PrivateSkillCatalogError("Hermes private skill catalog returned invalid JSON") from exc

    raw_skills = payload.get("skills") if isinstance(payload, dict) else None
    if not isinstance(raw_skills, list):
        raise PrivateSkillCatalogError("Hermes private skill catalog has no skills list")
    skills = [item for raw in raw_skills if (item := _normalize_skill(raw)) is not None]
    skills.sort(key=lambda item: (item["category"], item["name"]))
    with _cache_lock:
        _cache[DEFAULT_PROFILE] = (time.monotonic(), skills)
    return [dict(skill) for skill in skills]


def clear_private_skill_catalog_cache() -> None:
    with _cache_lock:
        _cache.clear()
