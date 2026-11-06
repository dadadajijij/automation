import asyncio
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import httpx

from skill_manager import app as manager

ADMIN = {"id": "panweikui@agora.io"}
USER = {"id": "someone@example.com"}


class CategoryTreeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patches = patch.multiple(manager, ROOT=self.root, SKILLS_ROOT=self.root / "skills",
                                 ARCHIVES_ROOT=self.root / "archives", DB_PATH=self.root / "db.sqlite")
        patches.start()
        self.addCleanup(patches.stop)
        manager.startup()
        manager.install_skill_packages([
            {"name": name, "files": {"SKILL.md": b"# Test", "assets/a.txt": name.encode()}}
            for name in ("alpha", "beta")
        ], USER, "Seed")
        self.request("/api/skills/alpha/name", {"name_zh": "中文名称"}, "PATCH")

    def request(self, path, payload=None, method="GET", user=USER, **kwargs):
        async def identity(request):
            return user

        async def run():
            with patch.object(manager, "current_user", identity):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.app), base_url="http://test") as client:
                    return await client.request(method, path, json=payload, **kwargs)
        return asyncio.run(run())

    def create(self, slug, parent=None):
        return self.request("/api/categories", {"slug": slug, "name_zh": slug, "parent_slug": parent}, "POST")

    def move(self, name, category, user=USER):
        return self.request(f"/api/skills/{name}/category", {"category": category}, "PATCH", user)

    def names(self):
        return json.loads(manager.skill_names_path().read_text())

    def test_fixed_nodes_paths_and_permissions(self):
        rows = self.request("/api/categories").json()["categories"]
        self.assertEqual([c["slug"] for c in rows], list(manager.FIXED_CATEGORIES))
        for c in rows:
            self.assertFalse(c["can_delete"])
            self.assertTrue(c["can_store_skills"])
            for user in (USER, ADMIN, None):
                self.assertEqual(self.request('/api/categories/' + c['slug'], method='DELETE', user=user).status_code, 409)
            self.assertEqual(self.request('/api/categories/' + c['slug'], {"slug": "renamed"}, "PATCH", ADMIN).status_code, 409)
        for slug, segment in (("customer-private", "customer_private"), ("customer-common", "customer_common"), ("current-common", "current_common")):
            self.assertEqual(manager.category_root(slug), self.root / "skills/customer" / segment / slug)
        self.assertEqual(self.move("alpha", "customer").status_code, 200)
        self.assertTrue((self.root / "skills/customer/alpha/SKILL.md").exists())

    def test_first_child_migration_and_later_child_leaves_parent_skills(self):
        self.assertEqual(self.create("education").status_code, 201)
        self.move("alpha", "education")
        before = self.names()
        created = self.create("primary-school", "education")
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["moved"], ["alpha"])
        target = self.root / "skills/education/primary_school/primary-school/alpha"
        self.assertTrue((target / "SKILL.md").exists())
        self.move("beta", "education")
        self.assertEqual(self.create("secondary-school", "education").json()["moved"], [])
        self.assertTrue((self.root / "skills/education/beta/SKILL.md").exists())
        self.assertEqual(self.names(), before)
        rows = {c["slug"]: c for c in self.request("/api/categories").json()["categories"]}
        self.assertEqual((rows["education"]["direct_skill_count"], rows["education"]["skill_count"]), (1, 2))
        self.assertEqual(self.request('/api/skills/alpha/files/assets/a.txt').content, b'alpha')
        with zipfile.ZipFile(io.BytesIO(self.request('/api/skills/alpha/download').content)) as archive:
            self.assertEqual(archive.read('assets/a.txt'), b'alpha')

    def test_delete_child_to_parent_then_root_to_general_preserves_names_history(self):
        self.create("education")
        self.move("alpha", "education")
        self.create("primary-school", "education")
        self.move("beta", "education")
        before = self.names()
        connection = manager.db()
        history = [tuple(row) for row in connection.execute("SELECT skill_name,version,archive_path FROM skill_versions")]
        connection.close()
        self.assertEqual(self.request('/api/categories/primary-school', method='DELETE').status_code, 200)
        self.assertTrue((self.root / 'skills/education/alpha/SKILL.md').exists())
        self.assertFalse((self.root / 'skills/education/primary_school').exists())
        self.create("new-school", "education")
        response = self.request('/api/categories/education', method='DELETE')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(response.json()['moved']), {'alpha', 'beta'})
        self.assertFalse((self.root / 'skills/education').exists())
        self.assertEqual(self.names(), before)
        connection = manager.db()
        self.assertEqual([tuple(row) for row in connection.execute("SELECT skill_name,version,archive_path FROM skill_versions")], history)
        connection.close()

    def test_parent_and_child_rename_updates_paths_and_keeps_archives(self):
        self.create('education')
        self.create('primary-school', 'education')
        self.move('alpha', 'primary-school')
        renamed = self.request('/api/categories/education', {'slug':'learning', 'name_zh':'学习'}, 'PATCH')
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(self.request('/api/categories/primary-school', {'slug':'junior-school', 'name_zh':'小学'}, 'PATCH').status_code, 200)
        self.assertTrue((self.root / 'skills/learning/junior_school/junior-school/alpha/SKILL.md').exists())
        self.assertFalse((self.root / 'skills/learning/primary_school').exists())
        self.assertEqual(self.names()['alpha'], '中文名称')
        manager.startup()
        self.assertEqual(self.request('/api/skills/alpha').status_code, 200)

    def test_depth_identity_and_directory_collisions(self):
        self.assertEqual(self.create('bad', 'missing').status_code, 404)
        self.create('education')
        self.create('primary-school', 'education')
        self.assertEqual(self.create('third', 'primary-school').status_code, 422)
        self.assertEqual(self.create('primary-school').status_code, 409)
        self.assertEqual(self.create('../escape').status_code, 400)
        self.move('alpha', 'education')
        self.assertEqual(self.create('alpha', 'education').status_code, 409)
        self.create('beta', 'education')
        self.assertEqual(self.move('beta', 'education').status_code, 409)
        self.assertEqual(self.request('/api/categories/primary-school', {'parent_slug':None}, 'PATCH').status_code, 409)

    def test_protected_groups_all_mutation_routes_and_reads(self):
        for category in ('customer-common', 'current-common'):
            self.assertEqual(self.move('alpha', category).status_code, 403)
            self.assertEqual(self.move('alpha', category, ADMIN).status_code, 200)
            for user in (USER, None):
                self.assertEqual(self.move('alpha', 'general', user).status_code, 403)
                self.assertEqual(self.request('/api/skills/alpha', {'files':{'SKILL.md':'Changed'}}, 'PUT', user).status_code, 403)
                self.assertEqual(self.request('/api/skills/alpha/name', {'name_zh':'Changed'}, 'PATCH', user).status_code, 403)
                self.assertEqual(self.request('/api/skills/alpha', method='DELETE', user=user).status_code, 403)
                detail = self.request('/api/skills/alpha', user=user)
                self.assertEqual(detail.status_code, 200)
                self.assertFalse(detail.json()['skill']['can_edit'])
                self.assertEqual(self.request('/api/skills/alpha/download', user=user).status_code, 200)
            self.assertEqual(self.request('/api/skills/alpha', {'files':{'SKILL.md':'# Admin'}}, 'PUT', ADMIN).status_code, 200)
            self.assertEqual(self.move('alpha', 'general', ADMIN).status_code, 200)

    def test_import_cannot_bypass_protection_and_rolls_back_whole_batch(self):
        self.move('alpha', 'customer-common', ADMIN)
        packages = [{"name":name,"files":{"SKILL.md":b'# Changed'}} for name in ('beta', 'alpha')]
        with self.assertRaises(manager.HTTPException) as error:
            manager.install_skill_packages(packages, USER, 'Batch')
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual((manager.skill_root('general','beta')/'SKILL.md').read_bytes(), b'# Test')
        with patch.object(manager, 'clone_git_packages', return_value=packages):
            self.assertEqual(self.request('/api/skills/import-git', {'url':'https://example.com/repo.git'}, 'POST').status_code, 403)
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w') as archive:
            for package in packages: archive.writestr(package['name']+'/SKILL.md', b'# Changed')
        response = self.request('/api/skills', method='POST', files={'file':('skills.zip',content.getvalue(),'application/zip')})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(manager.install_skill_packages(packages, USER, 'Skip', overwrite=False)['summary']['skipped'], 2)

    def test_first_child_and_delete_failure_roll_back_database_and_files(self):
        self.create('education')
        self.move('alpha', 'education')
        self.move('beta', 'education')
        rename = Path.rename
        def fail_second(path, target):
            if path.name == 'beta': raise OSError('injected failure')
            return rename(path,target)
        with patch.object(Path, 'rename', fail_second):
            with self.assertRaises(OSError): self.create('primary-school','education')
        self.assertTrue((self.root/'skills/education/alpha/SKILL.md').exists())
        self.assertFalse((self.root/'skills/education/primary_school').exists())
        self.assertNotIn('primary-school', [c['slug'] for c in self.request('/api/categories').json()['categories']])
        self.create('primary-school', 'education')
        with patch.object(Path, 'rename', fail_second):
            with self.assertRaises(OSError): self.request('/api/categories/education', method='DELETE')
        self.assertTrue((self.root/'skills/education/primary_school/primary-school/alpha/SKILL.md').exists())
        self.assertEqual(self.request('/api/skills/alpha').json()['skill']['category_slug'],'primary-school')

    def test_old_layout_migrates_once_and_preserves_mapping(self):
        self.move('alpha','customer-private')
        new = manager.category_root('customer-private')
        new.rename(self.root/'skills/customer-private')
        connection = manager.db()
        connection.execute("DELETE FROM schema_migrations")
        connection.commit(); connection.close()
        before = self.names()
        manager.startup()
        self.assertTrue((new/'alpha/SKILL.md').exists())
        self.assertFalse((self.root/'skills/customer-private').exists())
        self.assertTrue(list((self.root/'backups').glob('category-tree-*')))
        manager.startup()
        self.assertEqual(self.names(), before)

    def test_uncommitted_move_journal_recovers_on_startup(self):
        source = manager.skill_root('general','alpha')
        target = self.root/'skills/internal/alpha'
        manager.write_atomic_json(self.root/'.category-moves.json', {
            'id':'interrupted', 'created':[],
            'moves':[[str(source.relative_to(manager.SKILLS_ROOT)),str(target.relative_to(manager.SKILLS_ROOT))]]})
        source.rename(target)
        manager.startup()
        self.assertTrue((source/'SKILL.md').exists())
        self.assertFalse(target.exists())

    def test_migration_collision_refuses_to_overwrite(self):
        old = self.root/'skills/customer-private'
        old.mkdir(); (old/'unmanaged.txt').write_text('old')
        new = manager.category_root('customer-private')
        (new/'unmanaged.txt').write_text('new')
        with sqlite3.connect(manager.DB_PATH) as connection:
            connection.execute('DELETE FROM schema_migrations')
        with self.assertRaises(manager.HTTPException): manager.startup()
        self.assertEqual((old/'unmanaged.txt').read_text(),'old')
        self.assertEqual((new/'unmanaged.txt').read_text(),'new')

    def test_committed_journal_is_not_rolled_back(self):
        source = manager.skill_root('general','alpha')
        target = self.root/'skills/internal/alpha'
        source.rename(target)
        with sqlite3.connect(manager.DB_PATH) as connection:
            connection.execute("UPDATE skills SET category_slug='internal' WHERE name='alpha'")
            connection.execute("INSERT INTO storage_operations VALUES('committed')")
        manager.write_atomic_json(self.root/'.category-moves.json', {'id':'committed','created':[],
            'moves':[['general/alpha','internal/alpha']]})
        manager.startup()
        self.assertTrue((target/'SKILL.md').exists())
        self.assertFalse(source.exists())

    def test_symlink_and_unmanaged_file_do_not_get_deleted(self):
        self.create('education')
        extra = self.root/'skills/education/notes.txt'
        extra.write_text('keep me')
        self.assertEqual(self.request('/api/categories/education',method='DELETE').status_code,409)
        extra.unlink()
        outside = self.root/'outside'; outside.mkdir()
        link = self.root/'skills/education/link'; link.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.request('/api/categories/education',method='DELETE').status_code,409)
        self.assertTrue(outside.exists())

    def test_fixed_chinese_rename_is_admin_only_and_survives_restart(self):
        self.assertEqual(self.request('/api/categories/general',{'name_zh':'默认集合'},'PATCH').status_code,403)
        self.assertEqual(self.request('/api/categories/general',{'name_zh':'默认集合'},'PATCH',ADMIN).status_code,200)
        manager.startup()
        self.assertEqual(json.loads(manager.category_names_path().read_text())['general'],'默认集合')

    def test_readonly_group_batch_download_and_admin_batch_operations(self):
        self.move('alpha','customer-common',ADMIN)
        response = self.request('/api/skills/batch-download', {'names':['alpha','beta']}, 'POST')
        self.assertEqual(response.status_code,200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIn('alpha/SKILL.md',archive.namelist())
        payload={'names':['alpha','beta'],'category':'current-common'}
        self.assertEqual(self.request('/api/skills/batch-category',payload,'POST').status_code,403)
        self.assertEqual(self.request('/api/skills/batch-category',payload,'POST',ADMIN).status_code,200)
        self.assertEqual(self.request('/api/skills/batch-delete',{'names':['alpha','beta']},'POST',ADMIN).status_code,200)
        self.assertEqual(self.names(),{})


if __name__ == '__main__':
    unittest.main()
