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
        async def admin_user(request):
            return {"id": "panweikui@agora.io", "email": "panweikui@agora.io", "display_name": "Admin"}
        identity = patch.object(manager, "current_user", admin_user)
        identity.start()
        self.addCleanup(identity.stop)
        manager.ensure_dirs()
        manager.install_skill_packages([
            {"name": name, "files": {"SKILL.md": b"# Test", "assets/data.bin": b"\x00\x01"}}
            for name in ("alpha", "beta", "gamma")
        ], {"id": "test"}, "Test")

    def skill_path(self, name):
        return manager.SKILLS_ROOT / "general" / name

    def archive_path(self, name, version):
        return manager.ARCHIVES_ROOT / "general" / f"{name}-v{version}.zip"

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
        self.assertFalse(self.skill_path("alpha").exists())
        self.assertFalse(self.skill_path("beta").exists())
        self.assertTrue((self.skill_path("gamma") / "SKILL.md").exists())
        with manager.db() as connection:
            self.assertEqual([row["name"] for row in connection.execute("SELECT name FROM skills")], ["gamma"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0], 3)
        self.assertEqual(len(list(manager.ARCHIVES_ROOT.rglob("*.zip"))), 3)

    def test_missing_invalid_or_empty_selection_does_not_delete(self):
        for endpoint in ("batch-delete", "batch-download"):
            for names, status in ((["alpha", "missing"], 404), (["../alpha"], 400), ([], 422)):
                response = self.request("/api/skills/" + endpoint, {"names": names})
                self.assertEqual(response.status_code, status)
        self.assertEqual(len(list((manager.SKILLS_ROOT / "general").iterdir())), 3)

    def test_delete_rolls_back_directories_and_database_on_failure(self):
        rename = Path.rename
        def fail_second_move(path, target):
            if path == self.skill_path("beta"):
                raise OSError("Simulated failure")
            return rename(path, target)
        with patch.object(Path, "rename", fail_second_move):
            with self.assertRaises(OSError):
                manager.delete_selected_skills(["alpha", "beta"])
        self.assertTrue((self.skill_path("alpha") / "SKILL.md").exists())
        self.assertTrue((self.skill_path("beta") / "SKILL.md").exists())
        with manager.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM skills").fetchone()[0], 3)

    def test_single_delete_still_works(self):
        response = self.request("/api/skills/alpha", None, "DELETE")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_download_rejects_symlinks(self):
        (self.skill_path("alpha") / "external").symlink_to(self.root / "db.sqlite")
        response = self.request("/api/skills/batch-download", {"names": ["alpha", "beta"]})
        self.assertEqual(response.status_code, 409)

    def test_import_and_edit_use_description_rules(self):
        source = b'---\nname: alpha\ndescription: Explicit description.\n---\n# Alpha\n\nBody.'
        manager.install_skill_packages([{"name": "alpha", "files": {"SKILL.md": source}}], {"id": "test"}, "Import")
        detail = self.request("/api/skills/alpha", None, "GET")
        self.assertEqual(detail.json()["skill"]["description"], "Explicit description.")
        response = self.request("/api/skills/alpha", {"base_version": 2, "files": {"SKILL.md": "# Alpha\n\nFirst paragraph.\n\nOther paragraph."}}, "PUT")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["skill"]["description"], "First paragraph.")
        listing = self.request("/api/skills", None, "GET")
        self.assertEqual(next(s for s in listing.json()["skills"] if s["name"] == "alpha")["description"], "First paragraph.")

    def test_bad_description_is_validated_before_batch_write(self):
        with self.assertRaises(manager.HTTPException) as raised:
            manager.install_skill_packages([
                {"name": "alpha", "files": {"SKILL.md": b"# Alpha\n\nChanged."}},
                {"name": "beta", "files": {"SKILL.md": b"---\ndescription: [invalid]\n---"}},
            ], {"id": "test"}, "Import")
        self.assertIn("beta", str(raised.exception.detail))
        self.assertEqual((self.skill_path("alpha") / "SKILL.md").read_bytes(), b"# Test")
        self.assertFalse(self.archive_path("alpha", 2).exists())
        response = self.request("/api/skills/alpha", {"base_version": 1, "files": {"SKILL.md": "---\ndescription: 123\n---"}}, "PUT")
        self.assertEqual(response.status_code, 400)
        self.assertEqual((self.skill_path("alpha") / "SKILL.md").read_bytes(), b"# Test")

    def test_reimport_after_deletion_uses_retained_history(self):
        old_archive = self.archive_path("alpha", 1).read_bytes()
        self.assertEqual(self.request("/api/skills/batch-delete", {"names": ["alpha"]}).status_code, 200)
        result = manager.install_skill_packages([
            {"name": "alpha", "files": {"SKILL.md": b"# New Alpha"}},
        ], {"id": "test"}, "Reimport")
        self.assertEqual(result["skills"][0]["version"], 2)
        self.assertEqual(self.archive_path("alpha", 1).read_bytes(), old_archive)
        self.assertEqual(self.request("/api/skills/batch-delete", {"names": ["alpha", "beta"]}).status_code, 200)

    def test_only_two_latest_archives_and_history_rows_are_retained(self):
        for index in range(2, 5):
            manager.install_skill_packages(
                [{"name": "alpha", "files": {"SKILL.md": f"# Version {index}".encode()}}],
                {"id": "test"},
                f"Version {index}",
            )
        with manager.db() as connection:
            versions = [row[0] for row in connection.execute("SELECT version FROM skill_versions WHERE skill_name='alpha' ORDER BY version")]
        self.assertEqual(versions, [3, 4])
        self.assertFalse(self.archive_path("alpha", 1).exists())
        self.assertFalse(self.archive_path("alpha", 2).exists())
        self.assertTrue(self.archive_path("alpha", 3).exists())
        self.assertTrue(self.archive_path("alpha", 4).exists())

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
        self.assertEqual((self.skill_path("alpha") / "SKILL.md").read_bytes(), b"# Third")

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
        self.assertEqual((self.skill_path("alpha") / "SKILL.md").read_bytes(), b"# Test")
        self.assertEqual((self.skill_path("beta") / "SKILL.md").read_bytes(), b"# Test")
        self.assertFalse(self.archive_path("alpha", 2).exists())
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
        self.assertEqual((self.skill_path("alpha") / "SKILL.md").read_bytes(), b"# Test")

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
