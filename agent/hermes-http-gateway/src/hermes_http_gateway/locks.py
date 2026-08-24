from __future__ import annotations

import asyncio
import threading

_lock_registry: dict[str, asyncio.Lock] = {}
_registry_lock = threading.Lock()


def get_session_lock(key: str) -> asyncio.Lock:
    with _registry_lock:
        lock = _lock_registry.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _lock_registry[key] = lock
        return lock
