import json
import tempfile
import unittest
from pathlib import Path

import runner
from subpath.discovery import build_subpath_plan
from subpath.rewrite import find_plan_rewrite_targets
from subpath.static_audit import find_plan_audit_targets


class SubpathProjectTargetsTests(unittest.TestCase):
    def test_find_plan_rewrite_targets_includes_workspace_vite_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            src_dir = frontend_dir / "src"
            src_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "demo-workspaces", "private": True, "workspaces": ["frontend"]}) + "\n",
                encoding="utf-8",
            )
            (frontend_dir / "package.json").write_text(
                json.dumps({"name": "frontend", "private": True, "devDependencies": {"vite": "^5.0.0"}}) + "\n",
                encoding="utf-8",
            )
            (frontend_dir / "vite.config.ts").write_text("import { defineConfig } from 'vite'\nexport default defineConfig({})\n", encoding="utf-8")
            html_path = frontend_dir / "index.html"
            entry_path = src_dir / "main.tsx"
            html_path.write_text('<script type="module" src="/src/main.tsx"></script>\n', encoding="utf-8")
            entry_path.write_text("console.log('demo')\n", encoding="utf-8")

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

            targets = find_plan_rewrite_targets(
                repo_dir,
                plan,
                collect_matching_files=runner.collect_matching_files,
                discover_runtime_root_frontend_files=lambda path: runner.discover_runtime_root_frontend_files(path, include_code_files=True),
                is_server_side_code_file=runner.is_server_side_code_file,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertIn(html_path, targets)
            self.assertIn(entry_path, targets)

    def test_find_plan_audit_targets_includes_runtime_root_html_cluster(self) -> None:
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
            html_path = demo_dir / "index.html"
            html_path.write_text("<html></html>\n", encoding="utf-8")

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

            targets = find_plan_audit_targets(
                repo_dir,
                plan,
                collect_matching_files=runner.collect_matching_files,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertIn(html_path, targets)

    def test_find_plan_targets_skip_projects_without_relevant_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            active_dir = repo_dir / "active"
            inert_dir = repo_dir / "inert"
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "workspace-root", "private": True, "workspaces": ["active"]}) + "\n",
                encoding="utf-8",
            )
            (active_dir / "src").mkdir(parents=True)
            (active_dir / "index.html").write_text("<html></html>\n", encoding="utf-8")
            (active_dir / "src" / "main.tsx").write_text("console.log('active')\n", encoding="utf-8")
            (active_dir / "package.json").write_text(
                json.dumps({"name": "active", "private": True, "devDependencies": {"vite": "^5.0.0"}}) + "\n",
                encoding="utf-8",
            )
            (active_dir / "vite.config.ts").write_text(
                "import { defineConfig } from 'vite'\nexport default defineConfig({})\n",
                encoding="utf-8",
            )
            (inert_dir / "src").mkdir(parents=True)
            (inert_dir / "index.html").write_text("<html></html>\n", encoding="utf-8")
            (inert_dir / "src" / "main.tsx").write_text("console.log('inert')\n", encoding="utf-8")

            from subpath.models import FrontendProjectStrategy, SubpathPlan

            plan = SubpathPlan(
                projects=(
                    FrontendProjectStrategy(
                        project_id="active",
                        framework="vite",
                        proxy_mode="preserve_prefix",
                        adapter="vite",
                        project_root=active_dir.resolve(),
                        source_roots=(),
                        runtime_roots=(),
                        config_files=(),
                        capabilities=("client_code", "code_scan", "html_entry"),
                        evidence=(),
                    ),
                    FrontendProjectStrategy(
                        project_id="inert",
                        framework="generic",
                        proxy_mode="strip_prefix",
                        adapter="static_rewrite",
                        project_root=inert_dir.resolve(),
                        source_roots=(),
                        runtime_roots=(),
                        config_files=(),
                        capabilities=("runtime_root",),
                        evidence=(),
                    ),
                ),
                default_project="active",
            )

            rewrite_targets = find_plan_rewrite_targets(
                repo_dir,
                plan,
                collect_matching_files=runner.collect_matching_files,
                discover_runtime_root_frontend_files=lambda path: runner.discover_runtime_root_frontend_files(path, include_code_files=True),
                is_server_side_code_file=runner.is_server_side_code_file,
                vite_project_roots=runner.vite_project_roots,
            )
            audit_targets = find_plan_audit_targets(
                repo_dir,
                plan,
                collect_matching_files=runner.collect_matching_files,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertIn(active_dir / "src" / "main.tsx", rewrite_targets)
            self.assertIn(active_dir / "index.html", audit_targets)
            self.assertNotIn(inert_dir / "src" / "main.tsx", rewrite_targets)
            self.assertNotIn(inert_dir / "index.html", audit_targets)

    def test_find_plan_targets_include_declared_source_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            custom_src_dir = frontend_dir / "custom-src"
            custom_src_dir.mkdir(parents=True)
            (repo_dir / ".ka").mkdir(parents=True)
            (repo_dir / ".ka" / "subpath.json").write_text(
                json.dumps(
                    {
                        "projects": [
                            {
                                "root": "frontend",
                                "framework": "vite",
                                "proxy_mode": "preserve_prefix",
                                "source_roots": ["frontend/custom-src"],
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            declared_file = custom_src_dir / "widget.tsx"
            declared_file.write_text("export default function Widget() { return null }\n", encoding="utf-8")

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

            rewrite_targets = find_plan_rewrite_targets(
                repo_dir,
                plan,
                collect_matching_files=runner.collect_matching_files,
                discover_runtime_root_frontend_files=lambda path: runner.discover_runtime_root_frontend_files(path, include_code_files=True),
                is_server_side_code_file=runner.is_server_side_code_file,
                vite_project_roots=runner.vite_project_roots,
            )
            audit_targets = find_plan_audit_targets(
                repo_dir,
                plan,
                collect_matching_files=runner.collect_matching_files,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertIn(declared_file, rewrite_targets)
            self.assertIn(declared_file, audit_targets)


if __name__ == "__main__":
    unittest.main()
