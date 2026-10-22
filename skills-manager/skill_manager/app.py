from __future__ import annotations

import asyncio
import io
import ipaddress
import json
import logging
import mimetypes
import os
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx
import yaml
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

ROOT = Path(os.getenv("SKILL_MANAGER_ROOT", "data")).resolve()
SKILLS_ROOT = ROOT / "skills"
ARCHIVES_ROOT = ROOT / "archives"
DB_PATH = ROOT / "skill-manager.db"
ATHENA_ME_URL = os.getenv("ATHENA_ME_URL", "https://athena.agoralab.co/api/me")
SKILL_MANAGER_ADMIN_ID = "panweikui@agora.io"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
MAX_FILES = int(os.getenv("MAX_FILES", "2000"))
GIT_TIMEOUT_SECONDS = float(os.getenv("SKILL_GIT_TIMEOUT_SECONDS", "120"))
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
DEFAULT_CATEGORY = "general"
MAX_ARCHIVED_VERSIONS = 2

app = FastAPI(title="Athena Skill Manager", docs_url="/api/docs")
logger = logging.getLogger(__name__)


@app.exception_handler(sqlite3.OperationalError)
async def database_error(request: Request, exc: sqlite3.OperationalError) -> JSONResponse:
    logger.error("Database operation failed", exc_info=(type(exc), exc, exc.__traceback__))
    if "locked" in str(exc).lower() or "busy" in str(exc).lower():
        return JSONResponse(status_code=503, content={"detail": "Skill storage is busy; please retry shortly"}, headers={"Retry-After": "2"})
    return JSONResponse(status_code=500, content={"detail": "Skill database operation failed; check server logs"})


class FileUpdate(BaseModel):
    files: dict[str, str] = Field(min_length=1)
    base_version: int | None = None
    message: str = Field(default="Updated skill", max_length=500)


class GitImport(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    ref: str | None = Field(default=None, max_length=255)
    overwrite: bool = True


class SkillSelection(BaseModel):
    names: list[str] = Field(min_length=1, max_length=500)


class SkillNameUpdate(BaseModel):
    name_zh: str = Field(min_length=1, max_length=120)


class CategoryCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    name_zh: str | None = Field(default=None, min_length=1, max_length=120)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class CategoryUpdate(BaseModel):
    slug: str | None = Field(default=None, min_length=1, max_length=64)
    name_zh: str | None = Field(default=None, min_length=1, max_length=120)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)


class CategoryAssignment(BaseModel):
    category: str = Field(min_length=1, max_length=64)


class BatchCategoryAssignment(SkillSelection, CategoryAssignment):
    pass


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    ROOT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS categories (
              slug TEXT PRIMARY KEY, display_name TEXT NOT NULL, name_zh TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
              is_system INTEGER NOT NULL DEFAULT 0, created_by TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS skills (
              name TEXT PRIMARY KEY, name_zh TEXT NOT NULL DEFAULT '', category_slug TEXT NOT NULL DEFAULT 'general',
              description TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL,
              created_by TEXT NOT NULL, updated_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS skill_versions (
              id INTEGER PRIMARY KEY AUTOINCREMENT, skill_name TEXT NOT NULL, version INTEGER NOT NULL,
              archive_path TEXT NOT NULL, actor_id TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(skill_name, version)
            );
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(skills)").fetchall()}
        if "category_slug" not in columns:
            connection.execute("ALTER TABLE skills ADD COLUMN category_slug TEXT NOT NULL DEFAULT 'general'")
        if "name_zh" not in columns:
            connection.execute("ALTER TABLE skills ADD COLUMN name_zh TEXT NOT NULL DEFAULT ''")
        skill_name_mapping: dict[str, str] = {}
        try:
            mapping_path = ROOT / "skill_names.json"
            if mapping_path.is_file():
                raw_mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
                if isinstance(raw_mapping, dict):
                    skill_name_mapping = {
                        str(name): str(display_name).strip()
                        for name, display_name in raw_mapping.items()
                        if str(display_name).strip()
                    }
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            logger.warning("Unable to read skill_names.json; keeping database names", exc_info=True)
        for skill in connection.execute("SELECT name,name_zh FROM skills").fetchall():
            name_zh = skill["name_zh"] or skill_name_mapping.get(skill["name"]) or skill["name"]
            if name_zh != skill["name_zh"]:
                connection.execute("UPDATE skills SET name_zh=? WHERE name=?", (name_zh, skill["name"]))
        category_columns = {row[1] for row in connection.execute("PRAGMA table_info(categories)").fetchall()}
        if "name_zh" not in category_columns:
            connection.execute("ALTER TABLE categories ADD COLUMN name_zh TEXT NOT NULL DEFAULT ''")
        stamp = now()
        connection.execute(
            "INSERT OR IGNORE INTO categories(slug,display_name,name_zh,description,is_system,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (DEFAULT_CATEGORY, "默认", "默认", "", 1, "system", stamp, stamp),
        )
        for category in connection.execute("SELECT slug,display_name,name_zh FROM categories").fetchall():
            zh = "默认" if category["slug"] == DEFAULT_CATEGORY else category["name_zh"] or category["display_name"]
            if zh != category["name_zh"] or category["display_name"] != zh:
                connection.execute("UPDATE categories SET display_name=?,name_zh=? WHERE slug=?", (zh, zh, category["slug"]))
        connection.commit()
    except Exception:
        connection.close()
        raise
    return connection


def ensure_dirs() -> None:
    SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
    ARCHIVES_ROOT.mkdir(parents=True, exist_ok=True)


def validate_category(slug: str) -> str:
    if not NAME_RE.fullmatch(slug):
        raise HTTPException(400, "Category must use lowercase letters, numbers and hyphens")
    return slug


def category_root(category: str) -> Path:
    return SKILLS_ROOT / validate_category(category)


def skill_root(category: str, name: str) -> Path:
    return category_root(category) / validate_name(name)


def archive_path(category: str, name: str, version: int) -> Path:
    return ARCHIVES_ROOT / validate_category(category) / f"{validate_name(name)}-v{version}.zip"


def migrate_legacy_skill_paths() -> None:
    """Move legacy data/skills/<name> directories under data/skills/general."""
    legacy_entries = [path for path in SKILLS_ROOT.iterdir() if path.is_dir() and path.name != DEFAULT_CATEGORY]
    if not legacy_entries:
        return
    target_root = category_root(DEFAULT_CATEGORY)
    target_root.mkdir(parents=True, exist_ok=True)
    for path in legacy_entries:
        if not (path / "SKILL.md").is_file():
            continue
        target = target_root / path.name
        if target.exists():
            raise RuntimeError(f"Cannot migrate legacy Skill; target already exists: {target}")
        path.rename(target)


def category_names_path() -> Path:
    return ROOT / "category_names.json"


def skill_names_path() -> Path:
    return ROOT / "skill_names.json"


def write_category_names_file(connection: sqlite3.Connection | None = None) -> None:
    owns_connection = connection is None
    connection = connection or db()
    try:
        payload = {
            row["slug"]: row["name_zh"]
            for row in connection.execute("SELECT slug,name_zh FROM categories ORDER BY slug").fetchall()
        }
    finally:
        if owns_connection:
            connection.close()
    target = category_names_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def write_skill_names_file(connection: sqlite3.Connection | None = None) -> None:
    owns_connection = connection is None
    connection = connection or db()
    try:
        payload = {
            row["name"]: row["name_zh"] or row["name"]
            for row in connection.execute("SELECT name,name_zh FROM skills ORDER BY name").fetchall()
        }
    finally:
        if owns_connection:
            connection.close()
    target = skill_names_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


async def current_user(request: Request) -> dict[str, str] | None:
    cookie = request.headers.get("cookie", "")
    headers = {"cookie": cookie} if cookie else {}
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
            response = await client.get(ATHENA_ME_URL, headers=headers)
    except httpx.HTTPError:
        # Athena is only an optional identity lookup. A temporary outage
        # must not prevent the skills manager from serving local data.
        return None
    if response.status_code >= 400:
        # 401/403 means there is no usable Athena login; 5xx means the
        # upstream is unavailable. Both are treated as an unknown user.
        return None
    try:
        raw = response.json()["user"]
        user_id = str(raw["id"]).strip()
        if not user_id:
            raise KeyError("id")
        return {"id": user_id, "email": str(raw.get("email") or user_id), "display_name": str(raw.get("displayName") or user_id)}
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def is_manager(user: dict[str, str] | None) -> bool:
    if not user:
        return False
    return any(
        value.strip().casefold() == SKILL_MANAGER_ADMIN_ID
        for value in (user.get("id", ""), user.get("email", ""))
        if value
    )


async def require_manager(request: Request) -> dict[str, str]:
    user = await current_user(request)
    if not is_manager(user):
        raise HTTPException(403, "Administrator permission required")
    return user


def safe_member(name: str) -> str:
    name = name.replace("\\", "/")
    if not name or "\x00" in name or name.startswith("/") or ".." in Path(name).parts:
        raise HTTPException(400, "Invalid file path")
    return name


def anonymous_user() -> dict[str, str]:
    return {"id": "anonymous", "email": "", "display_name": "Anonymous"}


def actor_for(user: dict[str, str] | None) -> dict[str, str]:
    return user or anonymous_user()


def allowed_url_hosts() -> set[str]:
    return {
        item.strip().lower().lstrip(".")
        for item in os.getenv("SKILL_URL_ALLOWED_HOSTS", "").split(",")
        if item.strip()
    }


def validate_download_url(raw_url: str) -> str:
    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(400, "Skill URL must use http or https")
    if parsed.username or parsed.password:
        raise HTTPException(400, "Skill URL must not contain credentials")
    hostname = parsed.hostname.lower().rstrip(".")
    allowed_hosts = allowed_url_hosts()
    if hostname in allowed_hosts or any(hostname.endswith("." + suffix) for suffix in allowed_hosts):
        return parsed.geturl()
    try:
        addresses = {
            result[4][0]
            for result in socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as exc:
        raise HTTPException(400, "Skill URL host cannot be resolved") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise HTTPException(400, "Skill URL resolves to a non-public address")
    return parsed.geturl()


def validate_git_ref(ref: str | None) -> str | None:
    if ref is None:
        return None
    if not REF_RE.fullmatch(ref) or ref.startswith("-") or ".." in ref:
        raise HTTPException(400, "Invalid Git ref")
    return ref


def validate_git_url(raw_url: str) -> str:
    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in {"http", "https", "ssh"} or not parsed.hostname:
        raise HTTPException(400, "Git URL must use http, https, or ssh")
    if parsed.scheme in {"http", "https"}:
        return validate_download_url(parsed.geturl())
    hostname = parsed.hostname.lower().rstrip(".")
    allowed_hosts = allowed_url_hosts()
    if hostname in allowed_hosts or any(hostname.endswith("." + suffix) for suffix in allowed_hosts):
        return parsed.geturl()
    try:
        addresses = {
            result[4][0]
            for result in socket.getaddrinfo(hostname, parsed.port or 22, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as exc:
        raise HTTPException(400, "Git URL host cannot be resolved") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise HTTPException(400, "Git URL resolves to a non-public address")
    return parsed.geturl()


def parse_git_source(raw_url: str, requested_ref: str | None) -> tuple[str, str | None]:
    """Resolve the repository and ref; browse subdirectories do not limit imports."""
    parsed = urlparse(raw_url.strip())
    ref = validate_git_ref(requested_ref)

    browse_match = re.search(r"/projects/([^/]+)/repos/([^/]+)/browse(?:/(.*))?$", parsed.path)
    if browse_match:
        project, repo, _ = browse_match.groups()
        clone_url = f"{parsed.scheme}://{parsed.netloc}/scm/{project}/{repo}.git"
        at = parse_qs(parsed.query).get("at", [None])[0]
        if not ref and at:
            ref = at.removeprefix("refs/heads/").removeprefix("refs/tags/")
            ref = validate_git_ref(ref)
        return validate_git_url(clone_url), ref

    github_match = re.search(r"/([^/]+/[^/]+)/tree/([^/]+)(?:/(.*))?$", parsed.path)
    if parsed.hostname and parsed.hostname.lower() == "github.com" and github_match:
        repository, tree_ref, _ = github_match.groups()
        if not ref:
            ref = validate_git_ref(unquote(tree_ref))
        return validate_git_url(f"https://github.com/{repository}.git"), ref

    return validate_git_url(raw_url), ref


TEXT_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".css", ".csv", ".go", ".h", ".hpp", ".html", ".ini",
    ".java", ".js", ".json", ".jsx", ".md", ".py", ".rs", ".sh", ".sql", ".svg",
    ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
}

KNOWN_MIME_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".json": "application/json",
    ".js": "text/javascript",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".toml": "application/toml",
}


def mime_type_for(path: Path) -> str:
    return KNOWN_MIME_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def is_text_file(path: Path) -> bool:
    mime_type = mimetypes.guess_type(path.name)[0] or ""
    if mime_type.startswith("text/") or path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    try:
        with path.open("rb") as stream:
            return b"\x00" not in stream.read(8192)
    except OSError:
        return False


def skill_file_path(name: str, relative: str, category: str | None = None) -> Path:
    relative = safe_member(relative)
    if category is None:
        connection = db()
        row = connection.execute("SELECT category_slug FROM skills WHERE name=?", (validate_name(name),)).fetchone()
        connection.close()
        if row is None:
            raise HTTPException(404, "Skill not found")
        category = row["category_slug"]
    root_path = skill_root(category, name).resolve()
    candidate = (root_path / relative).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise HTTPException(400, "Invalid file path") from exc
    if not candidate.is_file():
        raise HTTPException(404, "File not found")
    return candidate


def file_metadata(path: Path, root: Path) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    mime_type = mime_type_for(path)
    return {
        "path": relative,
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "mime_type": mime_type,
        "is_text": is_text_file(path),
    }


def list_skill_files(name: str, category: str) -> list[dict[str, Any]]:
    root = skill_root(category, name).resolve()
    return [
        file_metadata(path, root)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def validate_name(name: str) -> str:
    if not NAME_RE.fullmatch(name):
        raise HTTPException(400, "Skill name must use lowercase letters, numbers and hyphens")
    return name


def description_from(content: str) -> str:
    lines = content.lstrip("\ufeff").splitlines()
    if lines and lines[0].strip() == "---":
        end = next((index for index in range(1, len(lines)) if lines[index].strip() in {"---", "..."}), None)
        if end is None:
            raise HTTPException(400, "SKILL.md frontmatter is missing its closing delimiter")
        try:
            metadata = yaml.safe_load("\n".join(lines[1:end]))
        except yaml.YAMLError as exc:
            raise HTTPException(400, "Invalid SKILL.md frontmatter") from exc
        if metadata is not None and not isinstance(metadata, dict):
            raise HTTPException(400, "SKILL.md frontmatter must be a mapping")
        if metadata and "description" in metadata:
            description = metadata["description"]
            if description is not None and not isinstance(description, str):
                raise HTTPException(400, "SKILL.md description must be a string")
            if description and description.strip():
                return description.strip()
        lines = lines[end + 1:]

    paragraph: list[str] = []
    fence = None
    in_comment = False
    for raw in lines:
        line = raw.strip()
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if line.startswith("<!--"):
            if paragraph:
                break
            in_comment = "-->" not in line
            continue
        marker = re.match(r"^(`{3,}|~{3,})", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
            continue
        if marker:
            if paragraph:
                break
            fence = marker[1]
            continue
        if re.fullmatch(r"(?:=+|-+)", line) and paragraph:
            # A setext underline makes the preceding lines a heading.
            paragraph.clear()
            continue
        if not line or re.match(r"^(?:#{1,6}(?:\s|$)|[-*+]\s|\d+[.)]\s|>|(?:\*\s*){3,}$|(?:_\s*){3,}$|(?:-\s*){3,}$)", line) or raw.startswith(("    ", "\t")):
            if paragraph:
                break
            continue
        paragraph.append(line)
    return " ".join(paragraph)


def write_skill_archive(source: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(source).as_posix())


def next_skill_version(connection: sqlite3.Connection, name: str, current_version: int = 0) -> int:
    history_version = connection.execute("SELECT COALESCE(MAX(version), 0) FROM skill_versions WHERE skill_name=?", (name,)).fetchone()[0]
    return max(current_version, history_version) + 1


def prune_skill_history(skill_name: str, keep: int = MAX_ARCHIVED_VERSIONS) -> None:
    """Keep only the newest version records and archive files for one Skill."""
    connection = db()
    try:
        rows = connection.execute(
            "SELECT id,archive_path FROM skill_versions WHERE skill_name=? ORDER BY version DESC, id DESC",
            (skill_name,),
        ).fetchall()
        stale = rows[keep:]
        if not stale:
            return
        for row in stale:
            archive = Path(row["archive_path"])
            if archive.exists():
                archive.unlink()
        connection.execute(
            f"DELETE FROM skill_versions WHERE id IN ({','.join('?' for _ in stale)})",
            [row["id"] for row in stale],
        )
        connection.commit()
    finally:
        connection.close()


def prune_all_skill_history() -> None:
    connection = db()
    names = [row[0] for row in connection.execute("SELECT DISTINCT skill_name FROM skill_versions").fetchall()]
    connection.close()
    for name in names:
        prune_skill_history(name)


def source_file_ignored(path: str) -> bool:
    parts = Path(path.replace("\\", "/")).parts
    return ".git" in parts or "__MACOSX" in parts or Path(path).name == ".DS_Store" or Path(path).name.startswith("._")


def discover_skill_packages(files: dict[str, bytes], fallback_name: str) -> list[dict[str, Any]]:
    """Find top-level Skill directories from a normalized relative-path map."""
    normalized: dict[str, bytes] = {}
    for raw_path, content in files.items():
        path = safe_member(raw_path)
        if source_file_ignored(path):
            continue
        if path in normalized:
            raise HTTPException(400, f"Duplicate file in source: {path}")
        normalized[path] = content
    if len(normalized) > MAX_FILES:
        raise HTTPException(400, "Too many files")
    if sum(len(content) for content in normalized.values()) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Uncompressed Skill package is too large")

    skill_dirs = {
        Path(path).parent.as_posix() if Path(path).parent.as_posix() != "." else ""
        for path in normalized
        if Path(path).name == "SKILL.md"
    }
    if not skill_dirs:
        raise HTTPException(400, "Source must contain at least one SKILL.md")

    if "" in skill_dirs and len(skill_dirs) > 1:
        raise HTTPException(400, "Nested Skills are not supported")
    top_level_dirs = [
        directory
        for directory in sorted(skill_dirs, key=lambda value: (len(Path(value).parts), value))
        if not any(other and other != directory and directory.startswith(other + "/") for other in skill_dirs)
    ]
    nested = [directory for directory in skill_dirs if directory not in top_level_dirs]
    if nested:
        raise HTTPException(400, "Nested Skills are not supported")

    packages: list[dict[str, Any]] = []
    names: set[str] = set()
    for directory in top_level_dirs:
        prefix = f"{directory}/" if directory else ""
        name = validate_name(Path(directory).name if directory else fallback_name)
        if name in names:
            raise HTTPException(400, f"Duplicate Skill name: {name}")
        names.add(name)
        package_files = {
            path[len(prefix):] if prefix else path: content
            for path, content in normalized.items()
            if (path.startswith(prefix) if prefix else True)
        }
        if "SKILL.md" not in package_files:
            raise HTTPException(400, f"Skill {name} is missing SKILL.md")
        packages.append({"name": name, "source_path": directory or ".", "files": package_files})
    return packages


def discover_zip_packages(content: bytes, filename: str) -> list[dict[str, Any]]:
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Upload is too large")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(400, "Skill package must be a valid ZIP archive") from exc
    files: dict[str, bytes] = {}
    total_uncompressed_size = 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        member = safe_member(info.filename)
        if source_file_ignored(member):
            continue
        total_uncompressed_size += info.file_size
        if total_uncompressed_size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Uncompressed Skill package is too large")
        if member in files:
            raise HTTPException(400, f"Duplicate file in archive: {member}")
        files[member] = archive.read(info)
    return discover_skill_packages(files, Path(filename).stem)


def discover_directory_packages(root: Path, fallback_name: str) -> list[dict[str, Any]]:
    # Discover Skill roots before reading resources so unrelated repo files
    # do not consume the import's file and byte limits.
    skill_roots: list[Path] = []
    for directory, dirs, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        dirs[:] = [name for name in dirs if not source_file_ignored(name) and not (base / name).is_symlink()]
        if "SKILL.md" in filenames:
            skill_roots.append(base)
    if not skill_roots:
        raise HTTPException(400, "Source must contain at least one SKILL.md")
    for directory in skill_roots:
        if any(other != directory and other in directory.parents for other in skill_roots):
            raise HTTPException(400, "Nested Skills are not supported")

    if root in skill_roots:
        skill_file = root / "SKILL.md"
        if skill_file.is_symlink():
            raise HTTPException(400, "Git source contains unsupported symbolic links")
        if skill_file.stat().st_size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Uncompressed Skill package is too large")
        try:
            text = skill_file.read_text(encoding="utf-8")
            lines = text.splitlines()
            if lines and lines[0].strip() == "---":
                end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
                if end is None:
                    raise ValueError("Unclosed frontmatter")
                metadata = yaml.safe_load("\n".join(lines[1:end]))
                if metadata is not None and not isinstance(metadata, dict):
                    raise ValueError("Frontmatter must be a mapping")
                if metadata and "name" in metadata:
                    fallback_name = validate_name(str(metadata["name"]))
        except (UnicodeError, yaml.YAMLError, ValueError) as exc:
            raise HTTPException(400, "Invalid root SKILL.md frontmatter") from exc

    files: dict[str, bytes] = {}
    total_bytes = 0
    for skill_root in skill_roots:
        for path in skill_root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if source_file_ignored(relative):
                continue
            if path.is_symlink():
                raise HTTPException(400, "Git source contains unsupported symbolic links")
            if not path.is_file():
                continue
            if len(files) >= MAX_FILES:
                raise HTTPException(400, "Too many files")
            remaining = MAX_UPLOAD_BYTES - total_bytes
            if path.stat().st_size > remaining:
                raise HTTPException(413, "Uncompressed Skill package is too large")
            with path.open("rb") as stream:
                content = stream.read(remaining + 1)
            total_bytes += len(content)
            if total_bytes > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "Uncompressed Skill package is too large")
            files[relative] = content
    return discover_skill_packages(files, fallback_name)


def batch_result(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {"total": len(results), "created": 0, "updated": 0, "skipped": 0, "failed": 0}
    for result in results:
        summary[result["status"]] += 1
    response: dict[str, Any] = {"summary": summary, "skills": results}
    if len(results) == 1 and results[0]["status"] in {"created", "updated"}:
        response["skill"] = results[0]
    return response


def install_skill_packages(packages: list[dict[str, Any]], user: dict[str, str], message: str, overwrite: bool = True, base_versions: dict[str, int] | None = None) -> dict[str, Any]:
    if not packages:
        raise HTTPException(400, "No Skills found")
    ensure_dirs()
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="skill-import-", dir=ROOT) as temp_dir:
        staging_root = Path(temp_dir) / "staging"
        backup_root = Path(temp_dir) / "backup"
        staging_root.mkdir()
        backup_root.mkdir()
        descriptions: dict[str, str] = {}
        for package in packages:
            name = validate_name(package["name"])
            try:
                descriptions[name] = description_from(package["files"]["SKILL.md"].decode("utf-8", errors="replace"))
            except HTTPException as exc:
                raise HTTPException(exc.status_code, f"{name}: {exc.detail}") from exc
            staged = staging_root / name
            staged.mkdir()
            for relative, content in package["files"].items():
                target = staged / safe_member(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)

        connection = db()
        moved: list[tuple[Path, Path | None]] = []
        archives: list[Path] = []
        try:
            # Allocate versions while holding the write lock, including retained history.
            connection.execute("BEGIN IMMEDIATE")
            for package in packages:
                name = package["name"]
                existing = connection.execute("SELECT * FROM skills WHERE name=?", (name,)).fetchone()
                category = validate_category(existing["category_slug"] if existing else DEFAULT_CATEGORY)
                category_row = connection.execute("SELECT slug FROM categories WHERE slug=?", (category,)).fetchone()
                if category_row is None:
                    raise HTTPException(409, f"Category not found for Skill {name}: {category}")
                if base_versions and name in base_versions:
                    if existing is None:
                        raise HTTPException(404, "Skill not found")
                    if existing["version"] != base_versions[name]:
                        raise HTTPException(409, "Skill changed by another user", headers={"X-Current-Version": str(existing["version"])})
                if existing and not overwrite:
                    results.append({"name": name, "status": "skipped", "reason": "Skill already exists"})
                    continue

                version = next_skill_version(connection, name, int(existing["version"]) if existing else 0)
                archive = archive_path(category, name, version)
                archive.parent.mkdir(parents=True, exist_ok=True)
                if archive.exists():
                    raise HTTPException(409, f"Version archive already exists for {name}; check storage consistency")
                staged = staging_root / name
                staged_archive = Path(temp_dir) / f"{name}.zip"
                write_skill_archive(staged, staged_archive)

                destination = skill_root(category, name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                backup = backup_root / name if destination.exists() else None
                if backup:
                    destination.rename(backup)
                moved.append((destination, backup))
                staged.rename(destination)
                staged_archive.rename(archive)
                archives.append(archive)
                stamp = now()
                description = descriptions[name]
                if existing:
                    connection.execute("UPDATE skills SET description=?,version=?,updated_by=?,updated_at=? WHERE name=?", (description, version, user["id"], stamp, name))
                else:
                    connection.execute("INSERT INTO skills(name,name_zh,category_slug,description,version,created_by,updated_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (name, name, category, description, version, user["id"], user["id"], stamp, stamp))
                connection.execute("INSERT INTO skill_versions(skill_name,version,archive_path,actor_id,message,created_at) VALUES(?,?,?,?,?,?)", (name, version, str(archive), user["id"], message, stamp))
                results.append({"name": name, "name_zh": existing["name_zh"] or name if existing else name, "category": category, "status": "updated" if existing else "created", "version": version})
            connection.commit()
        except Exception:
            connection.rollback()
            for archive in reversed(archives):
                archive.unlink()
            for destination, backup in reversed(moved):
                if destination.exists():
                    shutil.rmtree(destination)
                if backup and backup.exists():
                    backup.rename(destination)
            raise
        finally:
            connection.close()
    for result in results:
        if result.get("status") in {"created", "updated"}:
            prune_skill_history(result["name"])
    write_skill_names_file()
    return batch_result(results)


def clone_git_packages(raw_url: str, ref: str | None) -> list[dict[str, Any]]:
    clone_url, ref = parse_git_source(raw_url, ref)
    ensure_dirs()
    with tempfile.TemporaryDirectory(prefix="skill-git-", dir=ROOT) as temp_dir:
        checkout = Path(temp_dir) / "repository"
        command = ["git", "clone", "--depth", "1", "--no-tags"]
        git_env = os.environ.copy()
        username, password = git_env.get("username", ""), git_env.get("password", "")
        if urlparse(clone_url).scheme in {"http", "https"} and (username or password):
            if not username or not password:
                raise HTTPException(400, "Git authentication requires both username and password")
            askpass = Path(temp_dir) / "askpass"
            helper = Path(__file__).with_name("git_askpass.py").resolve()
            askpass.write_text(
                f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(helper))} "$@"\n',
                encoding="utf-8",
            )
            askpass.chmod(0o700)
            git_env.update(GIT_ASKPASS=str(askpass), GIT_TERMINAL_PROMPT="0", LC_ALL="C")
            # Do not save the injected credentials through an existing helper.
            command = ["git", "-c", "credential.helper=", *command[1:]]
        if ref:
            command.extend(["--branch", ref])
        command.extend(["--", clone_url, str(checkout)])
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
                env=git_env,
            )
        except FileNotFoundError as exc:
            raise HTTPException(500, "git is not installed on the Skill Manager host") from exc
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, "Git clone timed out") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "Git clone failed").strip().splitlines()[-1]
            for secret in (password, quote(password, safe="")):
                if secret:
                    detail = detail.replace(secret, "[redacted]")
            detail = detail[:500]
            raise HTTPException(502, f"Git clone failed: {detail}")

        repository_name = unquote(Path(urlparse(clone_url).path.rstrip("/")).name).removesuffix(".git")
        return discover_directory_packages(checkout, repository_name)


def skill_row(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


@app.on_event("startup")
def startup() -> None:
    ensure_dirs()
    connection = db()
    connection.close()
    migrate_legacy_skill_paths()
    prune_all_skill_history()
    write_category_names_file()
    write_skill_names_file()


@app.get("/api/me")
async def me(request: Request) -> dict[str, Any]:
    user = await current_user(request)
    return {"user": user, "can_manage": is_manager(user)}


@app.get("/api/skills")
async def list_skills(request: Request) -> dict[str, Any]:
    await current_user(request)
    connection = db(); rows = connection.execute("SELECT * FROM skills ORDER BY name").fetchall(); connection.close()
    return {"skills": [skill_row(row) for row in rows]}


@app.get("/api/categories")
async def list_categories(request: Request) -> dict[str, Any]:
    await current_user(request)
    connection = db()
    rows = connection.execute(
        """SELECT c.slug,c.display_name,c.name_zh,c.description,c.is_system,c.created_by,c.created_at,c.updated_at,
                  COUNT(s.name) AS skill_count
           FROM categories c LEFT JOIN skills s ON s.category_slug=c.slug
           GROUP BY c.slug ORDER BY c.is_system DESC, c.display_name, c.slug"""
    ).fetchall()
    connection.close()
    return {"categories": [skill_row(row) for row in rows]}


@app.post("/api/categories", status_code=201)
async def create_category(payload: CategoryCreate, request: Request) -> dict[str, Any]:
    user = await require_manager(request)
    slug = validate_category(payload.slug)
    name_zh = (payload.name_zh or payload.display_name or slug).strip()
    connection = db()
    try:
        stamp = now()
        connection.execute(
            "INSERT INTO categories(slug,display_name,name_zh,description,is_system,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (slug, name_zh, name_zh, payload.description.strip(), 0, user["id"], stamp, stamp),
        )
        connection.commit()
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        raise HTTPException(409, "Category already exists") from exc
    finally:
        connection.close()
    write_category_names_file()
    return {"category": {"slug": slug, "display_name": name_zh, "name_zh": name_zh, "description": payload.description.strip(), "is_system": False, "skill_count": 0}}


@app.patch("/api/categories/{slug}")
async def update_category(slug: str, payload: CategoryUpdate, request: Request) -> dict[str, Any]:
    await require_manager(request)
    slug = validate_category(slug)
    if all(value is None for value in (payload.slug, payload.name_zh, payload.display_name, payload.description)):
        raise HTTPException(422, "No category fields to update")
    connection = db()
    row = connection.execute("SELECT * FROM categories WHERE slug=?", (slug,)).fetchone()
    if row is None:
        connection.close()
        raise HTTPException(404, "Category not found")
    # `display_name` is retained for older API clients. New clients should
    # send the path slug and the two localized names independently.
    legacy_display_name_only = (
        payload.display_name is not None
        and payload.slug is None
        and payload.name_zh is None
    )
    name_zh = (
        payload.name_zh.strip()
        if payload.name_zh is not None
        else payload.display_name.strip()
        if payload.display_name is not None
        else row["name_zh"] or row["display_name"] or slug
    )
    description = payload.description.strip() if payload.description is not None else row["description"]
    new_slug = (
        validate_category(payload.slug.strip())
        if payload.slug is not None
        else validate_category(payload.display_name.strip())
        if legacy_display_name_only
        else slug
    )
    if row["is_system"] and new_slug != slug:
        connection.close()
        raise HTTPException(409, "The general category cannot be renamed")
    if new_slug != slug and connection.execute("SELECT 1 FROM categories WHERE slug=?", (new_slug,)).fetchone():
        connection.close()
        raise HTTPException(409, "Category already exists")
    old_category_dir = category_root(slug)
    new_category_dir = category_root(new_slug)
    old_archive_dir = ARCHIVES_ROOT / slug
    new_archive_dir = ARCHIVES_ROOT / new_slug
    moved_dirs: list[tuple[Path, Path]] = []
    try:
        if new_slug != slug:
            if new_category_dir.exists() or new_archive_dir.exists():
                raise HTTPException(409, "Target category path already exists")
            if old_category_dir.exists():
                old_category_dir.rename(new_category_dir)
                moved_dirs.append((old_category_dir, new_category_dir))
            if old_archive_dir.exists():
                new_archive_dir.parent.mkdir(parents=True, exist_ok=True)
                old_archive_dir.rename(new_archive_dir)
                moved_dirs.append((old_archive_dir, new_archive_dir))
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE categories SET slug=?,display_name=?,name_zh=?,description=?,updated_at=? WHERE slug=?",
                (new_slug, name_zh, name_zh, description, now(), slug),
            )
            connection.execute("UPDATE skills SET category_slug=?,updated_at=? WHERE category_slug=?", (new_slug, now(), slug))
            old_prefix = str(old_archive_dir) + "/"
            new_prefix = str(new_archive_dir) + "/"
            connection.execute("UPDATE skill_versions SET archive_path=REPLACE(archive_path, ?, ?) WHERE archive_path LIKE ?", (old_prefix, new_prefix, old_prefix + "%"))
        else:
            connection.execute(
                "UPDATE categories SET display_name=?,name_zh=?,description=?,updated_at=? WHERE slug=?",
                (name_zh, name_zh, description, now(), slug),
            )
        connection.commit()
    except HTTPException:
        connection.rollback()
        for old_path, new_path in reversed(moved_dirs):
            if new_path.exists():
                new_path.rename(old_path)
        raise
    except Exception:
        connection.rollback()
        for old_path, new_path in reversed(moved_dirs):
            if new_path.exists():
                new_path.rename(old_path)
        raise
    finally:
        connection.close()
    write_category_names_file()
    return {
        "category": {
            "slug": new_slug,
            "display_name": name_zh,
            "name_zh": name_zh,
            "description": description,
            "is_system": bool(row["is_system"]),
        }
    }


@app.delete("/api/categories/{slug}")
async def delete_category(slug: str, request: Request) -> dict[str, Any]:
    await require_manager(request)
    slug = validate_category(slug)
    if slug == DEFAULT_CATEGORY:
        raise HTTPException(409, "The general category cannot be deleted")
    connection = db()
    row = connection.execute("SELECT * FROM categories WHERE slug=?", (slug,)).fetchone()
    if row is None:
        connection.close()
        raise HTTPException(404, "Category not found")
    skill_names = [row[0] for row in connection.execute("SELECT name FROM skills WHERE category_slug=?", (slug,)).fetchall()]
    connection.close()
    if skill_names:
        move_selected_skills(skill_names, DEFAULT_CATEGORY)
    connection = db()
    connection.execute("DELETE FROM categories WHERE slug=?", (slug,))
    connection.commit()
    connection.close()
    write_category_names_file()
    category_dir = category_root(slug)
    if category_dir.is_dir() and not any(category_dir.iterdir()):
        category_dir.rmdir()
    return {"ok": True, "slug": slug}


@app.get("/api/skills/{name}")
async def get_skill(name: str, request: Request) -> dict[str, Any]:
    await current_user(request); name = validate_name(name)
    connection = db(); row = connection.execute("SELECT * FROM skills WHERE name=?", (name,)).fetchone(); connection.close()
    if row is None: raise HTTPException(404, "Skill not found")
    return {"skill": skill_row(row), "files": list_skill_files(name, row["category_slug"])}


@app.get("/api/skills/{name}/files/{path:path}")
async def get_skill_file(name: str, path: str, request: Request) -> FileResponse:
    """Serve one Skill file without decoding it in the API layer."""
    await current_user(request)
    name = validate_name(name)
    file_path = skill_file_path(name, path)
    mime_type = mime_type_for(file_path)
    return FileResponse(
        file_path,
        media_type=mime_type,
        headers={
            "Content-Disposition": f'inline; filename="{quote(file_path.name)}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/api/skills", status_code=201)
async def upload_skill(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    user = actor_for(await current_user(request))
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "Upload a .zip file")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    packages = discover_zip_packages(content, file.filename)
    return install_skill_packages(packages, user, "Uploaded skill")


@app.post("/api/skills/import-git", status_code=201)
async def import_skills_from_git(payload: GitImport, request: Request) -> dict[str, Any]:
    user = actor_for(await current_user(request))
    packages = await asyncio.to_thread(clone_git_packages, payload.url, payload.ref)
    return install_skill_packages(packages, user, "Imported Skills from Git", payload.overwrite)


@app.put("/api/skills/{name}")
async def update_skill(name: str, payload: FileUpdate, request: Request) -> dict[str, Any]:
    user = actor_for(await current_user(request))
    name = validate_name(name)
    connection = db()
    try:
        selected_skill_paths(connection, [name])
        row = connection.execute("SELECT * FROM skills WHERE name=?", (name,)).fetchone()
    finally:
        connection.close()
    if payload.base_version is not None and payload.base_version != row["version"]:
        raise HTTPException(409, "Skill changed by another user", headers={"X-Current-Version": str(row["version"])})
    category = row["category_slug"]
    current_root = skill_root(category, name)
    files: dict[str, bytes] = {}
    for path in current_root.rglob("*"):
        if path.is_symlink():
            raise HTTPException(400, "Skill contains unsupported symbolic links")
        if path.is_file():
            files[path.relative_to(current_root).as_posix()] = path.read_bytes()
    for relative, content in payload.files.items():
        files[safe_member(relative)] = content.encode("utf-8")
    result = install_skill_packages([{"name": name, "files": files}], user, payload.message, base_versions={name: row["version"]})
    return {"skill": {"name": name, "category_slug": category, "version": result["skills"][0]["version"], "description": description_from(files["SKILL.md"].decode("utf-8", errors="replace"))}}


@app.delete("/api/skills/{name}")
async def delete_skill(name: str, request: Request) -> dict[str, bool]:
    await current_user(request)
    delete_selected_skills([name])
    return {"ok": True}


@app.patch("/api/skills/{name}/name")
async def update_skill_name(name: str, payload: SkillNameUpdate, request: Request) -> dict[str, Any]:
    user = actor_for(await current_user(request))
    name = validate_name(name)
    name_zh = payload.name_zh.strip()
    connection = db()
    try:
        row = connection.execute("SELECT name FROM skills WHERE name=?", (name,)).fetchone()
        if row is None:
            raise HTTPException(404, "Skill not found")
        connection.execute("UPDATE skills SET name_zh=?,updated_by=?,updated_at=? WHERE name=?", (name_zh, user["id"], now(), name))
        connection.commit()
    finally:
        connection.close()
    write_skill_names_file()
    return {"skill": {"name": name, "name_zh": name_zh}}


def selected_skill_paths(connection: sqlite3.Connection, names: list[str]) -> list[tuple[str, str, Path]]:
    names = list(dict.fromkeys(validate_name(name) for name in names))
    placeholders = ",".join("?" for _ in names)
    found = {row["name"]: row["category_slug"] for row in connection.execute(
        f"SELECT name,category_slug FROM skills WHERE name IN ({placeholders})", names
    ).fetchall()}
    missing = [name for name in names if name not in found]
    if missing:
        raise HTTPException(404, "Skills not found: " + ", ".join(missing))
    paths = []
    for name in names:
        category = validate_category(found[name])
        path = skill_root(category, name)
        if path.is_symlink() or not path.is_dir():
            raise HTTPException(409, f"Skill directory is unavailable: {name}")
        paths.append((name, category, path))
    return paths


def delete_selected_skills(names: list[str]) -> list[str]:
    connection = db()
    moved: list[tuple[Path, Path]] = []
    try:
        connection.execute("BEGIN IMMEDIATE")
        paths = selected_skill_paths(connection, names)
        # Retain version archives; stage directories so a failed batch can roll back.
        with tempfile.TemporaryDirectory(prefix="skill-delete-", dir=ROOT) as temp_dir:
            try:
                for name, category, path in paths:
                    backup = Path(temp_dir) / name
                    path.rename(backup)
                    moved.append((path, backup))
                    connection.execute("DELETE FROM skills WHERE name=?", (name,))
                connection.commit()
            except Exception:
                connection.rollback()
                for path, backup in reversed(moved):
                    backup.rename(path)
                raise
        deleted = [name for name, _, _ in paths]
        write_skill_names_file(connection)
        return deleted
    finally:
        connection.close()


@app.post("/api/skills/batch-delete")
async def batch_delete_skills(payload: SkillSelection, request: Request) -> dict[str, Any]:
    await require_manager(request)
    deleted = delete_selected_skills(payload.names)
    return {"ok": True, "deleted": deleted, "count": len(deleted)}


def move_selected_skills(names: list[str], target_category: str) -> list[str]:
    target_category = validate_category(target_category)
    connection = db()
    moved: list[tuple[Path, Path]] = []
    try:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM categories WHERE slug=?", (target_category,)).fetchone() is None:
            raise HTTPException(404, "Category not found")
        paths = selected_skill_paths(connection, names)
        with tempfile.TemporaryDirectory(prefix="skill-move-", dir=ROOT) as temp_dir:
            staging = Path(temp_dir) / "staging"
            staging.mkdir()
            try:
                for name, category, source in paths:
                    if category == target_category:
                        continue
                    destination = skill_root(target_category, name)
                    if destination.exists():
                        raise HTTPException(409, f"Target category already contains Skill: {name}")
                    staged = staging / name
                    source.rename(staged)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    staged.rename(destination)
                    moved.append((source, destination))
                    connection.execute("UPDATE skills SET category_slug=?,updated_at=? WHERE name=?", (target_category, now(), name))
                connection.commit()
            except Exception:
                connection.rollback()
                for source, destination in reversed(moved):
                    if destination.exists():
                        source.parent.mkdir(parents=True, exist_ok=True)
                        destination.rename(source)
                raise
        return [name for name, _, _ in paths]
    finally:
        connection.close()


@app.patch("/api/skills/{name}/category")
async def move_skill_category(name: str, payload: CategoryAssignment, request: Request) -> dict[str, Any]:
    await current_user(request)
    moved = move_selected_skills([name], payload.category)
    connection = db()
    row = connection.execute("SELECT name,category_slug FROM skills WHERE name=?", (moved[0],)).fetchone()
    connection.close()
    return {"skill": dict(row)}


@app.post("/api/skills/batch-category")
async def move_batch_skill_category(payload: BatchCategoryAssignment, request: Request) -> dict[str, Any]:
    await require_manager(request)
    moved = move_selected_skills(payload.names, payload.category)
    return {"ok": True, "moved": moved, "category": validate_category(payload.category), "count": len(moved)}


@app.post("/api/skills/batch-download")
async def batch_download_skills(payload: SkillSelection, request: Request) -> StreamingResponse:
    await current_user(request)
    connection = db()
    try:
        paths = selected_skill_paths(connection, payload.names)
    finally:
        connection.close()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, category, root in paths:
            for path in sorted(root.rglob("*")):
                if path.is_symlink():
                    raise HTTPException(409, f"Skill contains unsupported symbolic links: {name}")
                if path.is_file():
                    archive.write(path, f"{name}/{path.relative_to(root).as_posix()}")
    output.seek(0)
    return StreamingResponse(output, media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="skills.zip"',
    })


@app.get("/api/skills/{name}/download")
async def download_skill(name: str, request: Request) -> StreamingResponse:
    await current_user(request); name = validate_name(name)
    connection = db(); row = connection.execute("SELECT version,category_slug FROM skills WHERE name=?", (name,)).fetchone(); connection.close()
    if row is None: raise HTTPException(404, "Skill not found")
    root = skill_root(row["category_slug"], name)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            if path.is_file(): archive.write(path, path.relative_to(root).as_posix())
    output.seek(0); return StreamingResponse(output, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}-v{row["version"]}.zip"'})


@app.get("/api/skills/{name}/history")
async def history(name: str, request: Request) -> dict[str, Any]:
    await current_user(request); name = validate_name(name); connection = db(); rows = connection.execute("SELECT id,skill_name,version,actor_id,message,created_at FROM skill_versions WHERE skill_name=? ORDER BY version DESC", (name,)).fetchall(); connection.close()
    return {"history": [skill_row(row) for row in rows]}


@app.get("/{path:path}", response_class=HTMLResponse)
async def frontend(path: str, request: Request) -> HTMLResponse:
    prefix = request.headers.get("x-forwarded-prefix", "").rstrip("/")
    if prefix and (
        not prefix.startswith("/")
        or prefix.startswith("//")
        or any(ch in prefix for ch in ("?", "#", "\\"))
    ):
        raise HTTPException(400, "Invalid deployment prefix")
    html = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    html = html.replace(
        '<meta name="skill-manager-base" content="">',
        f'<meta name="skill-manager-base" content="{escape(prefix, quote=True)}">',
        1,
    )
    return HTMLResponse(html)
