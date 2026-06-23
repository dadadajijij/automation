import tempfile
import unittest
from pathlib import Path

import runner
from subpath.models import FrontendProjectStrategy, SubpathPlan
from subpath.build_output_audit import detect_build_output_roots, run_build_output_subpath_audit
from subpath.build_output_policy import build_output_finding_policy, recommended_build_output_mode


class SubpathBuildOutputAuditTests(unittest.TestCase):
    def test_detect_build_output_roots_finds_dist_under_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            dist_dir = frontend_dir / "dist"
            dist_dir.mkdir(parents=True)

            plan = SubpathPlan(
                projects=(
                    FrontendProjectStrategy(
                        project_id="frontend",
                        framework="vite",
                        proxy_mode="preserve_prefix",
                        adapter="vite",
                        project_root=frontend_dir.resolve(),
                        source_roots=(),
                        runtime_roots=(),
                        config_files=(),
                        capabilities=(),
                        evidence=(),
                    ),
                ),
                default_project="frontend",
            )

            roots = detect_build_output_roots(repo_dir, plan)
            self.assertEqual(roots, [dist_dir.resolve()])

    def test_run_build_output_subpath_audit_flags_unprefixed_root_relative_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            dist_dir = repo_dir / "dist"
            dist_dir.mkdir()
            (dist_dir / "index.html").write_text('<script src="/assets/app.js"></script>\n', encoding="utf-8")

            plan = SubpathPlan(
                projects=(
                    FrontendProjectStrategy(
                        project_id="root",
                        framework="static_html",
                        proxy_mode="strip_prefix",
                        adapter="static_rewrite",
                        project_root=repo_dir.resolve(),
                        source_roots=(),
                        runtime_roots=(),
                        config_files=(),
                        capabilities=(),
                        evidence=(),
                    ),
                ),
                default_project="root",
            )

            audit = run_build_output_subpath_audit(
                repo_dir,
                "demo",
                plan,
                collect_matching_files=runner.collect_matching_files,
                read_text=runner.read_text,
            )

            self.assertEqual(audit["output_roots"], ["dist"])
            self.assertTrue(audit["findings"])
            self.assertEqual(audit["findings"][0]["code"], "build_output_html_root_relative_url")
            self.assertEqual(audit["summary"]["total_findings"], 1)
            self.assertEqual(audit["summary"]["policy_version"], "v1")
            self.assertEqual(audit["summary"]["findings_by_code"], {"build_output_html_root_relative_url": 1})
            self.assertEqual(audit["summary"]["findings_by_category"], {"html": 1})
            self.assertEqual(audit["summary"]["findings_by_severity"], {"error": 1})
            self.assertEqual(audit["summary"]["enforcement_candidates"], {"build_output_html_root_relative_url": "future_blocker"})
            self.assertEqual(audit["summary"]["enforcement_candidate_counts"], {"future_blocker": 1})
            self.assertEqual(audit["summary"]["recommended_mode"], "candidate_for_enforce")

    def test_run_build_output_subpath_audit_flags_built_js_api_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            dist_dir = repo_dir / "dist" / "assets"
            dist_dir.mkdir(parents=True)
            (dist_dir / "app.js").write_text('axios.get("/api/jobs")\n', encoding="utf-8")

            plan = SubpathPlan(
                projects=(
                    FrontendProjectStrategy(
                        project_id="root",
                        framework="vite",
                        proxy_mode="preserve_prefix",
                        adapter="vite",
                        project_root=repo_dir.resolve(),
                        source_roots=(),
                        runtime_roots=(),
                        config_files=(),
                        capabilities=(),
                        evidence=(),
                    ),
                ),
                default_project="root",
            )

            audit = run_build_output_subpath_audit(
                repo_dir,
                "demo",
                plan,
                collect_matching_files=runner.collect_matching_files,
                read_text=runner.read_text,
            )

            self.assertTrue(any(item["file"] == "assets/app.js" and item["code"] == "build_output_client_root_relative_url" for item in audit["findings"]))
            self.assertEqual(audit["summary"]["findings_by_code"], {"build_output_client_root_relative_url": 1})
            self.assertEqual(audit["summary"]["findings_by_category"], {"client": 1})
            self.assertEqual(audit["summary"]["findings_by_severity"], {"error": 1})
            self.assertEqual(audit["summary"]["enforcement_candidates"], {"build_output_client_root_relative_url": "observe"})
            self.assertEqual(audit["summary"]["enforcement_candidate_counts"], {"observe": 1})
            self.assertEqual(audit["summary"]["recommended_mode"], "shadow_only")

    def test_run_build_output_subpath_audit_flags_manifest_asset_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            dist_dir = repo_dir / "dist"
            dist_dir.mkdir()
            (dist_dir / "manifest.json").write_text('{"main":"/assets/app.js"}\n', encoding="utf-8")

            plan = SubpathPlan(
                projects=(
                    FrontendProjectStrategy(
                        project_id="root",
                        framework="vite",
                        proxy_mode="preserve_prefix",
                        adapter="vite",
                        project_root=repo_dir.resolve(),
                        source_roots=(),
                        runtime_roots=(),
                        config_files=(),
                        capabilities=(),
                        evidence=(),
                    ),
                ),
                default_project="root",
            )

            audit = run_build_output_subpath_audit(
                repo_dir,
                "demo",
                plan,
                collect_matching_files=runner.collect_matching_files,
                read_text=runner.read_text,
            )

            self.assertTrue(any(item["file"] == "manifest.json" and item["code"] == "build_output_manifest_root_relative_url" for item in audit["findings"]))
            self.assertEqual(audit["summary"]["findings_by_code"], {"build_output_manifest_root_relative_url": 1})
            self.assertEqual(audit["summary"]["findings_by_category"], {"manifest": 1})
            self.assertEqual(audit["summary"]["findings_by_severity"], {"error": 1})
            self.assertEqual(audit["summary"]["enforcement_candidates"], {"build_output_manifest_root_relative_url": "observe"})
            self.assertEqual(audit["summary"]["enforcement_candidate_counts"], {"observe": 1})
            self.assertEqual(audit["summary"]["recommended_mode"], "shadow_only")

    def test_detect_build_output_roots_skips_projects_without_build_output_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            active_dir = repo_dir / "active"
            inert_dir = repo_dir / "inert"
            active_dist = active_dir / "dist"
            inert_dist = inert_dir / "dist"
            active_dist.mkdir(parents=True)
            inert_dist.mkdir(parents=True)

            plan = SubpathPlan(
                projects=(
                    FrontendProjectStrategy(
                        project_id="active",
                        framework="vite",
                        proxy_mode="preserve_prefix",
                        adapter="vite",
                        project_root=active_dir.resolve(),
                        source_roots=(),
                        runtime_roots=(),
                        config_files=(),
                        capabilities=("build_output_expected",),
                        evidence=(),
                    ),
                    FrontendProjectStrategy(
                        project_id="inert",
                        framework="generic",
                        proxy_mode="strip_prefix",
                        adapter="static_rewrite",
                        project_root=inert_dir.resolve(),
                        source_roots=(),
                        runtime_roots=(),
                        config_files=(),
                        capabilities=("runtime_root",),
                        evidence=(),
                    ),
                ),
                default_project="active",
            )

            roots = detect_build_output_roots(repo_dir, plan)
            self.assertEqual(roots, [active_dist.resolve()])

    def test_build_output_policy_can_be_overridden(self) -> None:
        policy = build_output_finding_policy(
            "build_output_client_root_relative_url",
            policy_overrides={
                "build_output_client_root_relative_url": {
                    "category": "client",
                    "severity": "warning",
                    "enforcement_candidate": "future_blocker",
                }
            },
        )
        self.assertEqual(policy["severity"], "warning")
        self.assertEqual(policy["enforcement_candidate"], "future_blocker")

    def test_recommended_build_output_mode_can_be_enforce(self) -> None:
        self.assertEqual(recommended_build_output_mode({"enforce": 1}), "enforce")

    def test_run_build_output_subpath_audit_applies_policy_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            dist_dir = repo_dir / "dist" / "assets"
            dist_dir.mkdir(parents=True)
            (dist_dir / "app.js").write_text('axios.get("/api/jobs")\n', encoding="utf-8")

            plan = SubpathPlan(
                projects=(
                    FrontendProjectStrategy(
                        project_id="root",
                        framework="vite",
                        proxy_mode="preserve_prefix",
                        adapter="vite",
                        project_root=repo_dir.resolve(),
                        source_roots=(),
                        runtime_roots=(),
                        config_files=(),
                        capabilities=("build_output_expected",),
                        evidence=(),
                    ),
                ),
                default_project="root",
            )

            audit = run_build_output_subpath_audit(
                repo_dir,
                "demo",
                plan,
                policy_overrides={
                    "build_output_client_root_relative_url": {
                        "severity": "warning",
                        "enforcement_candidate": "future_blocker",
                    }
                },
                collect_matching_files=runner.collect_matching_files,
                read_text=runner.read_text,
            )

            self.assertEqual(audit["summary"]["findings_by_severity"], {"warning": 1})
            self.assertEqual(audit["summary"]["enforcement_candidates"], {"build_output_client_root_relative_url": "future_blocker"})
            self.assertEqual(audit["summary"]["recommended_mode"], "candidate_for_enforce")

    def test_run_build_output_subpath_audit_applies_enforce_policy_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            dist_dir = repo_dir / "dist" / "assets"
            dist_dir.mkdir(parents=True)
            (dist_dir / "app.js").write_text('axios.get("/api/jobs")\n', encoding="utf-8")

            plan = SubpathPlan(
                projects=(
                    FrontendProjectStrategy(
                        project_id="root",
                        framework="vite",
                        proxy_mode="preserve_prefix",
                        adapter="vite",
                        project_root=repo_dir.resolve(),
                        source_roots=(),
                        runtime_roots=(),
                        config_files=(),
                        capabilities=("build_output_expected",),
                        evidence=(),
                    ),
                ),
                default_project="root",
            )

            audit = run_build_output_subpath_audit(
                repo_dir,
                "demo",
                plan,
                policy_overrides={
                    "build_output_client_root_relative_url": {
                        "enforcement_candidate": "enforce",
                    }
                },
                collect_matching_files=runner.collect_matching_files,
                read_text=runner.read_text,
            )

            self.assertEqual(audit["summary"]["enforcement_candidates"], {"build_output_client_root_relative_url": "enforce"})
            self.assertEqual(audit["summary"]["recommended_mode"], "enforce")


if __name__ == "__main__":
    unittest.main()
