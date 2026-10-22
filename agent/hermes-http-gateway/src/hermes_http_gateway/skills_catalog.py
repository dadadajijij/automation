from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from typing import Any

from .config import Settings

DEFAULT_PROFILE = "default"
_CACHE_TTL_SECONDS = 45.0
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_category_names_cache: dict[str, tuple[int, dict[str, str]]] = {}
_skill_names_cache: dict[str, tuple[int, dict[str, str]]] = {}
_logger = logging.getLogger(__name__)


class PrivateSkillCatalogError(RuntimeError):
    pass


def _category_names(settings: Settings) -> dict[str, str]:
    path = settings.category_names_file
    cache_key = str(path)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        _logger.warning("Category names file is unavailable: %s", path)
        return {}
    with _cache_lock:
        cached = _category_names_cache.get(cache_key)
        if cached and cached[0] == mtime_ns:
            return dict(cached[1])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _logger.warning("Unable to load category names file %s: %s", path, exc)
        return {}
    if not isinstance(payload, dict):
        _logger.warning("Category names file must contain a JSON object: %s", path)
        return {}
    mapping = {
        str(key).strip(): str(value).strip()
        for key, value in payload.items()
        if str(key).strip() and isinstance(value, str) and value.strip()
    }
    with _cache_lock:
        _category_names_cache[cache_key] = (mtime_ns, mapping)
    return dict(mapping)


def _skill_names(settings: Settings) -> dict[str, str]:
    path = settings.skill_names_file
    cache_key = str(path)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        _logger.warning("Skill names file is unavailable: %s", path)
        return {}
    with _cache_lock:
        cached = _skill_names_cache.get(cache_key)
        if cached and cached[0] == mtime_ns:
            return dict(cached[1])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _logger.warning("Unable to load skill names file %s: %s", path, exc)
        return {}
    if not isinstance(payload, dict):
        _logger.warning("Skill names file must contain a JSON object: %s", path)
        return {}
    mapping = {
        str(key).strip(): value.strip()
        for key, value in payload.items()
        if str(key).strip() and isinstance(value, str) and value.strip()
    }
    with _cache_lock:
        _skill_names_cache[cache_key] = (mtime_ns, mapping)
    return dict(mapping)


def _normalize_skill(
    raw: Any,
    category_names: dict[str, str] | None = None,
    skill_names: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    category = str(raw.get("category") or "").strip()
    return {
        "id": name,
        "name": (skill_names or {}).get(name, name),
        "category": (category_names or {}).get(category, category),
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
        raise PrivateSkillCatalogError("Hermes skill catalog timed out") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()
        raise PrivateSkillCatalogError(f"Hermes skill catalog failed: {detail[:300]}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PrivateSkillCatalogError("Hermes skill catalog returned invalid JSON") from exc

    raw_skills = payload.get("skills") if isinstance(payload, dict) else None
    if not isinstance(raw_skills, list):
        raise PrivateSkillCatalogError("Hermes skill catalog has no skills list")
    category_names = _category_names(settings)
    skill_names = _skill_names(settings)
    skills = [
        item
        for raw in raw_skills
        if (item := _normalize_skill(raw, category_names, skill_names)) is not None
    ]
    skills.sort(key=lambda item: (item["category"], item["name"], item["id"]))
    with _cache_lock:
        _cache[DEFAULT_PROFILE] = (time.monotonic(), skills)
    return [dict(skill) for skill in skills]


def clear_private_skill_catalog_cache() -> None:
    with _cache_lock:
        _cache.clear()
        _category_names_cache.clear()
        _skill_names_cache.clear()
