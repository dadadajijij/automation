import json
import tempfile
import unittest
from pathlib import Path

import runner
from subpath.rewrite import rewrite_frontend_subpath_urls, rewrite_origin_based_subpath_logic


class SubpathRewriteTests(unittest.TestCase):
    def test_rewrite_html_relative_asset_url_resolves_under_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            html_path = repo_dir / "src" / "index.html"
            asset_path = repo_dir / "src" / "assets" / "app.css"
            html_path.parent.mkdir(parents=True)
            asset_path.parent.mkdir(parents=True)
            asset_path.write_text("body {}\n", encoding="utf-8")
            html_path.write_text('<link rel="stylesheet" href="./assets/app.css">\n', encoding="utf-8")

            changed = rewrite_frontend_subpath_urls(
                html_path,
                "/tools2/demo",
                repo_dir,
                read_text=runner.read_text,
                write_text=runner.write_text,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertTrue(changed)
            rewritten = html_path.read_text(encoding="utf-8")
            self.assertIn('href="/tools2/demo/assets/app.css"', rewritten)
            self.assertNotIn('/tools2/demo/src/assets/app.css', rewritten)

    def test_rewrite_origin_based_subpath_logic_wraps_origin_and_redirect(self) -> None:
        original = "\n".join(
            [
                "const ORIGIN_URL = window.location.origin;",
                "return origin;",
                "window.location.href = SETUP_PAGE_URL;",
            ]
        )
        rewritten = rewrite_origin_based_subpath_logic(original)
        self.assertIn("window.__TOOL_ORIGIN_URL__", rewritten)
        self.assertIn("return window.__TOOL_ORIGIN_URL__ || origin;", rewritten)
        self.assertIn('Reflect.get(window, "withToolBase")?.(SETUP_PAGE_URL) ?? SETUP_PAGE_URL', rewritten)

    def test_rewrite_frontend_subpath_urls_for_vite_index_keeps_src_entry_and_prefixes_icon(self) -> None:
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
                json.dumps(
                    {
                        "name": "frontend",
                        "private": True,
                        "dependencies": {"react": "^18.0.0", "react-dom": "^18.0.0"},
                        "devDependencies": {"vite": "^5.0.0", "@vitejs/plugin-react": "^4.0.0"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (frontend_dir / "vite.config.ts").write_text("import { defineConfig } from 'vite'\nexport default defineConfig({})\n", encoding="utf-8")
            html_path = frontend_dir / "index.html"
            html_path.write_text(
                '<!doctype html><html><head><link rel="icon" href="/vite.svg" /></head><body><script type="module" src="/src/main.tsx"></script></body></html>\n',
                encoding="utf-8",
            )

            changed = rewrite_frontend_subpath_urls(
                html_path,
                "/tools2/demo-workspaces",
                repo_dir,
                read_text=runner.read_text,
                write_text=runner.write_text,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )
            rewritten = html_path.read_text(encoding="utf-8")

            self.assertTrue(changed)
            self.assertIn('href="/tools2/demo-workspaces/vite.svg"', rewritten)
            self.assertIn('src="/src/main.tsx"', rewritten)

    def test_rewrite_frontend_subpath_urls_rewrites_vite_template_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            src_dir = frontend_dir / "src" / "api"
            src_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "demo-workspaces", "private": True, "workspaces": ["frontend"]}) + "\n",
                encoding="utf-8",
            )
            (frontend_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "frontend",
                        "private": True,
                        "dependencies": {"react": "^18.0.0", "react-dom": "^18.0.0"},
                        "devDependencies": {"vite": "^5.0.0", "@vitejs/plugin-react": "^4.0.0"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            file_path = src_dir / "jobs.ts"
            file_path.write_text("fetch(`/api/jobs/${jobId}`)\n", encoding="utf-8")

            changed = rewrite_frontend_subpath_urls(
                file_path,
                "/tools2/demo-workspaces",
                repo_dir,
                read_text=runner.read_text,
                write_text=runner.write_text,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )
            rewritten = file_path.read_text(encoding="utf-8")

            self.assertTrue(changed)
            self.assertIn('Reflect.get(window, "withToolBase")?.(`/api/jobs/${jobId}`) ?? `/api/jobs/${jobId}`', rewritten)

    def test_rewrite_frontend_subpath_urls_rewrites_eventsource_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            src_hooks_dir = frontend_dir / "src" / "hooks"
            src_hooks_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "demo-workspaces", "private": True, "workspaces": ["frontend"]}) + "\n",
                encoding="utf-8",
            )
            (frontend_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "frontend",
                        "private": True,
                        "dependencies": {"react": "^18.0.0", "react-dom": "^18.0.0"},
                        "devDependencies": {"vite": "^5.0.0", "@vitejs/plugin-react": "^4.0.0"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            file_path = src_hooks_dir / "useJobSSE.ts"
            file_path.write_text(
                "new EventSource('/api/jobs/123/progress')\nnew EventSource(`/api/jobs/${jobId}/progress`)\n",
                encoding="utf-8",
            )

            changed = rewrite_frontend_subpath_urls(
                file_path,
                "/tools2/demo-workspaces",
                repo_dir,
                read_text=runner.read_text,
                write_text=runner.write_text,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )
            rewritten = file_path.read_text(encoding="utf-8")

            self.assertTrue(changed)
            self.assertIn('new EventSource((Reflect.get(window, "withToolBase")?.(\'/api/jobs/123/progress\') ?? \'/api/jobs/123/progress\'))', rewritten)
            self.assertIn('new EventSource((Reflect.get(window, "withToolBase")?.(`/api/jobs/${jobId}/progress`) ?? `/api/jobs/${jobId}/progress`))', rewritten)

    def test_rewrite_frontend_subpath_urls_rewrites_returned_api_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            src_api_dir = frontend_dir / "src" / "api"
            src_api_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "demo-workspaces", "private": True, "workspaces": ["frontend"]}) + "\n",
                encoding="utf-8",
            )
            (frontend_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "frontend",
                        "private": True,
                        "dependencies": {"react": "^18.0.0", "react-dom": "^18.0.0"},
                        "devDependencies": {"vite": "^5.0.0", "@vitejs/plugin-react": "^4.0.0"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            file_path = src_api_dir / "jobs.ts"
            file_path.write_text("export function downloadUrl(jobId: string): string { return `/api/jobs/${jobId}/download` }\n", encoding="utf-8")

            changed = rewrite_frontend_subpath_urls(
                file_path,
                "/tools2/demo-workspaces",
                repo_dir,
                read_text=runner.read_text,
                write_text=runner.write_text,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )
            rewritten = file_path.read_text(encoding="utf-8")

            self.assertTrue(changed)
            self.assertIn('return (Reflect.get(window, "withToolBase")?.(`/api/jobs/${jobId}/download`) ?? `/api/jobs/${jobId}/download`)', rewritten)

    def test_rewrite_html_relative_assets_under_shared_runtime_root_preserves_nested_demo_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            scripts_dir = repo_dir / "scripts"
            demo_case_dir = repo_dir / "Demo" / "basicVoiceCall"
            scripts_dir.mkdir(parents=True)
            demo_case_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"scripts": {"dev": "node ./scripts/server.js"}}) + "\n",
                encoding="utf-8",
            )
            (scripts_dir / "server.js").write_text(
                "\n".join(
                    [
                        'const express = require("express");',
                        'const path = require("path");',
                        'const dir = path.join(__dirname, "../Demo");',
                        'const app = express();',
                        'app.use(express.static(dir));',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            html_path = demo_case_dir / "index.html"
            css_path = demo_case_dir / "index.css"
            js_path = demo_case_dir / "basicVoiceCall.js"
            css_path.write_text("body {}\n", encoding="utf-8")
            js_path.write_text("console.log('voice')\n", encoding="utf-8")
            html_path.write_text(
                '<link rel="stylesheet" href="./index.css">\n<script src="./basicVoiceCall.js"></script>\n',
                encoding="utf-8",
            )

            changed = rewrite_frontend_subpath_urls(
                html_path,
                "/tools2/demo",
                repo_dir,
                read_text=runner.read_text,
                write_text=runner.write_text,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertTrue(changed)
            rewritten = html_path.read_text(encoding="utf-8")
            self.assertIn('href="/tools2/demo/basicVoiceCall/index.css"', rewritten)
            self.assertIn('src="/tools2/demo/basicVoiceCall/basicVoiceCall.js"', rewritten)

    def test_frontend_runtime_root_prefers_earlier_runtime_root_order(self) -> None:
        from subpath.rewrite import frontend_runtime_root

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            shallow_root = repo_dir / "Demo"
            deep_root = repo_dir / "Demo" / "basicVoiceCall"
            deep_root.mkdir(parents=True)
            html_path = deep_root / "index.html"
            html_path.write_text("<html></html>\n", encoding="utf-8")

            selected = frontend_runtime_root(repo_dir, html_path, [shallow_root.resolve(), deep_root.resolve()])

            self.assertEqual(selected, shallow_root.resolve())


if __name__ == "__main__":
    unittest.main()
