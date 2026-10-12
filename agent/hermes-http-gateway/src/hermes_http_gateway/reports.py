from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import stat
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from .config import Settings


logger = logging.getLogger(__name__)
TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")


def cleanup_reports(settings: Settings) -> None:
    """Delete expired snapshots; active links keep their original expiry."""
    root = settings.report_storage_root
    if not root.exists():
        return
    try:
        for directory in root.iterdir():
            if not TOKEN_RE.fullmatch(directory.name) or directory.is_symlink():
                continue
            try:
                metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
                if float(metadata["expires_at"]) <= time.time():
                    shutil.rmtree(directory)
            except (OSError, ValueError, KeyError, TypeError):
                logger.warning("Unable to clean a report snapshot", exc_info=True)
    except OSError:
        logger.warning("Unable to scan report storage", exc_info=True)


def _publish_report(
    settings: Settings,
    source: Path,
    *,
    user_id: str,
    session_id: str,
    request_id: str,
) -> str:
    source = source.resolve(strict=True)
    if not source.is_relative_to(settings.report_source_root.resolve()):
        raise ValueError("Report path is outside the allowed directory")
    if source.suffix.lower() != ".html":
        raise ValueError("Only HTML reports can be published")
    base_url = settings.report_public_base_url.rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("REPORT_PUBLIC_BASE_URL must be an HTTPS gateway base URL")
    if settings.report_retention_days <= 0 or settings.report_max_bytes <= 0:
        raise ValueError("Report limits must be positive")

    storage = settings.report_storage_root
    storage.mkdir(parents=True, exist_ok=True, mode=0o700)
    cleanup_reports(settings)
    token = secrets.token_hex(32)
    # Stage both files before making the snapshot available under its token.
    with tempfile.TemporaryDirectory(prefix=".report-", dir=storage) as staging_name:
        staging = Path(staging_name)
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as input_file:
            info = os.fstat(input_file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > settings.report_max_bytes:
                raise ValueError("Report must be a regular file within the size limit")
            with (staging / "report.html").open("wb") as output_file:
                total = 0
                while chunk := input_file.read(64 * 1024):
                    total += len(chunk)
                    if total > settings.report_max_bytes:
                        raise ValueError("Report exceeds the size limit")
                    output_file.write(chunk)
        metadata = {
            "filename": source.name,
            "created_at": time.time(),
            "expires_at": time.time() + settings.report_retention_days * 86400,
            "user_id": user_id,
            "session_id": session_id,
            "request_id": request_id,
        }
        (staging / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        os.rename(staging, storage / token)
    return f"{base_url}/v1/reports/{quote(source.name, safe='')}/{token}"


def publish_report_answer(
    settings: Settings,
    answer: str,
    *,
    user_id: str,
    session_id: str,
    request_id: str,
) -> str:
    root = re.escape(str(settings.report_source_root).rstrip("/"))
    pattern = re.compile(r"(?<![\w/])(?:file://)?" + root + r"/[^\s`<>\"'()\[\]]+?\.html\b", re.I)
    match = pattern.search(answer)
    if match is None:
        return answer
    local_reference = match.group(0)
    try:
        link = _publish_report(
            settings,
            Path(local_reference.removeprefix("file://")),
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
        )
    except (OSError, ValueError) as exc:
        logger.warning("Report publication failed for request %s: %s", request_id, exc)
        link = "报告暂不可用"
    return answer.replace(local_reference, link)


def get_report_download(
    settings: Settings, token: str, *, expected_filename: str | None = None
) -> tuple[Path, str]:
    if not TOKEN_RE.fullmatch(token):
        raise FileNotFoundError("Report not found or expired")
    directory = settings.report_storage_root / token
    try:
        if directory.is_symlink():
            raise ValueError("Invalid report directory")
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        if float(metadata["expires_at"]) <= time.time():
            shutil.rmtree(directory)
            raise ValueError("Report expired")
        path = directory / "report.html"
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing report snapshot")
        filename = str(metadata["filename"])
        if Path(filename).name != filename or not filename.lower().endswith(".html"):
            raise ValueError("Invalid report filename")
        if expected_filename is not None and expected_filename != filename:
            raise ValueError("Report filename does not match the download token")
        return path, filename
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FileNotFoundError("Report not found or expired") from exc
