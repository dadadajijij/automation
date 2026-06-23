import re
from typing import List, Optional
from urllib.parse import urljoin, urlparse

from .models import HtmlUrlExtractor, HtmlUrlReference


def normalize_runtime_url_path(url: str, current_path: str, *, base_origin: Optional[str] = None) -> Optional[str]:
    stripped = url.strip()
    if not stripped or stripped.startswith(("#", "mailto:", "tel:", "data:", "javascript:")):
        return None
    if stripped.startswith("//"):
        parsed = urlparse(f"http:{stripped}")
        if base_origin:
            base_host = urlparse(base_origin).netloc.lower()
            if parsed.netloc and parsed.netloc.lower() != base_host:
                return None
        return parsed.path or None
    if re.match(r"^https?://", stripped):
        parsed = urlparse(stripped)
        if base_origin:
            base_host = urlparse(base_origin).netloc.lower()
            if parsed.netloc and parsed.netloc.lower() != base_host:
                return None
        return parsed.path or None
    if stripped.startswith("/"):
        return stripped
    base_url = f"{base_origin or 'http://local'}{current_path}"
    parsed = urlparse(urljoin(base_url, stripped))
    return parsed.path or "/"


def translate_external_to_upstream_path(external_path: str, base_path: str, proxy_mode: str) -> str:
    normalized = external_path if external_path.startswith("/") else f"/{external_path}"
    if proxy_mode != "strip_prefix":
        return normalized
    if normalized == base_path:
        return "/"
    if normalized.startswith(f"{base_path}/"):
        remainder = normalized[len(base_path):]
        return remainder if remainder.startswith("/") else f"/{remainder}"
    return normalized


def translate_upstream_to_external_path(upstream_path: str, base_path: str, proxy_mode: str) -> str:
    normalized = upstream_path if upstream_path.startswith("/") else f"/{upstream_path}"
    if proxy_mode != "strip_prefix":
        return normalized
    if normalized == "/":
        return f"{base_path}/"
    if normalized.startswith("/"):
        return f"{base_path}{normalized}"
    return f"{base_path}/{normalized}"


def extract_html_urls(html: str) -> List[HtmlUrlReference]:
    parser = HtmlUrlExtractor()
    parser.feed(html)
    return parser.references
