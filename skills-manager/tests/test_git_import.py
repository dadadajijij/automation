import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from skill_manager import app as manager


class GitImportTests(unittest.TestCase):
    def test_ssh_import_uses_ssh_auth_instead_of_https_credentials(self):
        url = "ssh://git@git.agoralab.co/ai/agora-skills.git"
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"username": "fake-user", "password": "fake-password"}), patch.object(manager, "ROOT", Path(tmp)), patch.object(manager, "ensure_dirs"), patch.object(manager, "validate_git_url", side_effect=lambda value: value), patch.object(manager.subprocess, "run", return_value=subprocess.CompletedProcess([], 128, "", "Permission denied (publickey).")) as run:
            with self.assertRaises(HTTPException):
                manager.clone_git_packages(url, None)
            command = run.call_args.args[0]
            self.assertEqual(command[:5], ["git", "clone", "--depth", "1", "--no-tags"])
            self.assertIn(url, command)
            self.assertFalse(list(Path(tmp).glob("skill-git-*")))

    def test_injected_credentials_work_with_git_and_are_redacted(self):
        run_git = subprocess.run
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def clone(command, **kwargs):
                self.assertEqual(command[:4], ["git", "-c", "credential.helper=", "clone"])
                self.assertNotIn("fake-password", " ".join(command))
                env = kwargs["env"]
                credentials = run_git(
                    ["git", "-c", "credential.helper=", "credential", "fill"],
                    input="protocol=https\nhost=example.com\n\n",
                    capture_output=True, text=True, env=env, timeout=10,
                )
                self.assertEqual(credentials.returncode, 0, credentials.stderr)
                self.assertIn("username=fake-user", credentials.stdout)
                self.assertIn("password=fake-password", credentials.stdout)
                return subprocess.CompletedProcess(command, 128, "", "fatal: fake-password rejected")

            with patch.dict(os.environ, {"username": "fake-user", "password": "fake-password"}), patch.object(manager, "ROOT", root), patch.object(manager, "ensure_dirs"), patch.object(manager, "parse_git_source", return_value=("https://example.com/repo.git", None)), patch.object(manager.subprocess, "run", side_effect=clone):
                with self.assertRaises(HTTPException) as error:
                    manager.clone_git_packages("https://example.com/repo.git", None)
                self.assertEqual(error.exception.status_code, 502)
                self.assertNotIn("fake-password", error.exception.detail)
                self.assertIn("[redacted]", error.exception.detail)
            self.assertFalse(list(root.glob("skill-git-*")))

    def test_incomplete_injected_credentials_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"username": "fake-user", "password": ""}), patch.object(manager, "ROOT", Path(tmp)), patch.object(manager, "ensure_dirs"), patch.object(manager, "parse_git_source", return_value=("https://example.com/repo.git", None)), patch.object(manager.subprocess, "run") as run:
            with self.assertRaises(HTTPException) as error:
                manager.clone_git_packages("https://example.com/repo.git", None)
            self.assertEqual(error.exception.status_code, 400)
            run.assert_not_called()

    def test_browse_paths_do_not_limit_repository(self):
        with patch.object(manager, "validate_git_url", side_effect=lambda url: url):
            self.assertEqual(manager.parse_git_source(
                "https://bitbucket.example/projects/AI/repos/skills/browse/skills/foo?at=refs/heads/dev", None
            ), ("https://bitbucket.example/scm/AI/skills.git", "dev"))
            self.assertEqual(manager.parse_git_source(
                "https://github.com/acme/skills/tree/main/skills/foo", "dev"
            ), ("https://github.com/acme/skills.git", "dev"))
            self.assertEqual(manager.parse_git_source("https://github.com/acme/skills.git", None)[1], None)

    def test_directory_scan_ignores_unrelated_files_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "large.bin").write_bytes(b"x" * 300)
            (root / ".git").mkdir()
            (root / ".git/SKILL.md").write_text("ignored")
            for name in ("alpha", "beta"):
                skill = root / "skills" / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text("# Test")
                (skill / "asset.bin").write_bytes(b"\x00\x01")
            with patch.object(manager, "MAX_UPLOAD_BYTES", 30), patch.object(manager, "MAX_FILES", 4):
                packages = manager.discover_directory_packages(root, "repo")
            self.assertEqual([item["name"] for item in packages], ["alpha", "beta"])
            self.assertEqual(packages[0]["files"]["asset.bin"], b"\x00\x01")

    def test_root_skill_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "SKILL.md"
            skill.write_text('---\nname: "declared-skill"\n---\n# Test')
            self.assertEqual(manager.discover_directory_packages(root, "repo-name")[0]["name"], "declared-skill")
            skill.write_text("# Test")
            self.assertEqual(manager.discover_directory_packages(root, "repo-name")[0]["name"], "repo-name")

    def test_nested_and_duplicate_skills_rejected(self):
        for files in (
            {"foo/SKILL.md": b"test", "foo/bar/SKILL.md": b"test"},
            {"a/foo/SKILL.md": b"test", "b/foo/SKILL.md": b"test"},
        ):
            with self.assertRaises(HTTPException):
                manager.discover_skill_packages(files, "repo")

    def test_skill_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SKILL.md").write_text("# Test")
            (root / "external").symlink_to(root.parent)
            with self.assertRaises(HTTPException):
                manager.discover_directory_packages(root, "repo")

    def test_real_clone_and_batch_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            for name in ("alpha", "beta"):
                skill = source / "skills" / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text("# Test")
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "Test"], check=True)
            data = root / "data"

            async def no_user(request):
                return None

            async def run():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.app), base_url="http://test") as client:
                    for expected in ("created", "updated"):
                        response = await client.post("/api/skills/import-git", json={"url": "https://example.com/source.git"})
                        self.assertEqual(response.status_code, 201, response.text)
                        self.assertEqual(response.json()["summary"][expected], 2)
                    self.assertTrue((data / "skills/alpha/SKILL.md").is_file())
                    self.assertEqual(len(list((data / "archives").glob("*.zip"))), 4)
                    self.assertFalse(list(data.glob("skill-git-*")))

            with patch.multiple(manager, ROOT=data, SKILLS_ROOT=data / "skills", ARCHIVES_ROOT=data / "archives", DB_PATH=data / "db.sqlite"), patch.object(manager, "current_user", no_user), patch.object(manager, "parse_git_source", return_value=(source.as_uri(), None)):
                asyncio.run(run())
            self.assertNotIn("path", manager.GitImport.model_fields)


if __name__ == "__main__":
    unittest.main()
