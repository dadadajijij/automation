import unittest

from subpath.common import build_deployment_base_path, is_allowed_root_relative_url, looks_like_templated_value, split_url_suffix


class SubpathCommonTests(unittest.TestCase):
    def test_build_deployment_base_path_uses_project_slug(self) -> None:
        self.assertEqual(build_deployment_base_path("agora-token-generator"), "/tools2/agora-token-generator")

    def test_is_allowed_root_relative_url_accepts_prefixed_and_special_urls(self) -> None:
        base_path = "/tools2/demo"
        self.assertTrue(is_allowed_root_relative_url("/tools2/demo", base_path))
        self.assertTrue(is_allowed_root_relative_url("/tools2/demo/api/jobs", base_path))
        self.assertTrue(is_allowed_root_relative_url("//cdn.example.com/app.js", base_path))
        self.assertTrue(is_allowed_root_relative_url("mailto:test@example.com", base_path))
        self.assertTrue(is_allowed_root_relative_url("#section", base_path))

    def test_is_allowed_root_relative_url_rejects_unprefixed_root_url(self) -> None:
        self.assertFalse(is_allowed_root_relative_url("/api/demo", "/tools2/demo"))

    def test_split_url_suffix_preserves_query_and_fragment(self) -> None:
        self.assertEqual(split_url_suffix("/api/jobs?id=1#done"), ("/api/jobs", "?id=1#done"))

    def test_looks_like_templated_value_detects_common_template_markers(self) -> None:
        self.assertTrue(looks_like_templated_value("/api/${jobId}"))
        self.assertTrue(looks_like_templated_value("{{ url_for('demo') }}"))
        self.assertFalse(looks_like_templated_value("/api/jobs"))


if __name__ == "__main__":
    unittest.main()
