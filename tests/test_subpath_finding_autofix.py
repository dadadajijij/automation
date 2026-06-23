import unittest
from pathlib import Path

from subpath.apply import auto_fix_findings, group_subpath_findings_by_code
from subpath.models import FrontendProjectStrategy, SubpathPlan


class SubpathFindingAutofixTests(unittest.TestCase):
    def test_group_subpath_findings_by_code_groups_multiple_codes(self) -> None:
        findings = [
            {"code": "root_relative_html_url", "file": "index.html"},
            {"code": "root_relative_html_url", "file": "page.html"},
            {"code": "root_relative_client_url", "file": "app.js"},
        ]
        grouped = group_subpath_findings_by_code(findings)
        self.assertEqual(len(grouped["root_relative_html_url"]), 2)
        self.assertEqual(len(grouped["root_relative_client_url"]), 1)

    def test_auto_fix_findings_reports_applied_codes_for_static_rewrite(self) -> None:
        touched = []

        def fake_find_targets(repo_dir, strategy):
            class DummyPath:
                def relative_to(self, _repo_dir):
                    class DummyRel:
                        def as_posix(self):
                            return "index.html"
                    return DummyRel()
            return [DummyPath()]

        def fake_rewrite(file_path, base_path, repo_dir):
            touched.append((file_path, base_path, repo_dir))
            return True

        report = auto_fix_findings(
            repo_dir="repo",  # type: ignore[arg-type]
            project_slug="demo",
            findings=[{"code": "root_relative_html_url", "file": "index.html"}],
            strategy={"framework": "generic", "proxy_mode": "strip_prefix", "adapter": "static_rewrite"},
            auto_fix_nextjs_subpath_issues_fn=lambda repo_dir, slug: [],
            find_subpath_audit_targets=fake_find_targets,  # type: ignore[arg-type]
            rewrite_frontend_subpath_urls=fake_rewrite,  # type: ignore[arg-type]
            sorted_unique=lambda items: sorted(set(items)),
        )

        self.assertEqual(report["changed_files"], ["index.html"])
        self.assertEqual(report["applied_codes"], ["root_relative_html_url"])
        self.assertEqual(report["applied_codes_by_file"], {"index.html": ["root_relative_html_url"]})
        self.assertEqual(report["applied_codes_by_reason"], {"html_attribute_rewrite": ["root_relative_html_url"]})
        self.assertEqual(report["unchanged_codes"], [])
        self.assertEqual(report["unchanged_codes_by_reason"], {})
        self.assertEqual(report["unchanged_codes_by_reason_and_file"], {})
        self.assertTrue(touched)

    def test_auto_fix_findings_reports_no_matching_fixer_reason(self) -> None:
        report = auto_fix_findings(
            repo_dir="repo",  # type: ignore[arg-type]
            project_slug="demo",
            findings=[{"code": "unknown_code", "file": "app.js"}],
            strategy={"framework": "generic", "proxy_mode": "strip_prefix", "adapter": "static_rewrite"},
            auto_fix_nextjs_subpath_issues_fn=lambda repo_dir, slug: [],
            find_subpath_audit_targets=lambda repo_dir, strategy: [],
            rewrite_frontend_subpath_urls=lambda file_path, base_path, repo_dir: False,
            sorted_unique=lambda items: sorted(set(items)),
        )
        self.assertEqual(report["changed_files"], [])
        self.assertEqual(report["unchanged_codes"], ["unknown_code"])
        self.assertEqual(report["unchanged_codes_by_reason"], {"no_matching_fixer": ["unknown_code"]})
        self.assertEqual(report["unchanged_codes_by_reason_and_file"], {"no_matching_fixer": {"app.js": ["unknown_code"]}})

    def test_auto_fix_findings_reports_build_output_only_reason(self) -> None:
        report = auto_fix_findings(
            repo_dir="repo",  # type: ignore[arg-type]
            project_slug="demo",
            findings=[{"code": "build_output_html_root_relative_url", "file": "dist/index.html"}],
            strategy={"framework": "vite", "proxy_mode": "preserve_prefix", "adapter": "vite"},
            auto_fix_nextjs_subpath_issues_fn=lambda repo_dir, slug: [],
            find_subpath_audit_targets=lambda repo_dir, strategy: [],
            rewrite_frontend_subpath_urls=lambda file_path, base_path, repo_dir: False,
            sorted_unique=lambda items: sorted(set(items)),
        )
        self.assertEqual(report["unchanged_codes_by_reason"], {"build_output_only": ["build_output_html_root_relative_url"]})
        self.assertEqual(
            report["unchanged_codes_by_reason_and_file"],
            {"build_output_only": {"dist/index.html": ["build_output_html_root_relative_url"]}},
        )

    def test_auto_fix_findings_reports_framework_specific_only_reason(self) -> None:
        report = auto_fix_findings(
            repo_dir="repo",  # type: ignore[arg-type]
            project_slug="demo",
            findings=[{"code": "nextjs_form_root_action", "file": "index.html"}],
            strategy={"framework": "vite", "proxy_mode": "preserve_prefix", "adapter": "vite"},
            auto_fix_nextjs_subpath_issues_fn=lambda repo_dir, slug: [],
            find_subpath_audit_targets=lambda repo_dir, strategy: [],
            rewrite_frontend_subpath_urls=lambda file_path, base_path, repo_dir: False,
            sorted_unique=lambda items: sorted(set(items)),
        )
        self.assertEqual(report["unchanged_codes_by_reason"], {"framework_specific_only": ["nextjs_form_root_action"]})
        self.assertEqual(
            report["unchanged_codes_by_reason_and_file"],
            {"framework_specific_only": {"index.html": ["nextjs_form_root_action"]}},
        )

    def test_auto_fix_findings_reports_target_not_discovered_reason(self) -> None:
        class DummyPath:
            def __init__(self, name):
                self.name = name

            def relative_to(self, _repo_dir):
                class DummyRel:
                    def __init__(self, value):
                        self.value = value

                    def as_posix(self):
                        return self.value
                return DummyRel(self.name)

        report = auto_fix_findings(
            repo_dir="repo",  # type: ignore[arg-type]
            project_slug="demo",
            findings=[{"code": "root_relative_client_url", "file": "missing.js", "message": "fetch('/api/jobs')"}],
            strategy={"framework": "generic", "proxy_mode": "strip_prefix", "adapter": "static_rewrite"},
            auto_fix_nextjs_subpath_issues_fn=lambda repo_dir, slug: [],
            find_subpath_audit_targets=lambda repo_dir, strategy: [DummyPath("other.js")],  # type: ignore[arg-type]
            rewrite_frontend_subpath_urls=lambda file_path, base_path, repo_dir: False,
            rewrite_frontend_request_api_urls=lambda file_path, base_path, repo_dir: False,
            sorted_unique=lambda items: sorted(set(items)),
        )
        self.assertEqual(report["unchanged_codes_by_reason"], {"target_not_discovered": ["root_relative_client_url"]})

    def test_auto_fix_findings_reports_file_type_not_supported_reason(self) -> None:
        class DummyPath:
            def __init__(self, name):
                self.name = name

            def relative_to(self, _repo_dir):
                class DummyRel:
                    def __init__(self, value):
                        self.value = value

                    def as_posix(self):
                        return self.value
                return DummyRel(self.name)

        report = auto_fix_findings(
            repo_dir="repo",  # type: ignore[arg-type]
            project_slug="demo",
            findings=[{"code": "root_relative_client_url", "file": "index.html", "message": "fetch('/api/jobs')"}],
            strategy={"framework": "generic", "proxy_mode": "strip_prefix", "adapter": "static_rewrite"},
            auto_fix_nextjs_subpath_issues_fn=lambda repo_dir, slug: [],
            find_subpath_audit_targets=lambda repo_dir, strategy: [DummyPath("index.html")],  # type: ignore[arg-type]
            rewrite_frontend_subpath_urls=lambda file_path, base_path, repo_dir: False,
            rewrite_frontend_request_api_urls=lambda file_path, base_path, repo_dir: False,
            sorted_unique=lambda items: sorted(set(items)),
        )
        self.assertEqual(report["unchanged_codes_by_reason"], {"file_type_not_supported": ["root_relative_client_url"]})

    def test_auto_fix_findings_reports_rewriter_made_no_changes_reason(self) -> None:
        class DummyPath:
            def __init__(self, name):
                self.name = name

            def relative_to(self, _repo_dir):
                class DummyRel:
                    def __init__(self, value):
                        self.value = value

                    def as_posix(self):
                        return self.value
                return DummyRel(self.name)

        report = auto_fix_findings(
            repo_dir="repo",  # type: ignore[arg-type]
            project_slug="demo",
            findings=[{"code": "root_relative_client_url", "file": "app.js", "message": "fetch('/api/jobs')"}],
            strategy={"framework": "generic", "proxy_mode": "strip_prefix", "adapter": "static_rewrite"},
            auto_fix_nextjs_subpath_issues_fn=lambda repo_dir, slug: [],
            find_subpath_audit_targets=lambda repo_dir, strategy: [DummyPath("app.js")],  # type: ignore[arg-type]
            rewrite_frontend_subpath_urls=lambda file_path, base_path, repo_dir: False,
            rewrite_frontend_request_api_urls=lambda file_path, base_path, repo_dir: False,
            sorted_unique=lambda items: sorted(set(items)),
        )
        self.assertEqual(report["unchanged_codes_by_reason"], {"rewriter_made_no_changes": ["root_relative_client_url"]})

    def test_auto_fix_findings_static_rewrite_only_touches_findings_files(self) -> None:
        touched = []

        def fake_find_targets(repo_dir, strategy):
            class DummyPath:
                def __init__(self, name):
                    self.name = name

                def relative_to(self, _repo_dir):
                    class DummyRel:
                        def __init__(self, value):
                            self.value = value

                        def as_posix(self):
                            return self.value
                    return DummyRel(self.name)

            return [DummyPath("index.html"), DummyPath("unrelated.js")]

        def fake_rewrite(file_path, base_path, repo_dir):
            touched.append(file_path.relative_to(repo_dir).as_posix())  # type: ignore[arg-type]
            return True

        report = auto_fix_findings(
            repo_dir="repo",  # type: ignore[arg-type]
            project_slug="demo",
            findings=[{"code": "root_relative_html_url", "file": "index.html"}],
            strategy={"framework": "generic", "proxy_mode": "strip_prefix", "adapter": "static_rewrite"},
            auto_fix_nextjs_subpath_issues_fn=lambda repo_dir, slug: [],
            find_subpath_audit_targets=fake_find_targets,  # type: ignore[arg-type]
            rewrite_frontend_subpath_urls=fake_rewrite,  # type: ignore[arg-type]
            sorted_unique=lambda items: sorted(set(items)),
        )

        self.assertEqual(touched, ["index.html"])
        self.assertEqual(report["changed_files"], ["index.html"])
        self.assertEqual(report["applied_codes_by_reason"], {"html_attribute_rewrite": ["root_relative_html_url"]})

    def test_auto_fix_findings_static_rewrite_uses_client_request_reason_for_js(self) -> None:
        touched = []

        def fake_find_targets(repo_dir, strategy):
            class DummyPath:
                def __init__(self, name):
                    self.name = name

                def relative_to(self, _repo_dir):
                    class DummyRel:
                        def __init__(self, value):
                            self.value = value

                        def as_posix(self):
                            return self.value
                    return DummyRel(self.name)

            return [DummyPath("app.js"), DummyPath("index.html")]

        def fake_rewrite(file_path, base_path, repo_dir):
            touched.append(file_path.relative_to(repo_dir).as_posix())  # type: ignore[arg-type]
            return True

        report = auto_fix_findings(
            repo_dir="repo",  # type: ignore[arg-type]
            project_slug="demo",
            findings=[{"code": "root_relative_client_url", "file": "app.js"}],
            strategy={"framework": "generic", "proxy_mode": "strip_prefix", "adapter": "static_rewrite"},
            auto_fix_nextjs_subpath_issues_fn=lambda repo_dir, slug: [],
            find_subpath_audit_targets=fake_find_targets,  # type: ignore[arg-type]
            rewrite_frontend_subpath_urls=fake_rewrite,  # type: ignore[arg-type]
            sorted_unique=lambda items: sorted(set(items)),
        )

        self.assertEqual(touched, ["app.js"])
        self.assertEqual(report["changed_files"], ["app.js"])
        self.assertEqual(report["applied_codes_by_reason"], {"request_api_rewrite": ["root_relative_client_url"]})

    def test_auto_fix_findings_prefers_specialized_rewriters_when_provided(self) -> None:
        calls = []

        def fake_find_targets(repo_dir, strategy):
            class DummyPath:
                def __init__(self, name):
                    self.name = name

                def relative_to(self, _repo_dir):
                    class DummyRel:
                        def __init__(self, value):
                            self.value = value

                        def as_posix(self):
                            return self.value
                    return DummyRel(self.name)

            return [DummyPath("index.html"), DummyPath("app.js")]

        def html_rewrite(file_path, base_path, repo_dir):
            calls.append(("html", file_path.relative_to(repo_dir).as_posix()))  # type: ignore[arg-type]
            return True

        def client_rewrite(file_path, base_path, repo_dir):
            calls.append(("client", file_path.relative_to(repo_dir).as_posix()))  # type: ignore[arg-type]
            return True

        report = auto_fix_findings(
            repo_dir="repo",  # type: ignore[arg-type]
            project_slug="demo",
            findings=[
                {"code": "root_relative_html_url", "file": "index.html"},
                {"code": "root_relative_client_url", "file": "app.js"},
            ],
            strategy={"framework": "generic", "proxy_mode": "strip_prefix", "adapter": "static_rewrite"},
            auto_fix_nextjs_subpath_issues_fn=lambda repo_dir, slug: [],
            find_subpath_audit_targets=fake_find_targets,  # type: ignore[arg-type]
            rewrite_frontend_subpath_urls=lambda file_path, base_path, repo_dir: False,
            rewrite_frontend_html_attribute_urls=html_rewrite,  # type: ignore[arg-type]
            rewrite_frontend_client_request_urls=client_rewrite,  # type: ignore[arg-type]
            sorted_unique=lambda items: sorted(set(items)),
        )

        self.assertEqual(calls, [("html", "index.html"), ("client", "app.js")])
        self.assertEqual(report["applied_codes_by_reason"], {"request_api_rewrite": ["root_relative_client_url"], "html_attribute_rewrite": ["root_relative_html_url"]})

    def test_auto_fix_findings_can_use_more_specific_client_rewriters(self) -> None:
        calls = []

        def fake_find_targets(repo_dir, strategy):
            class DummyPath:
                def __init__(self, name):
                    self.name = name

                def relative_to(self, _repo_dir):
                    class DummyRel:
                        def __init__(self, value):
                            self.value = value

                        def as_posix(self):
                            return self.value
                    return DummyRel(self.name)

            return [DummyPath("api.ts"), DummyPath("nav.ts"), DummyPath("events.ts"), DummyPath("returns.ts")]

        def api_rewrite(file_path, base_path, repo_dir):
            calls.append(("api", file_path.relative_to(repo_dir).as_posix()))  # type: ignore[arg-type]
            return True

        def nav_rewrite(file_path, base_path, repo_dir):
            calls.append(("nav", file_path.relative_to(repo_dir).as_posix()))  # type: ignore[arg-type]
            return True

        def event_rewrite(file_path, base_path, repo_dir):
            calls.append(("event", file_path.relative_to(repo_dir).as_posix()))  # type: ignore[arg-type]
            return True

        def return_rewrite(file_path, base_path, repo_dir):
            calls.append(("return", file_path.relative_to(repo_dir).as_posix()))  # type: ignore[arg-type]
            return True

        report = auto_fix_findings(
            repo_dir="repo",  # type: ignore[arg-type]
            project_slug="demo",
            findings=[
                {"code": "root_relative_client_url", "file": "api.ts", "detail_kind": "request_api"},
                {"code": "root_relative_client_url", "file": "nav.ts", "detail_kind": "navigation"},
                {"code": "root_relative_client_url", "file": "events.ts", "detail_kind": "eventsource"},
                {"code": "root_relative_client_url", "file": "returns.ts", "detail_kind": "return_value"},
            ],
            strategy={"framework": "generic", "proxy_mode": "strip_prefix", "adapter": "static_rewrite"},
            auto_fix_nextjs_subpath_issues_fn=lambda repo_dir, slug: [],
            find_subpath_audit_targets=fake_find_targets,  # type: ignore[arg-type]
            rewrite_frontend_subpath_urls=lambda file_path, base_path, repo_dir: False,
            rewrite_frontend_request_api_urls=api_rewrite,  # type: ignore[arg-type]
            rewrite_frontend_navigation_urls=nav_rewrite,  # type: ignore[arg-type]
            rewrite_frontend_eventsource_urls=event_rewrite,  # type: ignore[arg-type]
            rewrite_frontend_return_value_urls=return_rewrite,  # type: ignore[arg-type]
            sorted_unique=lambda items: sorted(set(items)),
        )

        self.assertEqual(
            calls,
            [("api", "api.ts"), ("nav", "nav.ts"), ("event", "events.ts"), ("return", "returns.ts")],
        )
        self.assertEqual(
            report["applied_codes_by_reason"],
            {
                "request_api_rewrite": ["root_relative_client_url"],
                "navigation_rewrite": ["root_relative_client_url"],
                "eventsource_rewrite": ["root_relative_client_url"],
                "return_value_rewrite": ["root_relative_client_url"],
            },
        )

    def test_auto_fix_findings_can_use_more_specific_html_rewriters(self) -> None:
        calls = []

        def fake_find_targets(repo_dir, strategy):
            class DummyPath:
                def __init__(self, name):
                    self.name = name

                def relative_to(self, _repo_dir):
                    class DummyRel:
                        def __init__(self, value):
                            self.value = value

                        def as_posix(self):
                            return self.value
                    return DummyRel(self.name)

            return [DummyPath("links.html"), DummyPath("scripts.html"), DummyPath("forms.html")]

        def link_rewrite(file_path, base_path, repo_dir):
            calls.append(("link", file_path.relative_to(repo_dir).as_posix()))  # type: ignore[arg-type]
            return True

        def script_rewrite(file_path, base_path, repo_dir):
            calls.append(("script", file_path.relative_to(repo_dir).as_posix()))  # type: ignore[arg-type]
            return True

        def form_rewrite(file_path, base_path, repo_dir):
            calls.append(("form", file_path.relative_to(repo_dir).as_posix()))  # type: ignore[arg-type]
            return True

        report = auto_fix_findings(
            repo_dir="repo",  # type: ignore[arg-type]
            project_slug="demo",
            findings=[
                {"code": "root_relative_html_url", "file": "links.html", "detail_kind": "html_link"},
                {"code": "root_relative_html_url", "file": "scripts.html", "detail_kind": "html_script"},
                {"code": "root_relative_html_url", "file": "forms.html", "detail_kind": "html_form_action"},
            ],
            strategy={"framework": "generic", "proxy_mode": "strip_prefix", "adapter": "static_rewrite"},
            auto_fix_nextjs_subpath_issues_fn=lambda repo_dir, slug: [],
            find_subpath_audit_targets=fake_find_targets,  # type: ignore[arg-type]
            rewrite_frontend_subpath_urls=lambda file_path, base_path, repo_dir: False,
            rewrite_frontend_html_link_urls=link_rewrite,  # type: ignore[arg-type]
            rewrite_frontend_html_script_urls=script_rewrite,  # type: ignore[arg-type]
            rewrite_frontend_html_form_action_urls=form_rewrite,  # type: ignore[arg-type]
            sorted_unique=lambda items: sorted(set(items)),
        )

        self.assertEqual(
            calls,
            [("link", "links.html"), ("script", "scripts.html"), ("form", "forms.html")],
        )
        self.assertEqual(
            report["applied_codes_by_reason"],
            {
                "html_link_rewrite": ["root_relative_html_url"],
                "html_script_rewrite": ["root_relative_html_url"],
                "html_form_action_rewrite": ["root_relative_html_url"],
            },
        )

    def test_auto_fix_findings_supports_generic_rewrite_adapters_beyond_static_rewrite(self) -> None:
        calls = []

        def fake_find_targets(repo_dir, strategy):
            class DummyPath:
                def __init__(self, name):
                    self.name = name

                def relative_to(self, _repo_dir):
                    class DummyRel:
                        def __init__(self, value):
                            self.value = value

                        def as_posix(self):
                            return self.value
                    return DummyRel(self.name)

            return [DummyPath("index.html"), DummyPath("src/api.ts")]

        def html_rewrite(file_path, base_path, repo_dir):
            calls.append(("html", file_path.relative_to(repo_dir).as_posix()))  # type: ignore[arg-type]
            return True

        def api_rewrite(file_path, base_path, repo_dir):
            calls.append(("api", file_path.relative_to(repo_dir).as_posix()))  # type: ignore[arg-type]
            return True

        report = auto_fix_findings(
            repo_dir="repo",  # type: ignore[arg-type]
            project_slug="demo",
            findings=[
                {"code": "root_relative_html_url", "file": "index.html", "detail_kind": "html_link"},
                {"code": "root_relative_client_url", "file": "src/api.ts", "detail_kind": "request_api"},
            ],
            strategy={"framework": "vite", "proxy_mode": "preserve_prefix", "adapter": "vite"},
            auto_fix_nextjs_subpath_issues_fn=lambda repo_dir, slug: [],
            find_subpath_audit_targets=fake_find_targets,  # type: ignore[arg-type]
            rewrite_frontend_subpath_urls=lambda file_path, base_path, repo_dir: False,
            rewrite_frontend_html_link_urls=html_rewrite,  # type: ignore[arg-type]
            rewrite_frontend_request_api_urls=api_rewrite,  # type: ignore[arg-type]
            sorted_unique=lambda items: sorted(set(items)),
        )

        self.assertEqual(calls, [("html", "index.html"), ("api", "src/api.ts")])
        self.assertEqual(
            report["applied_codes_by_reason"],
            {
                "html_link_rewrite": ["root_relative_html_url"],
                "request_api_rewrite": ["root_relative_client_url"],
            },
        )

    def test_auto_fix_findings_respects_plan_capabilities(self) -> None:
        calls = []

        def fake_find_targets(repo_dir, strategy):
            class DummyPath:
                def __init__(self, name):
                    self.name = name

                def relative_to(self, _repo_dir):
                    class DummyRel:
                        def __init__(self, value):
                            self.value = value

                        def as_posix(self):
                            return self.value
                    return DummyRel(self.name)

            return [DummyPath("index.html"), DummyPath("src/api.ts")]

        def html_rewrite(file_path, base_path, repo_dir):
            calls.append(("html", file_path.relative_to(repo_dir).as_posix()))  # type: ignore[arg-type]
            return True

        def api_rewrite(file_path, base_path, repo_dir):
            calls.append(("api", file_path.relative_to(repo_dir).as_posix()))  # type: ignore[arg-type]
            return True

        plan = SubpathPlan(
            projects=(
                FrontendProjectStrategy(
                    project_id="root",
                    framework="vite",
                    proxy_mode="preserve_prefix",
                    adapter="vite",
                    project_root=Path("/tmp/project"),
                    source_roots=(),
                    runtime_roots=(),
                    config_files=(),
                    capabilities=("config_adapter",),
                    evidence=(),
                ),
            ),
            default_project="root",
        )

        report = auto_fix_findings(
            repo_dir="repo",  # type: ignore[arg-type]
            project_slug="demo",
            findings=[
                {"code": "root_relative_html_url", "file": "index.html", "detail_kind": "html_link"},
                {"code": "root_relative_client_url", "file": "src/api.ts", "detail_kind": "request_api"},
            ],
            strategy={"framework": "vite", "proxy_mode": "preserve_prefix", "adapter": "vite"},
            plan=plan,
            auto_fix_nextjs_subpath_issues_fn=lambda repo_dir, slug: [],
            find_subpath_audit_targets=fake_find_targets,  # type: ignore[arg-type]
            rewrite_frontend_subpath_urls=lambda file_path, base_path, repo_dir: False,
            rewrite_frontend_html_link_urls=html_rewrite,  # type: ignore[arg-type]
            rewrite_frontend_request_api_urls=api_rewrite,  # type: ignore[arg-type]
            sorted_unique=lambda items: sorted(set(items)),
        )

        self.assertEqual(calls, [])
        self.assertEqual(report["unchanged_codes_by_reason"], {"file_type_not_supported": ["root_relative_client_url", "root_relative_html_url"]})

    def test_auto_fix_findings_allows_html_generic_rewriter_for_nextjs_strategy(self) -> None:
        calls = []

        def fake_find_targets(repo_dir, strategy):
            class DummyPath:
                def relative_to(self, _repo_dir):
                    class DummyRel:
                        def as_posix(self):
                            return "index.html"
                    return DummyRel()
            return [DummyPath()]

        def html_rewrite(file_path, base_path, repo_dir):
            calls.append((file_path, base_path, repo_dir))
            return True

        report = auto_fix_findings(
            repo_dir="repo",  # type: ignore[arg-type]
            project_slug="demo-next",
            findings=[{"code": "root_relative_html_url", "file": "index.html", "detail_kind": "html_link"}],
            strategy={"framework": "nextjs", "proxy_mode": "preserve_prefix", "adapter": "nextjs"},
            auto_fix_nextjs_subpath_issues_fn=lambda repo_dir, slug: [],
            find_subpath_audit_targets=fake_find_targets,  # type: ignore[arg-type]
            rewrite_frontend_subpath_urls=lambda file_path, base_path, repo_dir: False,
            rewrite_frontend_html_link_urls=html_rewrite,  # type: ignore[arg-type]
            sorted_unique=lambda items: sorted(set(items)),
        )

        self.assertTrue(calls)
        self.assertEqual(report["changed_files"], ["index.html"])
        self.assertEqual(report["applied_codes_by_reason"], {"html_link_rewrite": ["root_relative_html_url"]})


if __name__ == "__main__":
    unittest.main()
