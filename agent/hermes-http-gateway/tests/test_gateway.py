from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from hermes_http_gateway.admin_ui import render_dashboard_page, render_login_page
from hermes_http_gateway import main as gateway
from hermes_http_gateway.config import Settings, load_env_file
from hermes_http_gateway import db as gateway_db
from hermes_http_gateway.hermes_client import HermesRunResult


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
        timeout_seconds=300,
        source_tag="tool",
        default_model=None,
        default_provider=None,
    )


def _init_temp_db(settings: Settings) -> None:
    gateway_db._initialized = False
    gateway.init_db(settings)


class GatewayTests(unittest.TestCase):
    def test_search_route_is_not_shadowed(self):
        paths = [getattr(route, "path", "") for route in gateway.app.routes]
        self.assertIn("/v1/chat/{session_id}", paths)
        self.assertNotIn("/v1/sessions/{session_id}", paths)
        self.assertNotIn("/v1/chat/{session_id}/messages", paths)
        self.assertLess(paths.index("/v1/sessions/search"), paths.index("/v1/chat/{session_id}"))

    def test_default_provider_requires_default_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = replace(_make_settings(Path(tmp)), default_provider="sub2api")
            _init_temp_db(settings)
            body = gateway.ChatRequest(question="hi")
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(gateway._run_chat_turn(settings, user_id="alice", session_id=None, request=body))
            self.assertEqual(ctx.exception.status_code, 400)

    def test_user_id_defaults_to_guest(self):
        self.assertEqual(gateway._resolve_user_id(None), "guest")
        self.assertEqual(gateway._resolve_user_id(""), "guest")
        self.assertEqual(gateway._resolve_user_id("  "), "guest")
        self.assertEqual(gateway._resolve_user_id(" alice "), "alice")

    def test_chat_endpoint_passes_user_id_from_body(self):
        seen: list[tuple[str | None, str | None]] = []

        async def fake_run_chat_turn(settings, *, user_id, session_id, request):
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
                first = asyncio.run(gateway.chat(request, gateway.ChatRequest(question="hi", user_id="alice")))
                second = asyncio.run(gateway.chat(request, gateway.ChatRequest(question="hi")))
            finally:
                gateway._run_chat_turn = original_run

        self.assertEqual(first["user_id"], "alice")
        self.assertIsNone(second["user_id"])
        self.assertEqual(seen, [("alice", None), (None, None)])

    def test_chat_resume_can_infer_user_and_profile_from_session_id(self):
        calls: list[tuple[str, str]] = []

        def fake_first_turn(settings, *, prompt, profile, model=None, provider=None):
            calls.append(("first", profile))
            return HermesRunResult(
                answer="first answer",
                session_id="hermes-1",
                stdout="first answer",
                stderr="",
                returncode=0,
                usage={"session_id": "hermes-1"},
            )

        def fake_resume_turn(settings, *, prompt, hermes_session_id, profile, model=None, provider=None):
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
                        request=gateway.ChatRequest(
                            question="如何分析音画不同步问题",
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
                        request=gateway.ChatRequest(question="继续分析", session_id=first["session_id"]),
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
        def fake_first_turn(settings, *, prompt, profile, model=None, provider=None):
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
                        request=gateway.ChatRequest(
                            question="hello",
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
                            request=gateway.ChatRequest(
                                question="again",
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
                            request=gateway.ChatRequest(
                                question="again",
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

        def fake_first_turn(settings, *, prompt, profile, model=None, provider=None):
            calls.append(("first", prompt))
            return HermesRunResult(
                answer="first answer",
                session_id="hermes-1",
                stdout="first answer",
                stderr="",
                returncode=0,
                usage={"session_id": "hermes-1"},
            )

        def fake_resume_turn(settings, *, prompt, hermes_session_id, profile, model=None, provider=None):
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
                        request=gateway.ChatRequest(question="hello"),
                    )
                )
                self.assertEqual(first["hermes_session_id"], "hermes-1")

                second = asyncio.run(
                    gateway._run_chat_turn(
                        settings,
                        user_id="alice",
                        session_id=first["session_id"],
                        request=gateway.ChatRequest(question="again", session_id=first["session_id"]),
                    )
                )
                self.assertEqual(second["answer"], "second answer")
            finally:
                gateway.run_first_turn = original_first
                gateway.run_resume_turn = original_resume

        self.assertEqual(calls, [("first", "hello"), ("resume", "again")])

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

            results = gateway_db.search_messages_admin(settings, query="hello")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["external_session_id"], "sess-a")

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
        self.assertIn("Hermes 管理登录", login)
        self.assertIn("admin", login)
        self.assertIn("Hermes 管理面板", dash)
        self.assertIn("admin", dash)
        self.assertIn("userFilterText", dash)
        self.assertNotIn("els.userFilter.value = state.selectedUser", dash)

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
