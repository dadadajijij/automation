import json
import tempfile
import unittest
from pathlib import Path

import runner
from subpath.models import FrontendProjectStrategy, SubpathPlan
from subpath.static_audit import find_subpath_audit_targets, run_static_subpath_audit, scan_subpath_findings


class SubpathStaticAuditTests(unittest.TestCase):
    def _plan_for_project(self, project: FrontendProjectStrategy) -> SubpathPlan:
        return SubpathPlan(projects=(project,), default_project=project.project_id)

    def test_find_subpath_audit_targets_includes_tsx_for_nextjs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            src_dir = repo_dir / "src" / "components"
            src_dir.mkdir(parents=True)
            tsx_path = src_dir / "widget.tsx"
            tsx_path.write_text("export default function App() { return null }\n", encoding="utf-8")
            (repo_dir / "package.json").write_text(
                json.dumps({"dependencies": {"next": "^15.1.0"}}) + "\n",
                encoding="utf-8",
            )
            project = FrontendProjectStrategy(
                project_id="root",
                framework="nextjs",
                proxy_mode="preserve_prefix",
                adapter="nextjs",
                project_root=repo_dir.resolve(),
                source_roots=(),
                runtime_roots=(),
                config_files=(),
                capabilities=("code_scan", "client_code", "html_entry"),
                evidence=(),
            )

            targets = find_subpath_audit_targets(
                repo_dir,
                self._plan_for_project(project),
                collect_matching_files=runner.collect_matching_files,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertIn(tsx_path, targets)

    def test_find_subpath_audit_targets_include_runtime_root_html(self) -> None:
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
            project = FrontendProjectStrategy(
                project_id="root",
                framework="express_static",
                proxy_mode="strip_prefix",
                adapter="static_rewrite",
                project_root=repo_dir.resolve(),
                source_roots=(),
                runtime_roots=(demo_dir.resolve(),),
                config_files=(),
                capabilities=("runtime_root", "html_entry"),
                evidence=(),
            )

            targets = find_subpath_audit_targets(
                repo_dir,
                self._plan_for_project(project),
                collect_matching_files=runner.collect_matching_files,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertIn(html_path, targets)

    def test_scan_nextjs_flags_raw_anchor_root_href(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            file_path = repo_dir / "src" / "app" / "layout.tsx"
            file_path.parent.mkdir(parents=True)
            file_path.write_text('<a href="/media-api">Media</a>\n', encoding="utf-8")
            project = FrontendProjectStrategy(
                project_id="root",
                framework="nextjs",
                proxy_mode="preserve_prefix",
                adapter="nextjs",
                project_root=repo_dir.resolve(),
                source_roots=(),
                runtime_roots=(),
                config_files=(),
                capabilities=("code_scan", "client_code", "html_entry"),
                evidence=(),
            )

            findings = scan_subpath_findings(
                file_path,
                project,
                "/tools2/demo-next",
                repo_dir,
                read_text=runner.read_text,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertEqual(len(findings), 1)
            self.assertIn("subpath deployment", findings[0].message)

    def test_scan_subpath_findings_allows_vite_index_src_entry(self) -> None:
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
            (src_dir / "main.tsx").write_text("console.log('demo')\n", encoding="utf-8")
            html_path = frontend_dir / "index.html"
            html_path.write_text('<script type="module" src="/src/main.tsx"></script>\n', encoding="utf-8")
            project = FrontendProjectStrategy(
                project_id="frontend",
                framework="vite",
                proxy_mode="preserve_prefix",
                adapter="vite",
                project_root=frontend_dir.resolve(),
                source_roots=(),
                runtime_roots=(),
                config_files=(),
                capabilities=("code_scan", "client_code", "html_entry"),
                evidence=(),
            )

            findings = scan_subpath_findings(
                html_path,
                project,
                "/tools2/demo-workspaces",
                repo_dir,
                read_text=runner.read_text,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertEqual(findings, [])

    def test_scan_allow_root_comment_suppresses_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            file_path = repo_dir / "src" / "app" / "layout.tsx"
            file_path.parent.mkdir(parents=True)
            file_path.write_text("// ka-subpath-allow-root\n<a href=\"/media-api\">Media</a>\n", encoding="utf-8")
            project = FrontendProjectStrategy(
                project_id="root",
                framework="nextjs",
                proxy_mode="preserve_prefix",
                adapter="nextjs",
                project_root=repo_dir.resolve(),
                source_roots=(),
                runtime_roots=(),
                config_files=(),
                capabilities=("code_scan", "client_code", "html_entry"),
                evidence=(),
            )

            findings = scan_subpath_findings(
                file_path,
                project,
                "/tools2/demo-next",
                repo_dir,
                read_text=runner.read_text,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertEqual(findings, [])

    def test_run_static_subpath_audit_returns_structured_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            static_dir = repo_dir / "static"
            static_dir.mkdir()
            file_path = static_dir / "app.js"
            file_path.write_text("fetch('/api/analyze-upload')\n", encoding="utf-8")
            project = FrontendProjectStrategy(
                project_id="root",
                framework="generic",
                proxy_mode="strip_prefix",
                adapter="static_rewrite",
                project_root=repo_dir.resolve(),
                source_roots=(),
                runtime_roots=(static_dir.resolve(),),
                config_files=(),
                capabilities=("runtime_root", "html_entry", "client_code"),
                evidence=(),
            )

            audit = run_static_subpath_audit(
                repo_dir,
                "demo",
                self._plan_for_project(project),
                collect_matching_files=runner.collect_matching_files,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
                read_text=runner.read_text,
            )

            self.assertEqual(audit["framework"], "generic")
            self.assertTrue(audit["findings"])
            self.assertEqual(audit["findings"][0]["code"], "root_relative_client_url")

    def test_scan_subpath_findings_flags_api_base_concat_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            static_dir = repo_dir / "static"
            static_dir.mkdir()
            file_path = static_dir / "app.js"
            file_path.write_text("const API_BASE = '';\nfetch(API_BASE + '/api/analyze-stream')\n", encoding="utf-8")
            project = FrontendProjectStrategy(
                project_id="root",
                framework="generic",
                proxy_mode="strip_prefix",
                adapter="static_rewrite",
                project_root=repo_dir.resolve(),
                source_roots=(),
                runtime_roots=(static_dir.resolve(),),
                config_files=(),
                capabilities=("runtime_root", "html_entry", "client_code"),
                evidence=(),
            )

            findings = scan_subpath_findings(
                file_path,
                project,
                "/tools2/demo",
                repo_dir,
                read_text=runner.read_text,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].detail_kind, "request_api")
            self.assertIn("/api/analyze-stream", findings[0].message)

    def test_scan_subpath_findings_flags_origin_concat_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            static_dir = repo_dir / "static"
            static_dir.mkdir()
            file_path = static_dir / "app.js"
            file_path.write_text("fetch(window.location.origin + '/api/log-metadata')\n", encoding="utf-8")
            project = FrontendProjectStrategy(
                project_id="root",
                framework="generic",
                proxy_mode="strip_prefix",
                adapter="static_rewrite",
                project_root=repo_dir.resolve(),
                source_roots=(),
                runtime_roots=(static_dir.resolve(),),
                config_files=(),
                capabilities=("runtime_root", "html_entry", "client_code"),
                evidence=(),
            )

            findings = scan_subpath_findings(
                file_path,
                project,
                "/tools2/demo",
                repo_dir,
                read_text=runner.read_text,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].detail_kind, "request_api")
            self.assertIn("/api/log-metadata", findings[0].message)

    def test_scan_subpath_findings_flags_api_base_concat_axios_url_object(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            static_dir = repo_dir / "static"
            static_dir.mkdir()
            file_path = static_dir / "app.js"
            file_path.write_text("axios({ method: 'POST', url: API_BASE + '/api/api-tracer' })\n", encoding="utf-8")
            project = FrontendProjectStrategy(
                project_id="root",
                framework="generic",
                proxy_mode="strip_prefix",
                adapter="static_rewrite",
                project_root=repo_dir.resolve(),
                source_roots=(),
                runtime_roots=(static_dir.resolve(),),
                config_files=(),
                capabilities=("runtime_root", "html_entry", "client_code"),
                evidence=(),
            )

            findings = scan_subpath_findings(
                file_path,
                project,
                "/tools2/demo",
                repo_dir,
                read_text=runner.read_text,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].detail_kind, "request_api")
            self.assertIn("/api/api-tracer", findings[0].message)

    def test_scan_subpath_findings_allows_safe_tool_base_api_base_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            static_dir = repo_dir / "static"
            static_dir.mkdir()
            file_path = static_dir / "app.js"
            file_path.write_text(
                'const API_BASE = window.__TOOL_BASE_PATH__ || "";\nfetch(API_BASE + \'/api/analyze-stream\')\n',
                encoding="utf-8",
            )
            project = FrontendProjectStrategy(
                project_id="root",
                framework="generic",
                proxy_mode="strip_prefix",
                adapter="static_rewrite",
                project_root=repo_dir.resolve(),
                source_roots=(),
                runtime_roots=(static_dir.resolve(),),
                config_files=(),
                capabilities=("runtime_root", "html_entry", "client_code"),
                evidence=(),
            )

            findings = scan_subpath_findings(
                file_path,
                project,
                "/tools2/demo",
                repo_dir,
                read_text=runner.read_text,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertEqual(findings, [])

    def test_scan_subpath_findings_allows_safe_tool_origin_concat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            static_dir = repo_dir / "static"
            static_dir.mkdir()
            file_path = static_dir / "app.js"
            file_path.write_text(
                "fetch((window.__TOOL_ORIGIN_URL__ || window.location.origin) + '/api/log-metadata')\n",
                encoding="utf-8",
            )
            project = FrontendProjectStrategy(
                project_id="root",
                framework="generic",
                proxy_mode="strip_prefix",
                adapter="static_rewrite",
                project_root=repo_dir.resolve(),
                source_roots=(),
                runtime_roots=(static_dir.resolve(),),
                config_files=(),
                capabilities=("runtime_root", "html_entry", "client_code"),
                evidence=(),
            )

            findings = scan_subpath_findings(
                file_path,
                project,
                "/tools2/demo",
                repo_dir,
                read_text=runner.read_text,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertEqual(findings, [])

    def test_scan_subpath_findings_does_not_flag_non_api_origin_concat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            static_dir = repo_dir / "static"
            static_dir.mkdir()
            file_path = static_dir / "app.js"
            file_path.write_text("fetch(window.location.origin + '/login')\n", encoding="utf-8")
            project = FrontendProjectStrategy(
                project_id="root",
                framework="generic",
                proxy_mode="strip_prefix",
                adapter="static_rewrite",
                project_root=repo_dir.resolve(),
                source_roots=(),
                runtime_roots=(static_dir.resolve(),),
                config_files=(),
                capabilities=("runtime_root", "html_entry", "client_code"),
                evidence=(),
            )

            findings = scan_subpath_findings(
                file_path,
                project,
                "/tools2/demo",
                repo_dir,
                read_text=runner.read_text,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertEqual(findings, [])

    def test_scan_subpath_findings_marks_detail_kind_for_navigation_and_return(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            static_dir = repo_dir / "static"
            static_dir.mkdir()
            file_path = static_dir / "app.js"
            file_path.write_text(
                "window.location.assign('/login')\nreturn '/api/jobs'\n",
                encoding="utf-8",
            )
            project = FrontendProjectStrategy(
                project_id="root",
                framework="generic",
                proxy_mode="strip_prefix",
                adapter="static_rewrite",
                project_root=repo_dir.resolve(),
                source_roots=(),
                runtime_roots=(static_dir.resolve(),),
                config_files=(),
                capabilities=("runtime_root", "html_entry", "client_code"),
                evidence=(),
            )

            findings = scan_subpath_findings(
                file_path,
                project,
                "/tools2/demo",
                repo_dir,
                read_text=runner.read_text,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertEqual({item.detail_kind for item in findings}, {"navigation", "return_value"})

    def test_scan_subpath_findings_marks_detail_kind_for_html_attributes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            file_path = repo_dir / "index.html"
            file_path.write_text(
                '<a href="/docs"></a>\n<script src="/assets/app.js"></script>\n<form action="/api/submit"></form>\n',
                encoding="utf-8",
            )
            project = FrontendProjectStrategy(
                project_id="root",
                framework="generic",
                proxy_mode="strip_prefix",
                adapter="static_rewrite",
                project_root=repo_dir.resolve(),
                source_roots=(),
                runtime_roots=(repo_dir.resolve(),),
                config_files=(),
                capabilities=("html_entry", "client_code"),
                evidence=(),
            )

            findings = scan_subpath_findings(
                file_path,
                project,
                "/tools2/demo",
                repo_dir,
                read_text=runner.read_text,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
            )

            self.assertEqual(
                {item.detail_kind for item in findings},
                {"html_link", "html_script", "html_form_action"},
            )


if __name__ == "__main__":
    unittest.main()
