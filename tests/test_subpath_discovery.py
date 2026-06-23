import json
import tempfile
import unittest
from pathlib import Path

import runner
from subpath.discovery import (
    build_subpath_plan,
    collect_python_frontend_hint_files,
    detect_frontend_root_hints_from_python_file,
    detect_frontend_runtime_roots,
    detect_static_root_hints_from_node_entry,
    vite_project_roots,
    workspace_frontend_package_dirs,
)


class SubpathDiscoveryTests(unittest.TestCase):
    def test_detect_frontend_runtime_roots_from_express_static_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            scripts_dir = repo_dir / "scripts"
            demo_dir = repo_dir / "Demo"
            scripts_dir.mkdir(parents=True)
            demo_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"scripts": {"dev": "node ./scripts/server.js"}}) + "\n",
                encoding="utf-8",
            )
            (scripts_dir / "server.js").write_text(
                '\n'.join([
                    'const express = require("express");',
                    'const path = require("path");',
                    'const dir = path.join(__dirname, "../Demo");',
                    'const app = express();',
                    'app.use(express.static(dir));',
                ]),
                encoding="utf-8",
            )

            roots = detect_frontend_runtime_roots(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                detect_static_root_hints_from_node_entry_fn=lambda repo, entry: detect_static_root_hints_from_node_entry(
                    repo,
                    entry,
                    read_text_if_exists=runner.read_text_if_exists,
                ),
                collect_python_frontend_hint_files_fn=lambda path: collect_python_frontend_hint_files(
                    path,
                    collect_matching_files=runner.collect_matching_files,
                ),
                detect_frontend_root_hints_from_python_file_fn=lambda repo, source: detect_frontend_root_hints_from_python_file(
                    repo,
                    source,
                    read_text_if_exists=runner.read_text_if_exists,
                ),
                workspace_frontend_package_dirs_fn=lambda path: workspace_frontend_package_dirs(
                    path,
                    parse_package_json=runner.parse_package_json,
                    discover_workspace_packages=runner.discover_workspace_packages,
                ),
                read_text_if_exists=runner.read_text_if_exists,
                collect_matching_files=runner.collect_matching_files,
            )

            self.assertIn(demo_dir.resolve(), roots)

    def test_vite_project_roots_include_workspace_frontend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            frontend_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "demo-workspaces", "private": True, "workspaces": ["frontend"]}) + "\n",
                encoding="utf-8",
            )
            (frontend_dir / "package.json").write_text(
                json.dumps({"name": "frontend", "private": True, "devDependencies": {"vite": "^5.0.0"}}) + "\n",
                encoding="utf-8",
            )

            roots = vite_project_roots(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                workspace_frontend_package_dirs_fn=lambda path: workspace_frontend_package_dirs(
                    path,
                    parse_package_json=runner.parse_package_json,
                    discover_workspace_packages=runner.discover_workspace_packages,
                ),
            )

            self.assertIn(frontend_dir.resolve(), roots)

    def test_build_subpath_plan_for_vite_fastify_static_uses_strip_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            frontend_dist_dir = frontend_dir / "dist"
            backend_src_dir = repo_dir / "backend" / "src"
            frontend_dir.mkdir(parents=True)
            frontend_dist_dir.mkdir(parents=True)
            backend_src_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "decrypt-online",
                        "private": True,
                        "workspaces": ["frontend", "backend"],
                        "scripts": {"start": "npm run start --workspace=backend"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (frontend_dir / "package.json").write_text(
                json.dumps({"name": "frontend", "private": True, "devDependencies": {"vite": "^5.0.0"}}) + "\n",
                encoding="utf-8",
            )
            (frontend_dir / "vite.config.ts").write_text("import { defineConfig } from 'vite'\nexport default defineConfig({})\n", encoding="utf-8")
            (repo_dir / "backend" / "package.json").write_text(
                json.dumps({"name": "backend", "private": True, "scripts": {"start": "tsx src/index.ts"}}) + "\n",
                encoding="utf-8",
            )
            (backend_src_dir / "index.ts").write_text(
                "\n".join(
                    [
                        "import path from 'path'",
                        "const frontendDist = path.resolve(__dirname, '../../frontend/dist')",
                        "await fastify.register(fastifyStatic, {",
                        "  root: frontendDist,",
                        "  prefix: '/'",
                        "})",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            plan = build_subpath_plan(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                read_text_if_exists=runner.read_text_if_exists,
                collect_python_frontend_hint_files_fn=lambda path: collect_python_frontend_hint_files(
                    path,
                    collect_matching_files=runner.collect_matching_files,
                ),
                workspace_frontend_package_dirs_fn=lambda path: workspace_frontend_package_dirs(
                    path,
                    parse_package_json=runner.parse_package_json,
                    discover_workspace_packages=runner.discover_workspace_packages,
                ),
                vite_project_roots_fn=lambda path: vite_project_roots(
                    path,
                    parse_package_json=runner.parse_package_json,
                    workspace_frontend_package_dirs_fn=lambda path_arg: workspace_frontend_package_dirs(
                        path_arg,
                        parse_package_json=runner.parse_package_json,
                        discover_workspace_packages=runner.discover_workspace_packages,
                    ),
                ),
                collect_matching_files=runner.collect_matching_files,
                discover_workspace_packages=runner.discover_workspace_packages,
            )
            default_project = next(project for project in plan.projects if project.project_id == plan.default_project)

            self.assertEqual(default_project.framework, "vite")
            self.assertEqual(default_project.proxy_mode, "strip_prefix")

    def test_build_subpath_plan_for_vite_fastify_static_uses_strip_prefix_even_before_dist_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            backend_src_dir = repo_dir / "backend" / "src"
            frontend_dir.mkdir(parents=True)
            backend_src_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "decrypt-online",
                        "private": True,
                        "workspaces": ["frontend", "backend"],
                        "scripts": {"start": "npm run start --workspace=backend"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (frontend_dir / "package.json").write_text(
                json.dumps({"name": "frontend", "private": True, "devDependencies": {"vite": "^5.0.0"}}) + "\n",
                encoding="utf-8",
            )
            (frontend_dir / "vite.config.ts").write_text("import { defineConfig } from 'vite'\nexport default defineConfig({})\n", encoding="utf-8")
            (repo_dir / "backend" / "package.json").write_text(
                json.dumps({"name": "backend", "private": True, "scripts": {"start": "tsx src/index.ts"}}) + "\n",
                encoding="utf-8",
            )
            (backend_src_dir / "index.ts").write_text(
                "\n".join(
                    [
                        "import path from 'path'",
                        "const frontendDist = path.resolve(__dirname, '../../frontend/dist')",
                        "await fastify.register(fastifyStatic, {",
                        "  root: frontendDist,",
                        "  prefix: '/'",
                        "})",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            plan = build_subpath_plan(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                read_text_if_exists=runner.read_text_if_exists,
                collect_python_frontend_hint_files_fn=lambda path: collect_python_frontend_hint_files(
                    path,
                    collect_matching_files=runner.collect_matching_files,
                ),
                workspace_frontend_package_dirs_fn=lambda path: workspace_frontend_package_dirs(
                    path,
                    parse_package_json=runner.parse_package_json,
                    discover_workspace_packages=runner.discover_workspace_packages,
                ),
                vite_project_roots_fn=lambda path: vite_project_roots(
                    path,
                    parse_package_json=runner.parse_package_json,
                    workspace_frontend_package_dirs_fn=lambda path_arg: workspace_frontend_package_dirs(
                        path_arg,
                        parse_package_json=runner.parse_package_json,
                        discover_workspace_packages=runner.discover_workspace_packages,
                    ),
                ),
                collect_matching_files=runner.collect_matching_files,
                discover_workspace_packages=runner.discover_workspace_packages,
            )
            default_project = next(project for project in plan.projects if project.project_id == plan.default_project)

            self.assertEqual(default_project.framework, "vite")
            self.assertEqual(default_project.proxy_mode, "strip_prefix")

    def test_build_subpath_plan_prefers_declaration_over_auto_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            frontend_dist_dir = frontend_dir / "dist"
            frontend_src_dir = frontend_dir / "src"
            frontend_dist_dir.mkdir(parents=True)
            frontend_src_dir.mkdir(parents=True)
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
                workspace_frontend_package_dirs_fn=runner.workspace_frontend_package_dirs,
                vite_project_roots_fn=runner.vite_project_roots,
                collect_matching_files=runner.collect_matching_files,
                discover_workspace_packages=runner.discover_workspace_packages,
            )
            default_project = next(project for project in plan.projects if project.project_id == plan.default_project)

            self.assertEqual(default_project.framework, "vite")
            self.assertEqual(default_project.proxy_mode, "preserve_prefix")
            self.assertEqual(default_project.adapter, "vite")

    def test_detect_frontend_runtime_roots_prefers_declared_runtime_roots(self) -> None:
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
                                "framework": "vite",
                                "proxy_mode": "preserve_prefix",
                                "runtime_roots": ["frontend/dist"],
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            roots = detect_frontend_runtime_roots(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                detect_static_root_hints_from_node_entry_fn=lambda repo, entry: detect_static_root_hints_from_node_entry(
                    repo,
                    entry,
                    read_text_if_exists=runner.read_text_if_exists,
                ),
                collect_python_frontend_hint_files_fn=lambda path: collect_python_frontend_hint_files(
                    path,
                    collect_matching_files=runner.collect_matching_files,
                ),
                detect_frontend_root_hints_from_python_file_fn=lambda repo, source: detect_frontend_root_hints_from_python_file(
                    repo,
                    source,
                    read_text_if_exists=runner.read_text_if_exists,
                ),
                workspace_frontend_package_dirs_fn=lambda path: workspace_frontend_package_dirs(
                    path,
                    parse_package_json=runner.parse_package_json,
                    discover_workspace_packages=runner.discover_workspace_packages,
                ),
                read_text_if_exists=runner.read_text_if_exists,
                collect_matching_files=runner.collect_matching_files,
            )

            self.assertEqual(roots, [frontend_dist_dir.resolve()])


if __name__ == "__main__":
    unittest.main()
