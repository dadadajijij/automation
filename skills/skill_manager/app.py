from __future__ import annotations

import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

ROOT = Path(os.getenv("SKILL_MANAGER_ROOT", "data")).resolve()
SKILLS_ROOT = ROOT / "skills"
ARCHIVES_ROOT = ROOT / "archives"
DB_PATH = ROOT / "skill-manager.db"
ATHENA_ME_URL = os.getenv("ATHENA_ME_URL", "https://athena.agoralab.co/api/me")
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
MAX_FILES = 200
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

app = FastAPI(title="Athena Skill Manager", docs_url="/api/docs")


class FileUpdate(BaseModel):
    files: dict[str, str] = Field(min_length=1)
    base_version: int | None = None
    message: str = Field(default="Updated skill", max_length=500)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    ROOT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
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
    return connection


def ensure_dirs() -> None:
    SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
    ARCHIVES_ROOT.mkdir(parents=True, exist_ok=True)


async def current_user(request: Request) -> dict[str, str]:
    cookie = request.headers.get("cookie", "")
    headers = {"cookie": cookie} if cookie else {}
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
            response = await client.get(ATHENA_ME_URL, headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Athena authentication unavailable: {exc}") from exc
    if response.status_code in (401, 403):
        raise HTTPException(401, "Athena login required")
    if response.status_code >= 400:
        raise HTTPException(502, "Athena authentication failed")
    try:
        raw = response.json()["user"]
        user_id = str(raw["id"]).strip()
        if not user_id:
            raise KeyError("id")
        return {"id": user_id, "email": str(raw.get("email") or user_id), "display_name": str(raw.get("displayName") or user_id)}
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "Invalid Athena /api/me response") from exc


def safe_member(name: str) -> str:
    name = name.replace("\\", "/")
    if not name or name.startswith("/") or ".." in Path(name).parts:
        raise HTTPException(400, "Invalid file path")
    return name


def parse_skill_name(files: list[str]) -> str:
    roots = {Path(path).parts[0] for path in files if Path(path).parts}
    if "SKILL.md" in files:
        return ""
    candidates = [root for root in roots if root != "__MACOSX" and f"{root}/SKILL.md" in files]
    if len(candidates) != 1:
        raise HTTPException(400, "Archive must contain exactly one SKILL.md")
    return candidates[0]


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


def snapshot(name: str, version: int, actor: str, message: str) -> None:
    archive = ARCHIVES_ROOT / f"{name}-v{version}.zip"
    source = SKILLS_ROOT / name
    temp = archive.with_suffix(".tmp")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as output:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(source).as_posix())
    temp.replace(archive)
    connection = db()
    connection.execute("INSERT INTO skill_versions(skill_name,version,archive_path,actor_id,message,created_at) VALUES(?,?,?,?,?,?)", (name, version, str(archive), actor, message, now()))
    connection.commit(); connection.close()


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
    files = {}
    for path in (SKILLS_ROOT / name).rglob("*"):
        if path.is_file(): files[path.relative_to(SKILLS_ROOT / name).as_posix()] = path.read_text(encoding="utf-8")
    return {"skill": skill_row(row), "files": files}


@app.post("/api/skills", status_code=201)
async def upload_skill(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    user = await current_user(request)
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "Upload a .zip file")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES: raise HTTPException(413, "Upload is too large")
    try: archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc: raise HTTPException(400, "Invalid ZIP archive") from exc
    members = [safe_member(info.filename) for info in archive.infolist() if not info.is_dir()]
    if len(members) > MAX_FILES: raise HTTPException(400, "Too many files")
    root = parse_skill_name(members)
    name = validate_name(root or Path(file.filename).stem)
    prefix = f"{root}/" if root else ""
    files: dict[str, bytes] = {}
    for info in archive.infolist():
        if info.is_dir(): continue
        member = safe_member(info.filename)
        if root and not (member == root or member.startswith(prefix)): raise HTTPException(400, "Archive contains multiple roots")
        relative = member[len(prefix):] if prefix else member
        if not relative or relative == "SKILL.md": files[relative] = archive.read(info)
        else: files[relative] = archive.read(info)
    if "SKILL.md" not in files: raise HTTPException(400, "SKILL.md is required")
    destination = SKILLS_ROOT / name
    connection = db(); existing = connection.execute("SELECT * FROM skills WHERE name=?", (name,)).fetchone()
    version = int(existing["version"]) + 1 if existing else 1
    if destination.exists(): shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for relative, data in files.items():
        target = destination / safe_member(relative); target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
    stamp = now(); description = description_from(files["SKILL.md"].decode("utf-8", errors="replace"))
    if existing:
        connection.execute("UPDATE skills SET description=?,version=?,updated_by=?,updated_at=? WHERE name=?", (description, version, user["id"], stamp, name))
    else:
        connection.execute("INSERT INTO skills VALUES(?,?,?,?,?,?,?)", (name, description, version, user["id"], user["id"], stamp, stamp))
    connection.commit(); connection.close(); snapshot(name, version, user["id"], "Uploaded skill")
    return {"skill": {"name": name, "version": version, "description": description}}


@app.put("/api/skills/{name}")
async def update_skill(name: str, payload: FileUpdate, request: Request) -> dict[str, Any]:
    user = await current_user(request); name = validate_name(name); connection = db()
    row = connection.execute("SELECT * FROM skills WHERE name=?", (name,)).fetchone()
    if row is None: connection.close(); raise HTTPException(404, "Skill not found")
    if payload.base_version is not None and payload.base_version != row["version"]:
        connection.close(); raise HTTPException(409, "Skill changed by another user", headers={"X-Current-Version": str(row["version"])})
    destination = SKILLS_ROOT / name
    for relative, content in payload.files.items():
        relative = safe_member(relative)
        if relative.startswith("/") or not relative: raise HTTPException(400, "Invalid file path")
        target = destination / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(content, encoding="utf-8")
    if not (destination / "SKILL.md").exists(): raise HTTPException(400, "SKILL.md is required")
    version = row["version"] + 1; stamp = now(); description = description_from((destination / "SKILL.md").read_text(encoding="utf-8"))
    connection.execute("UPDATE skills SET description=?,version=?,updated_by=?,updated_at=? WHERE name=?", (description, version, user["id"], stamp, name)); connection.commit(); connection.close()
    snapshot(name, version, user["id"], payload.message)
    return {"skill": {"name": name, "version": version, "description": description}}


@app.delete("/api/skills/{name}")
async def delete_skill(name: str, request: Request) -> dict[str, bool]:
    user = await current_user(request); name = validate_name(name); connection = db(); row = connection.execute("SELECT * FROM skills WHERE name=?", (name,)).fetchone()
    if row is None: connection.close(); raise HTTPException(404, "Skill not found")
    # The latest archive already contains the deleted content. Keep it in the
    # version table so a future restore endpoint can recover the Skill.
    shutil.rmtree(SKILLS_ROOT / name); connection.execute("DELETE FROM skills WHERE name=?", (name,)); connection.commit(); connection.close()
    return {"ok": True}


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
async def frontend(path: str) -> HTMLResponse:
    return HTMLResponse((Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8"))

