import unittest
from pathlib import Path
from unittest import mock

from subpath.pipeline import findings_to_error_texts, prepare_subpath_sources, select_runtime_projects
from subpath.models import FrontendProjectStrategy, SubpathPlan


class SubpathPipelineTests(unittest.TestCase):
    def test_findings_to_error_texts_formats_static_findings(self) -> None:
        findings = [
            {
                "file": "index.html",
                "line": 1,
                "severity": "error",
                "code": "root_relative_html_url",
                "message": 'Root-relative HTML attribute `href="/api/demo"` is incompatible with deployment subpath `/tools2/demo`',
            }
        ]
        self.assertEqual(
            findings_to_error_texts(findings),
            ['index.html:1 Root-relative HTML attribute `href="/api/demo"` is incompatible with deployment subpath `/tools2/demo`'],
        )

    def test_prepare_subpath_sources_can_return_plan_and_evidence(self) -> None:
        plan = SubpathPlan(projects=(), default_project=None)
        evidence = object()
        result = prepare_subpath_sources(
            repo_dir=None,  # type: ignore[arg-type]
            project_slug="demo",
            build_subpath_plan=lambda repo_dir: plan,
            detect_runtime_root_evidence=lambda repo_dir: evidence,
            apply_subpath_rewrites=lambda repo_dir, slug, plan_arg: [],
            run_static_subpath_audit=lambda repo_dir, slug, plan_arg: {"framework": "generic", "proxy_mode": "strip_prefix", "scanned_files": [], "findings": [], "warnings": []},
            auto_fix_subpath_issues=lambda repo_dir, slug, plan_arg: [],
            sorted_unique=lambda items: sorted(set(items)),
        )
        self.assertIs(result["plan"], plan)
        self.assertIs(result["detection_evidence"], evidence)

    def test_prepare_subpath_sources_can_return_rewrite_report(self) -> None:
        report = {
            "changed_files": ["index.html"],
            "applied_codes": ["root_relative_html_url"],
            "applied_codes_by_file": {"index.html": ["root_relative_html_url"]},
            "applied_codes_by_reason": {"generic_static_rewrite": ["root_relative_html_url"]},
            "unchanged_codes": [],
            "unchanged_codes_by_reason": {},
            "unchanged_codes_by_reason_and_file": {},
            "grouped_findings": {"root_relative_html_url": [{}]},
        }
        result = prepare_subpath_sources(
            repo_dir=None,  # type: ignore[arg-type]
            project_slug="demo",
            build_subpath_plan=lambda repo_dir: SubpathPlan(projects=(), default_project=None),
            apply_subpath_rewrites=lambda repo_dir, slug, plan_arg: [],
            run_static_subpath_audit=lambda repo_dir, slug, plan_arg: {"framework": "generic", "proxy_mode": "strip_prefix", "scanned_files": [], "findings": [{"code": "root_relative_html_url"}], "warnings": []},
            auto_fix_subpath_issues=lambda repo_dir, slug, plan_arg: [],
            auto_fix_findings=lambda repo_dir, slug, findings, plan_arg: report,
            sorted_unique=lambda items: sorted(set(items)),
        )
        self.assertEqual(result["rewrite_report"], report)

    def test_runtime_subpath_phase_can_skip_when_plan_has_no_runtime_capability(self) -> None:
        from subpath.pipeline import runtime_subpath_phase

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
                    capabilities=("client_code", "config_adapter"),
                    evidence=(),
                ),
            ),
            default_project="root",
        )

        result = runtime_subpath_phase(
            8300,
            "demo",
            "preserve_prefix",
            plan=plan,
            run_runtime_subpath_audit=lambda host_port, slug, proxy_mode: {"checked_paths": ["/should-not-run"], "findings": [], "warnings": []},
        )

        self.assertEqual(result["runtime_findings"], [])
        self.assertEqual(result["runtime_subpath_audit"]["checked_paths"], [])
        self.assertTrue(result["runtime_subpath_audit"]["skipped_by_capability"])
        self.assertEqual(result["runtime_subpath_audit"]["project_count"], 1)
        self.assertEqual(result["runtime_subpath_audit"]["eligible_projects"], [])
        self.assertIsNone(result["runtime_subpath_audit"]["default_runtime_project"])
        self.assertEqual(result["runtime_subpath_audit"]["audited_projects"], [])
        self.assertEqual(result["runtime_subpath_audit"]["projects"], [])

    def test_runtime_subpath_phase_records_eligible_projects_when_runtime_runs(self) -> None:
        from subpath.pipeline import runtime_subpath_phase

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
                    capabilities=("html_entry", "client_code"),
                    evidence=(),
                ),
            ),
            default_project="root",
        )

        result = runtime_subpath_phase(
            8300,
            "demo",
            "preserve_prefix",
            plan=plan,
            run_runtime_subpath_audit=lambda host_port, slug, proxy_mode, entry_path_hint=None, entry_path_candidates=None: {"checked_paths": ["/tools2/demo/"], "findings": [], "warnings": [], "entry_path_hint": entry_path_hint, "entry_path_candidates": entry_path_candidates},
        )

        self.assertEqual(result["runtime_subpath_audit"]["project_count"], 1)
        self.assertEqual(result["runtime_subpath_audit"]["eligible_projects"], ["root"])
        self.assertEqual(result["runtime_subpath_audit"]["default_runtime_project"], "root")
        self.assertEqual(result["runtime_subpath_audit"]["entry_path_hint"], "/")
        self.assertEqual(result["runtime_subpath_audit"]["entry_path_candidates"], ["/"])
        self.assertEqual(result["runtime_subpath_audit"]["audited_projects"], ["root"])
        self.assertEqual(result["runtime_subpath_audit"]["projects_with_findings"], [])
        self.assertEqual(len(result["runtime_subpath_audit"]["projects"]), 1)
        self.assertEqual(
            result["runtime_subpath_audit"]["project_summaries"],
            [
                {
                    "project_id": "root",
                    "checked_paths": 1,
                    "findings": 0,
                    "warnings": 0,
                    "entry_path_hint": "/",
                    "entry_path_candidates": 1,
                }
            ],
        )

    def test_select_runtime_projects_prefers_default_when_eligible(self) -> None:
        plan = SubpathPlan(
            projects=(
                FrontendProjectStrategy(
                    project_id="api",
                    framework="generic",
                    proxy_mode="strip_prefix",
                    adapter="static_rewrite",
                    project_root=Path("/tmp/api"),
                    source_roots=(),
                    runtime_roots=(),
                    config_files=(),
                    capabilities=("runtime_root",),
                    evidence=(),
                ),
                FrontendProjectStrategy(
                    project_id="web",
                    framework="vite",
                    proxy_mode="preserve_prefix",
                    adapter="vite",
                    project_root=Path("/tmp/web"),
                    source_roots=(),
                    runtime_roots=(),
                    config_files=(),
                    capabilities=("html_entry", "client_code"),
                    evidence=(),
                ),
            ),
            default_project="web",
        )

        selection = select_runtime_projects(plan)
        self.assertEqual(selection["project_count"], 2)
        self.assertEqual(selection["eligible_projects"], ["api", "web"])
        self.assertEqual(selection["default_runtime_project"], "web")
        self.assertEqual(selection["entry_path_hint"], "/")

    def test_select_runtime_projects_prefers_single_nested_runtime_root_for_generic_project(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            nested_root = project_root / "Demo" / "basicLive"
            nested_root.mkdir(parents=True)
            (nested_root / "index.html").write_text("<html></html>\n", encoding="utf-8")

            plan = SubpathPlan(
                projects=(
                    FrontendProjectStrategy(
                        project_id="root",
                        framework="generic",
                        proxy_mode="strip_prefix",
                        adapter="static_rewrite",
                        project_root=project_root,
                        source_roots=(),
                        runtime_roots=(nested_root,),
                        config_files=(),
                        capabilities=("runtime_root", "html_entry"),
                        evidence=(),
                    ),
                ),
                default_project="root",
            )

            selection = select_runtime_projects(plan)

            self.assertEqual(selection["default_runtime_project"], "root")
            self.assertEqual(selection["entry_path_hint"], "/Demo/basicLive/")
            self.assertEqual(selection["entry_path_candidates"], ["/Demo/basicLive/", "/"])

    def test_select_runtime_projects_prefers_declared_runtime_entry_override(self) -> None:
        plan = SubpathPlan(
            projects=(
                FrontendProjectStrategy(
                    project_id="root",
                    framework="generic",
                    proxy_mode="strip_prefix",
                    adapter="static_rewrite",
                    project_root=Path("/tmp/project"),
                    source_roots=(),
                    runtime_roots=(Path("/tmp/project/dist"),),
                    config_files=(),
                    capabilities=("runtime_root", "html_entry"),
                    evidence=("declaration",),
                    runtime_entry_hint="/preview/",
                    runtime_entry_candidates=("/preview/", "/"),
                ),
            ),
            default_project="root",
        )

        selection = select_runtime_projects(plan)
        self.assertEqual(selection["entry_path_hint"], "/preview/")
        self.assertEqual(selection["entry_path_candidates"], ["/preview/", "/"])
        self.assertEqual(
            selection["runtime_targets"],
            [{"project_id": "root", "entry_path_hint": "/preview/", "entry_path_candidates": ["/preview/", "/"]}],
        )

    def test_select_runtime_projects_includes_fallback_candidates_from_other_eligible_projects(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            alpha_root = project_root / "Alpha"
            beta_root = project_root / "Beta"
            (alpha_root / "landing").mkdir(parents=True)
            (beta_root / "docs").mkdir(parents=True)
            (alpha_root / "landing" / "index.html").write_text("<html></html>\n", encoding="utf-8")
            (beta_root / "docs" / "index.html").write_text("<html></html>\n", encoding="utf-8")

            plan = SubpathPlan(
                projects=(
                    FrontendProjectStrategy(
                        project_id="alpha",
                        framework="generic",
                        proxy_mode="strip_prefix",
                        adapter="static_rewrite",
                        project_root=alpha_root,
                        source_roots=(),
                        runtime_roots=(alpha_root / "landing",),
                        config_files=(),
                        capabilities=("runtime_root", "html_entry"),
                        evidence=(),
                    ),
                    FrontendProjectStrategy(
                        project_id="beta",
                        framework="generic",
                        proxy_mode="strip_prefix",
                        adapter="static_rewrite",
                        project_root=beta_root,
                        source_roots=(),
                        runtime_roots=(beta_root / "docs",),
                        config_files=(),
                        capabilities=("runtime_root", "html_entry"),
                        evidence=(),
                    ),
                ),
                default_project="alpha",
            )

            selection = select_runtime_projects(plan)
            self.assertEqual(selection["default_runtime_project"], "alpha")
            self.assertEqual(selection["entry_path_hint"], "/landing/")
            self.assertEqual(selection["entry_path_candidates"], ["/landing/", "/"])
            self.assertEqual(
                selection["runtime_targets"],
                [
                    {"project_id": "alpha", "entry_path_hint": "/landing/", "entry_path_candidates": ["/landing/", "/"]},
                    {"project_id": "beta", "entry_path_hint": "/docs/", "entry_path_candidates": ["/docs/", "/"]},
                ],
            )

    def test_runtime_subpath_phase_aggregates_multiple_runtime_targets(self) -> None:
        from subpath.pipeline import runtime_subpath_phase

        plan = SubpathPlan(
            projects=(
                FrontendProjectStrategy(
                    project_id="alpha",
                    framework="generic",
                    proxy_mode="strip_prefix",
                    adapter="static_rewrite",
                    project_root=Path("/tmp/alpha"),
                    source_roots=(),
                    runtime_roots=(),
                    config_files=(),
                    capabilities=("runtime_root", "html_entry"),
                    evidence=(),
                ),
                FrontendProjectStrategy(
                    project_id="beta",
                    framework="generic",
                    proxy_mode="strip_prefix",
                    adapter="static_rewrite",
                    project_root=Path("/tmp/beta"),
                    source_roots=(),
                    runtime_roots=(),
                    config_files=(),
                    capabilities=("runtime_root", "html_entry"),
                    evidence=(),
                ),
            ),
            default_project="alpha",
        )

        def fake_runtime_audit(host_port, slug, proxy_mode, entry_path_hint=None, entry_path_candidates=None):
            if entry_path_hint == "/landing/":
                return {"checked_paths": ["/tools2/demo/landing/"], "findings": [{"message": "landing issue"}], "warnings": [], "entry_path_hint": entry_path_hint, "entry_path_candidates": entry_path_candidates}
            return {"checked_paths": ["/tools2/demo/docs/"], "findings": [], "warnings": ["docs warning"], "entry_path_hint": entry_path_hint, "entry_path_candidates": entry_path_candidates}

        with mock.patch(
            "subpath.pipeline.select_runtime_projects",
            return_value={
                "project_count": 2,
                "eligible_projects": ["alpha", "beta"],
                "default_runtime_project": "alpha",
                "entry_path_hint": "/landing/",
                "entry_path_candidates": ["/landing/", "/"],
                "runtime_targets": [
                    {"project_id": "alpha", "entry_path_hint": "/landing/", "entry_path_candidates": ["/landing/", "/"]},
                    {"project_id": "beta", "entry_path_hint": "/docs/", "entry_path_candidates": ["/docs/", "/"]},
                ],
            },
        ):
            result = runtime_subpath_phase(
                8300,
                "demo",
                "strip_prefix",
                plan=plan,
                run_runtime_subpath_audit=fake_runtime_audit,
            )

        self.assertEqual(result["runtime_subpath_audit"]["audited_projects"], ["alpha", "beta"])
        self.assertEqual(result["runtime_subpath_audit"]["projects_with_findings"], ["alpha"])
        self.assertEqual(len(result["runtime_subpath_audit"]["projects"]), 2)
        self.assertEqual(
            result["runtime_subpath_audit"]["project_summaries"],
            [
                {
                    "project_id": "alpha",
                    "checked_paths": 1,
                    "findings": 1,
                    "warnings": 0,
                    "entry_path_hint": "/landing/",
                    "entry_path_candidates": 2,
                },
                {
                    "project_id": "beta",
                    "checked_paths": 1,
                    "findings": 0,
                    "warnings": 1,
                    "entry_path_hint": "/docs/",
                    "entry_path_candidates": 2,
                },
            ],
        )
        self.assertEqual(len(result["runtime_findings"]), 1)
        self.assertEqual(result["runtime_findings"][0]["project_id"], "alpha")


if __name__ == "__main__":
    unittest.main()
