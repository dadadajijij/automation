import json
import tempfile
import unittest
from pathlib import Path

import runner
from subpath.apply import apply_subpath_rewrites, auto_fix_subpath_issues
from subpath.models import FrontendProjectStrategy, SubpathPlan
from subpath.adapters import (
    apply_nextjs_subpath_adapter,
    apply_vite_subpath_adapter,
    auto_fix_nextjs_subpath_issues,
    ensure_cra_homepage,
    ensure_vue_cli_public_path,
)


class SubpathApplyTests(unittest.TestCase):
    def _find_targets(self, repo_dir: Path):
        return runner.subpath_find_plan_rewrite_targets(
            repo_dir,
            runner.subpath_build_subpath_plan(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                read_text_if_exists=runner.read_text_if_exists,
                collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
                workspace_frontend_package_dirs_fn=runner.workspace_frontend_package_dirs,
                vite_project_roots_fn=runner.vite_project_roots,
                collect_matching_files=runner.collect_matching_files,
                discover_workspace_packages=runner.discover_workspace_packages,
            ),
            collect_matching_files=runner.collect_matching_files,
            discover_runtime_root_frontend_files=lambda path: runner.subpath_discover_runtime_root_frontend_files(
                path,
                include_code_files=True,
                collect_matching_files=runner.collect_matching_files,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
            ),
            is_server_side_code_file=lambda file_path, repo_dir_arg: runner.subpath_is_server_side_code_file(
                file_path,
                repo_dir_arg,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
            ),
            vite_project_roots=runner.vite_project_roots,
        )

    def test_apply_subpath_rewrites_for_vite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            src_dir = repo_dir / "src" / "api"
            src_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(json.dumps({"devDependencies": {"vite": "^5.0.0"}}) + "\n", encoding="utf-8")
            (repo_dir / "vite.config.ts").write_text("import { defineConfig } from 'vite'\nexport default defineConfig({})\n", encoding="utf-8")
            (repo_dir / "index.html").write_text('<!doctype html><html><head><link rel="icon" href="/vite.svg" /></head><body><script type="module" src="/src/main.tsx"></script></body></html>\n', encoding="utf-8")
            (repo_dir / "src" / "main.tsx").write_text("console.log('demo')\n", encoding="utf-8")
            (src_dir / "jobs.ts").write_text("fetch('/api/jobs')\n", encoding="utf-8")

            changed = apply_subpath_rewrites(
                repo_dir,
                "demo-vite",
                plan=runner.subpath_build_subpath_plan(
                    repo_dir,
                    parse_package_json=runner.parse_package_json,
                    detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                    read_text_if_exists=runner.read_text_if_exists,
                    collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
                    workspace_frontend_package_dirs_fn=runner.workspace_frontend_package_dirs,
                    vite_project_roots_fn=runner.vite_project_roots,
                    collect_matching_files=runner.collect_matching_files,
                    discover_workspace_packages=runner.discover_workspace_packages,
                ),
                apply_framework_config_adapters=lambda plan, slug: runner.subpath_apply_framework_config_adapters(
                    plan,
                    slug,
                    parse_package_json=runner.parse_package_json,
                    read_text=runner.read_text,
                    write_text=runner.write_text,
                ),
                apply_nextjs_subpath_adapter_fn=lambda repo_dir_arg, slug: apply_nextjs_subpath_adapter(
                    repo_dir_arg,
                    slug,
                    ensure_nextjs_basepath_config_fn=runner.ensure_nextjs_basepath_config,
                    auto_fix_nextjs_subpath_issues_fn=runner.auto_fix_nextjs_subpath_issues,
                ),
                apply_vite_subpath_adapter_fn=lambda repo_dir_arg, slug: apply_vite_subpath_adapter(
                    repo_dir_arg,
                    slug,
                    ensure_vite_base_config_fn=runner.ensure_vite_base_config,
                    find_frontend_rewrite_targets=self._find_targets,
                    rewrite_frontend_subpath_urls=runner.rewrite_frontend_subpath_urls,
                    sorted_unique=runner.sorted_unique,
                ),
                ensure_vue_cli_public_path_fn=lambda repo_dir_arg, slug: ensure_vue_cli_public_path(
                    repo_dir_arg,
                    slug,
                    read_text=runner.read_text,
                    write_text=runner.write_text,
                ),
                ensure_cra_homepage_fn=lambda repo_dir_arg, slug: ensure_cra_homepage(
                    repo_dir_arg,
                    slug,
                    parse_package_json=runner.parse_package_json,
                    write_text=runner.write_text,
                ),
                find_frontend_rewrite_targets=self._find_targets,
                rewrite_frontend_subpath_urls=runner.rewrite_frontend_subpath_urls,
            )

            self.assertTrue(any(item.endswith("vite.config.ts") for item in changed))
            self.assertIn("index.html", changed)
            self.assertIn("src/api/jobs.ts", changed)

    def test_auto_fix_subpath_issues_for_static_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            app_dir = repo_dir / "app"
            views_dir = repo_dir / "views"
            static_dir = repo_dir / "static"
            app_dir.mkdir(parents=True)
            views_dir.mkdir()
            static_dir.mkdir()
            (app_dir / "main.py").write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "from fastapi import FastAPI",
                        "from fastapi.staticfiles import StaticFiles",
                        "from fastapi.templating import Jinja2Templates",
                        "",
                        "BASE_DIR = Path(__file__).resolve().parent.parent",
                        "views = Jinja2Templates(directory=str(BASE_DIR / 'views'))",
                        "app = FastAPI()",
                        "app.mount('/static', StaticFiles(directory=str(BASE_DIR / 'static')), name='static')",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (views_dir / "index.html").write_text(
                '<!DOCTYPE html><html><head><link rel="stylesheet" href="/static/styles.css"></head><body><script src="/static/app.js"></script></body></html>\n',
                encoding="utf-8",
            )
            (static_dir / "app.js").write_text("fetch('/api/analyze-upload')\n", encoding="utf-8")

            changed = auto_fix_subpath_issues(
                repo_dir,
                "demo-fastapi",
                runner.subpath_build_subpath_plan(
                    repo_dir,
                    parse_package_json=runner.parse_package_json,
                    detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                    read_text_if_exists=runner.read_text_if_exists,
                    collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
                    workspace_frontend_package_dirs_fn=runner.workspace_frontend_package_dirs,
                    vite_project_roots_fn=runner.vite_project_roots,
                    collect_matching_files=runner.collect_matching_files,
                    discover_workspace_packages=runner.discover_workspace_packages,
                ),
                auto_fix_nextjs_subpath_issues_fn=lambda repo_dir_arg, slug: auto_fix_nextjs_subpath_issues(
                    repo_dir_arg,
                    slug,
                    nextjs_entry_file=runner.nextjs_entry_file,
                    collect_matching_files=runner.collect_matching_files,
                    read_text=runner.read_text,
                    read_text_if_exists=runner.read_text_if_exists,
                    write_text=runner.write_text,
                    sorted_unique=runner.sorted_unique,
                ),
                find_subpath_audit_targets=lambda repo_dir_arg, plan_arg: runner.subpath_find_subpath_audit_targets(
                    repo_dir_arg,
                    plan_arg,
                    collect_matching_files=runner.collect_matching_files,
                    detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                    vite_project_roots=runner.vite_project_roots,
                ),
                rewrite_frontend_subpath_urls=runner.rewrite_frontend_subpath_urls,
                sorted_unique=runner.sorted_unique,
            )

            self.assertIn("views/index.html", changed)
            self.assertIn("static/app.js", changed)

    def test_apply_subpath_rewrites_uses_plan_scoped_config_adapters(self) -> None:
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
            (frontend_dir / "vite.config.ts").write_text("import { defineConfig } from 'vite'\nexport default defineConfig({})\n", encoding="utf-8")
            (frontend_dir / "index.html").write_text('<script type="module" src="/src/main.tsx"></script>\n', encoding="utf-8")
            (frontend_src_dir / "main.tsx").write_text("console.log('demo')\n", encoding="utf-8")

            changed = apply_subpath_rewrites(
                repo_dir,
                "demo-workspaces",
                plan=runner.subpath_build_subpath_plan(
                    repo_dir,
                    parse_package_json=runner.parse_package_json,
                    detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                    read_text_if_exists=runner.read_text_if_exists,
                    collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
                    workspace_frontend_package_dirs_fn=runner.workspace_frontend_package_dirs,
                    vite_project_roots_fn=runner.vite_project_roots,
                    collect_matching_files=runner.collect_matching_files,
                    discover_workspace_packages=runner.discover_workspace_packages,
                ),
                apply_framework_config_adapters=lambda plan, slug: runner.subpath_apply_framework_config_adapters(
                    plan,
                    slug,
                    parse_package_json=runner.parse_package_json,
                    read_text=runner.read_text,
                    write_text=runner.write_text,
                ),
                apply_nextjs_subpath_adapter_fn=lambda repo_dir_arg, slug: apply_nextjs_subpath_adapter(
                    repo_dir_arg,
                    slug,
                    ensure_nextjs_basepath_config_fn=runner.ensure_nextjs_basepath_config,
                    auto_fix_nextjs_subpath_issues_fn=runner.auto_fix_nextjs_subpath_issues,
                ),
                apply_vite_subpath_adapter_fn=lambda repo_dir_arg, slug: apply_vite_subpath_adapter(
                    repo_dir_arg,
                    slug,
                    ensure_vite_base_config_fn=runner.ensure_vite_base_config,
                    find_frontend_rewrite_targets=self._find_targets,
                    rewrite_frontend_subpath_urls=runner.rewrite_frontend_subpath_urls,
                    sorted_unique=runner.sorted_unique,
                ),
                ensure_vue_cli_public_path_fn=lambda repo_dir_arg, slug: ensure_vue_cli_public_path(
                    repo_dir_arg,
                    slug,
                    read_text=runner.read_text,
                    write_text=runner.write_text,
                ),
                ensure_cra_homepage_fn=lambda repo_dir_arg, slug: ensure_cra_homepage(
                    repo_dir_arg,
                    slug,
                    parse_package_json=runner.parse_package_json,
                    write_text=runner.write_text,
                ),
                find_frontend_rewrite_targets=self._find_targets,
                rewrite_frontend_subpath_urls=runner.rewrite_frontend_subpath_urls,
            )

            self.assertIn("frontend/vite.config.ts", changed)

    def test_apply_subpath_rewrites_skips_projects_without_apply_capabilities(self) -> None:
        calls = []
        plan = SubpathPlan(
            projects=(
                FrontendProjectStrategy(
                    project_id="active",
                    framework="vite",
                    proxy_mode="preserve_prefix",
                    adapter="vite",
                    source_adapter="vite",
                    project_root=Path("/tmp/active"),
                    source_roots=(),
                    runtime_roots=(),
                    config_files=(),
                    capabilities=("config_adapter", "client_code"),
                    evidence=(),
                ),
                FrontendProjectStrategy(
                    project_id="inert",
                    framework="generic",
                    proxy_mode="strip_prefix",
                    adapter="static_rewrite",
                    source_adapter="static_rewrite",
                    project_root=Path("/tmp/inert"),
                    source_roots=(),
                    runtime_roots=(),
                    config_files=(),
                    capabilities=("runtime_root",),
                    evidence=(),
                ),
            ),
            default_project="active",
        )

        changed = apply_subpath_rewrites(
            Path("/tmp/repo"),
            "demo-workspaces",
            plan=plan,
            apply_framework_config_adapters=lambda plan_arg, slug: ["config.json"],
            apply_nextjs_subpath_adapter_fn=lambda repo_dir, slug: calls.append("nextjs") or [],
            apply_vite_subpath_adapter_fn=lambda repo_dir, slug: calls.append("vite") or ["vite.config.ts"],
            ensure_vue_cli_public_path_fn=lambda repo_dir, slug: calls.append("vue") or [],
            ensure_cra_homepage_fn=lambda repo_dir, slug: calls.append("cra") or [],
            find_frontend_rewrite_targets=lambda repo_dir: [],
            rewrite_frontend_subpath_urls=lambda file_path, base_path, repo_dir: False,
        )

        self.assertEqual(calls, ["vite"])
        self.assertIn("vite.config.ts", changed)

    def test_apply_subpath_rewrites_uses_source_adapter_not_adapter_for_source_phase(self) -> None:
        calls = []
        plan = SubpathPlan(
            projects=(
                FrontendProjectStrategy(
                    project_id="declared",
                    framework="vite",
                    proxy_mode="preserve_prefix",
                    adapter="vite",
                    source_adapter="static_rewrite",
                    project_root=Path("/tmp/declared"),
                    source_roots=(),
                    runtime_roots=(),
                    config_files=(),
                    capabilities=("config_adapter", "client_code"),
                    evidence=("declaration",),
                ),
            ),
            default_project="declared",
        )

        changed = apply_subpath_rewrites(
            Path("/tmp/repo"),
            "demo-workspaces",
            plan=plan,
            apply_framework_config_adapters=lambda plan_arg, slug: ["config.json"],
            apply_nextjs_subpath_adapter_fn=lambda repo_dir, slug: calls.append("nextjs") or [],
            apply_vite_subpath_adapter_fn=lambda repo_dir, slug: calls.append("vite") or ["vite.config.ts"],
            ensure_vue_cli_public_path_fn=lambda repo_dir, slug: calls.append("vue") or [],
            ensure_cra_homepage_fn=lambda repo_dir, slug: calls.append("cra") or [],
            find_frontend_rewrite_targets=lambda repo_dir: [],
            rewrite_frontend_subpath_urls=lambda file_path, base_path, repo_dir: False,
        )

        self.assertEqual(calls, [])
        self.assertIn("config.json", changed)


if __name__ == "__main__":
    unittest.main()
