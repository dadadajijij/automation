import argparse
from contextlib import ExitStack
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import runner


class RunnerSubpathPipelineTests(unittest.TestCase):
    def _plan_payload(self, repo_dir: Path, *, framework: str = "vite", proxy_mode: str = "preserve_prefix", capabilities: list[str] | None = None) -> dict:
        return {
            "projects": [
                {
                    "project_id": "root",
                    "framework": framework,
                    "proxy_mode": proxy_mode,
                    "adapter": "vite" if framework == "vite" else "static_rewrite",
                    "project_root": str(repo_dir),
                    "source_roots": [],
                    "runtime_roots": [],
                    "config_files": [],
                    "capabilities": capabilities or ["html_entry", "client_code", "config_adapter"],
                    "evidence": [],
                }
            ],
            "default_project": "root",
            "notes": [],
        }

    def _make_args(self, source_dir: Path, *, build: bool = False, run: bool = False) -> argparse.Namespace:
        return argparse.Namespace(
            source_type="local",
            source=str(source_dir),
            ref=None,
            tool_id=None,
            job_id="job-123",
            image_name=None,
            build=build,
            run=run,
            host_port=None,
        )

    def _base_patches(
        self,
        root: Path,
        source_dir: Path,
        repo_dir: Path,
        args: argparse.Namespace,
    ) -> list:
        shared_repo_dir = root / "jobs" / "demo-subpath" / "repo"
        shared_repo_metadata_path = root / "jobs" / "demo-subpath" / "repo-state.json"
        work_repo_dir = root / "jobs" / "demo-subpath" / "job-123" / "work-repo"

        return [
            mock.patch.object(runner.RunnerArgumentParser, "parse_args", return_value=args),
            mock.patch.object(runner, "JOBS_DIR", root / "jobs"),
            mock.patch.object(runner, "CODEX_HOME_CACHE_DIR", root / "codex-home-cache"),
            mock.patch.object(runner, "LOCAL_OFFICIAL_IMAGES_CACHE_PATH", root / "codex-home-cache" / "local-official-images.txt"),
            mock.patch.object(runner, "DEFAULT_CODEX_HOME_SEED", root / "seed-codex-home"),
            mock.patch.object(runner, "list_local_official_docker_images_from_env", return_value=[]),
            mock.patch.object(runner, "prepare_default_podman_environment", return_value=({"AUTOMATION_PODMAN_MODE": "default"}, {"podman_mode": "default"})),
            mock.patch.object(runner, "prepare_shared_repo", return_value=(shared_repo_dir, False, [])),
            mock.patch.object(runner, "prepare_job_repo_from_shared", return_value=repo_dir),
            mock.patch.object(runner, "collect_repo_analysis", return_value={}),
            mock.patch.object(runner, "summarize_analysis", return_value={"project_slug": "demo-subpath"}),
            mock.patch.object(runner, "emit_final_result", side_effect=lambda result, result_path: runner.write_json(result_path, {**result, "final_result": runner.build_final_result(result)})),
        ]

    def _run_with_patches(self, patches: list) -> int:
        with ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            return runner.main()

    def test_source_phase_records_subpath_artifacts_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "incoming" / "demo-subpath"
            repo_dir = root / "repo-work"
            source_dir.mkdir(parents=True)
            repo_dir.mkdir(parents=True)
            (source_dir / "package.json").write_text('{"name":"demo-subpath"}\n', encoding="utf-8")
            (repo_dir / "Dockerfile").write_text("FROM node:22-alpine\n", encoding="utf-8")
            (repo_dir / "PROJECT_ONBOARDING.md").write_text("端口 `3000`\n", encoding="utf-8")

            args = self._make_args(source_dir, build=False, run=False)
            patches = self._base_patches(root, source_dir, repo_dir, args) + [
                mock.patch.object(
                    runner,
                    "subpath_prepare_subpath_sources",
                    return_value={
                        "plan": self._plan_payload(repo_dir),
                        "detection_evidence": [],
                        "proxy_mode": "preserve_prefix",
                        "rewritten_files": ["index.html", "src/api/jobs.ts"],
                        "auto_fixed_files": [],
                        "rewrite_report": {
                            "changed_files": ["src/api/jobs.ts"],
                            "applied_codes": ["root_relative_client_url"],
                            "applied_codes_by_file": {"src/api/jobs.ts": ["root_relative_client_url"]},
                            "applied_codes_by_reason": {"generic_static_rewrite": ["root_relative_client_url"]},
                            "unchanged_codes": ["unknown_code"],
                            "unchanged_codes_by_reason": {"no_matching_fixer": ["unknown_code"]},
                            "unchanged_codes_by_reason_and_file": {"no_matching_fixer": {"index.html": ["unknown_code"]}},
                            "grouped_findings": {},
                        },
                        "static_subpath_audit": {
                            "framework": "vite",
                            "proxy_mode": "preserve_prefix",
                            "scanned_files": ["index.html", "src/api/jobs.ts"],
                            "findings": [],
                            "warnings": [],
                        },
                        "static_audit_attempts": [
                            {
                                "framework": "vite",
                                "proxy_mode": "preserve_prefix",
                                "scanned_files": ["index.html", "src/api/jobs.ts"],
                                "findings": [],
                                "warnings": [],
                            }
                        ],
                        "static_findings": [],
                    },
                ),
                mock.patch.object(runner, "validate_generated_files", return_value=([], [])),
                mock.patch.object(runner, "sync_generated_outputs_to_shared_repo", return_value=[]),
            ]
            exit_code = self._run_with_patches(patches)

            self.assertEqual(exit_code, 0)
            result_path = root / "jobs" / "demo-subpath" / "job-123" / "output" / "result.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "COMPLETED_WITHOUT_BUILD")
            self.assertEqual(payload["artifacts"]["subpath_proxy_mode"], "preserve_prefix")
            self.assertEqual(payload["artifacts"]["rewritten_frontend_files"], ["index.html", "src/api/jobs.ts"])
            self.assertEqual(payload["artifacts"]["subpath_static_audit"]["findings"], [])
            self.assertEqual(len(payload["artifacts"]["subpath_static_audit_attempts"]), 1)
            self.assertEqual(payload["analysis_summary"]["subpath_default_project"]["framework"], "vite")
            self.assertEqual(payload["analysis_summary"]["subpath_static_audit_summary"]["findings_count"], 0)
            self.assertEqual(payload["analysis_summary"]["subpath_rewrite_report_summary"]["unchanged_reason_counts"], {"no_matching_fixer": 1})
            self.assertEqual(payload["analysis_summary"]["subpath_rewrite_report_summary"]["applied_reason_counts"], {"generic_static_rewrite": 1})
            self.assertEqual(payload["analysis_summary"]["subpath_rewrite_report_summary"]["applied_files_count_by_reason"], {"generic_static_rewrite": 1})

    def test_source_phase_repairs_deterministic_dockerfile_finding_without_regenerating(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "incoming" / "demo-subpath"
            repo_dir = root / "repo-work"
            source_dir.mkdir(parents=True)
            repo_dir.mkdir(parents=True)
            (source_dir / "package.json").write_text('{"name":"demo-subpath"}\n', encoding="utf-8")
            (repo_dir / "package.json").write_text(
                '{"name":"demo-next","scripts":{"build":"next build","start":"next start"}}\n',
                encoding="utf-8",
            )
            (repo_dir / "next.config.ts").write_text("export default {}\n", encoding="utf-8")
            args = self._make_args(source_dir, build=False, run=False)
            analysis = {
                "service_runtime": "node",
                "package_scripts": ["build", "start"],
                "node_entry_command": "next start",
                "python_entry_command": None,
                "system_dependency_hints": [],
                "package_manager": "npm",
                "has_nextjs_ts_config": True,
                "requires_build_step": True,
                "required_build_commands": ["npm run build"],
                "python_install_validation_mode": "allow_package_install",
                "facts": [],
            }
            static_audit = {
                "framework": "nextjs",
                "proxy_mode": "preserve_prefix",
                "scanned_files": [],
                "findings": [],
                "warnings": [],
            }

            def generate_initial_files(*_args, **_kwargs) -> None:
                (repo_dir / "Dockerfile").write_text(
                    "\n".join(
                        [
                            "FROM node:22-alpine",
                            "WORKDIR /app",
                            "COPY package.json ./",
                            "RUN npm ci",
                            "COPY . .",
                            "RUN npm run build",
                            'CMD ["./node_modules/.bin/next", "start"]',
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (repo_dir / "PROJECT_ONBOARDING.md").write_text(
                    "# PROJECT_ONBOARDING\n## 1 项目基础信息\n## 2 代码和版本信息\n## 3 启动信息\n## 4 运行参数\n",
                    encoding="utf-8",
                )

            generate_mock = mock.Mock(side_effect=generate_initial_files)
            generate_patcher = mock.patch.object(
                runner,
                "invoke_codex_generation",
                new=generate_mock,
            )
            patches = self._base_patches(root, source_dir, repo_dir, args) + [
                mock.patch.object(runner, "collect_repo_analysis", return_value=analysis),
                mock.patch.object(runner, "summarize_analysis", return_value={"project_slug": "demo-subpath"}),
                mock.patch.object(
                    runner,
                    "subpath_prepare_subpath_sources",
                    return_value={
                        "plan": self._plan_payload(repo_dir, framework="nextjs"),
                        "detection_evidence": [],
                        "proxy_mode": "preserve_prefix",
                        "rewritten_files": [],
                        "auto_fixed_files": [],
                        "rewrite_report": {"changed_files": [], "applied_codes": [], "grouped_findings": {}},
                        "static_subpath_audit": static_audit,
                        "static_audit_attempts": [static_audit],
                        "static_findings": [],
                    },
                ),
                generate_patcher,
                mock.patch.object(runner, "sync_generated_outputs_to_shared_repo", return_value=[]),
            ]

            exit_code = self._run_with_patches(patches)

            self.assertEqual(exit_code, 0)
            self.assertEqual(generate_mock.call_count, 1)
            dockerfile_text = (repo_dir / "Dockerfile").read_text(encoding="utf-8")
            self.assertIn("RUN npm install --global pnpm", dockerfile_text)
            result_path = root / "jobs" / "demo-subpath" / "job-123" / "output" / "result.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["artifacts"]["deterministic_dockerfile_repairs"],
                ["provided pnpm with npm global install"],
            )

    def test_source_phase_restores_dockerfile_when_regeneration_loses_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "incoming" / "demo-subpath"
            repo_dir = root / "repo-work"
            source_dir.mkdir(parents=True)
            repo_dir.mkdir(parents=True)
            (source_dir / "package.json").write_text('{"name":"demo-subpath"}\n', encoding="utf-8")
            args = self._make_args(source_dir, build=False, run=False)
            analysis = {
                "service_runtime": "node",
                "has_nextjs_ts_config": True,
                "requires_build_step": True,
                "facts": [],
            }
            static_audit = {
                "framework": "nextjs",
                "proxy_mode": "preserve_prefix",
                "scanned_files": [],
                "findings": [],
                "warnings": [],
            }

            def generate_files(*_args, **_kwargs) -> None:
                has_feedback = bool(_kwargs.get("validation_findings"))
                pnpm_install = [] if has_feedback else ["RUN npm install --global pnpm"]
                (repo_dir / "Dockerfile").write_text(
                    "\n".join(
                        [
                            "FROM node:22-alpine",
                            *pnpm_install,
                            "COPY . .",
                            "RUN npm run build",
                            'CMD ["./node_modules/.bin/next", "start"]',
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (repo_dir / "PROJECT_ONBOARDING.md").write_text(
                    "# PROJECT_ONBOARDING\n## 4 运行参数\n",
                    encoding="utf-8",
                )

            generate_mock = mock.Mock(side_effect=generate_files)
            patches = self._base_patches(root, source_dir, repo_dir, args) + [
                mock.patch.object(runner, "collect_repo_analysis", return_value=analysis),
                mock.patch.object(runner, "summarize_analysis", return_value={"project_slug": "demo-subpath"}),
                mock.patch.object(
                    runner,
                    "subpath_prepare_subpath_sources",
                    return_value={
                        "plan": self._plan_payload(repo_dir, framework="nextjs"),
                        "detection_evidence": [],
                        "proxy_mode": "preserve_prefix",
                        "rewritten_files": [],
                        "auto_fixed_files": [],
                        "rewrite_report": {"changed_files": [], "applied_codes": [], "grouped_findings": {}},
                        "static_subpath_audit": static_audit,
                        "static_audit_attempts": [static_audit],
                        "static_findings": [],
                    },
                ),
                mock.patch.object(runner, "invoke_codex_generation", new=generate_mock),
                mock.patch.object(
                    runner,
                    "validate_generated_files",
                    side_effect=[(["PROJECT_ONBOARDING.md needs additional operator context"], []), ([], [])],
                ),
                mock.patch.object(runner, "sync_generated_outputs_to_shared_repo", return_value=[]),
            ]

            exit_code = self._run_with_patches(patches)

            self.assertEqual(exit_code, 0)
            self.assertEqual(generate_mock.call_count, 2)
            self.assertIn(
                "RUN npm install --global pnpm",
                (repo_dir / "Dockerfile").read_text(encoding="utf-8"),
            )
            result_path = root / "jobs" / "demo-subpath" / "job-123" / "output" / "result.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["artifacts"]["dockerfile_capability_regressions"],
                ["pnpm runtime availability"],
            )

    def test_source_phase_returns_subpath_static_audit_failed_with_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "incoming" / "demo-subpath"
            repo_dir = root / "repo-work"
            source_dir.mkdir(parents=True)
            repo_dir.mkdir(parents=True)
            (source_dir / "package.json").write_text('{"name":"demo-subpath"}\n', encoding="utf-8")

            args = self._make_args(source_dir, build=False, run=False)
            static_audit = {
                "framework": "generic",
                "proxy_mode": "strip_prefix",
                "scanned_files": ["index.html"],
                "findings": [
                    {
                        "file": "index.html",
                        "line": 1,
                        "severity": "error",
                        "code": "root_relative_html_url",
                        "message": 'Root-relative HTML attribute `href="/api/demo"` is incompatible with deployment subpath `/tools2/demo-subpath`',
                    }
                ],
                "warnings": [],
            }
            patches = self._base_patches(root, source_dir, repo_dir, args) + [
                mock.patch.object(
                    runner,
                    "subpath_prepare_subpath_sources",
                    return_value={
                        "plan": self._plan_payload(repo_dir, framework="generic", proxy_mode="strip_prefix"),
                        "detection_evidence": [],
                        "proxy_mode": "strip_prefix",
                        "rewritten_files": [],
                        "auto_fixed_files": [],
                        "rewrite_report": {"changed_files": [], "applied_codes": [], "grouped_findings": {}},
                        "static_subpath_audit": static_audit,
                        "static_audit_attempts": [static_audit],
                        "static_findings": static_audit["findings"],
                    },
                ),
            ]
            exit_code = self._run_with_patches(patches)

            self.assertEqual(exit_code, 1)
            result_path = root / "jobs" / "demo-subpath" / "job-123" / "output" / "result.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "SUBPATH_STATIC_AUDIT_FAILED")
            self.assertEqual(len(payload["errors"]), 1)
            self.assertIn("index.html:1", payload["errors"][0])
            self.assertEqual(payload["artifacts"]["subpath_static_audit"]["findings"], static_audit["findings"])
            self.assertEqual(len(payload["artifacts"]["subpath_static_audit_attempts"]), 1)

    def test_runtime_phase_returns_subpath_runtime_audit_failed_with_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "incoming" / "demo-subpath"
            repo_dir = root / "repo-work"
            source_dir.mkdir(parents=True)
            repo_dir.mkdir(parents=True)
            (source_dir / "package.json").write_text('{"name":"demo-subpath"}\n', encoding="utf-8")
            (repo_dir / "Dockerfile").write_text("FROM node:22-alpine\n", encoding="utf-8")
            (repo_dir / "PROJECT_ONBOARDING.md").write_text("端口 `3000`\n", encoding="utf-8")

            args = self._make_args(source_dir, build=True, run=True)
            runtime_audit = {
                "checked_paths": ["/tools2/demo-subpath/"],
                "findings": [
                    {
                        "project_id": "root",
                        "path": "/tools2/demo-subpath/",
                        "line": 1,
                        "tag": "a",
                        "attr": "href",
                        "url": "/api/demo",
                        "message": 'Runtime HTML emits root-relative `href="/api/demo"` outside deployment subpath `/tools2/demo-subpath`',
                    }
                ],
                "warnings": [],
                "audited_projects": ["root"],
                "projects": [{"project_id": "root", "checked_paths": ["/tools2/demo-subpath/"], "findings": [{"message": 'Runtime HTML emits root-relative `href="/api/demo"` outside deployment subpath `/tools2/demo-subpath`'}], "warnings": []}],
            }
            patches = self._base_patches(root, source_dir, repo_dir, args) + [
                mock.patch.object(
                    runner,
                    "subpath_prepare_subpath_sources",
                    return_value={
                        "plan": self._plan_payload(repo_dir),
                        "detection_evidence": [],
                        "proxy_mode": "preserve_prefix",
                        "rewritten_files": ["index.html"],
                        "auto_fixed_files": [],
                        "rewrite_report": {"changed_files": [], "applied_codes": [], "grouped_findings": {}},
                        "framework": "vite",
                        "static_subpath_audit": {
                            "framework": "vite",
                            "proxy_mode": "preserve_prefix",
                            "scanned_files": ["index.html"],
                            "findings": [],
                            "warnings": [],
                        },
                        "static_audit_attempts": [
                            {
                                "framework": "vite",
                                "proxy_mode": "preserve_prefix",
                                "scanned_files": ["index.html"],
                                "findings": [],
                                "warnings": [],
                            }
                        ],
                        "static_findings": [],
                    },
                ),
                mock.patch.object(runner, "validate_generated_files", return_value=([], [])),
                mock.patch.object(runner, "sync_generated_outputs_to_shared_repo", return_value=[]),
                mock.patch.object(runner, "command_exists", return_value=True),
                mock.patch.object(runner, "build_image", return_value=runner.CommandResult(args=["podman"], returncode=0, stdout="build ok")),
                mock.patch.object(runner, "inspect_image_id", return_value="sha256:demo"),
                mock.patch.object(runner, "parse_onboarding_run_spec", return_value={"container_port": 3000, "environment_variables": [], "env_file_hint": None, "start_command": None, "persistence_paths": [], "health_path_hint": "/"}),
                mock.patch.object(runner, "inspect_image_runtime_spec", return_value={"exposed_port": 3000, "env": [], "cmd": None, "entrypoint": None, "working_dir": None}),
                mock.patch.object(runner, "merge_run_spec", return_value={"container_port": 3000, "host_port": 8300, "environment_variables": [], "persistence_paths": [], "health_path_hint": "/"}),
                mock.patch.object(runner, "cleanup_container", return_value=None),
                mock.patch.object(runner, "run_container", return_value=runner.CommandResult(args=["podman"], returncode=0, stdout="container-123")),
                mock.patch.object(runner, "wait_for_container_ready", return_value=(True, None)),
                mock.patch.object(runner, "subpath_run_runtime_subpath_audit", return_value=runtime_audit),
                mock.patch.object(runner, "sync_athena_nginx_config", return_value={"status": "already_managed"}),
            ]
            exit_code = self._run_with_patches(patches)

            self.assertEqual(exit_code, 1)
            result_path = root / "jobs" / "demo-subpath" / "job-123" / "output" / "result.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "SUBPATH_RUNTIME_AUDIT_FAILED")
            self.assertEqual(payload["errors"], [runtime_audit["findings"][0]["message"]])
            self.assertEqual(payload["artifacts"]["subpath_runtime_audit"]["findings"], runtime_audit["findings"])
            self.assertEqual(payload["artifacts"]["subpath_runtime_audit"]["audited_projects"], ["root"])

    def test_runtime_phase_records_runtime_audit_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "incoming" / "demo-subpath"
            repo_dir = root / "repo-work"
            source_dir.mkdir(parents=True)
            repo_dir.mkdir(parents=True)
            (source_dir / "package.json").write_text('{"name":"demo-subpath"}\n', encoding="utf-8")
            (repo_dir / "Dockerfile").write_text("FROM node:22-alpine\n", encoding="utf-8")
            (repo_dir / "PROJECT_ONBOARDING.md").write_text("端口 `3000`\n", encoding="utf-8")

            args = self._make_args(source_dir, build=True, run=True)
            runtime_audit = {
                "checked_paths": ["/tools2/demo-subpath/"],
                "findings": [],
                "warnings": [],
                "audited_projects": ["root"],
                "projects": [{"project_id": "root", "checked_paths": ["/tools2/demo-subpath/"], "findings": [], "warnings": []}],
            }
            patches = self._base_patches(root, source_dir, repo_dir, args) + [
                mock.patch.object(
                    runner,
                    "subpath_prepare_subpath_sources",
                    return_value={
                        "plan": self._plan_payload(repo_dir),
                        "detection_evidence": [],
                        "proxy_mode": "preserve_prefix",
                        "rewritten_files": ["index.html"],
                        "auto_fixed_files": [],
                        "rewrite_report": {"changed_files": [], "applied_codes": [], "grouped_findings": {}},
                        "static_subpath_audit": {
                            "framework": "vite",
                            "proxy_mode": "preserve_prefix",
                            "scanned_files": ["index.html"],
                            "findings": [],
                            "warnings": [],
                        },
                        "static_audit_attempts": [
                            {
                                "framework": "vite",
                                "proxy_mode": "preserve_prefix",
                                "scanned_files": ["index.html"],
                                "findings": [],
                                "warnings": [],
                            }
                        ],
                        "static_findings": [],
                    },
                ),
                mock.patch.object(runner, "validate_generated_files", return_value=([], [])),
                mock.patch.object(runner, "sync_generated_outputs_to_shared_repo", return_value=[]),
                mock.patch.object(runner, "command_exists", return_value=True),
                mock.patch.object(runner, "build_image", return_value=runner.CommandResult(args=["podman"], returncode=0, stdout="build ok")),
                mock.patch.object(runner, "inspect_image_id", return_value="sha256:demo"),
                mock.patch.object(runner, "parse_onboarding_run_spec", return_value={"container_port": 3000, "environment_variables": [], "env_file_hint": None, "start_command": None, "persistence_paths": [], "health_path_hint": "/"}),
                mock.patch.object(runner, "inspect_image_runtime_spec", return_value={"exposed_port": 3000, "env": [], "cmd": None, "entrypoint": None, "working_dir": None}),
                mock.patch.object(runner, "merge_run_spec", return_value={"container_port": 3000, "host_port": 8300, "environment_variables": [], "persistence_paths": [], "health_path_hint": "/"}),
                mock.patch.object(runner, "cleanup_container", return_value=None),
                mock.patch.object(runner, "run_container", return_value=runner.CommandResult(args=["podman"], returncode=0, stdout="container-123")),
                mock.patch.object(runner, "wait_for_container_ready", return_value=(True, None)),
                mock.patch.object(runner, "subpath_run_runtime_subpath_audit", return_value=runtime_audit),
                mock.patch.object(runner, "sync_athena_nginx_config", return_value={"status": "already_managed"}),
            ]
            exit_code = self._run_with_patches(patches)

            self.assertEqual(exit_code, 0)
            result_path = root / "jobs" / "demo-subpath" / "job-123" / "output" / "result.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "RUN_SUCCEEDED")
            self.assertEqual(payload["artifacts"]["subpath_runtime_audit"]["findings"], runtime_audit["findings"])
            self.assertEqual(payload["artifacts"]["subpath_runtime_audit"]["audited_projects"], ["root"])
            self.assertEqual(payload["artifacts"]["subpath_runtime_audit"]["projects_with_findings"], [])
            self.assertEqual(payload["final_result"]["url"], "https://athena.agoralab.co/tools2/demo-subpath")
            self.assertEqual(payload["analysis_summary"]["subpath_runtime_audit_summary"]["findings"], 0)
            self.assertEqual(payload["analysis_summary"]["subpath_runtime_audit_summary"]["audited_projects"], 1)
            self.assertEqual(payload["analysis_summary"]["subpath_runtime_audit_summary"]["projects_with_findings"], 0)
            self.assertEqual(
                payload["analysis_summary"]["subpath_runtime_audit_summary"]["project_summaries"],
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

    def test_runtime_phase_can_skip_by_plan_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "incoming" / "demo-subpath"
            repo_dir = root / "repo-work"
            source_dir.mkdir(parents=True)
            repo_dir.mkdir(parents=True)
            (source_dir / "package.json").write_text('{"name":"demo-subpath"}\n', encoding="utf-8")
            (repo_dir / "Dockerfile").write_text("FROM node:22-alpine\n", encoding="utf-8")
            (repo_dir / "PROJECT_ONBOARDING.md").write_text("端口 `3000`\n", encoding="utf-8")

            args = self._make_args(source_dir, build=True, run=True)
            patches = self._base_patches(root, source_dir, repo_dir, args) + [
                mock.patch.object(
                    runner,
                    "subpath_prepare_subpath_sources",
                    return_value={
                        "plan": self._plan_payload(repo_dir, capabilities=["client_code", "config_adapter"]),
                        "detection_evidence": [],
                        "proxy_mode": "preserve_prefix",
                        "rewritten_files": ["index.html"],
                        "auto_fixed_files": [],
                        "static_subpath_audit": {"framework": "vite", "proxy_mode": "preserve_prefix", "scanned_files": [], "findings": [], "warnings": []},
                        "static_audit_attempts": [{"framework": "vite", "proxy_mode": "preserve_prefix", "scanned_files": [], "findings": [], "warnings": []}],
                        "static_findings": [],
                    },
                ),
                mock.patch.object(runner, "validate_generated_files", return_value=([], [])),
                mock.patch.object(runner, "sync_generated_outputs_to_shared_repo", return_value=[]),
                mock.patch.object(runner, "command_exists", return_value=True),
                mock.patch.object(runner, "build_image", return_value=runner.CommandResult(args=["podman"], returncode=0, stdout="build ok")),
                mock.patch.object(runner, "inspect_image_id", return_value="sha256:demo"),
                mock.patch.object(runner, "parse_onboarding_run_spec", return_value={"container_port": 3000, "environment_variables": [], "env_file_hint": None, "start_command": None, "persistence_paths": [], "health_path_hint": "/"}),
                mock.patch.object(runner, "inspect_image_runtime_spec", return_value={"exposed_port": 3000, "env": [], "cmd": None, "entrypoint": None, "working_dir": None}),
                mock.patch.object(runner, "merge_run_spec", return_value={"container_port": 3000, "host_port": 8300, "environment_variables": [], "persistence_paths": [], "health_path_hint": "/"}),
                mock.patch.object(runner, "cleanup_container", return_value=None),
                mock.patch.object(runner, "run_container", return_value=runner.CommandResult(args=["podman"], returncode=0, stdout="container-123")),
                mock.patch.object(runner, "wait_for_container_ready", return_value=(True, None)),
                mock.patch.object(runner, "subpath_run_runtime_subpath_audit", side_effect=AssertionError("runtime audit should be skipped")),
                mock.patch.object(runner, "sync_athena_nginx_config", return_value={"status": "already_managed"}),
            ]
            exit_code = self._run_with_patches(patches)

            self.assertEqual(exit_code, 0)
            result_path = root / "jobs" / "demo-subpath" / "job-123" / "output" / "result.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "RUN_SUCCEEDED")
            self.assertTrue(payload["artifacts"]["subpath_runtime_audit"]["skipped_by_capability"])
            self.assertEqual(payload["analysis_summary"]["subpath_runtime_audit_summary"]["findings"], 0)
            self.assertEqual(payload["artifacts"]["subpath_runtime_audit"]["project_count"], 1)
            self.assertEqual(payload["artifacts"]["subpath_runtime_audit"]["eligible_projects"], [])
            self.assertIsNone(payload["artifacts"]["subpath_runtime_audit"]["default_runtime_project"])
            self.assertEqual(payload["analysis_summary"]["subpath_runtime_audit_summary"]["entry_path_candidates"], 0)
            self.assertEqual(payload["analysis_summary"]["subpath_runtime_audit_summary"]["audited_projects"], 0)
            self.assertEqual(payload["analysis_summary"]["subpath_runtime_audit_summary"]["projects_with_findings"], 0)
            self.assertEqual(payload["analysis_summary"]["subpath_runtime_audit_summary"]["project_summaries"], [])

    def test_build_output_audit_shadow_mode_is_recorded_without_failing_build(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "incoming" / "demo-subpath"
            repo_dir = root / "repo-work"
            source_dir.mkdir(parents=True)
            repo_dir.mkdir(parents=True)
            (source_dir / "package.json").write_text('{"name":"demo-subpath"}\n', encoding="utf-8")
            (repo_dir / "Dockerfile").write_text("FROM node:22-alpine\n", encoding="utf-8")
            (repo_dir / "PROJECT_ONBOARDING.md").write_text("端口 `3000`\n", encoding="utf-8")

            args = self._make_args(source_dir, build=True, run=False)
            patches = self._base_patches(root, source_dir, repo_dir, args) + [
                mock.patch.object(
                    runner,
                    "subpath_prepare_subpath_sources",
                    return_value={
                        "plan": self._plan_payload(repo_dir, capabilities=[]),
                        "detection_evidence": [],
                        "proxy_mode": "preserve_prefix",
                        "rewritten_files": ["index.html"],
                        "auto_fixed_files": [],
                        "static_subpath_audit": {"framework": "vite", "proxy_mode": "preserve_prefix", "scanned_files": [], "findings": [], "warnings": []},
                        "static_audit_attempts": [{"framework": "vite", "proxy_mode": "preserve_prefix", "scanned_files": [], "findings": [], "warnings": []}],
                        "static_findings": [],
                    },
                ),
                mock.patch.object(runner, "validate_generated_files", return_value=([], [])),
                mock.patch.object(runner, "sync_generated_outputs_to_shared_repo", return_value=[]),
                mock.patch.object(runner, "command_exists", return_value=True),
                mock.patch.object(runner, "build_image", return_value=runner.CommandResult(args=["podman"], returncode=0, stdout="build ok")),
                mock.patch.object(runner, "inspect_image_id", return_value="sha256:demo"),
                mock.patch.object(
                    runner,
                    "subpath_run_build_output_subpath_audit",
                    return_value={
                        "output_roots": ["dist"],
                        "findings": [{"code": "build_output_html_root_relative_url"}],
                        "summary": {
                            "policy_version": "v1",
                            "total_findings": 1,
                            "findings_by_code": {"build_output_html_root_relative_url": 1},
                            "findings_by_category": {"html": 1},
                            "findings_by_severity": {"error": 1},
                            "enforcement_candidates": {"build_output_html_root_relative_url": "future_blocker"},
                            "enforcement_candidate_counts": {"future_blocker": 1},
                            "recommended_mode": "candidate_for_enforce",
                            "output_roots_count": 1,
                            "files_with_findings": ["index.html"],
                        },
                        "warnings": [],
                    },
                ),
            ]
            exit_code = self._run_with_patches(patches)

            self.assertEqual(exit_code, 0)
            result_path = root / "jobs" / "demo-subpath" / "job-123" / "output" / "result.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "COMPLETED_WITH_BUILD")
            self.assertIn("subpath_build_output_audit", payload["artifacts"])
            self.assertEqual(
                payload["artifacts"]["subpath_build_output_audit"]["summary"]["findings_by_code"],
                {"build_output_html_root_relative_url": 1},
            )
            self.assertEqual(
                payload["analysis_summary"]["subpath_build_output_audit_summary"]["findings_by_code"],
                {"build_output_html_root_relative_url": 1},
            )
            self.assertEqual(
                payload["analysis_summary"]["subpath_build_output_audit_summary"]["findings_by_category"],
                {"html": 1},
            )
            self.assertEqual(
                payload["analysis_summary"]["subpath_build_output_audit_summary"]["policy_version"],
                "v1",
            )
            self.assertEqual(
                payload["analysis_summary"]["subpath_build_output_audit_summary"]["findings_by_severity"],
                {"error": 1},
            )
            self.assertEqual(
                payload["analysis_summary"]["subpath_build_output_audit_summary"]["recommended_mode"],
                "candidate_for_enforce",
            )
            self.assertTrue(any("build_output_html_root_relative_url=1" in item for item in payload["warnings"]))

    def test_build_output_audit_uses_subpath_declaration_policy_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "incoming" / "demo-subpath"
            repo_dir = root / "repo-work"
            source_dir.mkdir(parents=True)
            repo_dir.mkdir(parents=True)
            (source_dir / "package.json").write_text('{"name":"demo-subpath"}\n', encoding="utf-8")
            (repo_dir / "Dockerfile").write_text("FROM node:22-alpine\n", encoding="utf-8")
            (repo_dir / "PROJECT_ONBOARDING.md").write_text("端口 `3000`\n", encoding="utf-8")
            (repo_dir / ".ka").mkdir(parents=True)
            (repo_dir / ".ka" / "subpath.json").write_text(
                json.dumps(
                    {
                        "build_output_policy": {
                            "build_output_client_root_relative_url": {
                                "severity": "warning",
                                "enforcement_candidate": "future_blocker",
                            }
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            args = self._make_args(source_dir, build=True, run=False)
            patches = self._base_patches(root, source_dir, repo_dir, args) + [
                mock.patch.object(
                    runner,
                    "subpath_prepare_subpath_sources",
                    return_value={
                        "plan": self._plan_payload(repo_dir, capabilities=["build_output_expected", "html_entry", "client_code", "config_adapter"]),
                        "detection_evidence": [],
                        "proxy_mode": "preserve_prefix",
                        "rewritten_files": ["index.html"],
                        "auto_fixed_files": [],
                        "static_subpath_audit": {"framework": "vite", "proxy_mode": "preserve_prefix", "scanned_files": [], "findings": [], "warnings": []},
                        "static_audit_attempts": [{"framework": "vite", "proxy_mode": "preserve_prefix", "scanned_files": [], "findings": [], "warnings": []}],
                        "static_findings": [],
                    },
                ),
                mock.patch.object(runner, "validate_generated_files", return_value=([], [])),
                mock.patch.object(runner, "sync_generated_outputs_to_shared_repo", return_value=[]),
                mock.patch.object(runner, "command_exists", return_value=True),
                mock.patch.object(runner, "build_image", return_value=runner.CommandResult(args=["podman"], returncode=0, stdout="build ok")),
                mock.patch.object(runner, "inspect_image_id", return_value="sha256:demo"),
                mock.patch.object(
                    runner,
                    "subpath_run_build_output_subpath_audit",
                    side_effect=lambda repo_dir_arg, project_slug_arg, plan_arg, policy_overrides=None, **kwargs: {
                        "output_roots": ["dist"],
                        "findings": [{"code": "build_output_client_root_relative_url", "severity": "warning"}],
                        "summary": {
                            "policy_version": "v1",
                            "total_findings": 1,
                            "findings_by_code": {"build_output_client_root_relative_url": 1},
                            "findings_by_category": {"client": 1},
                            "findings_by_severity": {"warning": 1},
                            "enforcement_candidates": {"build_output_client_root_relative_url": policy_overrides["build_output_client_root_relative_url"]["enforcement_candidate"]},
                            "enforcement_candidate_counts": {"future_blocker": 1},
                            "recommended_mode": "candidate_for_enforce",
                            "output_roots_count": 1,
                            "files_with_findings": ["assets/app.js"],
                        },
                        "warnings": [],
                    },
                ),
            ]
            exit_code = self._run_with_patches(patches)

            self.assertEqual(exit_code, 0)
            result_path = root / "jobs" / "demo-subpath" / "job-123" / "output" / "result.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["analysis_summary"]["subpath_build_output_audit_summary"]["findings_by_severity"], {"warning": 1})
            self.assertEqual(
                payload["analysis_summary"]["subpath_build_output_audit_summary"]["enforcement_candidates"],
                {"build_output_client_root_relative_url": "future_blocker"},
            )
            self.assertIn("subpath_declaration", payload["artifacts"])
            self.assertEqual(
                payload["artifacts"]["subpath_declaration_summary"],
                {"project_count": 0, "has_build_output_policy": True, "declared_default_project": None},
            )
            self.assertEqual(
                payload["analysis_summary"]["subpath_declaration_summary"],
                {"project_count": 0, "has_build_output_policy": True, "declared_default_project": None},
            )

    def test_build_output_audit_can_fail_when_policy_requests_enforce(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "incoming" / "demo-subpath"
            repo_dir = root / "repo-work"
            source_dir.mkdir(parents=True)
            repo_dir.mkdir(parents=True)
            (source_dir / "package.json").write_text('{"name":"demo-subpath"}\n', encoding="utf-8")
            (repo_dir / "Dockerfile").write_text("FROM node:22-alpine\n", encoding="utf-8")
            (repo_dir / "PROJECT_ONBOARDING.md").write_text("端口 `3000`\n", encoding="utf-8")
            (repo_dir / ".ka").mkdir(parents=True)
            (repo_dir / ".ka" / "subpath.json").write_text(
                json.dumps(
                    {
                        "build_output_policy": {
                            "build_output_client_root_relative_url": {
                                "enforcement_candidate": "enforce",
                            }
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            args = self._make_args(source_dir, build=True, run=False)
            patches = self._base_patches(root, source_dir, repo_dir, args) + [
                mock.patch.object(
                    runner,
                    "subpath_prepare_subpath_sources",
                    return_value={
                        "plan": self._plan_payload(repo_dir, capabilities=["build_output_expected", "html_entry", "client_code", "config_adapter"]),
                        "detection_evidence": [],
                        "strategy": {"framework": "vite", "proxy_mode": "preserve_prefix", "adapter": "vite"},
                        "proxy_mode": "preserve_prefix",
                        "rewritten_files": ["index.html"],
                        "auto_fixed_files": [],
                        "static_subpath_audit": {"framework": "vite", "proxy_mode": "preserve_prefix", "scanned_files": [], "findings": [], "warnings": []},
                        "static_audit_attempts": [{"framework": "vite", "proxy_mode": "preserve_prefix", "scanned_files": [], "findings": [], "warnings": []}],
                        "static_findings": [],
                    },
                ),
                mock.patch.object(runner, "validate_generated_files", return_value=([], [])),
                mock.patch.object(runner, "sync_generated_outputs_to_shared_repo", return_value=[]),
                mock.patch.object(runner, "command_exists", return_value=True),
                mock.patch.object(runner, "build_image", return_value=runner.CommandResult(args=["podman"], returncode=0, stdout="build ok")),
                mock.patch.object(runner, "inspect_image_id", return_value="sha256:demo"),
                mock.patch.object(
                    runner,
                    "subpath_run_build_output_subpath_audit",
                    return_value={
                        "output_roots": ["dist"],
                        "findings": [{"code": "build_output_client_root_relative_url", "message": "client asset escaped subpath"}],
                        "summary": {
                            "policy_version": "v1",
                            "total_findings": 1,
                            "findings_by_code": {"build_output_client_root_relative_url": 1},
                            "findings_by_category": {"client": 1},
                            "findings_by_severity": {"error": 1},
                            "enforcement_candidates": {"build_output_client_root_relative_url": "enforce"},
                            "enforcement_candidate_counts": {"enforce": 1},
                            "recommended_mode": "enforce",
                            "output_roots_count": 1,
                            "files_with_findings": ["assets/app.js"],
                        },
                        "warnings": [],
                    },
                ),
            ]
            exit_code = self._run_with_patches(patches)

            self.assertEqual(exit_code, 1)
            result_path = root / "jobs" / "demo-subpath" / "job-123" / "output" / "result.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "SUBPATH_BUILD_OUTPUT_AUDIT_FAILED")
            self.assertEqual(payload["errors"], ["client asset escaped subpath"])

    def test_main_passes_plan_only_subpath_dependencies_into_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "incoming" / "demo-subpath"
            repo_dir = root / "repo-work"
            source_dir.mkdir(parents=True)
            repo_dir.mkdir(parents=True)
            (source_dir / "package.json").write_text('{"name":"demo-subpath"}\n', encoding="utf-8")
            (repo_dir / "Dockerfile").write_text("FROM node:22-alpine\n", encoding="utf-8")
            (repo_dir / "PROJECT_ONBOARDING.md").write_text("端口 `3000`\n", encoding="utf-8")

            args = self._make_args(source_dir, build=False, run=False)

            def fake_prepare(repo_dir_arg, project_slug_arg, **kwargs):
                self.assertIn("build_subpath_plan", kwargs)
                self.assertIn("apply_subpath_rewrites", kwargs)
                self.assertIn("run_static_subpath_audit", kwargs)
                self.assertIn("auto_fix_subpath_issues", kwargs)
                self.assertNotIn("detect_subpath_strategy", kwargs)
                return {
                    "plan": self._plan_payload(repo_dir),
                    "proxy_mode": "preserve_prefix",
                    "rewritten_files": [],
                    "auto_fixed_files": [],
                    "static_subpath_audit": {
                        "framework": "vite",
                        "proxy_mode": "preserve_prefix",
                        "scanned_files": [],
                        "findings": [],
                        "warnings": [],
                    },
                    "static_audit_attempts": [
                        {
                            "framework": "vite",
                            "proxy_mode": "preserve_prefix",
                            "scanned_files": [],
                            "findings": [],
                            "warnings": [],
                        }
                    ],
                    "static_findings": [],
                }

            patches = self._base_patches(root, source_dir, repo_dir, args) + [
                mock.patch.object(runner, "subpath_prepare_subpath_sources", side_effect=fake_prepare),
                mock.patch.object(runner, "validate_generated_files", return_value=([], [])),
                mock.patch.object(runner, "sync_generated_outputs_to_shared_repo", return_value=[]),
            ]
            exit_code = self._run_with_patches(patches)

            self.assertEqual(exit_code, 0)

    def test_git_source_post_fetch_exception_is_not_misclassified_as_fetch_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "incoming" / "demo-subpath"
            repo_dir = root / "repo-work"
            source_dir.mkdir(parents=True)
            repo_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text('{"name":"demo-subpath"}\n', encoding="utf-8")

            args = argparse.Namespace(
                source_type="git",
                source="https://github.com/example/demo-subpath.git",
                ref="main",
                tool_id=None,
                job_id="job-123",
                image_name=None,
                build=False,
                run=False,
                host_port=None,
            )

            shared_repo_dir = root / "jobs" / "demo-subpath" / "repo"
            shared_repo_metadata_path = root / "jobs" / "demo-subpath" / "repo-state.json"
            patches = [
                mock.patch.object(runner.RunnerArgumentParser, "parse_args", return_value=args),
                mock.patch.object(runner, "JOBS_DIR", root / "jobs"),
                mock.patch.object(runner, "CODEX_HOME_CACHE_DIR", root / "codex-home-cache"),
                mock.patch.object(runner, "LOCAL_OFFICIAL_IMAGES_CACHE_PATH", root / "codex-home-cache" / "local-official-images.txt"),
                mock.patch.object(runner, "DEFAULT_CODEX_HOME_SEED", root / "seed-codex-home"),
                mock.patch.object(runner, "list_local_official_docker_images_from_env", return_value=[]),
                mock.patch.object(runner, "prepare_default_podman_environment", return_value=({"AUTOMATION_PODMAN_MODE": "default"}, {"podman_mode": "default"})),
                mock.patch.object(runner, "prepare_shared_repo", return_value=(shared_repo_dir, False, [])),
                mock.patch.object(runner, "prepare_job_repo_from_shared", return_value=repo_dir),
                mock.patch.object(runner, "collect_repo_analysis", return_value={}),
                mock.patch.object(runner, "summarize_analysis", return_value={"project_slug": "demo-subpath"}),
                mock.patch.object(runner, "subpath_prepare_subpath_sources", side_effect=RuntimeError("synthetic post-fetch failure")),
                mock.patch.object(runner, "emit_final_result", side_effect=lambda result, result_path: runner.write_json(result_path, {**result, "final_result": runner.build_final_result(result)})),
            ]

            exit_code = self._run_with_patches(patches)

            self.assertEqual(exit_code, 1)
            result_path = root / "jobs" / "demo-subpath" / "job-123" / "output" / "result.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "FAILED")
            self.assertEqual(payload["errors"], ["synthetic post-fetch failure"])


if __name__ == "__main__":
    unittest.main()
