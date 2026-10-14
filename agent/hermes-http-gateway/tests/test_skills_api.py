import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from hermes_http_gateway import db, main as gateway
from tests.test_gateway import _init_temp_db, _make_settings


class SkillsApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.settings = replace(_make_settings(Path(temp.name)), gateway_api_key="test-key")
        _init_temp_db(self.settings)
        self.admin = db.create_admin_session(self.settings, username="admin", ttl_seconds=3600)
        self.catalog = [
            {"id": "enabled", "name": "Enabled", "enabled": True},
            {"id": "disabled", "name": "Disabled", "enabled": False},
        ]

    def request(self, path, method="GET", body=None, admin=False):
        async def run():
            messages = []
            headers = [(b"content-type", b"application/json")]
            if admin:
                cookie = f"{self.settings.admin_cookie_name}={self.admin['session_token']}"
                headers.append((b"cookie", cookie.encode()))
                headers.append((b"x-csrf-token", gateway._admin_csrf_token(self.admin["session_token"]).encode()))
            else:
                headers.append((b"x-gateway-key", b"test-key"))
            route, _, query = path.partition("?")
            scope = {
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": method, "scheme": "https", "path": route,
                "raw_path": route.encode(), "root_path": "", "query_string": query.encode(),
                "headers": headers, "client": ("127.0.0.1", 1), "server": ("test", 443),
            }

            async def receive():
                return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}

            async def send(message):
                messages.append(message)

            with (
                mock.patch.object(gateway.app.state, "settings", self.settings, create=True),
                mock.patch.object(gateway, "list_private_skills", return_value=self.catalog),
            ):
                await gateway.app(scope, receive, send)
            status = next(m["status"] for m in messages if m["type"] == "http.response.start")
            content = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
            return status, json.loads(content)

        return asyncio.run(run())

    def test_capabilities_has_no_scope_and_rejects_old_parameter(self):
        for path, admin in [("/v1/capabilities", False), ("/admin/v1/capabilities", True)]:
            status, payload = self.request(path, admin=admin)
            self.assertEqual(status, 200)
            self.assertEqual(payload["skills"], self.catalog)
            if not admin:
                self.assertNotIn("profile", payload)
            self.assertNotIn("scope", payload)
            self.assertEqual(self.request(path + "?scope=private", admin=admin)[0], 422)

    def test_admin_and_public_share_skills_bindings(self):
        status, saved = self.request("/v1/users/customer/skills", "PATCH", {"add": [" enabled ", "enabled"]})
        self.assertEqual(status, 200)
        self.assertEqual(saved["bound_skill_ids"], ["enabled"])
        self.assertNotIn("profile", saved)
        self.assertEqual(saved["active_skill_ids"], ["enabled"])
        status, user = self.request("/admin/v1/users/customer", admin=True)
        self.assertEqual(status, 200)
        self.assertEqual(user["skills"]["bound_skill_ids"], ["enabled"])
        self.assertNotIn("profile", user["skills"])
        self.assertNotIn("private_skills", user)
        self.assertEqual(self.request("/admin/v1/users/customer", "PATCH", {"add": [], "remove": ["enabled"]}, admin=True)[0], 200)
        self.assertEqual(self.request("/v1/users/customer/skills")[1]["bound_skill_ids"], [])

    def test_invalid_skill_keeps_previous_bindings_and_uses_neutral_error(self):
        self.request("/v1/users/customer/skills", "PATCH", {"add": ["enabled"]})
        for path, method, admin in [("/v1/users/customer/skills", "PATCH", False), ("/admin/v1/users/customer", "PATCH", True)]:
            status, payload = self.request(path, method, {"add": ["disabled"]}, admin=admin)
            self.assertEqual(status, 422)
            self.assertEqual(payload["detail"], {"invalid_skill_ids": ["disabled"]})
        self.assertEqual(self.request("/v1/users/customer/skills")[1]["bound_skill_ids"], ["enabled"])

    def test_old_private_field_and_routes_are_rejected(self):
        self.assertEqual(self.request("/admin/v1/users/customer", "PATCH", {"private_skill_ids": []}, admin=True)[0], 422)
        for method in ("GET", "PUT"):
            self.assertEqual(self.request("/v1/users/customer/private-skills", method, {"skill_ids": []})[0], 404)

    def test_empty_incremental_updates_leave_existing_bindings_untouched(self):
        self.request("/v1/users/customer/skills", "PATCH", {"add": ["enabled"]})
        before = db.list_user_private_skill_bindings(self.settings, "customer")
        for path, admin in [("/v1/users/customer/skills", False), ("/admin/v1/users/customer", True)]:
            for body in ({}, {"add": [], "remove": []}):
                self.assertEqual(self.request(path, "PATCH", body, admin=admin)[0], 200)
                self.assertEqual(db.list_user_private_skill_bindings(self.settings, "customer"), before)

    def test_removal_of_disabled_and_deleted_bindings_is_allowed(self):
        db.get_or_create_user(self.settings, "customer")
        db.replace_user_private_skill_bindings(self.settings, user_id="customer", skill_ids=["enabled", "disabled", "removed"])
        self.assertEqual(self.request("/v1/users/customer/skills", "PATCH", {"remove": ["disabled", "removed", "not-bound"]})[0], 200)
        self.assertEqual(self.request("/v1/users/customer/skills")[1]["bound_skill_ids"], ["enabled"])

    def test_conflicting_add_remove_and_old_replacement_contract_are_rejected(self):
        for path, admin in [("/v1/users/customer/skills", False), ("/admin/v1/users/customer", True)]:
            status, body = self.request(path, "PATCH", {"add": [" enabled "], "remove": ["enabled"]}, admin=admin)
            self.assertEqual(status, 422)
            self.assertEqual(body["detail"], {"conflicting_skill_ids": ["enabled"]})
            self.assertEqual(self.request(path, "PATCH", {"skill_ids": []}, admin=admin)[0], 422)
        for method in ("PUT", "DELETE"):
            self.assertEqual(self.request("/v1/users/customer/skills", method, {})[0], 405)

    def test_resulting_binding_limit_is_enforced_without_partial_changes(self):
        self.catalog = [{"id": f"skill-{i}", "enabled": True} for i in range(11)]
        self.request("/v1/users/customer/skills", "PATCH", {"add": [f"skill-{i}" for i in range(10)]})
        status, _ = self.request("/v1/users/customer/skills", "PATCH", {"add": ["skill-10"]})
        self.assertEqual(status, 422)
        self.assertEqual(len(self.request("/v1/users/customer/skills")[1]["bound_skill_ids"]), 10)
        status, payload = self.request("/v1/users/customer/skills", "PATCH", {"add": ["skill-10"], "remove": ["skill-0"]})
        self.assertEqual(status, 200)
        self.assertNotIn("skill-0", payload["bound_skill_ids"])
        self.assertIn("skill-10", payload["bound_skill_ids"])

    def test_existing_database_bindings_survive_rename(self):
        db.get_or_create_user(self.settings, "customer")
        db.replace_user_private_skill_bindings(self.settings, user_id="customer", skill_ids=["enabled", "disabled", "removed"])
        status, payload = self.request("/v1/users/customer/skills")
        self.assertEqual(status, 200)
        self.assertEqual(payload["active_skill_ids"], ["enabled"])
        self.assertEqual(payload["unavailable_skill_ids"], ["disabled", "removed"])

    def test_keyword_search_filters_messages_in_both_api_surfaces(self):
        db.get_or_create_user(self.settings, "guest")
        db.create_conversation(
            self.settings, external_session_id="search-test", user_id="guest",
            hermes_profile="default", title="Search example",
        )
        for content in ("connection timeout", "normal playback"):
            db.append_message(
                self.settings, external_session_id="search-test", user_id="guest",
                role="assistant", kind="chat", content=content, title="Search example",
            )
        for path, key, admin in [
            ("/v1/messages?user_id=guest&keyword=timeout", "messages", False),
            ("/v1/messages?user_id=guest&session_id=search-test&keyword=timeout", "messages", False),
            ("/admin/v1/search?user_id=guest&keyword=timeout", "results", True),
        ]:
            status, payload = self.request(path, admin=admin)
            self.assertEqual(status, 200)
            self.assertEqual([item["content"] for item in payload[key]], ["connection timeout"])
        status, payload = self.request("/v1/messages?session_id=search-test")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["messages"]), 2)

    def test_old_q_parameter_is_rejected_even_with_session_or_keyword(self):
        for path, admin in [
            ("/v1/messages?q=timeout", False),
            ("/v1/messages?session_id=anything&q=timeout", False),
            ("/admin/v1/search?keyword=timeout&q=timeout", True),
        ]:
            status, payload = self.request(path, admin=admin)
            self.assertEqual(status, 422)
            self.assertIn("use keyword", payload["detail"])

    def test_search_requirements_describe_keyword(self):
        status, payload = self.request("/v1/messages")
        self.assertEqual(status, 422)
        self.assertEqual(payload["detail"], "keyword is required when session_id is not provided")
        status, payload = self.request("/admin/v1/search", admin=True)
        self.assertEqual(status, 422)
        self.assertEqual(payload["detail"][0]["loc"], ["query", "keyword"])
