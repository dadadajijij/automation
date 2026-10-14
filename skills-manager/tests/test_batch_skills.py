import asyncio
import io
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx

from skill_manager import app as manager


class BatchSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patches = patch.multiple(manager, ROOT=self.root, SKILLS_ROOT=self.root / "skills", ARCHIVES_ROOT=self.root / "archives", DB_PATH=self.root / "db.sqlite")
        patches.start()
        self.addCleanup(patches.stop)
        async def no_user(request):
            return None
        identity = patch.object(manager, "current_user", no_user)
        identity.start()
        self.addCleanup(identity.stop)
        manager.ensure_dirs()
        manager.install_skill_packages([
            {"name": name, "files": {"SKILL.md": b"# Test", "assets/data.bin": b"\x00\x01"}}
            for name in ("alpha", "beta", "gamma")
        ], {"id": "test"}, "Test")

    def request(self, path, payload, method="POST"):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.app), base_url="http://test") as client:
                return await client.request(method, path, json=payload)
        return asyncio.run(run())

    def test_download_preserves_resources_and_can_be_reimported(self):
        response = self.request("/api/skills/batch-download", {"names": ["alpha", "beta", "alpha"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(set(archive.namelist()), {"alpha/SKILL.md", "beta/SKILL.md", "alpha/assets/data.bin", "beta/assets/data.bin"})
            self.assertEqual(archive.read("alpha/assets/data.bin"), b"\x00\x01")
        packages = manager.discover_zip_packages(response.content, "skills.zip")
        self.assertEqual([p["name"] for p in packages], ["alpha", "beta"])

    def test_delete_only_selected_skills_and_keep_archives(self):
        response = self.request("/api/skills/batch-delete", {"names": ["alpha", "beta", "alpha"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 2)
        self.assertFalse((manager.SKILLS_ROOT / "alpha").exists())
        self.assertFalse((manager.SKILLS_ROOT / "beta").exists())
        self.assertTrue((manager.SKILLS_ROOT / "gamma/SKILL.md").exists())
        with manager.db() as connection:
            self.assertEqual([row["name"] for row in connection.execute("SELECT name FROM skills")], ["gamma"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0], 3)
        self.assertEqual(len(list(manager.ARCHIVES_ROOT.glob("*.zip"))), 3)

    def test_missing_invalid_or_empty_selection_does_not_delete(self):
        for endpoint in ("batch-delete", "batch-download"):
            for names, status in ((["alpha", "missing"], 404), (["../alpha"], 400), ([], 422)):
                response = self.request("/api/skills/" + endpoint, {"names": names})
                self.assertEqual(response.status_code, status)
        self.assertEqual(len(list(manager.SKILLS_ROOT.iterdir())), 3)

    def test_delete_rolls_back_directories_and_database_on_failure(self):
        rename = Path.rename
        def fail_second_move(path, target):
            if path == manager.SKILLS_ROOT / "beta":
                raise OSError("Simulated failure")
            return rename(path, target)
        with patch.object(Path, "rename", fail_second_move):
            with self.assertRaises(OSError):
                manager.delete_selected_skills(["alpha", "beta"])
        self.assertTrue((manager.SKILLS_ROOT / "alpha/SKILL.md").exists())
        self.assertTrue((manager.SKILLS_ROOT / "beta/SKILL.md").exists())
        with manager.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM skills").fetchone()[0], 3)

    def test_single_delete_still_works(self):
        response = self.request("/api/skills/alpha", None, "DELETE")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_download_rejects_symlinks(self):
        (manager.SKILLS_ROOT / "alpha/external").symlink_to(self.root / "db.sqlite")
        response = self.request("/api/skills/batch-download", {"names": ["alpha", "beta"]})
        self.assertEqual(response.status_code, 409)

    def test_reimport_after_deletion_uses_retained_history(self):
        old_archive = (manager.ARCHIVES_ROOT / "alpha-v1.zip").read_bytes()
        self.assertEqual(self.request("/api/skills/batch-delete", {"names": ["alpha"]}).status_code, 200)
        result = manager.install_skill_packages([
            {"name": "alpha", "files": {"SKILL.md": b"# New Alpha"}},
        ], {"id": "test"}, "Reimport")
        self.assertEqual(result["skills"][0]["version"], 2)
        self.assertEqual((manager.ARCHIVES_ROOT / "alpha-v1.zip").read_bytes(), old_archive)
        self.assertEqual(self.request("/api/skills/batch-delete", {"names": ["alpha", "beta"]}).status_code, 200)

    def test_edit_recovers_version_lower_than_history(self):
        manager.install_skill_packages([
            {"name": "alpha", "files": {"SKILL.md": b"# Second"}},
        ], {"id": "test"}, "Second")
        connection = manager.db()
        connection.execute("UPDATE skills SET version=1 WHERE name='alpha'")
        connection.commit()
        connection.close()
        response = self.request("/api/skills/alpha", {"files": {"SKILL.md": "# Third"}, "base_version": 1}, "PUT")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["skill"]["version"], 3)
        conflict = self.request("/api/skills/alpha", {"files": {"SKILL.md": "wrong"}, "base_version": 1}, "PUT")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual((manager.SKILLS_ROOT / "alpha/SKILL.md").read_bytes(), b"# Third")

    def test_archive_failure_rolls_back_entire_import_and_allows_delete(self):
        write_archive = manager.write_skill_archive
        def fail_second_archive(source, archive):
            if source.name == "beta":
                raise OSError("Simulated archive failure")
            write_archive(source, archive)
        with patch.object(manager, "write_skill_archive", fail_second_archive):
            with self.assertRaises(OSError):
                manager.install_skill_packages([
                    {"name": name, "files": {"SKILL.md": b"changed"}} for name in ("alpha", "beta")
                ], {"id": "test"}, "Fail")
        self.assertEqual((manager.SKILLS_ROOT / "alpha/SKILL.md").read_bytes(), b"# Test")
        self.assertEqual((manager.SKILLS_ROOT / "beta/SKILL.md").read_bytes(), b"# Test")
        self.assertFalse((manager.ARCHIVES_ROOT / "alpha-v2.zip").exists())
        connection = manager.db()
        try:
            self.assertEqual(connection.execute("SELECT version FROM skills WHERE name='alpha'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0], 3)
        finally:
            connection.close()
        self.assertEqual(self.request("/api/skills/batch-delete", {"names": ["alpha", "beta"]}).status_code, 200)

    def test_move_failure_restores_directory_already_backed_up(self):
        rename = Path.rename
        def fail_staged_move(path, target):
            if path.name == "alpha" and path.parent.name == "staging":
                raise OSError("Simulated move failure")
            return rename(path, target)
        with patch.object(Path, "rename", fail_staged_move):
            with self.assertRaises(OSError):
                manager.install_skill_packages([
                    {"name": "alpha", "files": {"SKILL.md": b"changed"}},
                ], {"id": "test"}, "Fail")
        self.assertEqual((manager.SKILLS_ROOT / "alpha/SKILL.md").read_bytes(), b"# Test")

    def test_busy_database_returns_retryable_error(self):
        writer = manager.db()
        connect = sqlite3.connect
        try:
            writer.execute("BEGIN IMMEDIATE")
            with patch.object(manager.sqlite3, "connect", side_effect=lambda *args, **kwargs: connect(*args, **{**kwargs, "timeout": 0.01})):
                with self.assertLogs(manager.logger, level="ERROR"):
                    response = self.request("/api/skills/batch-delete", {"names": ["alpha"]})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers["retry-after"], "2")
            self.assertIn("busy", response.json()["detail"])
        finally:
            writer.rollback()
            writer.close()
        self.assertEqual(self.request("/api/skills/batch-delete", {"names": ["alpha"]}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
