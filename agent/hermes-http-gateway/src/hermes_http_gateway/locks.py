from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from typing import Any

_lock_registry: dict[str, asyncio.Lock] = {}
_registry_lock = threading.Lock()
_run_registry_lock = threading.Lock()
_run_registry: dict[str, SessionRun] = {}


@dataclass(slots=True)
class SessionRun:
    generation: int = 0
    process: Any | None = None
    active_attachment_downloads: int = 0
    pending_images: list[Any] | None = None
    pending_files: list[Any] | None = None


_run_registry_condition = threading.Condition(_run_registry_lock)


def _get_or_create_run_locked(key: str) -> SessionRun:
    run = _run_registry.get(key)
    if run is None:
        run = SessionRun()
        _run_registry[key] = run
    return run


def get_session_lock(key: str) -> asyncio.Lock:
    with _registry_lock:
        lock = _lock_registry.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _lock_registry[key] = lock
        return lock


def _terminate_process(process: Any) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except Exception:
        try:
            process.terminate()
        except Exception:
            return

    def kill_if_needed() -> None:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        except Exception:
            pass

    threading.Thread(target=kill_if_needed, daemon=True).start()


def begin_session_turn(key: str) -> int:
    previous_process: Any | None = None
    with _run_registry_condition:
        run = _get_or_create_run_locked(key)
        run.generation += 1
        previous_process = run.process
        run.process = None
        generation = run.generation
    if previous_process is not None:
        _terminate_process(previous_process)
    return generation


def is_session_turn_current(key: str, generation: int) -> bool:
    with _run_registry_condition:
        run = _run_registry.get(key)
        return run is not None and run.generation == generation


def register_session_process(key: str | None, generation: int | None, process: Any) -> bool:
    if key is None or generation is None:
        return True
    should_terminate = False
    with _run_registry_condition:
        run = _run_registry.get(key)
        if run is None or run.generation != generation:
            should_terminate = True
        else:
            run.process = process
    if should_terminate:
        _terminate_process(process)
        return False
    return True


def clear_session_process(key: str | None, generation: int | None, process: Any) -> None:
    if key is None or generation is None:
        return
    with _run_registry_condition:
        run = _run_registry.get(key)
        if run is not None and run.generation == generation and run.process is process:
            run.process = None


def wait_for_attachment_downloads(key: str) -> None:
    with _run_registry_condition:
        while True:
            run = _run_registry.get(key)
            if run is None or run.active_attachment_downloads == 0:
                return
            _run_registry_condition.wait()


def begin_attachment_download(key: str) -> None:
    with _run_registry_condition:
        run = _get_or_create_run_locked(key)
        run.active_attachment_downloads += 1


def finish_attachment_download(
    key: str,
    *,
    images: list[Any] | None = None,
    files: list[Any] | None = None,
) -> None:
    with _run_registry_condition:
        run = _get_or_create_run_locked(key)
        run.active_attachment_downloads = max(0, run.active_attachment_downloads - 1)
        if images:
            if run.pending_images is None:
                run.pending_images = []
            run.pending_images.extend(images)
        if files:
            if run.pending_files is None:
                run.pending_files = []
            run.pending_files.extend(files)
        _run_registry_condition.notify_all()


def get_pending_attachments(key: str) -> tuple[list[Any], list[Any]]:
    with _run_registry_condition:
        run = _run_registry.get(key)
        if run is None:
            return [], []
        return list(run.pending_images or []), list(run.pending_files or [])


def clear_pending_attachments(
    key: str,
    *,
    images: list[Any] | None = None,
    files: list[Any] | None = None,
) -> None:
    image_paths = {str(item.local_path) for item in images or []}
    file_paths = {str(item.local_path) for item in files or []}
    with _run_registry_condition:
        run = _run_registry.get(key)
        if run is None:
            return
        if image_paths and run.pending_images:
            run.pending_images = [item for item in run.pending_images if str(item.local_path) not in image_paths]
        if file_paths and run.pending_files:
            run.pending_files = [item for item in run.pending_files if str(item.local_path) not in file_paths]
