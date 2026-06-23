import json
import tempfile
import unittest
from pathlib import Path

import runner
from subpath.discovery import build_subpath_plan


class SubpathPlanTests(unittest.TestCase):
    def test_build_subpath_plan_for_vite_root_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "demo-vite", "devDependencies": {"vite": "^5.0.0"}}) + "\n",
                encoding="utf-8",
            )
            (repo_dir / "vite.config.ts").write_text(
                "import { defineConfig } from 'vite'\nexport default defineConfig({})\n",
                encoding="utf-8",
            )
            (repo_dir / "src").mkdir()

            plan = build_subpath_plan(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                read_text_if_exists=runner.read_text_if_exists,
                collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
                workspace_frontend_package_dirs_fn=lambda path: runner.workspace_frontend_package_dirs(path),
                vite_project_roots_fn=lambda path: runner.vite_project_roots(path),
                collect_matching_files=runner.collect_matching_files,
            )

            self.assertEqual(len(plan.projects), 1)
            self.assertEqual(plan.projects[0].framework, "vite")
            self.assertEqual(plan.default_project, "root")
            self.assertIn("code_scan", plan.projects[0].capabilities)
            self.assertIn("client_code", plan.projects[0].capabilities)
            self.assertIn("config_adapter", plan.projects[0].capabilities)
            self.assertIn("build_output_expected", plan.projects[0].capabilities)

    def test_build_subpath_plan_exposes_default_project_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "demo-next", "dependencies": {"next": "^15.1.0"}}) + "\n",
                encoding="utf-8",
            )
            (repo_dir / "next.config.ts").write_text(
                "const nextConfig = {}\nexport default nextConfig\n",
                encoding="utf-8",
            )

            plan = build_subpath_plan(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                read_text_if_exists=runner.read_text_if_exists,
                collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
                workspace_frontend_package_dirs_fn=lambda path: runner.workspace_frontend_package_dirs(path),
                vite_project_roots_fn=lambda path: runner.vite_project_roots(path),
                collect_matching_files=runner.collect_matching_files,
            )
            default_project = next(project for project in plan.projects if project.project_id == plan.default_project)

            self.assertEqual(default_project.framework, "nextjs")
            self.assertEqual(default_project.proxy_mode, "preserve_prefix")
            self.assertEqual(default_project.adapter, "nextjs")

    def test_build_subpath_plan_includes_workspace_frontend_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            frontend_src_dir = frontend_dir / "src"
            frontend_src_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "demo-workspaces", "private": True, "workspaces": ["frontend"]}) + "\n",
                encoding="utf-8",
            )
            (frontend_dir / "package.json").write_text(
                json.dumps({"name": "frontend", "private": True, "devDependencies": {"vite": "^5.0.0"}}) + "\n",
                encoding="utf-8",
            )
            (frontend_dir / "vite.config.ts").write_text(
                "import { defineConfig } from 'vite'\nexport default defineConfig({})\n",
                encoding="utf-8",
            )

            plan = build_subpath_plan(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                read_text_if_exists=runner.read_text_if_exists,
                collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
                workspace_frontend_package_dirs_fn=lambda path: runner.workspace_frontend_package_dirs(path),
                vite_project_roots_fn=lambda path: runner.vite_project_roots(path),
                collect_matching_files=runner.collect_matching_files,
                discover_workspace_packages=runner.discover_workspace_packages,
            )

            frontend_project = next(project for project in plan.projects if project.project_root == frontend_dir.resolve())
            self.assertIn("code_scan", frontend_project.capabilities)
            self.assertIn("client_code", frontend_project.capabilities)
            self.assertIn("config_adapter", frontend_project.capabilities)
            self.assertIn("build_output_expected", frontend_project.capabilities)

    def test_build_subpath_plan_prefers_declared_projects_from_subpath_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            frontend_src_dir = frontend_dir / "src"
            frontend_dist_dir = frontend_dir / "dist"
            frontend_src_dir.mkdir(parents=True)
            frontend_dist_dir.mkdir(parents=True)
            (repo_dir / ".ka").mkdir(parents=True)
            (repo_dir / ".ka" / "subpath.json").write_text(
                json.dumps(
                    {
                        "projects": [
                            {
                                "root": "frontend",
                                "framework": "vite",
                                "proxy_mode": "preserve_prefix",
                                "config_file": "frontend/vite.config.ts",
                                "runtime_roots": ["frontend/dist"],
                                "source_roots": ["frontend/src"],
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (frontend_dir / "vite.config.ts").write_text(
                "import { defineConfig } from 'vite'\nexport default defineConfig({})\n",
                encoding="utf-8",
            )

            plan = build_subpath_plan(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                read_text_if_exists=runner.read_text_if_exists,
                collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
                workspace_frontend_package_dirs_fn=lambda path: runner.workspace_frontend_package_dirs(path),
                vite_project_roots_fn=lambda path: runner.vite_project_roots(path),
                collect_matching_files=runner.collect_matching_files,
                discover_workspace_packages=runner.discover_workspace_packages,
            )

            self.assertEqual(len(plan.projects), 1)
            self.assertEqual(plan.projects[0].project_root, frontend_dir.resolve())
            self.assertEqual(plan.projects[0].evidence, ("declaration",))
            self.assertEqual(plan.default_project, "frontend")

    def test_build_subpath_plan_uses_declared_default_project_when_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            admin_dir = repo_dir / "admin"
            frontend_src_dir = frontend_dir / "src"
            admin_src_dir = admin_dir / "src"
            frontend_src_dir.mkdir(parents=True)
            admin_src_dir.mkdir(parents=True)
            (repo_dir / ".ka").mkdir(parents=True)
            (repo_dir / ".ka" / "subpath.json").write_text(
                json.dumps(
                    {
                        "projects": [
                            {
                                "root": "frontend",
                                "framework": "vite",
                                "proxy_mode": "preserve_prefix",
                                "source_roots": ["frontend/src"],
                            },
                            {
                                "root": "admin",
                                "framework": "vite",
                                "proxy_mode": "preserve_prefix",
                                "source_roots": ["admin/src"],
                            },
                        ],
                        "default_project": "admin",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            plan = build_subpath_plan(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                read_text_if_exists=runner.read_text_if_exists,
                collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
                workspace_frontend_package_dirs_fn=lambda path: runner.workspace_frontend_package_dirs(path),
                vite_project_roots_fn=lambda path: runner.vite_project_roots(path),
                collect_matching_files=runner.collect_matching_files,
                discover_workspace_packages=runner.discover_workspace_packages,
            )

            self.assertEqual(plan.default_project, "admin")

    def test_build_subpath_plan_reads_declared_runtime_entry_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            frontend_dist_dir = frontend_dir / "dist"
            frontend_dist_dir.mkdir(parents=True)
            (repo_dir / ".ka").mkdir(parents=True)
            (repo_dir / ".ka" / "subpath.json").write_text(
                json.dumps(
                    {
                        "projects": [
                            {
                                "root": "frontend",
                                "framework": "generic",
                                "proxy_mode": "strip_prefix",
                                "runtime_roots": ["frontend/dist"],
                                "runtime_entry_hint": "/preview/",
                                "runtime_entry_candidates": ["/preview/", "/"],
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            plan = build_subpath_plan(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                read_text_if_exists=runner.read_text_if_exists,
                collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
                workspace_frontend_package_dirs_fn=lambda path: runner.workspace_frontend_package_dirs(path),
                vite_project_roots_fn=lambda path: runner.vite_project_roots(path),
                collect_matching_files=runner.collect_matching_files,
                discover_workspace_packages=runner.discover_workspace_packages,
            )

            self.assertEqual(plan.projects[0].runtime_entry_hint, "/preview/")
            self.assertEqual(plan.projects[0].runtime_entry_candidates, ("/preview/", "/"))

    def test_build_subpath_plan_keeps_declared_source_adapter_distinct_from_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            frontend_dir.mkdir(parents=True)
            (repo_dir / ".ka").mkdir(parents=True)
            (repo_dir / ".ka" / "subpath.json").write_text(
                json.dumps(
                    {
                        "projects": [
                            {
                                "root": "frontend",
                                "framework": "vite",
                                "proxy_mode": "preserve_prefix",
                                "adapter": "vite",
                                "source_adapter": "static_rewrite",
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            plan = build_subpath_plan(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                read_text_if_exists=runner.read_text_if_exists,
                collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
                workspace_frontend_package_dirs_fn=lambda path: runner.workspace_frontend_package_dirs(path),
                vite_project_roots_fn=lambda path: runner.vite_project_roots(path),
                collect_matching_files=runner.collect_matching_files,
                discover_workspace_packages=runner.discover_workspace_packages,
            )

            self.assertEqual(plan.projects[0].adapter, "vite")
            self.assertEqual(plan.projects[0].source_adapter, "static_rewrite")


if __name__ == "__main__":
    unittest.main()
