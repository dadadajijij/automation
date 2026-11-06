import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

from skill_manager import app as manager


class SkillNamesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.patch = patch.multiple(manager, ROOT=self.root, SKILLS_ROOT=self.root / "skills",
                                    ARCHIVES_ROOT=self.root / "archives", DB_PATH=self.root / "skill-manager.db")
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.import_skill()

    def import_skill(self):
        return manager.install_skill_packages(
            [{"name": "alpha", "files": {"SKILL.md": b"# Alpha"}}], {"id": "test"}, "Test")

    def mapping(self):
        return json.loads(manager.skill_names_path().read_text(encoding="utf-8"))

    def request(self, path, payload=None, method="GET", user=None):
        async def identity(request):
            return user

        async def run():
            with patch.object(manager, "current_user", identity):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.app), base_url="http://test") as client:
                    return await client.request(method, path, json=payload)
        return asyncio.run(run())

    def rename(self, value, user=None):
        return self.request("/api/skills/alpha/name", {"name_zh": value}, "PATCH", user)

    def test_names_survive_content_updates_moves_and_category_deletion(self):
        self.assertEqual(self.rename("  中文名称  ").status_code, 200)
        before = self.mapping()
        self.import_skill()
        admin = {"id": manager.SKILL_MANAGER_ADMIN_ID}
        self.assertEqual(self.request("/api/categories", {"slug": "education"}, "POST", admin).status_code, 201)
        self.assertEqual(self.request("/api/skills/alpha/category", {"category": "education"}, "PATCH").status_code, 200)
        self.assertEqual(self.request("/api/categories/education", method="DELETE", user=admin).status_code, 200)
        self.assertEqual(before, {"alpha": "中文名称"})
        self.assertEqual(self.mapping(), before)
        manager.startup()
        self.assertEqual(self.mapping(), before)

    def test_delete_cleans_mapping_and_reimport_uses_id(self):
        self.rename("中文名称")
        self.assertEqual(self.request("/api/skills/alpha", method="DELETE").status_code, 200)
        self.assertEqual(self.mapping(), {})
        self.import_skill()
        self.assertEqual(self.mapping(), {"alpha": "alpha"})

    def test_blank_and_overlong_name_rejected_without_mutation(self):
        before = self.mapping()
        for value in ("", " \t\n ", "a" * 121):
            self.assertEqual(self.rename(value).status_code, 422)
        self.assertEqual(self.mapping(), before)

    def test_atomic_replace_preserves_reader_permissions(self):
        manager.skill_names_path().chmod(0o640)
        self.assertEqual(self.rename("Readable").status_code, 200)
        self.assertEqual(manager.skill_names_path().stat().st_mode & 0o777, 0o640)

    def test_protected_name_permission_and_capability(self):
        for category in manager.PROTECTED_NAME_CATEGORIES:
            with sqlite3.connect(manager.DB_PATH) as connection:
                connection.execute("UPDATE skills SET category_slug=? WHERE name='alpha'", (category,))
            for user in (None, {"id": "ordinary@example.com"}):
                self.assertEqual(self.rename("Forbidden", user).status_code, 403)
                listing = self.request("/api/skills", user=user).json()
                self.assertFalse(listing["skills"][0]["can_rename"])
            self.assertEqual(self.rename("Allowed", {"id": manager.SKILL_MANAGER_ADMIN_ID}).status_code, 200)
        self.assertEqual(self.mapping(), {"alpha": "Allowed"})

    def test_failed_export_preserves_old_file_and_api_reports_saved_pending(self):
        before = manager.skill_names_path().read_bytes()
        with patch.object(manager.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertLogs(manager.logger, level="ERROR"):
                response = self.rename("已保存")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Skill-Names-Sync"], "pending")
        self.assertEqual(manager.skill_names_path().read_bytes(), before)
        self.assertFalse(list(self.root.glob(".skill_names-*.tmp")))
        with sqlite3.connect(manager.DB_PATH) as connection:
            self.assertEqual(connection.execute("SELECT name_zh FROM skills").fetchone()[0], "已保存")
            self.assertEqual(connection.execute("SELECT dirty FROM export_state").fetchone()[0], 1)
        response = self.request("/api/skills")
        self.assertEqual(response.headers["X-Skill-Names-Sync"], "synced")
        self.assertEqual(self.mapping(), {"alpha": "已保存"})

    def test_database_wins_and_restart_rebuilds_missing_export(self):
        self.rename("数据库名称")
        manager.skill_names_path().write_text('{"alpha":"manual override"}')
        manager.startup()
        self.assertEqual(self.mapping(), {"alpha": "数据库名称"})
        manager.skill_names_path().unlink()
        manager.startup()
        self.assertEqual(self.mapping(), {"alpha": "数据库名称"})

    def test_legacy_blank_names_only_accept_string_mapping(self):
        for value, expected in (("旧名称", "旧名称"), (None, "alpha"), (["bad"], "alpha")):
            with sqlite3.connect(manager.DB_PATH) as connection:
                connection.execute("UPDATE skills SET name_zh='' WHERE name='alpha'")
            manager.skill_names_path().write_text(json.dumps({"alpha": value}))
            manager.startup()
            self.assertEqual(self.mapping(), {"alpha": expected})

    def test_concurrent_process_exports_match_latest_database(self):
        manager.install_skill_packages([
            {"name": f"skill-{i}", "files": {"SKILL.md": b"# Test"}} for i in range(4)
        ], {"id": "test"}, "Batch")
        code = """
import sqlite3, sys
from skill_manager import app
for version in range(5):
    with sqlite3.connect(app.DB_PATH, timeout=15) as db:
        db.execute('UPDATE skills SET name_zh=? WHERE name=?', (sys.argv[1] + str(version), sys.argv[1]))
    if not app.write_skill_names_file():
        raise SystemExit(1)
"""
        env = {**os.environ, "SKILL_MANAGER_ROOT": str(self.root)}
        processes = [subprocess.Popen([sys.executable, "-c", code, f"skill-{i}"], env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(4)]
        try:
            for process in processes:
                stdout, stderr = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, stdout + stderr)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        with sqlite3.connect(manager.DB_PATH) as connection:
            expected = dict(connection.execute("SELECT name,name_zh FROM skills"))
        self.assertEqual(self.mapping(), expected)
        for i in range(4):
            self.assertEqual(expected[f"skill-{i}"], f"skill-{i}4")
        self.assertFalse(list(self.root.glob(".skill_names-*.tmp")))

    def test_background_retry_rebuilds_pending_export(self):
        async def run():
            original_sleep = asyncio.sleep
            with sqlite3.connect(manager.DB_PATH) as connection:
                connection.execute("UPDATE skills SET name_zh='background' WHERE name='alpha'")

            async def fast_sleep(seconds):
                await original_sleep(0.01)

            with patch.object(manager.asyncio, "sleep", fast_sleep):
                await manager.start_skill_names_retry()
                try:
                    for _ in range(100):
                        await original_sleep(0.02)
                        if self.mapping()["alpha"] == "background":
                            break
                    self.assertEqual(self.mapping()["alpha"], "background")
                finally:
                    await manager.stop_skill_names_retry()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
