from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException

from hermes_http_gateway.admin_ui import render_chat_page, render_dashboard_page, render_login_page
from hermes_http_gateway import main as gateway
from hermes_http_gateway.attachments import attachment_session_dir
from hermes_http_gateway.config import Settings, load_env_file
from hermes_http_gateway import db as gateway_db
from hermes_http_gateway import hermes_client as gateway_hermes_client
from hermes_http_gateway.hermes_client import HermesInvocationCancelled, HermesRunResult
from hermes_http_gateway.locks import begin_session_turn, get_pending_attachments


def _make_settings(tmpdir: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8011,
        database_url=tmpdir / "gateway.sqlite",
        hermes_bin=str(Path("/Users/panweikui/Pwk-Coding/automation/agent/hermes-agent/.venv/bin/hermes")),
        hermes_workdir=Path("/Users/panweikui/Pwk-Coding/automation/agent/hermes-agent"),
        hermes_profile_default="default",
        gateway_api_key=None,
        admin_username="admin",
        admin_password="secret",
        admin_cookie_name="hermes_admin_session",
        admin_session_ttl_seconds=3600,
        admin_cookie_secure=False,
        public_base_path="",
        timeout_seconds=300,
        source_tag="tool",
        default_model=None,
        default_provider=None,
        attachment_max_bytes=10 * 1024 * 1024,
        attachment_download_timeout_seconds=30,
        attachment_max_images=8,
        attachment_redirect_limit=3,
        attachment_storage_root=tmpdir / "attachments",
        attachment_retention_days=3,
    )


def _init_temp_db(settings: Settings) -> None:
    gateway_db._initialized = False
    gateway.init_db(settings)


def _chat_request(question: str, **kwargs) -> gateway.ChatRequest:
    attachments = kwargs.pop("attachments", None)
    return gateway.ChatRequest(
        question=gateway.ChatQuestion(
            text=question,
            attachments=attachments or [],
        ),
        **kwargs,
    )


class _FakeUpload:
    def __init__(self, filename: str, content_type: str, data: bytes):
        self.filename = filename
        self.content_type = content_type
        self._data = data
        self._offset = 0
        self.closed = False

    async def read(self, size: int) -> bytes:
        if self._offset >= len(self._data):
            return b""
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    async def close(self) -> None:
        self.closed = True


class _FakeForm(dict):
    def __init__(self, values: dict[str, object], files: list[_FakeUpload]):
        super().__init__(values)
        self._files = files

    def getlist(self, key: str) -> list[object]:
        return list(self._files) if key == "files" else []


class GatewayTests(unittest.TestCase):
    def test_search_route_is_not_shadowed(self):
        paths = [getattr(route, "path", "") for route in gateway.app.routes]
        self.assertIn("/v1/chat", paths)
        self.assertNotIn("/v1/chat/{session_id}", paths)
        self.assertNotIn("/v1/sessions/{session_id}", paths)
        self.assertNotIn("/v1/chat/{session_id}/messages", paths)
        self.assertNotIn("/v1/sessions/{session_id}/search", paths)
        self.assertLess(paths.index("/v1/sessions/search"), paths.index("/v1/sessions/{session_id}/messages"))

    def test_default_provider_requires_default_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = replace(_make_settings(Path(tmp)), default_provider="sub2api")
            _init_temp_db(settings)
            body = _chat_request("hi")
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(gateway._run_chat_turn(settings, user_id="alice", session_id=None, request=body))
            self.assertEqual(ctx.exception.status_code, 400)

    def test_user_id_defaults_to_guest(self):
        self.assertEqual(gateway._resolve_user_id(None), "guest")
        self.assertEqual(gateway._resolve_user_id(""), "guest")
        self.assertEqual(gateway._resolve_user_id("  "), "guest")
        self.assertEqual(gateway._resolve_user_id(" alice "), "alice")

    def test_chat_request_rejects_title_field(self):
        with self.assertRaises(Exception):
            _chat_request("hi", title="custom title")

    def test_chat_request_rejects_legacy_string_question(self):
        with self.assertRaises(Exception):
            gateway.ChatRequest(question="hi")

    def test_chat_request_rejects_legacy_top_level_attachments(self):
        with self.assertRaises(Exception):
            gateway.ChatRequest(
                question=gateway.ChatQuestion(text="hi"),
                attachments=[
                    gateway.ChatAttachment(url="https://example.com/a.png", type="image"),
                ],
            )

    def test_chat_question_allows_empty_text_with_attachments(self):
        attachment = gateway.ChatAttachment(url="https://example.com/a.png", type="image")
        explicit_empty = gateway.ChatRequest(
            question=gateway.ChatQuestion(text="", attachments=[attachment]),
        )
        omitted_text = gateway.ChatRequest(
            question=gateway.ChatQuestion(attachments=[attachment]),
        )
        self.assertEqual(explicit_empty.question.text, "")
        self.assertEqual(omitted_text.question.text, "")

    def test_chat_question_rejects_empty_text_without_attachments(self):
        with self.assertRaises(Exception):
            gateway.ChatRequest(question=gateway.ChatQuestion())
        with self.assertRaises(Exception):
            gateway.ChatRequest(question=gateway.ChatQuestion(text="   "))

    def test_prompt_with_image_attachments_uses_natural_language_hint(self):
        prompt = gateway._prompt_with_attachments(
            "请分析这张图",
            [
                SimpleNamespace(local_path=Path("/tmp/hermes/demo/image-1.png")),
                SimpleNamespace(local_path=Path("/tmp/hermes/demo/image-2.png")),
            ],
            [],
        )

        self.assertIn("请分析这张图", prompt)
        self.assertIn("本轮请求包含 2 张图片附件", prompt)
        self.assertNotIn("@file:", prompt)
        self.assertNotIn("image-1.png", prompt)
        self.assertNotIn("image-2.png", prompt)

    def test_prompt_with_file_attachments_keeps_file_refs(self):
        prompt = gateway._prompt_with_attachments(
            "请分析日志",
            [],
            [SimpleNamespace(local_path=Path("/tmp/hermes/demo/app.log"))],
        )

        self.assertIn("请分析日志", prompt)
        self.assertIn("附件文件", prompt)
        self.assertIn("@file:/tmp/hermes/demo/app.log", prompt)

    def test_audit_body_redacts_signed_url_query_values(self):
        serialized = gateway._serialize_audit_body(
            {
                "question": {
                    "text": "inspect",
                    "attachments": [
                        {"type": "file", "url": "https://example.com/a.log?token=secret&part=1"}
                    ],
                }
            }
        )
        self.assertIn("token=%5BREDACTED%5D", serialized)
        self.assertIn("part=1", serialized)
        self.assertNotIn("secret", serialized)

    def test_uploaded_attachments_use_the_same_attachment_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            image = _FakeUpload("diagram.png", "image/png", b"\x89PNG\r\n\x1a\ncontent")
            text = _FakeUpload("notes.txt", "text/plain", b"hello from upload")
            loop = asyncio.new_event_loop()
            try:
                prepared = loop.run_until_complete(
                    gateway._prepare_uploaded_attachments(
                        settings,
                        [image, text],
                        Path(tmp) / "attachments",
                    )
                )
            finally:
                loop.close()

        self.assertEqual(len(prepared.images), 1)
        self.assertEqual(len(prepared.files), 1)
        self.assertEqual(prepared.images[0].mime_type, "image/png")
        self.assertEqual(prepared.files[0].mime_type, "text/plain")
        self.assertTrue(image.closed)
        self.assertTrue(text.closed)

    def test_admin_chat_rejects_invalid_csrf_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            _init_temp_db(settings)
            admin = gateway_db.create_admin_session(settings, username="admin", ttl_seconds=3600)
            request = SimpleNamespace(
                headers={"x-csrf-token": "bad"},
                cookies={settings.admin_cookie_name: admin["session_token"]},
                app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
            )
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(gateway._admin_chat_request(request, settings))

        self.assertEqual(ctx.exception.status_code, 403)

    def test_admin_chat_uses_shared_chat_execution_with_uploaded_image(self):
        seen: dict[str, object] = {}

        def fake_first_turn(settings, *, prompt, profile, model=None, provider=None, image_paths=None, attachment_root=None, run_key=None, run_generation=None):
            seen["prompt"] = prompt
            seen["image_paths"] = image_paths
            return HermesRunResult(
                answer="uploaded image answer",
                session_id="hermes-admin-upload",
                stdout="uploaded image answer",
                stderr="",
                returncode=0,
                usage={"session_id": "hermes-admin-upload"},
            )

        class RequestWithForm(SimpleNamespace):
            async def form(self):
                return _FakeForm(
                    {"text": "请分析上传的图片", "user_id": "admin-playground", "session_id": "admin-upload", "profile": "default"},
                    [_FakeUpload("upload.png", "image/png", b"\x89PNG\r\n\x1a\ncontent")],
                )

        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            _init_temp_db(settings)
            admin = gateway_db.create_admin_session(settings, username="admin", ttl_seconds=3600)
            request = RequestWithForm(
                headers={"x-csrf-token": gateway._admin_csrf_token(admin["session_token"])},
                cookies={settings.admin_cookie_name: admin["session_token"]},
                app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
            )
            original_first = gateway.run_first_turn
            gateway.run_first_turn = fake_first_turn
            try:
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(gateway._admin_chat_request(request, settings))
                finally:
                    loop.close()
            finally:
                gateway.run_first_turn = original_first

        self.assertEqual(result["answer"], "uploaded image answer")
        self.assertEqual(result["session_id"], "admin-upload")
        self.assertIn("本轮请求包含 1 张图片附件", seen["prompt"])
        self.assertEqual(len(seen["image_paths"] or []), 1)

    def test_chat_endpoint_passes_user_id_from_body(self):
        seen: list[tuple[str | None, str | None]] = []

        async def fake_run_chat_turn(settings, *, user_id, session_id, request, request_body=None):
            seen.append((user_id, session_id))
            return {"user_id": user_id, "session_id": session_id}

        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            request = SimpleNamespace(
                headers={},
                app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
            )
            original_run = gateway._run_chat_turn
            gateway._run_chat_turn = fake_run_chat_turn
            try:
                first = asyncio.run(gateway.chat(request, _chat_request("hi", user_id="alice")))
                second = asyncio.run(gateway.chat(request, _chat_request("hi")))
            finally:
                gateway._run_chat_turn = original_run

        self.assertEqual(first["user_id"], "alice")
        self.assertIsNone(second["user_id"])
        self.assertEqual(seen, [("alice", None), (None, None)])

    def test_chat_resume_can_infer_user_and_profile_from_session_id(self):
        calls: list[tuple[str, str]] = []

        def fake_first_turn(settings, *, prompt, profile, model=None, provider=None, image_paths=None, attachment_root=None, run_key=None, run_generation=None):
            calls.append(("first", profile))
            return HermesRunResult(
                answer="first answer",
                session_id="hermes-1",
                stdout="first answer",
                stderr="",
                returncode=0,
                usage={"session_id": "hermes-1"},
            )

        def fake_resume_turn(settings, *, prompt, hermes_session_id, profile, model=None, provider=None, image_paths=None, attachment_root=None, run_key=None, run_generation=None):
            calls.append(("resume", profile))
            return HermesRunResult(
                answer="second answer",
                session_id=hermes_session_id,
                stdout="second answer",
                stderr="",
                returncode=0,
                usage=None,
            )

        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            _init_temp_db(settings)
            original_first = gateway.run_first_turn
            original_resume = gateway.run_resume_turn
            gateway.run_first_turn = fake_first_turn
            gateway.run_resume_turn = fake_resume_turn
            try:
                first = asyncio.run(
                    gateway._run_chat_turn(
                        settings,
                        user_id="toolless",
                        session_id="12a0015d-1428-4cd4-b3fc-4b0b33d33cd3",
                        request=_chat_request(
                            "如何分析音画不同步问题",
                            user_id="toolless",
                            profile="tool_less",
                            session_id="12a0015d-1428-4cd4-b3fc-4b0b33d33cd3",
                        ),
                    )
                )

                second = asyncio.run(
                    gateway._run_chat_turn(
                        settings,
                        user_id=None,
                        session_id=first["session_id"],
                        request=_chat_request("继续分析", session_id=first["session_id"]),
                    )
                )
            finally:
                gateway.run_first_turn = original_first
                gateway.run_resume_turn = original_resume

        self.assertEqual(second["answer"], "second answer")
        self.assertEqual(second["user_id"], "toolless")
        self.assertEqual(second["hermes_profile"], "tool_less")
        self.assertEqual(calls, [("first", "tool_less"), ("resume", "tool_less")])

    def test_chat_resume_rejects_explicit_user_or_profile_mismatch(self):
        def fake_first_turn(settings, *, prompt, profile, model=None, provider=None, image_paths=None, attachment_root=None, run_key=None, run_generation=None):
            return HermesRunResult(
                answer="first answer",
                session_id="hermes-1",
                stdout="first answer",
                stderr="",
                returncode=0,
                usage={"session_id": "hermes-1"},
            )

        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            _init_temp_db(settings)
            original_first = gateway.run_first_turn
            gateway.run_first_turn = fake_first_turn
            try:
                first = asyncio.run(
                    gateway._run_chat_turn(
                        settings,
                        user_id="toolless",
                        session_id="demo-session",
                        request=_chat_request(
                            "hello",
                            user_id="toolless",
                            profile="tool_less",
                            session_id="demo-session",
                        ),
                    )
                )

                with self.assertRaises(HTTPException) as user_ctx:
                    asyncio.run(
                        gateway._run_chat_turn(
                            settings,
                            user_id="alice",
                            session_id=first["session_id"],
                            request=_chat_request(
                                "again",
                                user_id="alice",
                                session_id=first["session_id"],
                            ),
                        )
                    )

                with self.assertRaises(HTTPException) as profile_ctx:
                    asyncio.run(
                        gateway._run_chat_turn(
                            settings,
                            user_id=None,
                            session_id=first["session_id"],
                            request=_chat_request(
                                "again",
                                profile="default",
                                session_id=first["session_id"],
                            ),
                        )
                    )
            finally:
                gateway.run_first_turn = original_first

        self.assertEqual(user_ctx.exception.status_code, 409)
        self.assertEqual(profile_ctx.exception.status_code, 409)

    def test_chat_creates_and_resumes_session(self):
        calls: list[tuple[str, str]] = []

        def fake_first_turn(settings, *, prompt, profile, model=None, provider=None, image_paths=None, attachment_root=None, run_key=None, run_generation=None):
            calls.append(("first", prompt))
            return HermesRunResult(
                answer="first answer",
                session_id="hermes-1",
                stdout="first answer",
                stderr="",
                returncode=0,
                usage={"session_id": "hermes-1"},
            )

        def fake_resume_turn(settings, *, prompt, hermes_session_id, profile, model=None, provider=None, image_paths=None, attachment_root=None, run_key=None, run_generation=None):
            calls.append(("resume", prompt))
            return HermesRunResult(
                answer="second answer",
                session_id=hermes_session_id,
                stdout="second answer",
                stderr="",
                returncode=0,
                usage=None,
            )

        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            _init_temp_db(settings)
            original_first = gateway.run_first_turn
            original_resume = gateway.run_resume_turn
            gateway.run_first_turn = fake_first_turn
            gateway.run_resume_turn = fake_resume_turn
            try:
                first = asyncio.run(
                    gateway._run_chat_turn(
                        settings,
                        user_id="alice",
                        session_id=None,
                        request=_chat_request("hello"),
                    )
                )
                self.assertEqual(first["hermes_session_id"], "hermes-1")

                second = asyncio.run(
                    gateway._run_chat_turn(
                        settings,
                        user_id="alice",
                        session_id=first["session_id"],
                        request=_chat_request("again", session_id=first["session_id"]),
                    )
                )
                self.assertEqual(second["answer"], "second answer")
            finally:
                gateway.run_first_turn = original_first
                gateway.run_resume_turn = original_resume

        self.assertEqual(calls, [("first", "hello"), ("resume", "again")])
        self.assertEqual(
            set(first),
            {"session_id", "hermes_session_id", "user_id", "hermes_profile", "answer"},
        )
        self.assertEqual(
            set(second),
            {"session_id", "hermes_session_id", "user_id", "hermes_profile", "answer"},
        )

    def test_new_chat_turn_supersedes_inflight_same_session(self):
        started = threading.Event()
        calls: list[str] = []

        def fake_first_turn(
            settings,
            *,
            prompt,
            profile,
            model=None,
            provider=None,
            image_paths=None,
            attachment_root=None,
            run_key=None,
            run_generation=None,
        ):
            calls.append(prompt)
            if prompt == "first":
                started.set()
                while gateway.is_session_turn_current(run_key, run_generation):
                    time.sleep(0.01)
                raise HermesInvocationCancelled("request superseded by a newer request")
            return HermesRunResult(
                answer="second answer",
                session_id="hermes-2",
                stdout="second answer",
                stderr="",
                returncode=0,
                usage={"session_id": "hermes-2"},
            )

        async def run_case(settings):
            first_task = asyncio.create_task(
                gateway._run_chat_turn(
                    settings,
                    user_id="alice",
                    session_id="demo-session",
                    request=_chat_request(
                        "first",
                        user_id="alice",
                        session_id="demo-session",
                    ),
                )
            )
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            second = await gateway._run_chat_turn(
                settings,
                user_id="alice",
                session_id="demo-session",
                request=_chat_request(
                    "second",
                    user_id="alice",
                    session_id="demo-session",
                ),
            )
            with self.assertRaises(HTTPException) as ctx:
                await first_task
            return second, ctx.exception

        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            _init_temp_db(settings)
            original_first = gateway.run_first_turn
            gateway.run_first_turn = fake_first_turn
            try:
                second, first_error = asyncio.run(run_case(settings))
                messages = gateway_db.list_messages(settings, "alice", "demo-session")
                events = gateway_db.list_request_events_by_session(settings, "demo-session")
            finally:
                gateway.run_first_turn = original_first

        self.assertEqual(first_error.status_code, 409)
        self.assertEqual(second["answer"], "second answer")
        self.assertEqual(calls, ["first", "second"])
        self.assertEqual([row["content"] for row in messages], ["second", "second answer"])
        self.assertEqual([row["prompt"] for row in events], ["first", "second"])
        self.assertEqual([row["status"] for row in events], ["superseded", "completed"])
        self.assertEqual(events[0]["http_status"], 409)
        self.assertIn("superseded", events[0]["error_detail"])

    def test_admin_timeline_includes_completed_failed_and_superseded_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            _init_temp_db(settings)
            gateway_db.create_conversation(
                settings,
                external_session_id="timeline-session",
                user_id="alice",
                hermes_profile="default",
                title="Timeline",
            )
            completed = gateway_db.create_request_event(
                settings,
                external_session_id="timeline-session",
                user_id="alice",
                prompt="completed prompt",
                request_body='{"question":{"text":"completed prompt"}}',
            )
            gateway_db.finish_request_event(
                settings, event_id=completed["id"], status="completed", http_status=200
            )
            gateway_db.record_request_stage(
                settings,
                request_event_id=completed["id"],
                stage="Hermes CLI 运行",
                duration_ms=1250.4,
            )
            failed = gateway_db.create_request_event(
                settings,
                external_session_id="timeline-session",
                user_id="alice",
                prompt="failed prompt",
            )
            gateway_db.finish_request_event(
                settings,
                event_id=failed["id"],
                status="failed",
                http_status=502,
                error_detail="Hermes timed out",
            )
            superseded = gateway_db.create_request_event(
                settings,
                external_session_id="timeline-session",
                user_id="alice",
                prompt="cancelled prompt",
            )
            gateway_db.finish_request_event(
                settings,
                event_id=superseded["id"],
                status="superseded",
                http_status=409,
                error_detail="request superseded by a newer request",
            )
            gateway_db.append_message(
                settings,
                external_session_id="timeline-session",
                user_id="alice",
                role="assistant",
                kind="chat",
                content="completed answer",
            )
            admin = gateway_db.create_admin_session(settings, username="admin", ttl_seconds=3600)
            request = SimpleNamespace(
                cookies={settings.admin_cookie_name: admin["session_token"]},
                app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
            )
            payload = gateway.admin_conversation_messages(request, "timeline-session")

        self.assertEqual([row["status"] for row in payload["requests"]], ["completed", "failed", "superseded"])
        self.assertEqual(len(payload["timeline"]), 4)
        self.assertEqual(payload["timeline"][0]["event_type"], "request")
        self.assertIn("Hermes timed out", payload["timeline"][1]["error_detail"])
        self.assertEqual(payload["timeline"][2]["http_status"], 409)
        self.assertEqual(payload["timeline"][3]["content"], "completed answer")
        self.assertEqual(
            payload["requests"][0]["request_body"],
            '{"question":{"text":"completed prompt"}}',
        )
        self.assertEqual(payload["requests"][0]["stages"][0]["stage"], "Hermes CLI 运行")
        self.assertEqual(payload["requests"][0]["stages"][0]["duration_ms"], 1250.4)

    def test_new_chat_turn_waits_for_previous_attachment_and_reuses_it(self):
        download_started = threading.Event()
        release_download = threading.Event()
        hermes_calls: list[tuple[str, list[Path], Path | None]] = []

        class FakeDownloadedImage:
            def __init__(self, local_path: Path):
                self.local_path = local_path

        def fake_prepare_images(settings, attachments, target_dir):
            download_started.set()
            release_download.wait(timeout=1)
            image_path = target_dir / "old-image.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
            return [FakeDownloadedImage(image_path)]

        def fake_first_turn(
            settings,
            *,
            prompt,
            profile,
            model=None,
            provider=None,
            image_paths=None,
            attachment_root=None,
            run_key=None,
            run_generation=None,
        ):
            hermes_calls.append((prompt, list(image_paths or []), attachment_root))
            if prompt.startswith("first"):
                while gateway.is_session_turn_current(run_key, run_generation):
                    time.sleep(0.01)
                raise HermesInvocationCancelled("request superseded by a newer request")
            return HermesRunResult(
                answer="second answer",
                session_id="hermes-2",
                stdout="second answer",
                stderr="",
                returncode=0,
                usage={"session_id": "hermes-2"},
            )

        async def run_case(settings):
            first_task = asyncio.create_task(
                gateway._run_chat_turn(
                    settings,
                    user_id="alice",
                    session_id="pending-session",
                    request=_chat_request(
                        "first with image",
                        user_id="alice",
                        session_id="pending-session",
                        attachments=[
                            gateway.ChatAttachment(
                                url="https://example.com/old-image.png",
                                type="image",
                            )
                        ],
                    ),
                )
            )
            self.assertTrue(await asyncio.to_thread(download_started.wait, 1))
            second_task = asyncio.create_task(
                gateway._run_chat_turn(
                    settings,
                    user_id="alice",
                    session_id="pending-session",
                    request=_chat_request(
                        "second question",
                        user_id="alice",
                        session_id="pending-session",
                    ),
                )
            )
            await asyncio.sleep(0.05)
            self.assertFalse(second_task.done())
            release_download.set()
            second = await second_task
            with self.assertRaises(HTTPException) as ctx:
                await first_task
            return second, ctx.exception

        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            _init_temp_db(settings)
            original_prepare_images = gateway._prepare_image_attachments
            original_first = gateway.run_first_turn
            gateway._prepare_image_attachments = fake_prepare_images
            gateway.run_first_turn = fake_first_turn
            try:
                second, first_error = asyncio.run(run_case(settings))
                pending_images, pending_files = get_pending_attachments("alice:pending-session")
                messages = gateway_db.list_messages(settings, "alice", "pending-session")
            finally:
                gateway._prepare_image_attachments = original_prepare_images
                gateway.run_first_turn = original_first

        self.assertEqual(first_error.status_code, 409)
        self.assertEqual(second["answer"], "second answer")
        self.assertEqual(hermes_calls[-1][0].splitlines()[0], "second question")
        self.assertIn("本轮请求包含 1 张图片附件", hermes_calls[-1][0])
        self.assertNotIn("@file:", hermes_calls[-1][0])
        self.assertEqual([path.name for path in hermes_calls[-1][1]], ["old-image.png"])
        self.assertEqual(Path(hermes_calls[-1][2]).name, "pending-session")
        self.assertEqual(pending_images, [])
        self.assertEqual(pending_files, [])
        self.assertEqual([row["content"] for row in messages], ["second question", "second answer"])

    def test_file_attachment_adds_file_ref_and_attachment_root(self):
        seen: dict[str, object] = {}

        class FakeDownloadedFile:
            def __init__(self, local_path: Path):
                self.local_path = local_path

            def public_payload(self):
                return {
                    "url": "https://example.com/a.txt",
                    "type": "file",
                    "mime_type": "text/plain",
                    "size_bytes": 12,
                    "sha256": "fileabc",
                    "local_path": str(self.local_path),
                }

        def fake_prepare_files(settings, attachments, target_dir):
            file_path = target_dir / "a.txt"
            file_path.write_text("hello file", encoding="utf-8")
            return [FakeDownloadedFile(file_path)]

        def fake_first_turn(settings, *, prompt, profile, model=None, provider=None, image_paths=None, attachment_root=None, run_key=None, run_generation=None):
            seen["prompt"] = prompt
            seen["attachment_root"] = attachment_root
            return HermesRunResult(
                answer="file answer",
                session_id="hermes-file-1",
                stdout="file answer",
                stderr="",
                returncode=0,
                usage={"session_id": "hermes-file-1"},
            )

        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            _init_temp_db(settings)
            original_prepare_files = gateway._prepare_file_attachments
            original_first = gateway.run_first_turn
            gateway._prepare_file_attachments = fake_prepare_files
            gateway.run_first_turn = fake_first_turn
            try:
                result = asyncio.run(
                    gateway._run_chat_turn(
                        settings,
                        user_id="alice",
                        session_id="demo-session",
                        request=_chat_request(
                            "请分析这个文件",
                            session_id="demo-session",
                            user_id="alice",
                            profile="default",
                            attachments=[
                                gateway.ChatAttachment(url="https://example.com/a.txt", type="file"),
                            ],
                        ),
                    )
                )
            finally:
                gateway._prepare_file_attachments = original_prepare_files
                gateway.run_first_turn = original_first

        self.assertEqual(result["answer"], "file answer")
        self.assertNotIn("attachments", result)
        self.assertNotIn("assistant_message", result)
        self.assertNotIn("conversation", result)
        self.assertNotIn("usage", result)
        self.assertIn("@file:", seen["prompt"])
        self.assertIn("a.txt", seen["prompt"])
        self.assertEqual(Path(seen["attachment_root"]).name, "demo-session")

    def test_file_attachment_can_run_without_question_text(self):
        seen: dict[str, object] = {}

        class FakeDownloadedFile:
            def __init__(self, local_path: Path):
                self.local_path = local_path

        def fake_prepare_files(settings, attachments, target_dir):
            file_path = target_dir / "a.txt"
            file_path.write_text("hello file", encoding="utf-8")
            return [FakeDownloadedFile(file_path)]

        def fake_first_turn(settings, *, prompt, profile, model=None, provider=None, image_paths=None, attachment_root=None, run_key=None, run_generation=None):
            seen["prompt"] = prompt
            seen["attachment_root"] = attachment_root
            return HermesRunResult(
                answer="file answer",
                session_id="hermes-file-empty-text",
                stdout="file answer",
                stderr="",
                returncode=0,
                usage={"session_id": "hermes-file-empty-text"},
            )

        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            _init_temp_db(settings)
            original_prepare_files = gateway._prepare_file_attachments
            original_first = gateway.run_first_turn
            gateway._prepare_file_attachments = fake_prepare_files
            gateway.run_first_turn = fake_first_turn
            try:
                result = asyncio.run(
                    gateway._run_chat_turn(
                        settings,
                        user_id="alice",
                        session_id="empty-text-file-session",
                        request=gateway.ChatRequest(
                            question=gateway.ChatQuestion(
                                attachments=[
                                    gateway.ChatAttachment(url="https://example.com/a.txt", type="file"),
                                ],
                            ),
                            session_id="empty-text-file-session",
                            user_id="alice",
                            profile="default",
                        ),
                    )
                )
            finally:
                gateway._prepare_file_attachments = original_prepare_files
                gateway.run_first_turn = original_first

        self.assertEqual(result["answer"], "file answer")
        self.assertTrue(seen["prompt"].startswith("附件文件"))
        self.assertIn("@file:", seen["prompt"])
        self.assertEqual(Path(seen["attachment_root"]).name, "empty-text-file-session")

    def test_image_attachments_are_passed_to_hermes(self):
        seen: dict[str, object] = {}

        class FakeDownloadedImage:
            def __init__(self, local_path: Path):
                self.local_path = local_path

            def public_payload(self):
                return {
                    "url": "https://example.com/image.png",
                    "type": "image",
                    "mime_type": "image/png",
                    "size_bytes": 8,
                    "sha256": "abc",
                }

        def fake_prepare(settings, attachments, target_dir):
            image_path1 = target_dir / "image-1.png"
            image_path2 = target_dir / "image-2.png"
            image_path1.write_bytes(b"\x89PNG\r\n\x1a\n")
            image_path2.write_bytes(b"\x89PNG\r\n\x1a\n")
            return [FakeDownloadedImage(image_path1), FakeDownloadedImage(image_path2)]

        def fake_first_turn(settings, *, prompt, profile, model=None, provider=None, image_paths=None, attachment_root=None, run_key=None, run_generation=None):
            seen["prompt"] = prompt
            seen["image_paths"] = image_paths
            seen["attachment_root"] = attachment_root
            return HermesRunResult(
                answer="image answer",
                session_id="hermes-image-1",
                stdout="image answer",
                stderr="",
                returncode=0,
                usage={"session_id": "hermes-image-1"},
            )

        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            _init_temp_db(settings)
            original_prepare = gateway._prepare_image_attachments
            original_first = gateway.run_first_turn
            gateway._prepare_image_attachments = fake_prepare
            gateway.run_first_turn = fake_first_turn
            try:
                result = asyncio.run(
                    gateway._run_chat_turn(
                        settings,
                        user_id="alice",
                        session_id="demo-session",
                        request=_chat_request(
                            "请分析这张图",
                            session_id="demo-session",
                            user_id="alice",
                            profile="default",
                            attachments=[
                                gateway.ChatAttachment(
                                    url="https://example.com/image-1.png",
                                    type="image",
                                ),
                                gateway.ChatAttachment(
                                    url="https://example.com/image-2.png",
                                    type="image",
                                ),
                            ],
                        ),
                    )
                )
            finally:
                gateway._prepare_image_attachments = original_prepare
                gateway.run_first_turn = original_first

        self.assertEqual(result["answer"], "image answer")
        self.assertNotIn("attachments", result)
        image_paths = seen["image_paths"]
        self.assertEqual(len(image_paths), 2)
        self.assertEqual([Path(path).name for path in image_paths], ["image-1.png", "image-2.png"])
        self.assertIn("本轮请求包含 2 张图片附件", seen["prompt"])
        self.assertNotIn("@file:", seen["prompt"])
        self.assertNotIn("image-1.png", seen["prompt"])
        self.assertNotIn("image-2.png", seen["prompt"])
        self.assertEqual(Path(seen["attachment_root"]).name, "demo-session")

    def test_hermes_client_repeats_image_flags(self):
        class FakeProcess:
            returncode = 0

            def communicate(self, timeout=None):
                return "answer\n", ""

        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            img1 = Path(tmp) / "a.png"
            img2 = Path(tmp) / "b.png"
            img1.write_bytes(b"\x89PNG\r\n\x1a\n")
            img2.write_bytes(b"\x89PNG\r\n\x1a\n")

            with mock.patch.object(gateway_hermes_client.subprocess, "Popen", return_value=FakeProcess()) as popen_mock:
                with mock.patch.object(gateway_hermes_client, "_read_usage_file", return_value={"session_id": "hermes-1"}):
                    result = gateway_hermes_client.run_first_turn(
                        settings,
                        prompt="compare",
                        profile="default",
                        image_paths=[img1, img2],
                    )

        self.assertEqual(result.session_id, "hermes-1")
        cmd = popen_mock.call_args.args[0]
        self.assertEqual(cmd.count("--image"), 2)
        self.assertIn(str(img1), cmd)
        self.assertIn(str(img2), cmd)

    def test_hermes_client_passes_attachment_root(self):
        class FakeProcess:
            returncode = 0

            def communicate(self, timeout=None):
                return "answer\n", ""

        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            root = Path(tmp) / "attachments" / "demo-session"
            root.mkdir(parents=True)

            with mock.patch.object(gateway_hermes_client.subprocess, "Popen", return_value=FakeProcess()) as popen_mock:
                with mock.patch.object(gateway_hermes_client, "_read_usage_file", return_value={"session_id": "hermes-1"}):
                    result = gateway_hermes_client.run_first_turn(
                        settings,
                        prompt=f"read @file:{root / 'a.txt'}",
                        profile="default",
                        attachment_root=root,
                    )

        self.assertEqual(result.session_id, "hermes-1")
        cmd = popen_mock.call_args.args[0]
        self.assertIn("chat", cmd)
        self.assertIn("--attachment-root", cmd)
        self.assertEqual(cmd[cmd.index("--attachment-root") + 1], str(root))

    def test_hermes_client_cancels_registered_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "fake-hermes.sh"
            started = root / "started"
            script.write_text(
                "#!/bin/sh\n"
                "echo started > \"$HERMES_CANCEL_STARTED\"\n"
                "sleep 30\n"
                "echo done\n",
                encoding="utf-8",
            )
            script.chmod(0o755)
            settings = replace(
                _make_settings(root),
                hermes_bin=str(script),
                hermes_workdir=root,
                timeout_seconds=30,
            )
            run_key = f"cancel-test:{time.time_ns()}"
            generation = begin_session_turn(run_key)
            original_started = os.environ.get("HERMES_CANCEL_STARTED")
            os.environ["HERMES_CANCEL_STARTED"] = str(started)
            error: list[BaseException] = []

            def run_command():
                try:
                    gateway_hermes_client.run_first_turn(
                        settings,
                        prompt="slow",
                        profile="default",
                        run_key=run_key,
                        run_generation=generation,
                    )
                except BaseException as exc:
                    error.append(exc)

            thread = threading.Thread(target=run_command)
            try:
                thread.start()
                deadline = time.time() + 2
                while time.time() < deadline and not started.exists():
                    time.sleep(0.01)
                self.assertTrue(started.exists())
                begin_session_turn(run_key)
                thread.join(timeout=3)
            finally:
                if original_started is None:
                    os.environ.pop("HERMES_CANCEL_STARTED", None)
                else:
                    os.environ["HERMES_CANCEL_STARTED"] = original_started

        self.assertFalse(thread.is_alive())
        self.assertTrue(error)
        self.assertIsInstance(error[0], HermesInvocationCancelled)

    def test_attachment_session_dir_keeps_recent_three_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            root = settings.attachment_storage_root
            old_dir = root / "2020-01-01" / "old-session"
            old_dir.mkdir(parents=True)
            (old_dir / "old.png").write_bytes(b"old")

            target = attachment_session_dir(settings, "demo-session")

            self.assertTrue(target.exists())
            self.assertEqual(target.parent.name, date.today().isoformat())
            self.assertEqual(target.name, "demo-session")
            self.assertFalse((root / "2020-01-01").exists())

    def test_session_messages_can_filter_with_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            _init_temp_db(settings)
            gateway_db.create_conversation(
                settings,
                external_session_id="sess-a",
                user_id="alice",
                hermes_profile="default",
                title="Alpha chat",
            )
            gateway_db.append_message(
                settings,
                external_session_id="sess-a",
                user_id="alice",
                role="user",
                kind="chat",
                content="hello world",
                title="Alpha chat",
                model="gpt-5.5",
                provider="sub2api",
            )
            gateway_db.append_message(
                settings,
                external_session_id="sess-a",
                user_id="alice",
                role="assistant",
                kind="chat",
                content="another answer",
                title="Alpha chat",
            )
            request = SimpleNamespace(
                headers={},
                app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
            )

            all_messages = gateway.session_messages(request, "sess-a", user_id="alice")
            filtered = gateway.session_messages(request, "sess-a", user_id="alice", q="hello")

        self.assertEqual(len(all_messages["messages"]), 2)
        self.assertEqual(len(filtered["messages"]), 1)
        self.assertEqual(filtered["messages"][0]["content"], "hello world")
        self.assertEqual(filtered["messages"][0]["model"], "gpt-5.5")
        self.assertEqual(filtered["messages"][0]["provider"], "sub2api")

    def test_admin_db_queries_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            _init_temp_db(settings)
            gateway_db.upsert_user(settings, "alice", "default")
            gateway_db.upsert_user(settings, "bob", "review")
            gateway_db.create_conversation(
                settings,
                external_session_id="sess-a",
                user_id="alice",
                hermes_profile="default",
                title="Alpha chat",
            )
            gateway_db.create_conversation(
                settings,
                external_session_id="sess-b",
                user_id="bob",
                hermes_profile="review",
                title="Beta chat",
            )
            gateway_db.append_message(
                settings,
                external_session_id="sess-a",
                user_id="alice",
                role="user",
                kind="chat",
                content="hello world",
                title="Alpha chat",
            )

            self.assertEqual(gateway_db.count_users(settings), 2)
            self.assertEqual(gateway_db.count_conversations(settings), 2)
            self.assertEqual(gateway_db.count_messages(settings), 1)

            users = gateway_db.list_users(settings)
            self.assertEqual([row["user_id"] for row in users], ["bob", "alice"])

            conversations = gateway_db.list_all_conversations(settings, query="Alpha")
            self.assertEqual(len(conversations), 1)
            self.assertEqual(conversations[0]["external_session_id"], "sess-a")
            detail = gateway_db.get_conversation_by_session(settings, "sess-a")
            empty_detail = gateway_db.get_conversation_by_session(settings, "sess-b")
            self.assertEqual(detail["message_count"], 1)
            self.assertEqual(empty_detail["message_count"], 0)

            results = gateway_db.search_messages_admin(settings, query="hello")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["external_session_id"], "sess-a")

    def test_request_runtime_audit_is_persisted_for_admin_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            _init_temp_db(settings)
            gateway_db.create_conversation(
                settings,
                external_session_id="sess-audit",
                user_id="alice",
                hermes_profile="default",
                title="Audit chat",
            )
            event = gateway_db.create_request_event(
                settings,
                external_session_id="sess-audit",
                user_id="alice",
                prompt="inspect image",
                model="gpt-5.6-terra",
                provider="custom",
            )
            audit = {
                "version": 1,
                "main": {"provider": "custom", "model": "qwen3.7-plus"},
                "events": [
                    {"kind": "main_fallback", "task": "main", "model": "qwen3.7-plus"},
                    {"kind": "image_input", "task": "image", "input_mode": "native"},
                    {"kind": "auxiliary", "task": "vision", "model": "qwen3.7-plus"},
                ],
            }
            gateway_db.finish_request_event(
                settings,
                event_id=event["id"],
                status="completed",
                http_status=200,
                runtime_audit=audit,
            )

            stored = gateway_db.list_request_events_by_session(settings, "sess-audit")
            payload = gateway._request_event_payload(stored[0])

        self.assertEqual(payload["runtime_audit"], audit)

    def test_admin_login_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            _init_temp_db(settings)
            gateway._validate_admin_login(settings, "admin", "secret")
            with self.assertRaises(HTTPException):
                gateway._validate_admin_login(settings, "admin", "bad")

            session = gateway_db.create_admin_session(
                settings,
                username="admin",
                ttl_seconds=3600,
            )
            request = SimpleNamespace(
                cookies={settings.admin_cookie_name: session["session_token"]},
                app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
            )
            resolved = gateway._admin_session_from_request(request, settings)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved["username"], "admin")

    def test_admin_pages_render(self):
        login = render_login_page(username_default="admin", password_configured=True)
        dash = render_dashboard_page(username="admin")
        chat = render_chat_page(
            username="admin",
            csrf_token="csrf",
            public_base_path="/tools2/hermes-gateway",
        )
        self.assertIn("Hermes 管理登录", login)
        self.assertIn("admin", login)
        self.assertIn("Hermes 管理面板", dash)
        self.assertIn("admin", dash)
        self.assertNotIn('id="loginLabel"', dash)
        self.assertNotIn("当前管理员", dash)
        self.assertNotIn('id="currentAdmin"', dash)
        self.assertIn("<tr><th>会话</th><th>状态</th><th>消息</th></tr>", dash)
        self.assertNotIn("<tr><th>会话</th><th>用户</th><th>状态</th><th>消息</th></tr>", dash)
        self.assertIn('class="sessions-table"', dash)
        self.assertIn("table-layout: fixed", dash)
        self.assertIn("userFilterText", dash)
        self.assertNotIn("els.userFilter.value = state.selectedUser", dash)
        self.assertIn("grid-template-columns: minmax(0, .72fr) minmax(0, 1fr) minmax(0, 1.9fr);", dash)
        self.assertIn('"publicBasePath": "/tools2/hermes-gateway"', chat)
        self.assertIn("adminPath('/api/chat')", chat)
        self.assertIn("'/' + 'admin' + suffix", chat)
        self.assertIn("const requestKeys = new Set", dash)
        self.assertIn("const visibleItems = items.filter", dash)
        self.assertIn("row.event_type === 'request' ? '用户请求' : esc(row.role)", dash)
        self.assertNotIn('<strong>用户请求</strong>\\n${{esc(row.prompt)}}', dash)
        self.assertIn("展开原始 Body", dash)
        self.assertIn("row.request_body ?", dash)
        self.assertIn("展开阶段耗时", dash)
        self.assertIn("formatDuration(stage.duration_ms)", dash)
        self.assertIn("实际主模型", dash)
        self.assertIn("展开模型执行详情", dash)
        self.assertIn("csrf", chat)

    def test_env_file_loader_keeps_existing_values(self):
        original = os.environ.get("ADMIN_PASSWORD")
        try:
            os.environ.pop("ADMIN_PASSWORD", None)
            with tempfile.TemporaryDirectory() as tmp:
                env_path = Path(tmp) / ".env"
                env_path.write_text("ADMIN_PASSWORD=from_file\nADMIN_USERNAME=admin\n", encoding="utf-8")
                loaded = load_env_file(env_path)
                self.assertEqual(loaded, 2)
                self.assertEqual(os.environ["ADMIN_PASSWORD"], "from_file")

                os.environ["ADMIN_PASSWORD"] = "from_env"
                loaded_again = load_env_file(env_path)
                self.assertEqual(loaded_again, 0)
                self.assertEqual(os.environ["ADMIN_PASSWORD"], "from_env")
        finally:
            if original is None:
                os.environ.pop("ADMIN_PASSWORD", None)
            else:
                os.environ["ADMIN_PASSWORD"] = original


if __name__ == "__main__":
    unittest.main()
