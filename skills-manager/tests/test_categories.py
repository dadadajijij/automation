import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from skill_manager import app as manager


class CategoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patches = patch.multiple(manager, ROOT=self.root, SKILLS_ROOT=self.root / "skills", ARCHIVES_ROOT=self.root / "archives", DB_PATH=self.root / "db.sqlite")
        patches.start()
        self.addCleanup(patches.stop)

        async def admin_user(request):
            return {"id": "panweikui@agora.io", "email": "panweikui@agora.io", "display_name": "Admin"}

        identity = patch.object(manager, "current_user", admin_user)
        identity.start()
        self.addCleanup(identity.stop)
        manager.ensure_dirs()
        manager.db().close()

    def request(self, path, payload=None, method="GET"):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.app), base_url="http://test") as client:
                return await client.request(method, path, json=payload)

        return asyncio.run(run())

    def test_new_skill_defaults_to_general(self):
        result = manager.install_skill_packages(
            [{"name": "alpha", "files": {"SKILL.md": b"# Alpha"}}],
            {"id": "test"},
            "Import",
        )
        self.assertEqual(result["skills"][0]["status"], "created")
        self.assertTrue((self.root / "skills/general/alpha/SKILL.md").is_file())
        self.assertFalse((self.root / "skills/alpha").exists())
        row = manager.db().execute("SELECT category_slug FROM skills WHERE name='alpha'").fetchone()
        self.assertEqual(row[0], "general")

    def test_skill_name_defaults_and_can_be_changed_by_regular_user(self):
        manager.install_skill_packages(
            [{"name": "lre-issue-triage", "files": {"SKILL.md": b"# Skill"}}],
            {"id": "test"},
            "Import",
        )
        created = self.request("/api/skills/lre-issue-triage", method="GET")
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["skill"]["name_zh"], "lre-issue-triage")

        async def regular_user(request):
            return {"id": "customer@example.com", "email": "customer@example.com", "display_name": "Customer"}

        with patch.object(manager, "current_user", regular_user):
            renamed = self.request("/api/skills/lre-issue-triage/name", {"name_zh": "火花问题排查"}, "PATCH")
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["skill"]["name_zh"], "火花问题排查")
        mapping = json.loads(manager.skill_names_path().read_text(encoding="utf-8"))
        self.assertEqual(mapping["lre-issue-triage"], "火花问题排查")

        listing = self.request("/api/skills", method="GET")
        self.assertEqual(next(item for item in listing.json()["skills"] if item["name"] == "lre-issue-triage")["name_zh"], "火花问题排查")

    def test_create_and_move_skill_category(self):
        manager.install_skill_packages(
            [{"name": "alpha", "files": {"SKILL.md": b"# Alpha"}}],
            {"id": "test"},
            "Import",
        )
        created = self.request("/api/categories", {"slug": "customer-private", "name_zh": "客户私有"}, "POST")
        self.assertEqual(created.status_code, 201, created.text)
        moved = self.request("/api/skills/alpha/category", {"category": "customer-private"}, "PATCH")
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual(moved.json()["skill"]["category_slug"], "customer-private")
        self.assertTrue((self.root / "skills/customer-private/alpha/SKILL.md").is_file())
        self.assertFalse((self.root / "skills/general/alpha").exists())

        detail = self.request("/api/skills/alpha")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["skill"]["category_slug"], "customer-private")

    def test_batch_move_and_category_delete_moves_skills_to_general(self):
        manager.install_skill_packages(
            [{"name": name, "files": {"SKILL.md": b"# Skill"}} for name in ("alpha", "beta")],
            {"id": "test"},
            "Import",
        )
        self.assertEqual(self.request("/api/categories", {"slug": "rtc", "display_name": "RTC"}, "POST").status_code, 201)
        moved = self.request("/api/skills/batch-category", {"names": ["alpha", "beta"], "category": "rtc"}, "POST")
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual(moved.json()["count"], 2)
        deleted = self.request("/api/categories/rtc", method="DELETE")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertFalse((self.root / "skills/rtc").exists())
        self.assertTrue((self.root / "skills/general/alpha/SKILL.md").exists())
        self.assertTrue((self.root / "skills/general/beta/SKILL.md").exists())
        self.assertEqual(self.request("/api/categories/general", method="DELETE").status_code, 409)

    def test_legacy_directory_migrates_to_general(self):
        legacy = self.root / "skills/legacy-skill"
        legacy.mkdir(parents=True)
        (legacy / "SKILL.md").write_text("# Legacy")
        manager.db().close()
        manager.migrate_legacy_skill_paths()
        self.assertFalse(legacy.exists())
        self.assertTrue((self.root / "skills/general/legacy-skill/SKILL.md").exists())

    def test_category_validation(self):
        response = self.request("/api/categories", {"slug": "../private", "display_name": "Bad"}, "POST")
        self.assertEqual(response.status_code, 400)

    def test_category_display_name_defaults_to_slug(self):
        response = self.request("/api/categories", {"slug": "internal-tools"}, "POST")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["category"]["display_name"], "internal-tools")

    def test_category_rename_updates_slug_and_paths(self):
        manager.install_skill_packages(
            [{"name": "alpha", "files": {"SKILL.md": b"# Alpha"}}],
            {"id": "test"},
            "Import",
        )
        self.assertEqual(self.request("/api/categories", {"slug": "customer-private"}, "POST").status_code, 201)
        self.assertEqual(self.request("/api/skills/alpha/category", {"category": "customer-private"}, "PATCH").status_code, 200)
        renamed = self.request("/api/categories/customer-private", {"display_name": "customer-internal"}, "PATCH")
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["category"]["slug"], "customer-internal")
        self.assertTrue((self.root / "skills/customer-internal/alpha/SKILL.md").exists())
        self.assertFalse((self.root / "skills/customer-private").exists())
        row = manager.db().execute("SELECT category_slug FROM skills WHERE name='alpha'").fetchone()
        self.assertEqual(row[0], "customer-internal")

    def test_category_json_lifecycle(self):
        created = self.request(
            "/api/categories",
            {"slug": "customer-private", "name_zh": "客户私有"},
            "POST",
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["category"]["name_zh"], "客户私有")
        self.assertEqual(
            set(created.json()["category"]),
            {"slug", "display_name", "name_zh", "description", "is_system", "skill_count"},
        )

        mapping = json.loads(manager.category_names_path().read_text(encoding="utf-8"))
        self.assertEqual(mapping["customer-private"], "客户私有")

        renamed = self.request(
            "/api/categories/customer-private",
            {"slug": "customer-internal", "name_zh": "客户内部"},
            "PATCH",
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        mapping = json.loads(manager.category_names_path().read_text(encoding="utf-8"))
        self.assertNotIn("customer-private", mapping)
        self.assertEqual(mapping["customer-internal"], "客户内部")

        deleted = self.request("/api/categories/customer-internal", method="DELETE")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        mapping = json.loads(manager.category_names_path().read_text(encoding="utf-8"))
        self.assertNotIn("customer-internal", mapping)

    def test_category_names_survive_startup(self):
        self.assertEqual(
            self.request(
                "/api/categories",
                {"slug": "customer-private", "name_zh": "客户私有A"},
                "POST",
            ).status_code,
            201,
        )
        self.assertEqual(
            self.request(
                "/api/categories/customer-private",
                {"name_zh": "客户私有B"},
                "PATCH",
            ).status_code,
            200,
        )
        manager.startup()
        row = manager.db().execute("SELECT name_zh FROM categories WHERE slug='customer-private'").fetchone()
        self.assertEqual(row[0], "客户私有B")

    def test_general_cannot_rename_slug(self):
        response = self.request("/api/categories/general", {"slug": "shared", "name_zh": "共享"}, "PATCH")
        self.assertEqual(response.status_code, 409)

    def test_non_admin_cannot_manage_categories_but_can_move_and_delete_one_skill(self):
        manager.install_skill_packages(
            [{"name": "alpha", "files": {"SKILL.md": b"# Alpha"}}],
            {"id": "test"},
            "Import",
        )
        self.request("/api/categories", {"slug": "customer-private", "name_zh": "客户私有"}, "POST")

        async def regular_user(request):
            return {"id": "customer@example.com", "email": "customer@example.com", "display_name": "Customer"}

        with patch.object(manager, "current_user", regular_user):
            self.assertEqual(self.request("/api/categories", {"slug": "blocked", "name_zh": "禁止"}, "POST").status_code, 403)
            self.assertEqual(self.request("/api/skills/batch-category", {"names": ["alpha"], "category": "customer-private"}, "POST").status_code, 403)
            self.assertEqual(self.request("/api/skills/batch-delete", {"names": ["alpha"]}, "POST").status_code, 403)
            self.assertEqual(self.request("/api/skills/alpha/category", {"category": "customer-private"}, "PATCH").status_code, 200)
            self.assertEqual(self.request("/api/skills/alpha", method="DELETE").status_code, 200)

    def test_me_reports_management_capability(self):
        admin = self.request("/api/me")
        self.assertEqual(admin.status_code, 200)
        self.assertTrue(admin.json()["can_manage"])

        async def regular_user(request):
            return {"id": "customer@example.com", "email": "customer@example.com", "display_name": "Customer"}

        with patch.object(manager, "current_user", regular_user):
            regular = self.request("/api/me")
        self.assertEqual(regular.status_code, 200)
        self.assertFalse(regular.json()["can_manage"])


if __name__ == "__main__":
    unittest.main()
