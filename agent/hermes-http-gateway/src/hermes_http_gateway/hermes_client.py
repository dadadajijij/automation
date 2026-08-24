from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings


class HermesInvocationError(RuntimeError):
    def __init__(self, message: str, *, returncode: int | None = None, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


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


def run_first_turn(
    settings: Settings,
    *,
    prompt: str,
    profile: str,
    model: str | None = None,
    provider: str | None = None,
) -> HermesRunResult:
    usage_path = Path(tempfile.mkstemp(prefix="hermes-gateway-", suffix=".json")[1])
    cmd = _build_hermes_command(
        settings,
        profile=profile,
        model=model or settings.default_model,
        provider=provider or settings.default_provider,
    )
    cmd.extend(["-z", prompt, "--usage-file", str(usage_path)])

    try:
        completed = subprocess.run(
            cmd,
            cwd=str(settings.hermes_workdir),
            env=_base_env(settings),
            capture_output=True,
            text=True,
            timeout=settings.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HermesInvocationError(f"Hermes timed out after {settings.timeout_seconds}s") from exc
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
) -> HermesRunResult:
    cmd = _build_hermes_command(
        settings,
        profile=profile,
        model=model or settings.default_model,
        provider=provider or settings.default_provider,
    )
    cmd.extend(["chat", "--resume", hermes_session_id, "-Q", "-q", prompt])

    try:
        completed = subprocess.run(
            cmd,
            cwd=str(settings.hermes_workdir),
            env=_base_env(settings),
            capture_output=True,
            text=True,
            timeout=settings.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HermesInvocationError(f"Hermes timed out after {settings.timeout_seconds}s") from exc
    except FileNotFoundError as exc:
        raise HermesInvocationError(f"Hermes binary not found: {settings.hermes_bin}") from exc

    if completed.returncode != 0:
        raise HermesInvocationError(
            "Hermes returned a non-zero exit code",
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    return HermesRunResult(
        answer=completed.stdout.strip(),
        session_id=hermes_session_id,
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
        usage=None,
    )
