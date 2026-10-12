"""Turn-scoped authorization for explicitly preloaded private skills."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Iterable

_LOCK = threading.Lock()
_DIRS_BY_TASK: dict[str, frozenset[Path]] = {}


def _resolve_dirs(entries: Iterable[Any]) -> frozenset[Path]:
    dirs: set[Path] = set()
    for entry in entries:
        raw = entry.get("skill_dir") if isinstance(entry, dict) else entry
        if not raw:
            continue
        try:
            path = Path(str(raw)).resolve()
        except (OSError, RuntimeError):
            continue
        if path.is_dir():
            dirs.add(path)
    return frozenset(dirs)


def bind_task_private_skills(task_id: str, entries: Iterable[Any]) -> None:
    """Grant one active turn access to the explicitly selected skill dirs."""
    if not task_id:
        return
    dirs = _resolve_dirs(entries)
    if not dirs:
        return
    with _LOCK:
        _DIRS_BY_TASK[str(task_id)] = dirs


def get_task_private_skill_dirs(task_id: str | None) -> frozenset[Path]:
    if not task_id:
        return frozenset()
    with _LOCK:
        return _DIRS_BY_TASK.get(str(task_id), frozenset())


def clear_task_private_skills(task_id: str | None) -> None:
    if not task_id:
        return
    with _LOCK:
        _DIRS_BY_TASK.pop(str(task_id), None)
