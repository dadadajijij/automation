import tempfile
import unittest
from pathlib import Path

from subpath import rewrite as subpath_rewrite


class SubpathRewriteTests(unittest.TestCase):
    def _rewrite_request_api_text(self, text: str) -> str:
        return subpath_rewrite._rewrite_request_api_text(text)

    def _rewrite_inline_html_api_fetch_path(self, text: str) -> str:
        return subpath_rewrite.rewrite_inline_html_api_fetch_path(text)

    def test_rewrite_request_api_text_rewrites_empty_api_base_assignment(self) -> None:
        rewritten = self._rewrite_request_api_text(
            "const API_BASE = '';\nfetch(API_BASE + '/api/analyze-stream')\n"
        )
        self.assertIn('const API_BASE = window.__TOOL_BASE_PATH__ || "";', rewritten)
        self.assertIn("fetch(API_BASE + '/api/analyze-stream')", rewritten)

    def test_rewrite_request_api_text_rewrites_origin_api_base_assignment(self) -> None:
        rewritten = self._rewrite_request_api_text(
            "var API_BASE = window.location.origin;\nfetch(API_BASE + '/api/log-metadata')\n"
        )
        self.assertIn("var API_BASE = window.__TOOL_ORIGIN_URL__ || window.location.origin;", rewritten)
        self.assertIn("fetch(API_BASE + '/api/log-metadata')", rewritten)

    def test_rewrite_request_api_text_rewrites_inline_origin_concat(self) -> None:
        rewritten = self._rewrite_request_api_text(
            "fetch(window.location.origin + '/api/log-metadata')\n"
        )
        self.assertIn("(window.__TOOL_ORIGIN_URL__ || window.location.origin) + \"/api/log-metadata\"", rewritten)

    def test_rewrite_request_api_text_does_not_rewrite_non_api_origin_concat(self) -> None:
        original = "fetch(window.location.origin + '/login')\n"
        rewritten = self._rewrite_request_api_text(original)
        self.assertEqual(rewritten, original)

    def test_rewrite_request_api_text_does_not_rewrite_external_api_base(self) -> None:
        original = "const API_BASE = 'https://example.com';\nfetch(API_BASE + '/api/log-metadata')\n"
        rewritten = self._rewrite_request_api_text(original)
        self.assertEqual(rewritten, original)

    def test_rewrite_request_api_text_is_idempotent(self) -> None:
        once = self._rewrite_request_api_text(
            "const API_BASE = '';\nfetch(API_BASE + '/api/analyze-stream')\n"
        )
        twice = self._rewrite_request_api_text(once)
        self.assertEqual(twice, once)

    def test_rewrite_client_request_text_keeps_jsx_path_attributes_valid(self) -> None:
        original = '<Route path="/" element={<Home />} />'
        rewritten = subpath_rewrite._rewrite_client_request_text(original)
        self.assertIn(
            'path={(Reflect.get(window, "withToolBase")?.("/") ?? "/")}',
            rewritten,
        )
        self.assertNotIn('path=(', rewritten)
        self.assertEqual(
            subpath_rewrite._rewrite_client_request_text(rewritten),
            rewritten,
        )

    def test_rewrite_client_request_text_rewrites_js_path_assignments(self) -> None:
        rewritten = subpath_rewrite._rewrite_client_request_text(
            'const path = "/api/jobs";\n'
        )
        self.assertIn(
            'const path = (Reflect.get(window, "withToolBase")?.("/api/jobs") ?? "/api/jobs");',
            rewritten,
        )

    def test_rewrite_inline_html_api_fetch_path_rewrites_wrapped_fetch_path(self) -> None:
        rewritten = self._rewrite_inline_html_api_fetch_path(
            "async function api(path, options) {\n"
            "  const res = await fetch(path, options);\n"
            "}\n"
        )
        self.assertIn(
            "const res = await fetch((Reflect.get(window, \"withToolBase\")?.(path) ?? path), options);",
            rewritten,
        )

    def test_rewrite_inline_html_api_fetch_path_is_idempotent(self) -> None:
        original = (
            "async function api(path, options) {\n"
            "  const res = await fetch(path, options);\n"
            "}\n"
        )
        once = self._rewrite_inline_html_api_fetch_path(original)
        twice = self._rewrite_inline_html_api_fetch_path(once)
        self.assertEqual(twice, once)

    def test_rewrite_inline_html_api_fetch_path_leaves_other_fetches_alone(self) -> None:
        original = (
            "fetch('/api/jobs')\n"
            "const data = await api('/api/jobs')\n"
            "function unrelated(path, options) {\n"
            "  const res = await fetch(path, options);\n"
            "}\n"
        )
        self.assertEqual(self._rewrite_inline_html_api_fetch_path(original), original)


if __name__ == "__main__":
    unittest.main()
