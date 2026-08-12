import tempfile
import unittest
from pathlib import Path

from subpath import rewrite as subpath_rewrite


class SubpathRewriteTests(unittest.TestCase):
    def _rewrite_request_api_text(self, text: str) -> str:
        return subpath_rewrite._rewrite_request_api_text(text)

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


if __name__ == "__main__":
    unittest.main()
