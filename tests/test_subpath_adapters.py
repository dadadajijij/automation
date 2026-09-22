import json
import tempfile
import unittest
from pathlib import Path

import runner
from subpath.adapters import (
    apply_framework_config_adapters,
    apply_nextjs_subpath_adapter,
    apply_vite_subpath_adapter,
    auto_fix_nextjs_subpath_issues,
    ensure_cra_homepage,
    ensure_nextjs_helper_module,
    ensure_vite_preview_allowed_host,
    ensure_vite_base_config,
    ensure_vue_cli_public_path,
)
from subpath.models import FrontendProjectStrategy, SubpathPlan


class SubpathAdaptersTests(unittest.TestCase):
    def test_ensure_vite_base_config_updates_first_vite_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "package.json").write_text(json.dumps({"devDependencies": {"vite": "^5.0.0"}}) + "\n", encoding="utf-8")
            config_path = repo_dir / "vite.config.ts"
            config_path.write_text("import { defineConfig } from 'vite'\nexport default defineConfig({})\n", encoding="utf-8")

            changed = ensure_vite_base_config(
                repo_dir,
                "demo-vite",
                vite_config_file=runner.vite_config_file,
                read_text=runner.read_text,
                write_text=runner.write_text,
            )

            self.assertEqual(changed, ["vite.config.ts"])
            self.assertIn('base: "/tools2/demo-vite/"', config_path.read_text(encoding="utf-8"))

    def test_ensure_vite_preview_allowed_host_adds_host_to_function_config(self) -> None:
        original = (
            "import { defineConfig } from 'vite'\n"
            "export default defineConfig(() => {\n"
            "  return {\n"
            "    base: './',\n"
            "  }\n"
            "})\n"
        )
        rewritten = ensure_vite_preview_allowed_host(original, "athena.agoralab.co")
        self.assertIn(
            'preview: { allowedHosts: ["athena.agoralab.co"] },',
            rewritten,
        )

    def test_ensure_vite_preview_allowed_host_merges_existing_array_and_is_idempotent(self) -> None:
        original = (
            "export default {\n"
            "  preview: { allowedHosts: ['localhost'] },\n"
            "}\n"
        )
        once = ensure_vite_preview_allowed_host(original, "athena.agoralab.co")
        twice = ensure_vite_preview_allowed_host(once, "athena.agoralab.co")
        self.assertIn("allowedHosts: ['localhost', \"athena.agoralab.co\"]", once)
        self.assertEqual(once, twice)

    def test_ensure_vite_preview_allowed_host_preserves_true(self) -> None:
        original = "export default { preview: { allowedHosts: true } }\n"
        self.assertEqual(
            ensure_vite_preview_allowed_host(original, "athena.agoralab.co"),
            original,
        )

    def test_ensure_vue_cli_public_path_writes_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)

            changed = ensure_vue_cli_public_path(
                repo_dir,
                "demo-vue",
                read_text=runner.read_text,
                write_text=runner.write_text,
            )

            self.assertEqual(changed, ["vue.config.js"])
            self.assertIn("publicPath: '/tools2/demo-vue/'", (repo_dir / "vue.config.js").read_text(encoding="utf-8"))

    def test_ensure_cra_homepage_updates_package_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            package_path = repo_dir / "package.json"
            package_path.write_text(json.dumps({"dependencies": {"react-scripts": "^5.0.1"}}) + "\n", encoding="utf-8")

            changed = ensure_cra_homepage(
                repo_dir,
                "demo-cra",
                parse_package_json=runner.parse_package_json,
                write_text=runner.write_text,
            )

            self.assertEqual(changed, ["package.json"])
            self.assertIn('"homepage": "/tools2/demo-cra"', package_path.read_text(encoding="utf-8"))

    def test_ensure_nextjs_helper_module_exports_pure_function(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            src_app_dir = repo_dir / "src" / "app"
            src_app_dir.mkdir(parents=True)
            entry_path = src_app_dir / "layout.tsx"
            entry_path.write_text("export default function RootLayout() { return null }\n", encoding="utf-8")

            changed = ensure_nextjs_helper_module(
                entry_path,
                repo_dir,
                "demo-next",
                read_text_if_exists=runner.read_text_if_exists,
                write_text=runner.write_text,
            )
            helper_text = (repo_dir / "ka_tool_base_runtime.ts").read_text(encoding="utf-8")

            self.assertTrue(changed)
            self.assertIn("export function withToolBase", helper_text)
            self.assertNotIn("window.withToolBase", helper_text)

    def test_apply_vite_subpath_adapter_updates_config_and_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            src_dir = repo_dir / "src" / "api"
            src_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(json.dumps({"devDependencies": {"vite": "^5.0.0"}}) + "\n", encoding="utf-8")
            (repo_dir / "vite.config.ts").write_text("import { defineConfig } from 'vite'\nexport default defineConfig({})\n", encoding="utf-8")
            (repo_dir / "index.html").write_text('<!doctype html><html><head><link rel="icon" href="/vite.svg" /></head><body><script type="module" src="/src/main.tsx"></script></body></html>\n', encoding="utf-8")
            (repo_dir / "src" / "main.tsx").write_text("console.log('demo')\n", encoding="utf-8")
            (src_dir / "jobs.ts").write_text("fetch('/api/jobs')\n", encoding="utf-8")

            changed = apply_vite_subpath_adapter(
                repo_dir,
                "demo-vite",
                ensure_vite_base_config_fn=runner.ensure_vite_base_config,
                find_frontend_rewrite_targets=runner.find_frontend_rewrite_targets,
                rewrite_frontend_subpath_urls=runner.rewrite_frontend_subpath_urls,
                sorted_unique=runner.sorted_unique,
            )

            self.assertIn("vite.config.ts", changed)
            self.assertIn("index.html", changed)
            self.assertIn("src/api/jobs.ts", changed)

    def test_apply_nextjs_subpath_adapter_updates_config_and_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            src_app_dir = repo_dir / "src" / "app"
            src_components_dir = repo_dir / "src" / "components"
            src_app_dir.mkdir(parents=True)
            src_components_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "demo-next", "dependencies": {"next": "^15.1.0", "react": "^19.0.0", "react-dom": "^19.0.0"}}) + "\n",
                encoding="utf-8",
            )
            (repo_dir / "next.config.ts").write_text(
                "import type { NextConfig } from 'next'\nconst nextConfig: NextConfig = {}\nexport default nextConfig\n",
                encoding="utf-8",
            )
            (src_app_dir / "layout.tsx").write_text(
                "export default function RootLayout({ children }: { children: React.ReactNode }) {\n"
                "  return <html><body><a href=\"/media-api\">Media</a>{children}</body></html>\n"
                "}\n",
                encoding="utf-8",
            )
            (src_components_dir / "widget.tsx").write_text(
                "'use client'\nexport async function loadData() { return fetch('/api/token') }\n",
                encoding="utf-8",
            )

            changed = apply_nextjs_subpath_adapter(
                repo_dir,
                "demo-next",
                ensure_nextjs_basepath_config_fn=runner.ensure_nextjs_basepath_config,
                auto_fix_nextjs_subpath_issues_fn=runner.auto_fix_nextjs_subpath_issues,
            )

            self.assertIn("next.config.ts", changed)
            self.assertIn("src/app/layout.tsx", changed)
            self.assertIn("src/components/widget.tsx", changed)

    def test_auto_fix_nextjs_subpath_issues_rewrites_form_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            src_app_dir = repo_dir / "src" / "app"
            src_app_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "demo-next", "dependencies": {"next": "^15.1.0", "react": "^19.0.0", "react-dom": "^19.0.0"}}) + "\n",
                encoding="utf-8",
            )
            (repo_dir / "next.config.ts").write_text(
                "import type { NextConfig } from 'next'\nconst nextConfig: NextConfig = {}\nexport default nextConfig\n",
                encoding="utf-8",
            )
            page_path = src_app_dir / "page.tsx"
            page_path.write_text(
                "'use client'\nexport default function Page() { return <form action=\"/api/upload\"></form> }\n",
                encoding="utf-8",
            )

            changed = auto_fix_nextjs_subpath_issues(
                repo_dir,
                "demo-next",
                nextjs_entry_file=runner.nextjs_entry_file,
                collect_matching_files=runner.collect_matching_files,
                read_text=runner.read_text,
                read_text_if_exists=runner.read_text_if_exists,
                write_text=runner.write_text,
                sorted_unique=runner.sorted_unique,
            )

            self.assertIn("src/app/page.tsx", changed)
            rewritten = page_path.read_text(encoding="utf-8")
            self.assertIn('action={withToolBase("/api/upload")}', rewritten)

    def test_auto_fix_nextjs_subpath_issues_rewrites_dynamic_metadata_icon(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            src_app_dir = repo_dir / "src" / "app"
            src_app_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "demo-next", "dependencies": {"next": "^15.1.0"}}) + "\n",
                encoding="utf-8",
            )
            (repo_dir / "next.config.ts").write_text(
                "const nextConfig = {}\nexport default nextConfig\n",
                encoding="utf-8",
            )
            layout_path = src_app_dir / "layout.tsx"
            layout_path.write_text(
                "\n".join(
                    [
                        'import type { Metadata } from "next";',
                        'const normalizedBasePath = process.env.NEXT_PUBLIC_APP_BASE_PATH || "";',
                        "export const metadata: Metadata = {",
                        "  icons: {",
                        '    icon: `${normalizedBasePath}/favicon.ico`,',
                        "  },",
                        "};",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            changed = auto_fix_nextjs_subpath_issues(
                repo_dir,
                "demo-next",
                nextjs_entry_file=runner.nextjs_entry_file,
                collect_matching_files=runner.collect_matching_files,
                read_text=runner.read_text,
                read_text_if_exists=runner.read_text_if_exists,
                write_text=runner.write_text,
                sorted_unique=runner.sorted_unique,
            )

            self.assertIn("src/app/layout.tsx", changed)
            self.assertIn('icon: "/tools2/demo-next/favicon.ico"', layout_path.read_text(encoding="utf-8"))

    def test_auto_fix_nextjs_subpath_issues_rewrites_request_assign_and_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            src_app_dir = repo_dir / "src" / "app"
            src_app_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "demo-next", "dependencies": {"next": "^15.1.0", "react": "^19.0.0", "react-dom": "^19.0.0"}}) + "\n",
                encoding="utf-8",
            )
            (repo_dir / "next.config.ts").write_text(
                "import type { NextConfig } from 'next'\nconst nextConfig: NextConfig = {}\nexport default nextConfig\n",
                encoding="utf-8",
            )
            page_path = src_app_dir / "page.tsx"
            page_path.write_text(
                "'use client'\n"
                "export default function Page() {\n"
                "  const req = new Request('/api/upload')\n"
                "  window.location.assign('/setup')\n"
                "  window.open('/docs')\n"
                "  return null\n"
                "}\n",
                encoding="utf-8",
            )

            changed = auto_fix_nextjs_subpath_issues(
                repo_dir,
                "demo-next",
                nextjs_entry_file=runner.nextjs_entry_file,
                collect_matching_files=runner.collect_matching_files,
                read_text=runner.read_text,
                read_text_if_exists=runner.read_text_if_exists,
                write_text=runner.write_text,
                sorted_unique=runner.sorted_unique,
            )

            self.assertIn("src/app/page.tsx", changed)
            rewritten = page_path.read_text(encoding="utf-8")
            self.assertIn('new Request(withToolBase("/api/upload"))', rewritten)
            self.assertIn('window.location.assign(withToolBase("/setup"))', rewritten)
            self.assertIn('window.open(withToolBase("/docs"))', rewritten)

    def test_auto_fix_nextjs_subpath_issues_rewrites_eventsource_and_returned_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            src_components_dir = repo_dir / "src" / "components"
            src_components_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "demo-next", "dependencies": {"next": "^15.1.0", "react": "^19.0.0", "react-dom": "^19.0.0"}}) + "\n",
                encoding="utf-8",
            )
            (repo_dir / "next.config.ts").write_text(
                "import type { NextConfig } from 'next'\nconst nextConfig: NextConfig = {}\nexport default nextConfig\n",
                encoding="utf-8",
            )
            widget_path = src_components_dir / "widget.tsx"
            widget_path.write_text(
                "'use client'\n"
                "export function connect() {\n"
                "  new EventSource('/api/jobs/123/progress')\n"
                "  return '/api/jobs/123/download'\n"
                "}\n",
                encoding="utf-8",
            )

            changed = auto_fix_nextjs_subpath_issues(
                repo_dir,
                "demo-next",
                nextjs_entry_file=runner.nextjs_entry_file,
                collect_matching_files=runner.collect_matching_files,
                read_text=runner.read_text,
                read_text_if_exists=runner.read_text_if_exists,
                write_text=runner.write_text,
                sorted_unique=runner.sorted_unique,
            )

            self.assertIn("src/components/widget.tsx", changed)
            rewritten = widget_path.read_text(encoding="utf-8")
            self.assertIn('new EventSource(withToolBase("/api/jobs/123/progress"))', rewritten)
            self.assertIn('return withToolBase("/api/jobs/123/download")', rewritten)

    def test_auto_fix_nextjs_subpath_issues_rewrites_href_axios_object_and_template_forms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            src_components_dir = repo_dir / "src" / "components"
            src_components_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "demo-next", "dependencies": {"next": "^15.1.0", "react": "^19.0.0", "react-dom": "^19.0.0"}}) + "\n",
                encoding="utf-8",
            )
            (repo_dir / "next.config.ts").write_text(
                "import type { NextConfig } from 'next'\nconst nextConfig: NextConfig = {}\nexport default nextConfig\n",
                encoding="utf-8",
            )
            widget_path = src_components_dir / "widget.tsx"
            widget_path.write_text(
                "'use client'\n"
                "export function connect(jobId: string) {\n"
                "  axios({ url: '/api/jobs' })\n"
                "  window.location.href = '/login'\n"
                "  new EventSource(`/api/jobs/${jobId}/progress`)\n"
                "  return `/api/jobs/${jobId}/download`\n"
                "}\n",
                encoding="utf-8",
            )

            changed = auto_fix_nextjs_subpath_issues(
                repo_dir,
                "demo-next",
                nextjs_entry_file=runner.nextjs_entry_file,
                collect_matching_files=runner.collect_matching_files,
                read_text=runner.read_text,
                read_text_if_exists=runner.read_text_if_exists,
                write_text=runner.write_text,
                sorted_unique=runner.sorted_unique,
            )

            self.assertIn("src/components/widget.tsx", changed)
            rewritten = widget_path.read_text(encoding="utf-8")
            self.assertIn('url: withToolBase("/api/jobs")', rewritten)
            self.assertIn('window.location.href = withToolBase("/login")', rewritten)
            self.assertIn('new EventSource(withToolBase("/api/jobs/${jobId}/progress"))', rewritten)
            self.assertIn('return withToolBase("/api/jobs/${jobId}/download")', rewritten)

    def test_apply_framework_config_adapters_updates_workspace_vite_config(self) -> None:
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
            config_path = frontend_dir / "vite.config.ts"
            config_path.write_text("import { defineConfig } from 'vite'\nexport default defineConfig({})\n", encoding="utf-8")

            plan = runner.subpath_build_subpath_plan(
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

            changed = apply_framework_config_adapters(
                plan,
                "demo-workspaces",
                parse_package_json=runner.parse_package_json,
                read_text=runner.read_text,
                write_text=runner.write_text,
            )

            self.assertIn("frontend/vite.config.ts", changed)
            self.assertIn('base: "/tools2/demo-workspaces/"', config_path.read_text(encoding="utf-8"))

    def test_apply_framework_config_adapter_honors_declared_nonstandard_vite_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            frontend_dir.mkdir(parents=True)
            config_path = frontend_dir / "custom.vite.ts"
            config_path.write_text("import { defineConfig } from 'vite'\nexport default defineConfig({})\n", encoding="utf-8")

            project = FrontendProjectStrategy(
                project_id="frontend",
                framework="vite",
                proxy_mode="preserve_prefix",
                adapter="vite",
                project_root=frontend_dir.resolve(),
                source_roots=(),
                runtime_roots=(),
                config_files=(config_path.resolve(),),
                capabilities=("config_adapter",),
                evidence=("declaration",),
            )

            changed = apply_framework_config_adapters(
                SubpathPlan(projects=(project,), default_project="frontend"),
                "demo-workspaces",
                parse_package_json=runner.parse_package_json,
                read_text=runner.read_text,
                write_text=runner.write_text,
            )

            self.assertTrue(any(item.endswith("custom.vite.ts") for item in changed))
            self.assertIn('base: "/tools2/demo-workspaces/"', config_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
