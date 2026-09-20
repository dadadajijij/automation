import tempfile
import unittest
from pathlib import Path

import runner
from subpath.common import is_allowed_root_relative_url
from subpath.models import FrontendProjectStrategy, SubpathPlan
from subpath.python_embedded_html import (
    find_python_embedded_html_files,
    rewrite_python_embedded_html_subpath_urls,
    scan_python_embedded_html_findings,
)
from subpath.runtime_audit import runtime_subpath_findings_for_inline_scripts
from subpath.static_audit import run_static_subpath_audit


class PythonEmbeddedHtmlSubpathTests(unittest.TestCase):
    def _write_fastapi_embedded_html(self, repo_dir: Path) -> Path:
        file_path = repo_dir / "meeting_minutes_web.py"
        file_path.write_text(
            '\n'.join(
                [
                    "from fastapi import FastAPI",
                    "from fastapi.responses import HTMLResponse",
                    "",
                    "app = FastAPI()",
                    'HTML = """<!doctype html><html><head></head><body>',
                    "<script>",
                    "async function loadTasks(){const r=await fetch('/api/jobs');}",
                    "async function poll(id){return fetch('/api/jobs/'+id);}",
                    "</script>",
                    '</body></html>"""',
                    "",
                    '@app.get("/", response_class=HTMLResponse)',
                    "def index():",
                    "    return HTML",
                    "",
                    '@app.get("/api/jobs")',
                    "def list_jobs():",
                    "    return []",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return file_path

    def _generic_plan(self, repo_dir: Path) -> SubpathPlan:
        project = FrontendProjectStrategy(
            project_id="root",
            framework="generic",
            proxy_mode="strip_prefix",
            adapter="static_rewrite",
            project_root=repo_dir.resolve(),
            source_roots=(),
            runtime_roots=(),
            config_files=(),
            capabilities=("html_entry", "build_output_expected"),
            evidence=("fallback generic project",),
        )
        return SubpathPlan(projects=(project,), default_project="root")

    def test_detects_root_python_fastapi_embedded_html_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            file_path = self._write_fastapi_embedded_html(repo_dir)

            files = find_python_embedded_html_files(
                repo_dir,
                collect_matching_files=runner.collect_matching_files,
                read_text_if_exists=runner.read_text_if_exists,
            )

            self.assertEqual(files, [file_path])

    def test_rewrites_embedded_html_but_not_fastapi_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            file_path = self._write_fastapi_embedded_html(repo_dir)

            changed = rewrite_python_embedded_html_subpath_urls(
                file_path,
                "/tools2/src",
                repo_dir,
                read_text=runner.read_text,
                write_text=runner.write_text,
                read_text_if_exists=runner.read_text_if_exists,
            )

            text = file_path.read_text(encoding="utf-8")
            self.assertTrue(changed)
            self.assertIn('window.__TOOL_BASE_PATH__ = "/tools2/src";', text)
            self.assertIn('Reflect.get(window, "withToolBase")?.(', text)
            self.assertIn("/api/jobs", text)
            self.assertIn('@app.get("/api/jobs")', text)
            self.assertNotIn('href="/tools2/src/api/jobs/"+', text)

    def test_rewrites_dynamic_anchor_href_without_breaking_inline_js(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            file_path = repo_dir / "meeting_minutes_web.py"
            file_path.write_text(
                '\n'.join(
                    [
                        "from fastapi import FastAPI",
                        "from fastapi.responses import HTMLResponse",
                        "",
                        "app = FastAPI()",
                        'HTML = """<!doctype html><html><body>',
                        "<script>",
                        "function render(j){return '<a href=\"/api/jobs/'+j.id+'/minutes\" download>下载纪要</a>';}",
                        "</script>",
                        '</body></html>"""',
                        "",
                        '@app.get("/", response_class=HTMLResponse)',
                        "def index():",
                        "    return HTML",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            changed = rewrite_python_embedded_html_subpath_urls(
                file_path,
                "/tools2/src",
                repo_dir,
                read_text=runner.read_text,
                write_text=runner.write_text,
                read_text_if_exists=runner.read_text_if_exists,
            )

            text = file_path.read_text(encoding="utf-8")
            self.assertTrue(changed)
            self.assertIn("withToolBase", text)
            self.assertIn("('/api/jobs/')", text)
            self.assertIn("+j.id+'/minutes", text.replace(" ", ""))
            self.assertNotIn('href="/tools2/src/api/jobs/"+', text)

    def test_static_audit_includes_python_embedded_html_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            self._write_fastapi_embedded_html(repo_dir)

            audit = run_static_subpath_audit(
                repo_dir,
                "src",
                self._generic_plan(repo_dir),
                collect_matching_files=runner.collect_matching_files,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
                read_text=runner.read_text,
                find_python_embedded_html_files=lambda path: find_python_embedded_html_files(
                    path,
                    collect_matching_files=runner.collect_matching_files,
                    read_text_if_exists=runner.read_text_if_exists,
                ),
                scan_python_embedded_html_findings=lambda path, slug: scan_python_embedded_html_findings(
                    path,
                    slug,
                    collect_matching_files=runner.collect_matching_files,
                    read_text_if_exists=runner.read_text_if_exists,
                ),
            )

            self.assertEqual(audit["scanned_files"], ["meeting_minutes_web.py"])
            self.assertEqual({item["code"] for item in audit["findings"]}, {"python_embedded_root_relative_client_url"})

    def test_does_not_rewrite_duty_style_error_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            file_path = repo_dir / "backend" / "main.py"
            file_path.parent.mkdir()
            original = (
                "from fastapi.responses import HTMLResponse\n"
                "def deny():\n"
                "    return HTMLResponse('<html><body>denied</body></html>', status_code=403)\n"
            )
            file_path.write_text(original, encoding="utf-8")

            changed = rewrite_python_embedded_html_subpath_urls(
                file_path,
                "/tools2/duty",
                repo_dir,
                read_text=runner.read_text,
                write_text=runner.write_text,
                read_text_if_exists=runner.read_text_if_exists,
            )

            self.assertFalse(changed)
            self.assertEqual(file_path.read_text(encoding="utf-8"), original)

    def test_runtime_inline_script_flags_fetch_but_allows_athena_me(self) -> None:
        html = (
            "<html><body><script>"
            "fetch('/api/me');"
            "fetch('/api/jobs');"
            "</script></body></html>"
        )

        findings = runtime_subpath_findings_for_inline_scripts(html, "/tools2/src/", "/tools2/src")

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["url"], "/api/jobs")

    def test_common_allowlist_keeps_athena_me_root_path(self) -> None:
        self.assertTrue(is_allowed_root_relative_url("/api/me", "/tools2/abtest"))
        self.assertFalse(is_allowed_root_relative_url("/api/jobs", "/tools2/src"))


if __name__ == "__main__":
    unittest.main()
