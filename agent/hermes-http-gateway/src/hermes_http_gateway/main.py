from __future__ import annotations

import asyncio
import re
import uuid
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .admin_ui import render_dashboard_page, render_login_page
from .attachments import (
    AttachmentError,
    DownloadedFile,
    DownloadedImage,
    attachment_session_dir,
    cleanup_old_attachment_dirs,
    download_file,
    download_image,
)
from .config import Settings, load_env_file, load_settings
from .db import (
    append_message,
    create_admin_session,
    count_conversations,
    count_messages,
    count_users,
    delete_admin_session,
    delete_message,
    create_conversation,
    get_conversation_by_session,
    get_conversation,
    get_admin_session,
    get_or_create_user,
    init_db,
    list_all_conversations,
    list_messages_by_session,
    list_users,
    list_conversations,
    list_messages,
    search_messages,
    search_messages_admin,
    set_user_default_profile,
    touch_admin_session,
    update_conversation,
)
from .hermes_client import HermesInvocationCancelled, HermesInvocationError, run_first_turn, run_resume_turn
from .locks import (
    begin_attachment_download,
    begin_session_turn,
    clear_pending_attachments,
    finish_attachment_download,
    get_pending_attachments,
    get_session_lock,
    is_session_turn_current,
    wait_for_attachment_downloads,
)


SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
DEFAULT_USER_ID = "guest"


class ChatAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    type: Literal["image", "file"]


class ChatQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="", max_length=20000)
    attachments: list[ChatAttachment] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def require_text_or_attachment(self) -> "ChatQuestion":
        if not self.text.strip() and not self.attachments:
            raise ValueError("question.text is required when question.attachments is empty")
        return self


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: ChatQuestion
    user_id: str | None = Field(default=None, max_length=120)
    session_id: str | None = Field(default=None, max_length=120)
    profile: str | None = Field(default=None, max_length=120)


class ProfileUpdate(BaseModel):
    user_id: str | None = Field(default=None, max_length=120)
    default_hermes_profile: str = Field(min_length=1, max_length=120)


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=200)


def _settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise RuntimeError("gateway settings missing")
    return settings


def _require_gateway_key(request: Request, settings: Settings) -> None:
    if not settings.gateway_api_key:
        return
    supplied = (
        request.headers.get("x-gateway-key")
        or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    )
    if supplied != settings.gateway_api_key:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _admin_session_from_request(request: Request, settings: Settings) -> dict[str, Any] | None:
    session_token = request.cookies.get(settings.admin_cookie_name, "").strip()
    if not session_token:
        return None
    session = get_admin_session(settings, session_token)
    if session is None:
        return None
    return touch_admin_session(settings, session_token, settings.admin_session_ttl_seconds) or session


def _require_admin_session(request: Request, settings: Settings) -> dict[str, Any]:
    session = _admin_session_from_request(request, settings)
    if session is None:
        raise HTTPException(status_code=401, detail="Admin login required")
    return session


def _set_admin_cookie(response: JSONResponse | HTMLResponse | RedirectResponse, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.admin_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.admin_cookie_secure,
        max_age=settings.admin_session_ttl_seconds,
        path="/",
    )


def _clear_admin_cookie(response: JSONResponse | HTMLResponse | RedirectResponse, settings: Settings) -> None:
    response.delete_cookie(key=settings.admin_cookie_name, path="/")


def _validate_admin_login(settings: Settings, username: str, password: str) -> None:
    if not settings.admin_password:
        raise HTTPException(status_code=503, detail="Admin password is not configured")
    if username.strip() != settings.admin_username or password != settings.admin_password:
        raise HTTPException(status_code=401, detail="Invalid username or password")


def _resolve_user_id(value: str | None = None) -> str:
    user_id = (value or "").strip()
    return user_id or DEFAULT_USER_ID


def _validate_session_id(session_id: str) -> str:
    value = session_id.strip()
    if not value or not SESSION_ID_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail="session_id must match ^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$",
        )
    return value


def _conversation_title(question: str) -> str:
    title = " ".join(question.strip().split())
    if not title:
        return "附件请求"
    if len(title) <= 80:
        return title
    return title[:77].rstrip() + "..."


def _conversation_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": row["external_session_id"],
        "user_id": row["user_id"],
        "hermes_session_id": row["hermes_session_id"],
        "hermes_profile": row["hermes_profile"],
        "title": row["title"],
        "status": row["status"],
        "last_error": row["last_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "message_count": row.get("message_count"),
    }


def _message_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "session_id": row["external_session_id"],
        "user_id": row["user_id"],
        "role": row["role"],
        "kind": row["kind"],
        "content": row["content"],
        "model": row["model"],
        "provider": row["provider"],
        "created_at": row["created_at"],
    }


def _search_message_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["message_id"],
        "session_id": row["external_session_id"],
        "user_id": row["user_id"],
        "role": row["role"],
        "kind": row["kind"],
        "content": row["content"],
        "model": row["model"],
        "provider": row["provider"],
        "created_at": row["created_at"],
    }


def _prepare_image_attachments(
    settings: Settings,
    attachments: list[ChatAttachment],
    target_dir: Path,
) -> list[DownloadedImage]:
    image_attachments = [item for item in attachments if item.type == "image"]
    if len(image_attachments) > settings.attachment_max_images:
        raise HTTPException(
            status_code=400,
            detail=f"only {settings.attachment_max_images} image attachment(s) are supported",
        )

    downloaded: list[DownloadedImage] = []
    for item in image_attachments:
        try:
            downloaded.append(download_image(settings, url=item.url, target_dir=target_dir))
        except AttachmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return downloaded


def _prepare_file_attachments(
    settings: Settings,
    attachments: list[ChatAttachment],
    target_dir: Path,
) -> list[DownloadedFile]:
    file_attachments = [item for item in attachments if item.type == "file"]

    downloaded: list[DownloadedFile] = []
    for item in file_attachments:
        try:
            downloaded.append(download_file(settings, url=item.url, target_dir=target_dir))
        except AttachmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return downloaded


def _format_file_ref(path: Path) -> str:
    value = str(path)
    if not any(ch.isspace() or ch in "()[]{}<>\"'`" for ch in value):
        return f"@file:{value}"
    for quote in ("`", '"', "'"):
        if quote not in value:
            return f"@file:{quote}{value}{quote}"
    return f"@file:{value}"


def _prompt_with_attachments(
    prompt: str,
    images: list[DownloadedImage],
    files: list[DownloadedFile],
) -> str:
    if not images and not files:
        return prompt

    sections: list[str] = []
    if images:
        image_refs = "\n".join(f"- {_format_file_ref(item.local_path)}" for item in images)
        sections.append(f"附件图片：\n{image_refs}")
    if files:
        file_refs = "\n".join(f"- {_format_file_ref(item.local_path)}" for item in files)
        sections.append(f"附件文件：\n{file_refs}")
    prefix = prompt.rstrip()
    if not prefix:
        return "\n\n".join(sections)
    return f"{prefix}\n\n" + "\n\n".join(sections)


def _dedupe_downloaded(items: list[Any]) -> list[Any]:
    seen: set[str] = set()
    deduped: list[Any] = []
    for item in items:
        key = str(item.local_path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _attachment_root_for_prompt(
    settings: Settings,
    external_session_id: str,
    prompt: str,
    images: list[DownloadedImage],
    files: list[DownloadedFile],
) -> Path | None:
    paths = [item.local_path for item in images] + [item.local_path for item in files]
    if paths:
        parents = {path.parent for path in paths}
        if len(parents) == 1:
            return next(iter(parents))
        return settings.attachment_storage_root
    if "@" in prompt:
        return attachment_session_dir(settings, external_session_id)
    return None


def _ensure_turn_current(run_key: str, run_generation: int) -> None:
    if not is_session_turn_current(run_key, run_generation):
        raise HermesInvocationCancelled("request superseded by a newer request")


def _ensure_conversation(
    settings: Settings,
    *,
    user_id: str | None,
    session_id: str | None,
    profile: str | None,
    title: str | None,
) -> tuple[str, dict[str, Any]]:
    requested_user_id = _resolve_user_id(user_id)
    user_id_supplied = bool((user_id or "").strip())
    external_session_id = _validate_session_id(session_id) if session_id else str(uuid.uuid4())

    existing = get_conversation_by_session(settings, external_session_id) if session_id else None
    if existing:
        if user_id_supplied and existing["user_id"] != requested_user_id:
            raise HTTPException(
                status_code=409,
                detail="session already belongs to a different user",
            )
        if profile and existing["hermes_profile"] != profile:
            raise HTTPException(
                status_code=409,
                detail="session already belongs to a different Hermes profile",
            )
        return existing["user_id"], existing

    user = get_or_create_user(settings, requested_user_id)
    effective_profile = profile or user["default_hermes_profile"] or settings.hermes_profile_default
    return (
        requested_user_id,
        create_conversation(
            settings,
            external_session_id=external_session_id,
            user_id=requested_user_id,
            hermes_profile=effective_profile,
            title=title,
        ),
    )


async def _run_chat_turn(
    settings: Settings,
    *,
    user_id: str | None,
    session_id: str | None,
    request: ChatRequest,
) -> dict[str, Any]:
    question_text = request.question.text
    attachments = request.question.attachments
    effective_model = settings.default_model
    effective_provider = settings.default_provider
    if effective_provider and not effective_model:
        raise HTTPException(
            status_code=400,
            detail="DEFAULT_PROVIDER requires DEFAULT_MODEL",
        )
    user_id, conversation = _ensure_conversation(
        settings,
        user_id=user_id,
        session_id=session_id or request.session_id,
        profile=request.profile,
        title=_conversation_title(question_text),
    )

    external_session_id = conversation["external_session_id"]
    run_key = f"{user_id}:{external_session_id}"
    await asyncio.to_thread(wait_for_attachment_downloads, run_key)
    run_generation = begin_session_turn(run_key)
    lock = get_session_lock(run_key)

    try:
        _ensure_turn_current(run_key, run_generation)
    except HermesInvocationCancelled as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if attachments:
        begin_attachment_download(run_key)
        try:
            target_dir = attachment_session_dir(settings, external_session_id)
            downloaded_images = await asyncio.to_thread(
                _prepare_image_attachments,
                settings,
                attachments,
                target_dir,
            )
            downloaded_files = await asyncio.to_thread(
                _prepare_file_attachments,
                settings,
                attachments,
                target_dir,
            )
        except Exception:
            finish_attachment_download(run_key)
            raise
        else:
            finish_attachment_download(
                run_key,
                images=downloaded_images,
                files=downloaded_files,
            )
    else:
        cleanup_old_attachment_dirs(settings)
    try:
        _ensure_turn_current(run_key, run_generation)
    except HermesInvocationCancelled as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    user_message_id: str | None = None
    async with lock:
        try:
            _ensure_turn_current(run_key, run_generation)
        except HermesInvocationCancelled as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        conversation = get_conversation(settings, user_id, external_session_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Session not found")

        pending_images, pending_files = get_pending_attachments(run_key)
        turn_images = _dedupe_downloaded(pending_images)
        turn_files = _dedupe_downloaded(pending_files)
        hermes_prompt = _prompt_with_attachments(question_text, turn_images, turn_files)
        image_paths = [item.local_path for item in turn_images]
        attachment_root = _attachment_root_for_prompt(
            settings,
            external_session_id,
            hermes_prompt,
            turn_images,
            turn_files,
        )

        user_message = append_message(
            settings,
            external_session_id=external_session_id,
            user_id=user_id,
            role="user",
            kind="chat",
            content=question_text,
            title=conversation["title"],
        )
        user_message_id = user_message["id"]
        update_conversation(
            settings,
            external_session_id=external_session_id,
            user_id=user_id,
            status="running",
            last_error=None,
        )

        try:
            if conversation["hermes_session_id"]:
                result = await asyncio.to_thread(
                    run_resume_turn,
                    settings,
                    prompt=hermes_prompt,
                    hermes_session_id=conversation["hermes_session_id"],
                    profile=conversation["hermes_profile"],
                    model=effective_model,
                    provider=effective_provider,
                    image_paths=image_paths,
                    attachment_root=attachment_root,
                    run_key=run_key,
                    run_generation=run_generation,
                )
            else:
                result = await asyncio.to_thread(
                    run_first_turn,
                    settings,
                    prompt=hermes_prompt,
                    profile=conversation["hermes_profile"],
                    model=effective_model,
                    provider=effective_provider,
                    image_paths=image_paths,
                    attachment_root=attachment_root,
                    run_key=run_key,
                    run_generation=run_generation,
                )
            _ensure_turn_current(run_key, run_generation)
        except HermesInvocationCancelled as exc:
            if user_message_id:
                delete_message(
                    settings,
                    external_session_id=external_session_id,
                    user_id=user_id,
                    message_id=user_message_id,
                )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except HermesInvocationError as exc:
            append_message(
                settings,
                external_session_id=external_session_id,
                user_id=user_id,
                role="assistant",
                kind="error",
                content=str(exc),
                title=conversation["title"],
                model=effective_model,
                provider=effective_provider,
            )
            update_conversation(
                settings,
                external_session_id=external_session_id,
                user_id=user_id,
                status="error",
                last_error=str(exc),
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "Hermes invocation failed",
                    "error": str(exc),
                    "stdout": exc.stdout,
                    "stderr": exc.stderr,
                    "returncode": exc.returncode,
                    "session_id": external_session_id,
                },
            ) from exc

        if not conversation["hermes_session_id"]:
            if not result.session_id:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "message": "Hermes did not return a session_id",
                        "session_id": external_session_id,
                    },
                )
            conversation = update_conversation(
                settings,
                external_session_id=external_session_id,
                user_id=user_id,
                hermes_session_id=result.session_id,
                status="open",
                last_error=None,
            ) or conversation
        else:
            conversation = update_conversation(
                settings,
                external_session_id=external_session_id,
                user_id=user_id,
                hermes_session_id=result.session_id or conversation["hermes_session_id"],
                status="open",
                last_error=None,
            ) or conversation

        append_message(
            settings,
            external_session_id=external_session_id,
            user_id=user_id,
            role="assistant",
            kind="chat",
            content=result.answer,
            title=conversation["title"],
            model=effective_model,
            provider=effective_provider,
        )
        clear_pending_attachments(run_key, images=turn_images, files=turn_files)

        response = {
            "session_id": external_session_id,
            "hermes_session_id": conversation["hermes_session_id"] or result.session_id,
            "user_id": user_id,
            "hermes_profile": conversation["hermes_profile"],
            "answer": result.answer,
        }
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_env_file()
    settings = load_settings()
    init_db(settings)
    app.state.settings = settings
    yield


app = FastAPI(title="Hermes HTTP Gateway", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health(request: Request):
    settings = _settings(request)
    hermes_exists = bool(settings.hermes_bin) and (
        shutil.which(settings.hermes_bin) is not None or settings.hermes_bin.startswith("/")
    )
    return {
        "ok": True,
        "database": str(settings.database_url),
        "hermes_bin": settings.hermes_bin,
        "hermes_workdir": str(settings.hermes_workdir),
        "hermes_available": hermes_exists,
    }


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    settings = _settings(request)
    session = _admin_session_from_request(request, settings)
    if session is not None:
        return RedirectResponse("/admin", status_code=302)
    return HTMLResponse(
        render_login_page(
            username_default=settings.admin_username,
            password_configured=bool(settings.admin_password),
        )
    )


@app.post("/admin/login")
def admin_login(request: Request, body: AdminLoginRequest):
    settings = _settings(request)
    _validate_admin_login(settings, body.username, body.password)
    session = create_admin_session(
        settings,
        username=settings.admin_username,
        ttl_seconds=settings.admin_session_ttl_seconds,
    )
    response = JSONResponse({"ok": True, "redirect": "/admin", "username": session["username"]})
    _set_admin_cookie(response, settings, session["session_token"])
    return response


@app.get("/admin/logout")
def admin_logout(request: Request):
    settings = _settings(request)
    session_token = request.cookies.get(settings.admin_cookie_name, "").strip()
    if session_token:
        delete_admin_session(settings, session_token)
    response = RedirectResponse("/admin/login", status_code=302)
    _clear_admin_cookie(response, settings)
    return response


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    settings = _settings(request)
    session = _admin_session_from_request(request, settings)
    if session is None:
        return RedirectResponse("/admin/login", status_code=302)
    return HTMLResponse(render_dashboard_page(username=session["username"]))


@app.get("/admin/api/summary")
def admin_summary(request: Request):
    settings = _settings(request)
    _require_admin_session(request, settings)
    return {
        "users": count_users(settings),
        "conversations": count_conversations(settings),
        "messages": count_messages(settings),
    }


@app.get("/admin/api/users")
def admin_users(request: Request, query: str | None = None, limit: int = 100, offset: int = 0):
    settings = _settings(request)
    _require_admin_session(request, settings)
    return {"users": list_users(settings, limit=limit, offset=offset, query=query)}


@app.get("/admin/api/conversations")
def admin_conversations(
    request: Request,
    query: str | None = None,
    user_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    settings = _settings(request)
    _require_admin_session(request, settings)
    return {
        "conversations": list_all_conversations(
            settings,
            limit=limit,
            offset=offset,
            query=query,
            user_id=user_id,
        )
    }


@app.get("/admin/api/conversations/{session_id}")
def admin_conversation_detail(request: Request, session_id: str):
    settings = _settings(request)
    _require_admin_session(request, settings)
    conversation = get_conversation_by_session(settings, _validate_session_id(session_id))
    if conversation is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"conversation": _conversation_payload(conversation)}


@app.get("/admin/api/conversations/{session_id}/messages")
def admin_conversation_messages(request: Request, session_id: str):
    settings = _settings(request)
    _require_admin_session(request, settings)
    conversation_id = _validate_session_id(session_id)
    conversation = get_conversation_by_session(settings, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "conversation": _conversation_payload(conversation),
        "messages": [
            _message_payload(row) for row in list_messages_by_session(settings, conversation_id)
        ],
    }


@app.get("/admin/api/search")
def admin_search(
    request: Request,
    q: str,
    user_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
):
    settings = _settings(request)
    _require_admin_session(request, settings)
    return {
        "results": search_messages_admin(
            settings,
            query=q,
            user_id=user_id,
            external_session_id=_validate_session_id(session_id) if session_id else None,
            limit=limit,
        )
    }


@app.get("/v1/users/me")
def get_me(request: Request, user_id: str | None = None):
    settings = _settings(request)
    _require_gateway_key(request, settings)
    resolved_user_id = _resolve_user_id(user_id)
    user = get_or_create_user(settings, resolved_user_id)
    return {"user": user}


@app.put("/v1/users/me")
def update_me(request: Request, body: ProfileUpdate):
    settings = _settings(request)
    _require_gateway_key(request, settings)
    resolved_user_id = _resolve_user_id(body.user_id)
    user = set_user_default_profile(settings, resolved_user_id, body.default_hermes_profile.strip())
    return {"user": user}


@app.get("/v1/sessions")
def sessions(request: Request, user_id: str | None = None, limit: int = 50, offset: int = 0):
    settings = _settings(request)
    _require_gateway_key(request, settings)
    resolved_user_id = _resolve_user_id(user_id)
    return {
        "sessions": [
            _conversation_payload(row)
            for row in list_conversations(settings, resolved_user_id, limit=limit, offset=offset)
        ]
    }


@app.get("/v1/sessions/search")
def session_search(
    request: Request,
    q: str,
    user_id: str | None = None,
    session_id: str | None = None,
    limit: int = 20,
):
    settings = _settings(request)
    _require_gateway_key(request, settings)
    resolved_user_id = _resolve_user_id(user_id)
    results = search_messages(
        settings,
        user_id=resolved_user_id,
        query=q,
        external_session_id=_validate_session_id(session_id) if session_id else None,
        limit=limit,
    )
    return {"results": results}


@app.get("/v1/sessions/{session_id}/messages")
def session_messages(
    request: Request,
    session_id: str,
    user_id: str | None = None,
    q: str | None = None,
    limit: int = 20,
):
    settings = _settings(request)
    _require_gateway_key(request, settings)
    resolved_user_id = _resolve_user_id(user_id)
    conversation_id = _validate_session_id(session_id)
    conversation = get_conversation(settings, resolved_user_id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if q:
        messages = [
            _search_message_payload(row)
            for row in search_messages(
                settings,
                user_id=resolved_user_id,
                query=q,
                external_session_id=conversation_id,
                limit=limit,
            )
        ]
    else:
        messages = [
            _message_payload(row)
            for row in list_messages(settings, resolved_user_id, conversation["external_session_id"])
        ]
    return {
        "conversation": _conversation_payload(conversation),
        "messages": messages,
    }


@app.post("/v1/chat")
async def chat(request: Request, body: ChatRequest):
    settings = _settings(request)
    _require_gateway_key(request, settings)
    return await _run_chat_turn(settings, user_id=body.user_id, session_id=body.session_id, request=body)


def main() -> None:
    load_env_file()
    settings = load_settings()
    uvicorn.run(
        "hermes_http_gateway.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
