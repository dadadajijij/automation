from __future__ import annotations

import hashlib
import ipaddress
import mimetypes
import re
import shutil
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import Settings


class AttachmentError(ValueError):
    pass


@dataclass(slots=True)
class DownloadedImage:
    source_url: str
    local_path: Path
    mime_type: str
    size_bytes: int
    sha256: str

    def public_payload(self) -> dict[str, object]:
        return {
            "url": self.source_url,
            "type": "image",
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "local_path": str(self.local_path),
        }


@dataclass(slots=True)
class DownloadedFile:
    source_url: str
    local_path: Path
    mime_type: str
    size_bytes: int
    sha256: str

    def public_payload(self) -> dict[str, object]:
        return {
            "url": self.source_url,
            "type": "file",
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "local_path": str(self.local_path),
        }


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_MIME_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_SAFE_SUFFIX_RE = re.compile(r"^\.[A-Za-z0-9][A-Za-z0-9._-]{0,15}$")


def _detect_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _validate_url_is_public(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise AttachmentError("attachment url only supports http or https")
    if not parsed.hostname:
        raise AttachmentError("attachment url must include a host")

    host = parsed.hostname.strip()
    try:
        ip = ipaddress.ip_address(host)
        addresses = [ip]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        except socket.gaierror as exc:
            raise AttachmentError(f"attachment host cannot be resolved: {host}") from exc
        addresses = []
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue

    if not addresses:
        raise AttachmentError("attachment host did not resolve to an IP address")
    for ip in addresses:
        if not ip.is_global:
            raise AttachmentError("attachment url resolves to a non-public address")


def _read_response_body(response, max_bytes: int, *, label: str = "attachment") -> bytes:  # noqa: ANN001
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise AttachmentError(f"{label} exceeds max size of {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _download_url(settings: Settings, *, url: str, accept: str) -> tuple[bytes, str]:
    current_url = url.strip()
    if not current_url:
        raise AttachmentError("attachment url cannot be empty")

    opener = build_opener(_NoRedirect)
    redirects = 0
    while True:
        _validate_url_is_public(current_url)
        req = Request(
            current_url,
            headers={
                "User-Agent": "hermes-http-gateway/0.1",
                "Accept": accept,
            },
        )
        try:
            response = opener.open(req, timeout=settings.attachment_download_timeout_seconds)
        except HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location", "").strip()
                if not location:
                    raise AttachmentError("attachment redirect missing Location header") from exc
                redirects += 1
                if redirects > settings.attachment_redirect_limit:
                    raise AttachmentError("attachment redirect limit exceeded") from exc
                current_url = urljoin(current_url, location)
                continue
            raise AttachmentError(f"attachment download failed with HTTP {exc.code}") from exc
        except URLError as exc:
            raise AttachmentError(f"attachment download failed: {exc.reason}") from exc
        break

    with response:
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        data = _read_response_body(response, settings.attachment_max_bytes)
    return data, content_type or "application/octet-stream"


def _safe_file_suffix(url: str, mime_type: str) -> str:
    suffix = Path(urlparse(url).path).suffix
    if suffix and _SAFE_SUFFIX_RE.match(suffix):
        return suffix[:16]
    guessed = mimetypes.guess_extension(mime_type or "")
    if guessed and _SAFE_SUFFIX_RE.match(guessed):
        return guessed[:16]
    return ".bin"


def cleanup_old_attachment_dirs(settings: Settings) -> None:
    root = settings.attachment_storage_root
    if not root.exists():
        return

    retention_days = max(1, settings.attachment_retention_days)
    cutoff = datetime.now().date() - timedelta(days=retention_days - 1)
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            child_date = datetime.strptime(child.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if child_date < cutoff:
            shutil.rmtree(child, ignore_errors=True)


def attachment_session_dir(settings: Settings, external_session_id: str) -> Path:
    cleanup_old_attachment_dirs(settings)
    today = datetime.now().date().isoformat()
    target = settings.attachment_storage_root / today / external_session_id
    target.mkdir(parents=True, exist_ok=True)
    return target


def download_image(settings: Settings, *, url: str, target_dir: Path) -> DownloadedImage:
    data, content_type = _download_url(
        settings,
        url=url,
        accept="image/png,image/jpeg,image/webp,image/gif;q=0.8,*/*;q=0.1",
    )
    return store_image(
        data=data,
        source=url,
        declared_content_type=content_type,
        target_dir=target_dir,
    )


def download_file(settings: Settings, *, url: str, target_dir: Path) -> DownloadedFile:
    data, content_type = _download_url(
        settings,
        url=url,
        accept="text/*,application/json,application/pdf,application/octet-stream,*/*;q=0.1",
    )
    return store_file(
        data=data,
        source=url,
        declared_content_type=content_type,
        target_dir=target_dir,
    )


def store_image(
    *,
    data: bytes,
    source: str,
    declared_content_type: str,
    target_dir: Path,
) -> DownloadedImage:
    content_type = declared_content_type.split(";", 1)[0].strip().lower()
    if content_type and content_type not in _MIME_SUFFIX and not content_type.startswith("application/octet-stream"):
        raise AttachmentError(f"unsupported image content-type: {content_type}")

    mime_type = _detect_image_mime(data)
    if mime_type is None:
        raise AttachmentError("attachment is not a supported image")

    digest = hashlib.sha256(data).hexdigest()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{digest}{_MIME_SUFFIX[mime_type]}"
    path.write_bytes(data)
    return DownloadedImage(
        source_url=source,
        local_path=path,
        mime_type=mime_type,
        size_bytes=len(data),
        sha256=digest,
    )


def store_file(
    *,
    data: bytes,
    source: str,
    declared_content_type: str,
    target_dir: Path,
) -> DownloadedFile:
    content_type = declared_content_type.split(";", 1)[0].strip().lower() or "application/octet-stream"
    digest = hashlib.sha256(data).hexdigest()
    suffix = _safe_file_suffix(source, content_type)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{digest}{suffix}"
    path.write_bytes(data)
    return DownloadedFile(
        source_url=source,
        local_path=path,
        mime_type=content_type,
        size_bytes=len(data),
        sha256=digest,
    )
