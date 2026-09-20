from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .locks import clear_session_process, is_session_turn_current, register_session_process


class HermesInvocationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        stdout: str = "",
        stderr: str = "",
        usage: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.usage = usage


class HermesInvocationCancelled(HermesInvocationError):
    pass


@dataclass(slots=True)
class HermesRunResult:
    answer: str
    session_id: str | None
    stdout: str
    stderr: str
    returncode: int
    usage: dict[str, Any] | None


def _base_env(settings: Settings) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("HERMES_YOLO_MODE", "1")
    if settings.source_tag:
        env.setdefault("HERMES_SESSION_SOURCE", settings.source_tag)
    return env


def _build_hermes_command(
    settings: Settings,
    *,
    profile: str,
    model: str | None = None,
    provider: str | None = None,
) -> list[str]:
    cmd = [settings.hermes_bin]
    if profile:
        cmd.extend(["-p", profile])
    if model:
        cmd.extend(["-m", model])
    if provider:
        cmd.extend(["--provider", provider])
    return cmd


def _read_usage_file(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _make_usage_path() -> Path:
    fd, path = tempfile.mkstemp(prefix="hermes-gateway-", suffix=".json")
    os.close(fd)
    return Path(path)


def _raise_if_cancelled(run_key: str | None, run_generation: int | None) -> None:
    if run_key is not None and run_generation is not None and not is_session_turn_current(run_key, run_generation):
        raise HermesInvocationCancelled("Hermes invocation superseded by a newer request")


def _run_command(
    settings: Settings,
    cmd: list[str],
    *,
    run_key: str | None,
    run_generation: int | None,
) -> subprocess.CompletedProcess[str]:
    _raise_if_cancelled(run_key, run_generation)
    process = subprocess.Popen(
        cmd,
        cwd=str(settings.hermes_workdir),
        env=_base_env(settings),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    registered = register_session_process(run_key, run_generation, process)
    if not registered:
        raise HermesInvocationCancelled("Hermes invocation superseded by a newer request")
    try:
        stdout, stderr = process.communicate(timeout=settings.timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        try:
            process.kill()
        except Exception:
            pass
        process.communicate()
        raise HermesInvocationError(f"Hermes timed out after {settings.timeout_seconds}s") from exc
    finally:
        clear_session_process(run_key, run_generation, process)

    _raise_if_cancelled(run_key, run_generation)
    return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)


def run_first_turn(
    settings: Settings,
    *,
    prompt: str,
    profile: str,
    model: str | None = None,
    provider: str | None = None,
    image_paths: list[Path] | None = None,
    attachment_root: Path | None = None,
    run_key: str | None = None,
    run_generation: int | None = None,
) -> HermesRunResult:
    usage_path = _make_usage_path()
    cmd = _build_hermes_command(
        settings,
        profile=profile,
        model=model or settings.default_model,
        provider=provider or settings.default_provider,
    )
    images = image_paths or []
    if images or attachment_root:
        cmd.extend(["chat", "-Q", "-q", prompt, "--usage-file", str(usage_path)])
        if attachment_root:
            cmd.extend(["--attachment-root", str(attachment_root)])
        for image_path in images:
            cmd.extend(["--image", str(image_path)])
    else:
        cmd.extend(["-z", prompt, "--usage-file", str(usage_path)])

    try:
        completed = _run_command(
            settings,
            cmd,
            run_key=run_key,
            run_generation=run_generation,
        )
    except FileNotFoundError as exc:
        raise HermesInvocationError(f"Hermes binary not found: {settings.hermes_bin}") from exc
    finally:
        usage = _read_usage_file(usage_path)
        try:
            usage_path.unlink(missing_ok=True)
        except Exception:
            pass

    if completed.returncode != 0:
        raise HermesInvocationError(
            "Hermes returned a non-zero exit code",
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            usage=usage,
        )

    session_id = None
    if isinstance(usage, dict):
        value = usage.get("session_id")
        if isinstance(value, str) and value.strip():
            session_id = value.strip()

    answer = completed.stdout.strip()
    return HermesRunResult(
        answer=answer,
        session_id=session_id,
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
        usage=usage,
    )


def run_resume_turn(
    settings: Settings,
    *,
    prompt: str,
    hermes_session_id: str,
    profile: str,
    model: str | None = None,
    provider: str | None = None,
    image_paths: list[Path] | None = None,
    attachment_root: Path | None = None,
    run_key: str | None = None,
    run_generation: int | None = None,
) -> HermesRunResult:
    usage_path = _make_usage_path()
    cmd = _build_hermes_command(
        settings,
        profile=profile,
        model=model or settings.default_model,
        provider=provider or settings.default_provider,
    )
    images = image_paths or []
    cmd.extend(["chat", "--resume", hermes_session_id, "-Q", "-q", prompt, "--usage-file", str(usage_path)])
    if attachment_root:
        cmd.extend(["--attachment-root", str(attachment_root)])
    for image_path in images:
        cmd.extend(["--image", str(image_path)])

    try:
        completed = _run_command(
            settings,
            cmd,
            run_key=run_key,
            run_generation=run_generation,
        )
    except FileNotFoundError as exc:
        raise HermesInvocationError(f"Hermes binary not found: {settings.hermes_bin}") from exc
    finally:
        usage = _read_usage_file(usage_path)
        try:
            usage_path.unlink(missing_ok=True)
        except Exception:
            pass

    if completed.returncode != 0:
        raise HermesInvocationError(
            "Hermes returned a non-zero exit code",
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            usage=usage,
        )

    session_id = hermes_session_id
    if isinstance(usage, dict):
        value = usage.get("session_id")
        if isinstance(value, str) and value.strip():
            session_id = value.strip()

    return HermesRunResult(
        answer=completed.stdout.strip(),
        session_id=session_id,
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
        usage=usage,
    )
