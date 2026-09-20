from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = PROJECT_ROOT.parent
DEFAULT_HERMES_REPO = AGENT_ROOT / "hermes-agent"
DEFAULT_DOTENV_PATH = PROJECT_ROOT / ".env"


def load_env_file(path: Path | None = None) -> int:
    """Load simple KEY=VALUE pairs from a .env file into os.environ.

    Existing environment variables win. Returns the number of values loaded.
    """

    dotenv_path = path or DEFAULT_DOTENV_PATH
    if not dotenv_path.exists():
        return 0

    loaded = 0
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        key = name.strip()
        if not key or key in os.environ:
            continue
        val = value.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in {'"', "'"}:
            val = val[1:-1]
        os.environ[key] = val
        loaded += 1
    return loaded


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def _detect_hermes_bin() -> str:
    explicit = os.environ.get("HERMES_BIN", "").strip()
    if explicit:
        return explicit

    candidates = [
        DEFAULT_HERMES_REPO / ".venv" / "bin" / "hermes",
        DEFAULT_HERMES_REPO / "hermes",
        Path(shutil.which("hermes") or ""),
    ]
    for candidate in candidates:
        if candidate and str(candidate) and candidate.exists():
            return str(candidate)

    return "hermes"


def _default_workdir() -> Path:
    workdir = os.environ.get("HERMES_WORKDIR", "").strip()
    if workdir:
        return _env_path("HERMES_WORKDIR", DEFAULT_HERMES_REPO)
    if DEFAULT_HERMES_REPO.exists():
        return DEFAULT_HERMES_REPO
    return PROJECT_ROOT


@dataclass(frozen=True, slots=True)
class Settings:
    host: str
    port: int
    database_url: Path
    hermes_bin: str
    hermes_workdir: Path
    hermes_profile_default: str
    gateway_api_key: str | None
    admin_username: str
    admin_password: str | None
    admin_cookie_name: str
    admin_session_ttl_seconds: int
    admin_cookie_secure: bool
    public_base_path: str
    timeout_seconds: int
    source_tag: str
    default_model: str | None
    default_provider: str | None
    attachment_max_bytes: int
    attachment_download_timeout_seconds: int
    attachment_max_images: int
    attachment_redirect_limit: int
    attachment_storage_root: Path
    attachment_retention_days: int


def load_settings() -> Settings:
    return Settings(
        host=os.environ.get("HOST", "0.0.0.0").strip() or "0.0.0.0",
        port=_env_int("PORT", 8011),
        database_url=_env_path("DATABASE_URL", PROJECT_ROOT / "data" / "hermes_gateway.sqlite"),
        hermes_bin=_detect_hermes_bin(),
        hermes_workdir=_default_workdir(),
        hermes_profile_default=os.environ.get("HERMES_PROFILE_DEFAULT", "default").strip() or "default",
        gateway_api_key=os.environ.get("GATEWAY_API_KEY", "").strip() or None,
        admin_username=os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin",
        admin_password=os.environ.get("ADMIN_PASSWORD", "").strip() or None,
        admin_cookie_name=os.environ.get("ADMIN_COOKIE_NAME", "hermes_admin_session").strip()
        or "hermes_admin_session",
        admin_session_ttl_seconds=_env_int("ADMIN_SESSION_TTL_SECONDS", 86400),
        admin_cookie_secure=os.environ.get("ADMIN_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"},
        public_base_path=os.environ.get("PUBLIC_BASE_PATH", "").strip().rstrip("/"),
        timeout_seconds=_env_int("HERMES_TIMEOUT_SECONDS", 300),
        source_tag=os.environ.get("HERMES_SOURCE_TAG", "tool").strip() or "tool",
        default_model=os.environ.get("DEFAULT_MODEL", "").strip() or None,
        default_provider=os.environ.get("DEFAULT_PROVIDER", "").strip() or None,
        attachment_max_bytes=_env_int("ATTACHMENT_MAX_BYTES", 10 * 1024 * 1024),
        attachment_download_timeout_seconds=_env_int("ATTACHMENT_DOWNLOAD_TIMEOUT_SECONDS", 30),
        attachment_max_images=_env_int("ATTACHMENT_MAX_IMAGES", 8),
        attachment_redirect_limit=_env_int("ATTACHMENT_REDIRECT_LIMIT", 3),
        attachment_storage_root=_env_path("ATTACHMENT_STORAGE_ROOT", Path("/tmp/hermes")),
        attachment_retention_days=_env_int("ATTACHMENT_RETENTION_DAYS", 3),
    )
