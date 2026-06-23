import unittest
from unittest import mock

import runner
from subpath.runtime_audit import collect_runtime_follow_links, run_runtime_subpath_audit, runtime_subpath_findings_for_html
from subpath.runtime_urls import normalize_runtime_url_path, translate_external_to_upstream_path, translate_upstream_to_external_path


class SubpathRuntimeUrlTests(unittest.TestCase):
    def test_runtime_subpath_audit_flags_unprefixed_anchor(self) -> None:
        html = '<html><body><a href="/media-api">Media</a></body></html>'
        findings = runtime_subpath_findings_for_html(html, "/tools2/demo-next", "/tools2/demo-next")
        self.assertEqual(len(findings), 1)
        self.assertIn("/media-api", findings[0]["message"])

    def test_runtime_subpath_audit_allows_relative_links_under_strip_prefix(self) -> None:
        html = '<html><head><link rel="stylesheet" href="./assets/app.css"></head><body><a href="./basic/index.html">Demo</a></body></html>'
        findings = runtime_subpath_findings_for_html(html, "/tools2/demo/", "/tools2/demo")
        self.assertEqual(findings, [])

    def test_runtime_subpath_audit_allows_prefixed_urls(self) -> None:
        html = '<html><body><a href="/tools2/demo-next/media-api">Media</a></body></html>'
        findings = runtime_subpath_findings_for_html(html, "/tools2/demo-next", "/tools2/demo-next")
        self.assertEqual(findings, [])

    def test_runtime_subpath_audit_ignores_external_absolute_links(self) -> None:
        html = '<html><body><a href="https://doc.shengwang.cn/">Docs</a></body></html>'
        findings = runtime_subpath_findings_for_html(
            html,
            "/tools2/demo-next",
            "/tools2/demo-next",
            base_origin="https://athena.agoralab.co",
        )
        self.assertEqual(findings, [])

    def test_runtime_subpath_audit_still_checks_same_host_absolute_links(self) -> None:
        html = '<html><body><a href="https://athena.agoralab.co/media-api">Media</a></body></html>'
        findings = runtime_subpath_findings_for_html(
            html,
            "/tools2/demo-next",
            "/tools2/demo-next",
            base_origin="https://athena.agoralab.co",
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("/media-api", findings[0]["message"])

    def test_collect_runtime_follow_links_limits_to_in_scope_paths(self) -> None:
        html = '<html><body><a href="./a">A</a><a href="/tools2/demo/b">B</a><a href="https://example.com/x">X</a></body></html>'
        paths = collect_runtime_follow_links(
            html,
            "/tools2/demo/",
            "/tools2/demo",
            base_origin="https://athena.agoralab.co",
            limit=5,
        )
        self.assertEqual(paths, ["/tools2/demo/a", "/tools2/demo/b"])

    def test_translate_external_to_upstream_path_strips_prefix(self) -> None:
        self.assertEqual(
            translate_external_to_upstream_path("/tools2/loga/static/style.css", "/tools2/loga", "strip_prefix"),
            "/static/style.css",
        )
        self.assertEqual(
            translate_external_to_upstream_path("/tools2/loga/", "/tools2/loga", "strip_prefix"),
            "/",
        )

    def test_translate_external_to_upstream_path_preserves_prefix_when_requested(self) -> None:
        self.assertEqual(
            translate_external_to_upstream_path("/tools2/demo-next/media-api", "/tools2/demo-next", "preserve_prefix"),
            "/tools2/demo-next/media-api",
        )

    def test_translate_upstream_to_external_path_adds_prefix_for_root(self) -> None:
        self.assertEqual(translate_upstream_to_external_path("/", "/tools2/loga", "strip_prefix"), "/tools2/loga/")

    def test_normalize_runtime_url_path_resolves_relative_url(self) -> None:
        self.assertEqual(
            normalize_runtime_url_path("./assets/app.css", "/tools2/demo/", base_origin="https://athena.agoralab.co"),
            "/tools2/demo/assets/app.css",
        )

    def test_runtime_subpath_audit_strip_prefix_requests_upstream_paths(self) -> None:
        responses = {
            "/": (200, {"content-type": "text/html"}, '<html><head><link rel="stylesheet" href="/tools2/loga/static/style.css"></head></html>', "/"),
            "/static/style.css": (200, {"content-type": "text/css"}, "body {}", "/static/style.css"),
        }
        audit = run_runtime_subpath_audit(
            8005,
            "loga",
            "strip_prefix",
            external_access_host="athena.agoralab.co",
            fetch_http_text=lambda host_port, path: responses[path],
        )
        self.assertEqual(audit["findings"], [])
        self.assertEqual(audit["checked_paths"], ["/tools2/loga/", "/tools2/loga/static/style.css"])
        self.assertEqual(audit["entry_path_hint"], "/")
        self.assertEqual(audit["entry_path_candidates"], ["/"])

    def test_runtime_subpath_audit_strip_prefix_resolves_relative_urls_from_external_base(self) -> None:
        responses = {
            "/": (200, {"content-type": "text/html"}, '<html><head><link rel="stylesheet" href="./assets/app.css"></head><body><a href="./basic/index.html">Demo</a></body></html>', "/"),
            "/assets/app.css": (200, {"content-type": "text/css"}, "body {}", "/assets/app.css"),
            "/basic/index.html": (200, {"content-type": "text/html"}, "<html><body>ok</body></html>", "/basic/index.html"),
        }
        audit = run_runtime_subpath_audit(
            8005,
            "demo",
            "strip_prefix",
            external_access_host="athena.agoralab.co",
            fetch_http_text=lambda host_port, path: responses[path],
        )
        self.assertEqual(audit["findings"], [])
        self.assertEqual(
            audit["checked_paths"],
            ["/tools2/demo/", "/tools2/demo/assets/app.css", "/tools2/demo/basic/index.html"],
        )

    def test_runtime_subpath_audit_can_fallback_to_second_entry_candidate(self) -> None:
        responses = {
            "/missing/": (404, {"content-type": "text/html"}, "<html><body>missing</body></html>", "/missing/"),
            "/": (200, {"content-type": "text/html"}, "<html><body>ok</body></html>", "/"),
        }
        audit = run_runtime_subpath_audit(
            8005,
            "demo",
            "strip_prefix",
            entry_path_hint="/missing/",
            entry_path_candidates=["/missing/", "/"],
            external_access_host="athena.agoralab.co",
            fetch_http_text=lambda host_port, path: responses[path],
        )
        self.assertEqual(audit["findings"], [])
        self.assertEqual(audit["entry_path_hint"], "/")
        self.assertEqual(audit["entry_path_candidates"], ["/missing/", "/"])
        self.assertEqual(audit["checked_paths"], ["/tools2/demo/", "/tools2/demo/missing/"])

    def test_runner_compatibility_wrapper_still_uses_runtime_audit_module(self) -> None:
        responses = {
            "/": (200, {"content-type": "text/html"}, "<html><body>ok</body></html>", "/"),
        }
        with mock.patch.object(runner, "fetch_http_text", side_effect=lambda host_port, path, **kwargs: responses[path]):
            audit = runner.run_runtime_subpath_audit(8005, "demo", "strip_prefix")
        self.assertEqual(audit["findings"], [])


if __name__ == "__main__":
    unittest.main()
