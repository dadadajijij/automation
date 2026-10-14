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
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

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
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
MAX_FILES = int(os.getenv("MAX_FILES", "2000"))
MAX_REDIRECTS = 5
DOWNLOAD_TIMEOUT_SECONDS = float(os.getenv("SKILL_URL_TIMEOUT_SECONDS", "30"))
GIT_TIMEOUT_SECONDS = float(os.getenv("SKILL_GIT_TIMEOUT_SECONDS", "120"))
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")

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


class UrlImport(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    filename: str | None = Field(default=None, max_length=255)


class GitImport(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    ref: str | None = Field(default=None, max_length=255)
    overwrite: bool = True


class SkillSelection(BaseModel):
    names: list[str] = Field(min_length=1, max_length=500)


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
            CREATE TABLE IF NOT EXISTS skills (
              name TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL,
              created_by TEXT NOT NULL, updated_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS skill_versions (
              id INTEGER PRIMARY KEY AUTOINCREMENT, skill_name TEXT NOT NULL, version INTEGER NOT NULL,
              archive_path TEXT NOT NULL, actor_id TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(skill_name, version)
            );
            """
        )
    except Exception:
        connection.close()
        raise
    return connection


def ensure_dirs() -> None:
    SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
    ARCHIVES_ROOT.mkdir(parents=True, exist_ok=True)


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


def skill_file_path(name: str, relative: str) -> Path:
    relative = safe_member(relative)
    skill_root = (SKILLS_ROOT / name).resolve()
    candidate = (skill_root / relative).resolve()
    try:
        candidate.relative_to(skill_root)
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


def list_skill_files(name: str) -> list[dict[str, Any]]:
    root = (SKILLS_ROOT / name).resolve()
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
    for line in content.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:500]
    return ""


def write_skill_archive(source: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(source).as_posix())


def next_skill_version(connection: sqlite3.Connection, name: str, current_version: int = 0) -> int:
    history_version = connection.execute("SELECT COALESCE(MAX(version), 0) FROM skill_versions WHERE skill_name=?", (name,)).fetchone()[0]
    return max(current_version, history_version) + 1


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
        for package in packages:
            name = validate_name(package["name"])
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
                if base_versions and name in base_versions:
                    if existing is None:
                        raise HTTPException(404, "Skill not found")
                    if existing["version"] != base_versions[name]:
                        raise HTTPException(409, "Skill changed by another user", headers={"X-Current-Version": str(existing["version"])})
                if existing and not overwrite:
                    results.append({"name": name, "status": "skipped", "reason": "Skill already exists"})
                    continue

                version = next_skill_version(connection, name, int(existing["version"]) if existing else 0)
                archive = ARCHIVES_ROOT / f"{name}-v{version}.zip"
                if archive.exists():
                    raise HTTPException(409, f"Version archive already exists for {name}; check storage consistency")
                staged = staging_root / name
                staged_archive = Path(temp_dir) / f"{name}.zip"
                write_skill_archive(staged, staged_archive)

                destination = SKILLS_ROOT / name
                backup = backup_root / name if destination.exists() else None
                if backup:
                    destination.rename(backup)
                moved.append((destination, backup))
                staged.rename(destination)
                staged_archive.rename(archive)
                archives.append(archive)
                stamp = now()
                description = description_from(package["files"]["SKILL.md"].decode("utf-8", errors="replace"))
                if existing:
                    connection.execute("UPDATE skills SET description=?,version=?,updated_by=?,updated_at=? WHERE name=?", (description, version, user["id"], stamp, name))
                else:
                    connection.execute("INSERT INTO skills VALUES(?,?,?,?,?,?,?)", (name, description, version, user["id"], user["id"], stamp, stamp))
                connection.execute("INSERT INTO skill_versions(skill_name,version,archive_path,actor_id,message,created_at) VALUES(?,?,?,?,?,?)", (name, version, str(archive), user["id"], message, stamp))
                results.append({"name": name, "status": "updated" if existing else "created", "version": version})
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
    return batch_result(results)


async def download_skill_archive(raw_url: str, filename: str | None = None) -> tuple[bytes, str]:
    current_url = validate_download_url(raw_url)
    timeout = httpx.Timeout(DOWNLOAD_TIMEOUT_SECONDS, connect=min(DOWNLOAD_TIMEOUT_SECONDS, 10))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            for _ in range(MAX_REDIRECTS + 1):
                response = await client.get(current_url)
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise HTTPException(502, "Skill URL returned an invalid redirect")
                    current_url = validate_download_url(urljoin(current_url, location))
                    continue
                if response.status_code < 200 or response.status_code >= 300:
                    raise HTTPException(502, f"Skill URL returned HTTP {response.status_code}")
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Remote Skill package is too large")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "Remote Skill package is too large")
                if not bytes(content).startswith(b"PK"):
                    raise HTTPException(400, "Remote URL must point to a ZIP Skill package")
                parsed = urlparse(current_url)
                remote_name = unquote(Path(parsed.path).name) or "skill.zip"
                return bytes(content), filename or remote_name
            raise HTTPException(502, "Too many redirects while downloading Skill package")
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"Unable to download Skill package: {exc}") from exc


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
    ensure_dirs(); connection = db(); connection.close()


@app.get("/api/me")
async def me(request: Request) -> dict[str, Any]:
    return {"user": await current_user(request)}


@app.get("/api/skills")
async def list_skills(request: Request) -> dict[str, Any]:
    await current_user(request)
    connection = db(); rows = connection.execute("SELECT * FROM skills ORDER BY name").fetchall(); connection.close()
    return {"skills": [skill_row(row) for row in rows]}


@app.get("/api/skills/{name}")
async def get_skill(name: str, request: Request) -> dict[str, Any]:
    await current_user(request); name = validate_name(name)
    connection = db(); row = connection.execute("SELECT * FROM skills WHERE name=?", (name,)).fetchone(); connection.close()
    if row is None: raise HTTPException(404, "Skill not found")
    return {"skill": skill_row(row), "files": list_skill_files(name)}


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


@app.post("/api/skills/import-url", status_code=201)
async def import_skill_url(payload: UrlImport, request: Request) -> dict[str, Any]:
    user = actor_for(await current_user(request))
    content, filename = await download_skill_archive(payload.url, payload.filename)
    packages = discover_zip_packages(content, filename)
    return install_skill_packages(packages, user, "Imported Skill from URL")


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
    files: dict[str, bytes] = {}
    for path in (SKILLS_ROOT / name).rglob("*"):
        if path.is_symlink():
            raise HTTPException(400, "Skill contains unsupported symbolic links")
        if path.is_file():
            files[path.relative_to(SKILLS_ROOT / name).as_posix()] = path.read_bytes()
    for relative, content in payload.files.items():
        files[safe_member(relative)] = content.encode("utf-8")
    result = install_skill_packages([{"name": name, "files": files}], user, payload.message, base_versions={name: row["version"]})
    return {"skill": {"name": name, "version": result["skills"][0]["version"], "description": description_from(files["SKILL.md"].decode("utf-8", errors="replace"))}}


@app.delete("/api/skills/{name}")
async def delete_skill(name: str, request: Request) -> dict[str, bool]:
    await current_user(request)
    delete_selected_skills([name])
    return {"ok": True}


def selected_skill_paths(connection: sqlite3.Connection, names: list[str]) -> list[tuple[str, Path]]:
    names = list(dict.fromkeys(validate_name(name) for name in names))
    placeholders = ",".join("?" for _ in names)
    found = {row["name"] for row in connection.execute(
        f"SELECT name FROM skills WHERE name IN ({placeholders})", names
    ).fetchall()}
    missing = [name for name in names if name not in found]
    if missing:
        raise HTTPException(404, "Skills not found: " + ", ".join(missing))
    paths = []
    for name in names:
        path = SKILLS_ROOT / name
        if path.is_symlink() or not path.is_dir():
            raise HTTPException(409, f"Skill directory is unavailable: {name}")
        paths.append((name, path))
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
                for name, path in paths:
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
        return [name for name, _ in paths]
    finally:
        connection.close()


@app.post("/api/skills/batch-delete")
async def batch_delete_skills(payload: SkillSelection, request: Request) -> dict[str, Any]:
    await current_user(request)
    deleted = delete_selected_skills(payload.names)
    return {"ok": True, "deleted": deleted, "count": len(deleted)}


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
        for name, root in paths:
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
    connection = db(); row = connection.execute("SELECT version FROM skills WHERE name=?", (name,)).fetchone(); connection.close()
    if row is None: raise HTTPException(404, "Skill not found")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in (SKILLS_ROOT / name).rglob("*"):
            if path.is_file(): archive.write(path, path.relative_to(SKILLS_ROOT / name).as_posix())
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
