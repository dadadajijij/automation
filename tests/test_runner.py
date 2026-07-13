import tempfile
import unittest
from pathlib import Path
import json
from unittest import mock

import runner
from subpath.apply import apply_subpath_rewrites as subpath_apply_subpath_rewrites, auto_fix_subpath_issues as subpath_auto_fix_subpath_issues
from subpath.static_audit import run_static_subpath_audit as subpath_run_static_subpath_audit


class RunnerProjectSlugTests(unittest.TestCase):
    def _build_plan(self, repo_dir: Path):
        return runner.subpath_build_subpath_plan(
            repo_dir,
            parse_package_json=runner.parse_package_json,
            detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
            read_text_if_exists=runner.read_text_if_exists,
            collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
            workspace_frontend_package_dirs_fn=runner.workspace_frontend_package_dirs,
            vite_project_roots_fn=runner.vite_project_roots,
            collect_matching_files=runner.collect_matching_files,
            discover_workspace_packages=runner.discover_workspace_packages,
        )

    def test_json_safe_converts_path_set_and_dataclass(self) -> None:
        payload = {
            "path": Path("/tmp/demo"),
            "items": {"b", "a"},
            "command": runner.CommandResult(args=["echo", "ok"], returncode=0, stdout="ok"),
        }

        safe = runner.json_safe(payload)

        self.assertEqual(safe["path"], "/tmp/demo")
        self.assertEqual(safe["items"], ["a", "b"])
        self.assertEqual(
            safe["command"],
            {"args": ["echo", "ok"], "returncode": 0, "stdout": "ok"},
        )
        json.dumps(safe)

    def test_json_dumps_safe_handles_non_serializable_objects(self) -> None:
        text = runner.json_dumps_safe({"path": Path("/tmp/demo"), "items": {"b", "a"}})
        self.assertIn('"/tmp/demo"', text)
        self.assertIn('"items"', text)

    def test_build_access_url_does_not_require_tool_id(self) -> None:
        url = runner.build_access_url({"project_slug": "agora-token-generator"})
        self.assertEqual(url, "https://athena.agoralab.co/tools2/agora-token-generator")

    def test_build_access_url_returns_none_without_project_slug(self) -> None:
        self.assertIsNone(runner.build_access_url({"tool_id": "tool-abc"}))

    def test_build_deployment_base_path_uses_project_slug(self) -> None:
        self.assertEqual(
            runner.build_deployment_base_path("agora-token-generator"),
            "/tools2/agora-token-generator",
        )

    def test_normalize_local_copy_slug_collapses_space_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "agora-token-generator 2"
            source_dir.mkdir()
            self.assertEqual(
                runner.derive_project_slug("local", str(source_dir)),
                "agora-token-generator",
            )

    def test_normalize_local_copy_slug_collapses_dash_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "agora-token-generator-2"
            source_dir.mkdir()
            self.assertEqual(
                runner.derive_project_slug("local", str(source_dir)),
                "agora-token-generator",
            )

    def test_local_project_slug_prefers_package_json_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "agora-token-generator-2"
            source_dir.mkdir()
            (source_dir / "package.json").write_text(
                '{\n  "name": "agora-token-generator"\n}\n',
                encoding="utf-8",
            )
            self.assertEqual(
                runner.derive_project_slug("local", str(source_dir)),
                "agora-token-generator",
            )

    def test_local_project_slug_prefers_pyproject_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "audioqas 3"
            source_dir.mkdir()
            (source_dir / "pyproject.toml").write_text(
                '[project]\nname = "audioqas"\n',
                encoding="utf-8",
            )
            self.assertEqual(
                runner.derive_project_slug("local", str(source_dir)),
                "audioqas",
            )

    def test_render_nginx_add_conf_uses_project_slug_path(self) -> None:
        conf = runner.render_nginx_add_conf("agora-token-generator", 3000)
        self.assertIn("location = /tools2/agora-token-generator {", conf)
        self.assertIn("location ^~ /tools2/agora-token-generator/ {", conf)
        self.assertIn("proxy_set_header X-Forwarded-Prefix /tools2/agora-token-generator;", conf)
        self.assertIn("proxy_pass http://127.0.0.1:3000/;", conf)

    def test_render_nginx_add_conf_preserves_prefix_for_nextjs_mode(self) -> None:
        conf = runner.render_nginx_add_conf("agora-rest-api-debugger", 8004, proxy_mode="preserve_prefix")
        self.assertIn("location = /tools2/agora-rest-api-debugger {", conf)
        self.assertIn("proxy_pass http://127.0.0.1:8004/tools2/agora-rest-api-debugger;", conf)
        self.assertIn("proxy_pass http://127.0.0.1:8004/tools2/agora-rest-api-debugger/;", conf)
        self.assertNotIn("return 301 /tools2/agora-rest-api-debugger/;", conf)

    def test_upsert_tool_nginx_into_tools_conf_inserts_managed_block(self) -> None:
        updated, status = runner.upsert_tool_nginx_into_tools_conf("", "audioqas", 8001)
        self.assertEqual(status, "inserted_managed")
        self.assertIn("# BEGIN KA TOOL audioqas", updated)
        self.assertIn("location = /tools2/audioqas {", updated)

    def test_upsert_tool_nginx_into_tools_conf_is_idempotent_for_managed_block(self) -> None:
        original = runner.render_athena_managed_tool_block("audioqas", 8001, indent="")
        first, first_status = runner.upsert_tool_nginx_into_tools_conf(original, "audioqas", 8001)
        second, second_status = runner.upsert_tool_nginx_into_tools_conf(first, "audioqas", 8001)
        self.assertEqual(first_status, "already_managed")
        self.assertEqual(second_status, "already_managed")
        self.assertEqual(first, second)

    def test_upsert_tool_nginx_into_tools_conf_updates_existing_managed_block_when_mode_changes(self) -> None:
        original = runner.render_athena_managed_tool_block("agora-rest-api-debugger", 8004, indent="")
        updated, status = runner.upsert_tool_nginx_into_tools_conf(
            original,
            "agora-rest-api-debugger",
            8004,
            proxy_mode="preserve_prefix",
        )
        self.assertEqual(status, "updated_managed")
        self.assertIn("proxy_pass http://127.0.0.1:8004/tools2/agora-rest-api-debugger/;", updated)
        self.assertNotIn("proxy_pass http://127.0.0.1:8004/;", updated)

    def test_upsert_tool_nginx_into_tools_conf_appends_after_existing_block(self) -> None:
        original = runner.render_athena_managed_tool_block("mos-video-compare", 8006, indent="")
        updated, status = runner.upsert_tool_nginx_into_tools_conf(original, "loga", 8005)
        self.assertEqual(status, "inserted_managed")
        self.assertIn("# BEGIN KA TOOL mos-video-compare", updated)
        self.assertIn("# BEGIN KA TOOL loga", updated)
        self.assertLess(updated.index("# BEGIN KA TOOL mos-video-compare"), updated.index("# BEGIN KA TOOL loga"))

    def test_build_sudo_command_prefixes_non_interactive_sudo(self) -> None:
        self.assertEqual(
            runner.build_sudo_command(["nginx", "-t"]),
            ["sudo", "-n", "nginx", "-t"],
        )

    def test_athena_backup_path_uses_timestamp_in_same_directory(self) -> None:
        backup_path = runner.ATHENA_NGINX_CONFIG_PATH.with_name(
            f"{runner.ATHENA_NGINX_CONFIG_PATH.stem}_20260607T010203Z{runner.ATHENA_NGINX_CONFIG_PATH.suffix}"
        )
        self.assertEqual(str(backup_path), "/etc/nginx/sites-available/tools_20260607T010203Z.conf")

    def test_local_source_signature_ignores_temp_source_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_a = root / "tool-deploy-a" / "agora-token-generator"
            source_b = root / "tool-deploy-b" / "agora-token-generator"
            source_a.mkdir(parents=True)
            source_b.mkdir(parents=True)
            (source_a / "package.json").write_text('{"name":"agora-token-generator"}\n', encoding="utf-8")
            (source_b / "package.json").write_text('{"name":"agora-token-generator"}\n', encoding="utf-8")
            signature_a = runner.local_source_signature(source_a, root / "jobs-a" / "repo")
            signature_b = runner.local_source_signature(source_b, root / "jobs-b" / "repo")
            self.assertEqual(signature_a, signature_b)
            self.assertNotIn("source", signature_a)

    def test_prepare_shared_repo_carries_forward_existing_dockerfile_for_local_source_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            shared_repo_dir = root / "jobs" / "agora-token-generator" / "repo"
            shared_repo_metadata_path = root / "jobs" / "agora-token-generator" / "repo-state.json"
            source_dir = root / "incoming" / "agora-token-generator"
            source_dir.mkdir(parents=True)
            (source_dir / "package.json").write_text('{"name":"agora-token-generator"}\n', encoding="utf-8")
            shared_repo_dir.mkdir(parents=True)
            (shared_repo_dir / "Dockerfile").write_text("FROM node:22-alpine\n", encoding="utf-8")
            runner.write_json(shared_repo_metadata_path, {"source_type": "local", "fingerprint": "old"})

            repo_dir, reused, carried = runner.prepare_shared_repo(
                source_type="local",
                source=str(source_dir),
                ref=None,
                fetch_log_path=None,
                shared_repo_dir=shared_repo_dir,
                shared_repo_metadata_path=shared_repo_metadata_path,
            )

            self.assertEqual(repo_dir, shared_repo_dir)
            self.assertFalse(reused)
            self.assertEqual(carried, ["Dockerfile"])
            self.assertTrue((shared_repo_dir / "Dockerfile").exists())
            self.assertEqual((shared_repo_dir / "Dockerfile").read_text(encoding="utf-8"), "FROM node:22-alpine\n")

    def test_is_git_tracked_file_distinguishes_generated_untracked_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            runner.run_command(["git", "init"], cwd=repo_dir)
            runner.run_command(["git", "config", "user.email", "test@example.com"], cwd=repo_dir)
            runner.run_command(["git", "config", "user.name", "Test User"], cwd=repo_dir)
            tracked = repo_dir / "README.md"
            untracked = repo_dir / "Dockerfile"
            tracked.write_text("# demo\n", encoding="utf-8")
            untracked.write_text("FROM node:22-alpine\n", encoding="utf-8")
            runner.run_command(["git", "add", "README.md"], cwd=repo_dir)
            runner.run_command(["git", "commit", "-m", "init"], cwd=repo_dir)

            self.assertTrue(runner.is_git_tracked_file(repo_dir, tracked))
            self.assertFalse(runner.is_git_tracked_file(repo_dir, untracked))

    def test_determine_generation_mode_only_generates_onboarding_when_dockerfile_exists(self) -> None:
        prebuilt_outputs, generation_mode = runner.determine_generation_mode(True, False)
        self.assertFalse(prebuilt_outputs)
        self.assertEqual(generation_mode, "onboarding_only")

    def test_determine_generation_mode_skips_codex_only_when_both_outputs_exist(self) -> None:
        prebuilt_outputs, generation_mode = runner.determine_generation_mode(True, True)
        self.assertTrue(prebuilt_outputs)
        self.assertEqual(generation_mode, "both")

    def test_determine_generation_mode_ignores_untracked_git_generated_outputs(self) -> None:
        prebuilt_outputs, generation_mode = runner.determine_generation_mode(
            True,
            True,
            existing_dockerfile_tracked=False,
            existing_onboarding_tracked=False,
        )
        self.assertFalse(prebuilt_outputs)
        self.assertEqual(generation_mode, "both")

    def test_raw_runner_invocation_log_line_contains_original_argv(self) -> None:
        argv = ["--job-id", "deploy-123", "--tool-id", "tool-abc", "--source-type", "local", "--source", "/tmp/src"]
        line = f'raw_runner_invocation argv={json.dumps(argv, ensure_ascii=False)} cwd=/home/devops/ka/automation'
        self.assertIn('"--source"', line)
        self.assertIn('"/tmp/src"', line)

    def test_runtime_environment_log_line_contains_path_and_codex_path_fields(self) -> None:
        line = "runtime_environment path=/usr/bin:/bin codex_path=/home/devops/.nvm/versions/node/v24.16.0/bin/codex"
        self.assertIn("path=/usr/bin:/bin", line)
        self.assertIn("codex_path=/home/devops/.nvm/versions/node/v24.16.0/bin/codex", line)

    def test_build_runtime_rules_for_node_stays_compact_but_keeps_critical_constraints(self) -> None:
        rules = runner.build_runtime_rules(
            {
                "service_runtime": "node",
                "python_entry_command": None,
                "node_entry_command": "node scripts/server.js",
                "requires_python": None,
                "package_scripts": ["dev", "build"],
                "detected_port": 3000,
                "has_nextjs_ts_config": False,
                "package_manager": "npm",
                "build_command_candidates": [],
                "requires_build_step": False,
                "runtime_build_artifact_paths": [],
            }
        )
        self.assertIn("这是 Node 运行时项目时", rules)
        self.assertIn("npm ci", rules)
        self.assertIn("JSON-form CMD", rules)
        self.assertIn("EXPOSE 3000", rules)

    def test_build_runtime_rules_mentions_required_build_and_candidates(self) -> None:
        rules = runner.build_runtime_rules(
            {
                "service_runtime": "node",
                "python_entry_command": None,
                "node_entry_command": "npm run start --workspace=backend",
                "requires_python": None,
                "package_scripts": ["build", "start"],
                "detected_port": 44002,
                "has_nextjs_ts_config": False,
                "package_manager": "npm",
                "required_build_commands": ["npm run build --workspace=frontend"],
                "build_command_candidates": ["npm run build --workspace=frontend"],
                "requires_build_step": True,
                "runtime_build_artifact_paths": ["frontend/dist"],
            }
        )
        self.assertIn("最低必需构建命令", rules)
        self.assertIn("构建命令候选", rules)
        self.assertIn("npm run build --workspace=frontend", rules)
        self.assertIn("不要为了保险把所有候选 build 命令都用 `&&` 串起来全部执行", rules)
        self.assertIn("运行时依赖预构建产物", rules)
        self.assertIn("frontend/dist", rules)

    def test_detect_node_entrypoint_reads_script_target_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            server_path = repo_dir / "server.js"
            server_path.write_text("const PORT = process.env.PORT || 3010;\n", encoding="utf-8")

            entry_path, command, entry_text = runner.detect_node_entrypoint(
                repo_dir,
                {"start": "node server.js"},
            )

            self.assertEqual(entry_path, server_path)
            self.assertEqual(command, "node server.js")
            self.assertEqual(entry_text, "const PORT = process.env.PORT || 3010;\n")

    def test_detect_port_from_text_recognizes_node_port_fallback_constant(self) -> None:
        text = "const PORT = process.env.PORT || 3010;\napp.listen(PORT, () => {})\n"
        self.assertEqual(runner.detect_port_from_text(text), 3010)

    def test_build_codex_prompt_includes_repo_facts_lines(self) -> None:
        with mock.patch.object(
            runner,
            "collect_repo_analysis",
            return_value={
                "service_runtime": "node",
                "python_entry_command": None,
                "node_entry_command": "node scripts/server.js",
                "requires_python": None,
                "package_scripts": ["dev"],
                "detected_port": 3000,
                "has_nextjs_ts_config": False,
                "package_manager": "npm",
                "build_command_candidates": [],
                "requires_build_step": False,
                "runtime_build_artifact_paths": [],
                "facts": [
                    '- source.url: `https://example.com/demo.git`',
                    '- runtime: `node`',
                    '- node_entry: `node scripts/server.js`',
                ],
            },
        ):
            prompt = runner.build_codex_prompt(
                "git",
                "https://example.com/demo.git",
                "main",
                Path("/tmp/demo"),
            )

        self.assertIn("- runtime: `node`", prompt)
        self.assertIn("- node_entry: `node scripts/server.js`", prompt)

    def test_build_codex_prompt_appends_validation_feedback_block(self) -> None:
        with mock.patch.object(
            runner,
            "collect_repo_analysis",
            return_value={
                "service_runtime": "node",
                "python_entry_command": None,
                "node_entry_command": "node scripts/server.js",
                "requires_python": None,
                "package_scripts": ["build", "start"],
                "detected_port": 3000,
                "has_nextjs_ts_config": False,
                "package_manager": "npm",
                "build_command_candidates": ["npm run build"],
                "requires_build_step": True,
                "runtime_build_artifact_paths": ["dist"],
                "facts": ['- runtime: `node`'],
            },
        ):
            prompt = runner.build_codex_prompt(
                "git",
                "https://example.com/demo.git",
                "main",
                Path("/tmp/demo"),
                validation_findings=["Dockerfile is missing an application build step even though package.json.scripts.build exists"],
                validation_warnings=["PROJECT_ONBOARDING.md still contains unresolved confirmation items"],
            )

        self.assertIn("上一次生成未通过校验", prompt)
        self.assertIn("Dockerfile is missing an application build step", prompt)
        self.assertIn("PROJECT_ONBOARDING.md still contains unresolved confirmation items", prompt)

    def test_resolve_project_port_mapping_allocates_next_host_port_from_8003(self) -> None:
        mapping = {
            "ka-tools": {"port": "3000:3000"},
            "audioqas": {"port": "8001:8000"},
            "agora-token-generator": {"port": "8002:3010"},
        }
        with mock.patch.object(runner, "load_project_port_configs", return_value=dict(mapping)):
            saved: dict[str, dict[str, object]] = {}

            def capture(updated: dict[str, dict[str, object]]) -> None:
                saved.update(updated)

            with mock.patch.object(runner, "save_project_port_configs", side_effect=capture):
                host_port, container_port = runner.resolve_project_port_mapping("next-tool", 9000)

        self.assertEqual(host_port, 8003)
        self.assertEqual(container_port, 9000)
        self.assertEqual(saved["next-tool"]["port"], "8003:9000")

    def test_load_project_port_configs_returns_only_object_entries_with_valid_ports(self) -> None:
        payload = {
            "audioqas": {
                "port": "8001:8000",
                "volumes": ["/host/cache:/model-cache"],
                "env": {"HF_HOME": "/model-cache/huggingface"},
            },
            "ka-tools": {
                "port": "3000:3000",
                "env_file": "/host/.env.local",
                "volumes": ["/host/data:/app/data"],
            },
            "broken-tool": "8008:8080",
        }
        with mock.patch.object(runner, "read_json", return_value=payload):
            mapping = runner.load_project_port_configs()

        self.assertEqual(
            mapping,
            {
                "audioqas": {
                    "port": "8001:8000",
                    "volumes": ["/host/cache:/model-cache"],
                    "env": {"HF_HOME": "/model-cache/huggingface"},
                },
                "ka-tools": {
                    "port": "3000:3000",
                    "env_file": "/host/.env.local",
                    "volumes": ["/host/data:/app/data"],
                },
            },
        )

    def test_save_project_port_configs_preserves_existing_runtime_overrides(self) -> None:
        payload = {
            "audioqas": {
                "port": "8001:8000",
                "volumes": ["/host/cache:/model-cache"],
                "env": {"HF_HOME": "/model-cache/huggingface"},
            },
            "ka-tools": {
                "port": "3000:3000",
                "env_file": "/host/.env.local",
                "volumes": ["/host/data:/app/data"],
            },
        }
        saved_text: dict[str, str] = {}

        def capture(_path: Path, content: str) -> None:
            saved_text["content"] = content

        with mock.patch.object(runner, "read_json", return_value=payload):
            with mock.patch.object(runner, "write_text", side_effect=capture):
                runner.save_project_port_configs(
                    {
                        "audioqas": {
                            "port": "8101:8000",
                            "volumes": ["/host/cache:/model-cache"],
                            "env": {"HF_HOME": "/model-cache/huggingface"},
                        },
                        "next-tool": {
                            "port": "8003:9000",
                        },
                    }
                )

        written = json.loads(saved_text["content"])
        self.assertEqual(written["audioqas"]["port"], "8101:8000")
        self.assertEqual(written["audioqas"]["volumes"], ["/host/cache:/model-cache"])
        self.assertEqual(written["audioqas"]["env"], {"HF_HOME": "/model-cache/huggingface"})
        self.assertEqual(written["next-tool"]["port"], "8003:9000")

    def test_load_project_runtime_overrides_supports_env_file(self) -> None:
        payload = {
            "ka-tools": {
                "port": "3000:3000",
                "env_file": "/host/.env.local",
                "volumes": ["/host/data:/app/data"],
            }
        }
        with mock.patch.object(runner, "read_json", return_value=payload):
            overrides = runner.load_project_runtime_overrides("ka-tools")

        self.assertEqual(overrides["env_file"], "/host/.env.local")
        self.assertEqual(overrides["volumes"], ["/host/data:/app/data"])

    def test_derive_runtime_env_vars_uses_onboarding_env_file_hint_without_project_override(self) -> None:
        env_args = runner.derive_runtime_env_vars(
            {
                "environment_variables": ["PORT"],
                "env_file_hint": ".env",
                "project_env_file": None,
            },
            host_port=8001,
            container_port=8000,
        )

        self.assertEqual(env_args, ["-e", "PORT=8000", "--env-file", ".env"])

    def test_derive_runtime_env_vars_skips_onboarding_env_file_hint_when_project_override_exists(self) -> None:
        env_args = runner.derive_runtime_env_vars(
            {
                "environment_variables": ["PORT"],
                "env_file_hint": ".env",
                "project_env_file": "/host/.env.local",
            },
            host_port=8001,
            container_port=8000,
        )

        self.assertEqual(env_args, ["-e", "PORT=8000"])

    def test_run_container_binds_host_port_to_loopback_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            log_path = repo_dir / "run.log"
            with mock.patch.object(runner, "run_command") as run_command_mock:
                run_command_mock.return_value = runner.CommandResult(args=[], returncode=0, stdout="container-id")
                runner.run_container(
                    repo_dir=repo_dir,
                    image_name="localhost/example:latest",
                    host_port=8001,
                    container_port=8000,
                    container_name="example",
                    log_path=log_path,
                    podman_env={},
                )

        called_args = run_command_mock.call_args.args[0]
        self.assertIn("-p", called_args)
        self.assertIn("127.0.0.1:8001:8000", called_args)

    def test_run_container_includes_extra_volume_args(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            log_path = repo_dir / "run.log"
            with mock.patch.object(runner, "run_command") as run_command_mock:
                run_command_mock.return_value = runner.CommandResult(args=[], returncode=0, stdout="container-id")
                runner.run_container(
                    repo_dir=repo_dir,
                    image_name="localhost/example:latest",
                    host_port=8001,
                    container_port=8000,
                    container_name="example",
                    log_path=log_path,
                    podman_env={},
                    extra_volume_args=["/host/cache:/model-cache"],
                )

        called_args = run_command_mock.call_args.args[0]
        self.assertIn("-v", called_args)
        self.assertIn("/host/cache:/model-cache", called_args)

    def test_run_container_includes_restart_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            log_path = repo_dir / "run.log"
            with mock.patch.object(runner, "run_command") as run_command_mock:
                run_command_mock.return_value = runner.CommandResult(args=[], returncode=0, stdout="container-id")
                runner.run_container(
                    repo_dir=repo_dir,
                    image_name="localhost/example:latest",
                    host_port=8001,
                    container_port=8000,
                    container_name="example",
                    log_path=log_path,
                    podman_env={},
                )

        called_args = run_command_mock.call_args.args[0]
        self.assertIn("--restart", called_args)
        self.assertIn("always", called_args)

    def test_merge_run_spec_includes_project_runtime_overrides(self) -> None:
        overrides = {
            "env": {"HF_HOME": "/model-cache/huggingface"},
            "env_file": "/host/.env.local",
            "volumes": ["/host/cache:/model-cache"],
        }
        with mock.patch.object(runner, "load_project_port_configs", return_value={"audioqas": {"port": "8001:8000"}}):
            with mock.patch.object(runner, "load_project_runtime_overrides", return_value=overrides):
                run_spec = runner.merge_run_spec(
                    project_slug="audioqas",
                    user_host_port=None,
                    onboarding_spec={},
                    image_spec={},
                )

        self.assertEqual(run_spec["host_port"], 8001)
        self.assertEqual(run_spec["container_port"], 8000)
        self.assertEqual(run_spec["project_env"], {"HF_HOME": "/model-cache/huggingface"})
        self.assertEqual(run_spec["project_env_file"], "/host/.env.local")
        self.assertEqual(run_spec["project_volumes"], ["/host/cache:/model-cache"])

    def test_run_container_uses_only_explicit_project_volume_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            log_path = repo_dir / "run.log"
            with mock.patch.object(runner, "run_command", return_value=runner.CommandResult(args=[], returncode=0, stdout="ok")) as run_command_mock:
                runner.run_container(
                    repo_dir=repo_dir,
                    image_name="localhost/example:latest",
                    host_port=8001,
                    container_port=8000,
                    container_name="example",
                    log_path=log_path,
                    podman_env={},
                    extra_volume_args=["/host/cache:/model-cache"],
                )

        called_args = run_command_mock.call_args.args[0]
        self.assertIn("-v", called_args)
        self.assertIn("/host/cache:/model-cache", called_args)

    def test_run_container_does_not_derive_volume_mounts_from_onboarding_persistence_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            log_path = repo_dir / "run.log"
            with mock.patch.object(runner, "run_command", return_value=runner.CommandResult(args=[], returncode=0, stdout="ok")) as run_command_mock:
                runner.run_container(
                    repo_dir=repo_dir,
                    image_name="localhost/example:latest",
                    host_port=8001,
                    container_port=8000,
                    container_name="example",
                    log_path=log_path,
                    podman_env={},
                    extra_volume_args=[],
                )

        called_args = run_command_mock.call_args.args[0]
        self.assertNotIn("web/uploads", called_args)

    def test_find_frontend_rewrite_targets_includes_src_javascript(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            target_file = repo_dir / "src" / "common" / "constant.js"
            target_file.parent.mkdir(parents=True)
            target_file.write_text("const ORIGIN_URL = window.location.origin;\n", encoding="utf-8")
            targets = runner.find_frontend_rewrite_targets(repo_dir)
            self.assertIn(target_file, targets)

    def test_find_frontend_rewrite_targets_excludes_typescript_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            tsx_path = repo_dir / "src" / "components" / "widget.tsx"
            js_path = repo_dir / "src" / "components" / "widget.js"
            tsx_path.parent.mkdir(parents=True)
            tsx_path.write_text("export default function App() { return null }\n", encoding="utf-8")
            js_path.write_text("console.log('demo')\n", encoding="utf-8")

            targets = runner.find_frontend_rewrite_targets(repo_dir)

            self.assertIn(js_path, targets)
            self.assertNotIn(tsx_path, targets)

    def test_find_frontend_rewrite_targets_include_runtime_root_html_and_js(self) -> None:
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
            js_path = demo_dir / "index.js"
            html_path.write_text("<html></html>\n", encoding="utf-8")
            js_path.write_text("console.log('demo')\n", encoding="utf-8")

            targets = runner.find_frontend_rewrite_targets(repo_dir)

            self.assertIn(html_path, targets)
            self.assertIn(js_path, targets)

    def test_find_frontend_rewrite_targets_include_fastapi_views_and_static_app_js(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            app_dir = repo_dir / "app"
            views_dir = repo_dir / "views"
            static_dir = repo_dir / "static"
            app_dir.mkdir(parents=True)
            views_dir.mkdir()
            static_dir.mkdir()
            (app_dir / "main.py").write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "from fastapi import FastAPI",
                        "from fastapi.staticfiles import StaticFiles",
                        "from fastapi.templating import Jinja2Templates",
                        "",
                        "BASE_DIR = Path(__file__).resolve().parent.parent",
                        "views = Jinja2Templates(directory=str(BASE_DIR / 'views'))",
                        "app = FastAPI()",
                        "app.mount('/static', StaticFiles(directory=str(BASE_DIR / 'static')), name='static')",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            html_path = views_dir / "index.html"
            js_path = static_dir / "app.js"
            html_path.write_text('<link rel="stylesheet" href="/static/styles.css">\n', encoding="utf-8")
            js_path.write_text("fetch('/api/analyze-upload')\n", encoding="utf-8")

            targets = runner.find_frontend_rewrite_targets(repo_dir)

            self.assertIn(html_path, targets)
            self.assertIn(js_path, targets)

    def test_find_frontend_rewrite_targets_include_workspace_vite_index_and_src_tsx(self) -> None:
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
            html_path = frontend_dir / "index.html"
            entry_path = src_dir / "main.tsx"
            html_path.write_text('<link rel="icon" href="/vite.svg">\n<script type="module" src="/src/main.tsx"></script>\n', encoding="utf-8")
            entry_path.write_text("fetch('/api/jobs')\n", encoding="utf-8")

            targets = runner.find_frontend_rewrite_targets(repo_dir)

            self.assertIn(html_path, targets)
            self.assertIn(entry_path, targets)

    def test_apply_subpath_rewrites_nextjs_adds_basepath_and_autofix_runtime_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            src_app_dir = repo_dir / "src" / "app"
            src_components_dir = repo_dir / "src" / "components"
            src_app_dir.mkdir(parents=True)
            src_components_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "demo-next",
                        "dependencies": {"next": "^15.1.0", "react": "^19.0.0", "react-dom": "^19.0.0"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            layout_path = src_app_dir / "layout.tsx"
            component_path = src_components_dir / "api-explorer.tsx"
            config_path = repo_dir / "next.config.ts"
            config_path.write_text(
                "import type { NextConfig } from 'next'\n\n"
                "const nextConfig: NextConfig = {\n"
                "  outputFileTracingRoot: process.cwd(),\n"
                "}\n\n"
                "export default nextConfig\n",
                encoding="utf-8",
            )
            layout_path.write_text(
                'import "../../ka_tool_base_runtime"\n'
                "export default function RootLayout({ children }: { children: React.ReactNode }) {\n"
                "  return <html><body><a href=\"/media-api\">Media</a><a href=\"https://example.com\">Docs</a>{children}</body></html>\n"
                "}\n",
                encoding="utf-8",
            )
            component_path.write_text(
                "'use client'\n"
                "export async function loadData() {\n"
                "  return fetch('/api/token')\n"
                "}\n",
                encoding="utf-8",
            )
            (repo_dir / "ka_tool_base_runtime.ts").write_text("legacy shim\n", encoding="utf-8")
            (repo_dir / "ka_tool_window.d.ts").write_text("legacy types\n", encoding="utf-8")

            changed = subpath_apply_subpath_rewrites(
                repo_dir,
                "demo-next",
                plan=self._build_plan(repo_dir),
                apply_framework_config_adapters=lambda plan, slug: runner.subpath_apply_framework_config_adapters(
                    plan,
                    slug,
                    parse_package_json=runner.parse_package_json,
                    read_text=runner.read_text,
                    write_text=runner.write_text,
                ),
                apply_nextjs_subpath_adapter_fn=runner.apply_nextjs_subpath_adapter,
                apply_vite_subpath_adapter_fn=runner.apply_vite_subpath_adapter,
                ensure_vue_cli_public_path_fn=runner.ensure_vue_cli_public_path,
                ensure_cra_homepage_fn=runner.ensure_cra_homepage,
                find_frontend_rewrite_targets=runner.find_frontend_rewrite_targets,
                rewrite_frontend_subpath_urls=runner.rewrite_frontend_subpath_urls,
            )

            self.assertIn("next.config.ts", changed)
            self.assertIn("src/app/layout.tsx", changed)
            self.assertIn("src/components/api-explorer.tsx", changed)
            self.assertIn("ka_tool_base_runtime.ts", changed)
            self.assertIn("ka_tool_window.d.ts", changed)
            self.assertIn('import Link from "next/link"', layout_path.read_text(encoding="utf-8"))
            self.assertIn('<Link href="/media-api">', layout_path.read_text(encoding="utf-8"))
            self.assertIn('<a href="https://example.com">Docs</a>', layout_path.read_text(encoding="utf-8"))
            self.assertIn('import { withToolBase } from "../../ka_tool_base_runtime"', component_path.read_text(encoding="utf-8"))
            self.assertIn("'use client'\nimport { withToolBase }", component_path.read_text(encoding="utf-8"))
            self.assertIn('fetch(withToolBase("/api/token"))', component_path.read_text(encoding="utf-8"))
            config_text = config_path.read_text(encoding="utf-8")
            self.assertIn('basePath: "/tools2/demo-next"', config_text)
            self.assertIn('assetPrefix: "/tools2/demo-next"', config_text)
            self.assertTrue((repo_dir / "ka_tool_base_runtime.ts").exists())
            self.assertFalse((repo_dir / "ka_tool_window.d.ts").exists())

    def test_apply_subpath_rewrites_nextjs_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            src_app_dir = repo_dir / "src" / "app"
            src_app_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "demo-next",
                        "dependencies": {"next": "^15.1.0"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (repo_dir / "next.config.ts").write_text(
                "const nextConfig = {}\nexport default nextConfig\n",
                encoding="utf-8",
            )
            layout_path = src_app_dir / "layout.tsx"
            layout_path.write_text("export default function RootLayout() { return null }\n", encoding="utf-8")

            first = subpath_apply_subpath_rewrites(
                repo_dir,
                "demo-next",
                plan=self._build_plan(repo_dir),
                apply_framework_config_adapters=lambda plan, slug: runner.subpath_apply_framework_config_adapters(
                    plan,
                    slug,
                    parse_package_json=runner.parse_package_json,
                    read_text=runner.read_text,
                    write_text=runner.write_text,
                ),
                apply_nextjs_subpath_adapter_fn=runner.apply_nextjs_subpath_adapter,
                apply_vite_subpath_adapter_fn=runner.apply_vite_subpath_adapter,
                ensure_vue_cli_public_path_fn=runner.ensure_vue_cli_public_path,
                ensure_cra_homepage_fn=runner.ensure_cra_homepage,
                find_frontend_rewrite_targets=runner.find_frontend_rewrite_targets,
                rewrite_frontend_subpath_urls=runner.rewrite_frontend_subpath_urls,
            )
            second = subpath_apply_subpath_rewrites(
                repo_dir,
                "demo-next",
                plan=self._build_plan(repo_dir),
                apply_framework_config_adapters=lambda plan, slug: runner.subpath_apply_framework_config_adapters(
                    plan,
                    slug,
                    parse_package_json=runner.parse_package_json,
                    read_text=runner.read_text,
                    write_text=runner.write_text,
                ),
                apply_nextjs_subpath_adapter_fn=runner.apply_nextjs_subpath_adapter,
                apply_vite_subpath_adapter_fn=runner.apply_vite_subpath_adapter,
                ensure_vue_cli_public_path_fn=runner.ensure_vue_cli_public_path,
                ensure_cra_homepage_fn=runner.ensure_cra_homepage,
                find_frontend_rewrite_targets=runner.find_frontend_rewrite_targets,
                rewrite_frontend_subpath_urls=runner.rewrite_frontend_subpath_urls,
            )

            self.assertTrue(first)
            self.assertEqual(second, [])
            self.assertNotIn("ka_tool_base_runtime", layout_path.read_text(encoding="utf-8"))
            config_text = (repo_dir / "next.config.ts").read_text(encoding="utf-8")
            self.assertEqual(config_text.count('basePath: "/tools2/demo-next"'), 1)
            self.assertEqual(config_text.count('assetPrefix: "/tools2/demo-next"'), 1)

    def test_auto_fix_nextjs_subpath_issues_allows_static_audit_to_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            src_app_dir = repo_dir / "src" / "app"
            src_components_dir = repo_dir / "src" / "components"
            src_app_dir.mkdir(parents=True)
            src_components_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"dependencies": {"next": "^15.1.0", "react": "^19.0.0", "react-dom": "^19.0.0"}}) + "\n",
                encoding="utf-8",
            )
            (repo_dir / "next.config.ts").write_text(
                "import type { NextConfig } from 'next'\nconst nextConfig: NextConfig = {}\nexport default nextConfig\n",
                encoding="utf-8",
            )
            (src_app_dir / "layout.tsx").write_text(
                "export default function RootLayout({ children }: { children: React.ReactNode }) {\n"
                "  return <html><body><a href=\"/media-api\">Media</a>{children}</body></html>\n"
                "}\n",
                encoding="utf-8",
            )
            (src_components_dir / "widget.tsx").write_text(
                "'use client'\nexport async function loadData() { return fetch('/api/token') }\n",
                encoding="utf-8",
            )

            plan = self._build_plan(repo_dir)
            initial = subpath_run_static_subpath_audit(
                repo_dir,
                "demo-next",
                plan,
                collect_matching_files=runner.collect_matching_files,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
                read_text=runner.read_text,
            )
            changed = subpath_auto_fix_subpath_issues(
                repo_dir,
                "demo-next",
                plan,
                auto_fix_nextjs_subpath_issues_fn=runner.auto_fix_nextjs_subpath_issues,
                find_subpath_audit_targets=lambda repo_dir_arg, plan_arg: runner.subpath_find_subpath_audit_targets(
                    repo_dir_arg,
                    plan_arg,
                    collect_matching_files=runner.collect_matching_files,
                    detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                    vite_project_roots=runner.vite_project_roots,
                ),
                rewrite_frontend_subpath_urls=runner.rewrite_frontend_subpath_urls,
                sorted_unique=runner.sorted_unique,
            )
            after = subpath_run_static_subpath_audit(
                repo_dir,
                "demo-next",
                plan,
                collect_matching_files=runner.collect_matching_files,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
                read_text=runner.read_text,
            )

            self.assertTrue(initial["findings"])
            self.assertTrue(changed)
            self.assertEqual(after["findings"], [])

    def test_auto_fix_subpath_issues_rewrites_fastapi_views_and_static_js(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            app_dir = repo_dir / "app"
            views_dir = repo_dir / "views"
            static_dir = repo_dir / "static"
            app_dir.mkdir(parents=True)
            views_dir.mkdir()
            static_dir.mkdir()
            (app_dir / "main.py").write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "from fastapi import FastAPI",
                        "from fastapi.staticfiles import StaticFiles",
                        "from fastapi.templating import Jinja2Templates",
                        "",
                        "BASE_DIR = Path(__file__).resolve().parent.parent",
                        "views = Jinja2Templates(directory=str(BASE_DIR / 'views'))",
                        "app = FastAPI()",
                        "app.mount('/static', StaticFiles(directory=str(BASE_DIR / 'static')), name='static')",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            html_path = views_dir / "index.html"
            js_path = static_dir / "app.js"
            html_path.write_text(
                '<!DOCTYPE html><html><head><link rel="stylesheet" href="/static/styles.css"></head><body><script src="/static/app.js"></script></body></html>\n',
                encoding="utf-8",
            )
            js_path.write_text("fetch('/api/analyze-upload')\n", encoding="utf-8")

            plan = self._build_plan(repo_dir)
            initial = subpath_run_static_subpath_audit(
                repo_dir,
                "demo-fastapi",
                plan,
                collect_matching_files=runner.collect_matching_files,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
                read_text=runner.read_text,
            )
            changed = subpath_auto_fix_subpath_issues(
                repo_dir,
                "demo-fastapi",
                plan,
                auto_fix_nextjs_subpath_issues_fn=runner.auto_fix_nextjs_subpath_issues,
                find_subpath_audit_targets=lambda repo_dir_arg, plan_arg: runner.subpath_find_subpath_audit_targets(
                    repo_dir_arg,
                    plan_arg,
                    collect_matching_files=runner.collect_matching_files,
                    detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                    vite_project_roots=runner.vite_project_roots,
                ),
                rewrite_frontend_subpath_urls=runner.rewrite_frontend_subpath_urls,
                sorted_unique=runner.sorted_unique,
            )
            after = subpath_run_static_subpath_audit(
                repo_dir,
                "demo-fastapi",
                plan,
                collect_matching_files=runner.collect_matching_files,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
                read_text=runner.read_text,
            )

            self.assertTrue(initial["findings"])
            self.assertIn("views/index.html", changed)
            self.assertIn("static/app.js", changed)
            self.assertEqual(after["findings"], [])

    def test_auto_fix_subpath_issues_rewrites_workspace_vite_html_and_api_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            src_api_dir = frontend_dir / "src" / "api"
            src_dir = frontend_dir / "src"
            src_api_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "decrypt-online", "private": True, "workspaces": ["frontend"]}) + "\n",
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
            api_path = src_api_dir / "jobs.ts"
            html_path.write_text(
                '<!doctype html><html><head><link rel="icon" href="/vite.svg" /></head><body><script type="module" src="/src/main.tsx"></script></body></html>\n',
                encoding="utf-8",
            )
            api_path.write_text("fetch('/api/jobs')\nfetch(`/api/jobs/${jobId}`)\n", encoding="utf-8")

            plan = self._build_plan(repo_dir)
            initial = subpath_run_static_subpath_audit(
                repo_dir,
                "decrypt-online",
                plan,
                collect_matching_files=runner.collect_matching_files,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
                read_text=runner.read_text,
            )
            report = runner.subpath_auto_fix_findings(
                repo_dir,
                "decrypt-online",
                initial["findings"],
                plan,
                auto_fix_nextjs_subpath_issues_fn=runner.auto_fix_nextjs_subpath_issues,
                find_subpath_audit_targets=lambda repo_dir_arg, plan_arg: runner.subpath_find_subpath_audit_targets(
                    repo_dir_arg,
                    plan_arg,
                    collect_matching_files=runner.collect_matching_files,
                    detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                    vite_project_roots=runner.vite_project_roots,
                ),
                rewrite_frontend_subpath_urls=runner.rewrite_frontend_subpath_urls,
                rewrite_frontend_html_attribute_urls=runner.rewrite_frontend_html_attribute_urls,
                rewrite_frontend_html_link_urls=runner.rewrite_frontend_html_link_urls,
                rewrite_frontend_html_script_urls=runner.rewrite_frontend_html_script_urls,
                rewrite_frontend_html_form_action_urls=runner.rewrite_frontend_html_form_action_urls,
                rewrite_frontend_client_request_urls=runner.rewrite_frontend_client_request_urls,
                rewrite_frontend_request_api_urls=lambda file_path, base_path, repo_dir_arg: runner.subpath_rewrite_frontend_request_api_urls(
                    file_path,
                    base_path,
                    repo_dir_arg,
                    read_text=runner.read_text,
                    write_text=runner.write_text,
                ),
                rewrite_frontend_navigation_urls=lambda file_path, base_path, repo_dir_arg: runner.subpath_rewrite_frontend_navigation_urls(
                    file_path,
                    base_path,
                    repo_dir_arg,
                    read_text=runner.read_text,
                    write_text=runner.write_text,
                ),
                rewrite_frontend_eventsource_urls=lambda file_path, base_path, repo_dir_arg: runner.subpath_rewrite_frontend_eventsource_urls(
                    file_path,
                    base_path,
                    repo_dir_arg,
                    read_text=runner.read_text,
                    write_text=runner.write_text,
                ),
                rewrite_frontend_return_value_urls=lambda file_path, base_path, repo_dir_arg: runner.subpath_rewrite_frontend_return_value_urls(
                    file_path,
                    base_path,
                    repo_dir_arg,
                    read_text=runner.read_text,
                    write_text=runner.write_text,
                ),
                sorted_unique=runner.sorted_unique,
            )
            changed = report["changed_files"]
            after = subpath_run_static_subpath_audit(
                repo_dir,
                "decrypt-online",
                plan,
                collect_matching_files=runner.collect_matching_files,
                detect_frontend_runtime_roots=runner.detect_frontend_runtime_roots,
                vite_project_roots=runner.vite_project_roots,
                read_text=runner.read_text,
            )

            self.assertTrue(initial["findings"])
            self.assertIn("frontend/index.html", changed)
            self.assertIn("frontend/src/api/jobs.ts", changed)
            self.assertEqual(after["findings"], [])

    def test_discover_workspace_packages_reads_pnpm_workspace_members(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "demo", "packageManager": "pnpm@9.0.0"}) + "\n",
                encoding="utf-8",
            )
            (repo_dir / "pnpm-workspace.yaml").write_text('packages:\n  - "apps/*"\n', encoding="utf-8")
            portal_dir = repo_dir / "apps" / "portal"
            portal_dir.mkdir(parents=True)
            (portal_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "@demo/portal",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "^15.0.0"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            root_package = runner.parse_package_json(repo_dir)
            packages = runner.discover_workspace_packages(repo_dir, root_package)

            self.assertEqual(len(packages), 1)
            self.assertEqual(packages[0]["name"], "@demo/portal")
            self.assertEqual(packages[0]["relative_dir"], "apps/portal")

    def test_select_service_package_prefers_workspace_app_with_runtime_scripts(self) -> None:
        root_package = {"name": "demo", "scripts": {"build": "turbo build"}, "dependencies": {"turbo": "^2.0.0"}}
        workspace_packages = [
            {
                "name": "@demo/portal",
                "relative_dir": "apps/portal",
                "package_dir": Path("/tmp/apps/portal"),
                "scripts": {"build": "next build", "start": "next start"},
                "dependencies": ["next", "react", "react-dom"],
            }
        ]

        selected = runner.select_service_package(Path("/tmp/repo"), root_package, workspace_packages)

        assert selected is not None
        self.assertEqual(selected["name"], "@demo/portal")
        self.assertEqual(selected["relative_dir"], "apps/portal")

    def test_dockerfile_has_build_step_recognizes_filtered_pnpm_and_turbo_build(self) -> None:
        docker_text = "\n".join(
            [
                "FROM node:22-alpine AS build",
                "RUN pnpm --filter @demo/portal build",
                "RUN turbo build",
            ]
        )

        self.assertTrue(runner.dockerfile_has_build_step(docker_text))

    def test_dockerfile_has_build_step_recognizes_chained_pnpm_build(self) -> None:
        docker_text = "\n".join(
            [
                "FROM node:22-alpine AS build",
                "RUN corepack prepare pnpm@9.15.4 --activate && pnpm build",
            ]
        )

        self.assertTrue(runner.dockerfile_has_build_step(docker_text))

    def test_dockerfile_has_build_step_recognizes_multiline_pnpm_build(self) -> None:
        docker_text = "\n".join(
            [
                "FROM node:22-alpine AS build",
                "RUN corepack enable \\",
                "    && corepack prepare pnpm@9.15.4 --activate \\",
                "    && pnpm install \\",
                "    && pnpm build",
            ]
        )

        self.assertTrue(runner.dockerfile_has_build_step(docker_text))

    def test_validate_multistage_copy_sources_accepts_known_stage_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "package.json").write_text(json.dumps({"name": "demo"}) + "\n", encoding="utf-8")
            docker_text = "\n".join(
                [
                    "FROM node:22-alpine AS deps",
                    "WORKDIR /app",
                    "COPY package.json ./",
                    "RUN pnpm install --frozen-lockfile",
                    "FROM node:22-alpine AS build",
                    "WORKDIR /app",
                    "COPY --from=deps /app /app",
                    "COPY . .",
                    "RUN pnpm --filter @demo/portal build",
                    "FROM node:22-alpine AS runtime",
                    "WORKDIR /app",
                    "COPY --from=build /app/node_modules ./node_modules",
                    "COPY --from=build /app/apps/portal ./apps/portal",
                ]
            )

            findings = runner.validate_multistage_copy_sources(repo_dir, docker_text)

            self.assertEqual(findings, [])

    def test_validate_multistage_copy_sources_accepts_full_app_copy_after_build(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "package.json").write_text(json.dumps({"name": "demo"}) + "\n", encoding="utf-8")
            docker_text = "\n".join(
                [
                    "FROM node:22-alpine AS deps",
                    "WORKDIR /app",
                    "COPY package.json pnpm-lock.yaml ./",
                    "RUN pnpm install --frozen-lockfile",
                    "FROM node:22-alpine AS builder",
                    "WORKDIR /app",
                    "COPY --from=deps /app/node_modules ./node_modules",
                    "COPY . .",
                    "RUN next build",
                    "FROM node:22-alpine AS runner",
                    "WORKDIR /app",
                    "COPY --from=builder /app /app",
                ]
            )

            findings = runner.validate_multistage_copy_sources(repo_dir, docker_text)

            self.assertEqual(findings, [])

    def test_validate_multistage_copy_sources_reports_unknown_stage_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "package.json").write_text(json.dumps({"name": "demo"}) + "\n", encoding="utf-8")
            docker_text = "\n".join(
                [
                    "FROM node:22-alpine AS build",
                    "WORKDIR /app",
                    "COPY . .",
                    "FROM node:22-alpine AS runtime",
                    "WORKDIR /app",
                    "COPY --from=build /tmp/missing ./missing",
                ]
            )

            findings = runner.validate_multistage_copy_sources(repo_dir, docker_text)

            self.assertEqual(len(findings), 1)
            self.assertIn('"/tmp/missing"', findings[0])

    def test_collect_repo_analysis_selects_workspace_service_package_and_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "agora-rest-api-debugger",
                        "private": True,
                        "packageManager": "pnpm@9.15.4",
                        "scripts": {"build": "turbo build"},
                        "devDependencies": {"turbo": "^2.3.0"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (repo_dir / "pnpm-workspace.yaml").write_text('packages:\n  - "apps/*"\n', encoding="utf-8")
            portal_dir = repo_dir / "apps" / "portal"
            portal_dir.mkdir(parents=True)
            (portal_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "@agora-rest-api-debugger/portal",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "^15.1.0", "react": "^19.0.0", "react-dom": "^19.0.0"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (repo_dir / "README.md").write_text("http://localhost:3000\n", encoding="utf-8")

            analysis = runner.collect_repo_analysis(repo_dir, "git", "https://example.com/demo.git", "main")

            self.assertEqual(analysis["service_runtime"], "node")
            self.assertEqual(analysis["package_manager"], "pnpm")
            self.assertEqual(analysis["node_entry_command"], "pnpm --dir apps/portal start")
            selected = analysis["selected_service_package"]
            assert isinstance(selected, dict)
            self.assertEqual(selected["name"], "@agora-rest-api-debugger/portal")
            self.assertEqual(selected["relative_dir"], "apps/portal")
            self.assertEqual(selected["build_command"], "pnpm --dir apps/portal build")

    def test_collect_repo_analysis_detects_runtime_build_artifacts_for_workspace_frontend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_dir = repo_dir / "frontend"
            backend_src_dir = repo_dir / "backend" / "src"
            frontend_dir.mkdir(parents=True)
            backend_src_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "decrypt-online",
                        "private": True,
                        "workspaces": ["frontend", "backend"],
                        "scripts": {
                            "build": "npm run build --workspace=frontend",
                            "start": "npm run start --workspace=backend",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (repo_dir / "package-lock.json").write_text("{}\n", encoding="utf-8")
            (frontend_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "decrypt-online-frontend",
                        "private": True,
                        "scripts": {"build": "vite build"},
                        "devDependencies": {"vite": "^5.4.3"},
                        "dependencies": {"react": "^18.3.1", "react-dom": "^18.3.1"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (frontend_dir / "vite.config.ts").write_text(
                "import { defineConfig } from 'vite'\nexport default defineConfig({})\n",
                encoding="utf-8",
            )
            (repo_dir / "backend" / "package.json").write_text(
                json.dumps(
                    {
                        "name": "decrypt-online-backend",
                        "private": True,
                        "scripts": {"start": "tsx src/index.ts"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (backend_src_dir / "index.ts").write_text(
                "\n".join(
                    [
                        "import path from 'path'",
                        "const frontendDist = path.resolve(__dirname, '../../frontend/dist')",
                        "await fastify.register(fastifyStatic, {",
                        "  root: frontendDist,",
                        "  prefix: '/'",
                        "})",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            analysis = runner.collect_repo_analysis(repo_dir, "git", "https://example.com/demo.git", "main")

            self.assertTrue(analysis["requires_build_step"])
            self.assertIn("frontend/dist", analysis["runtime_build_artifact_paths"])
            self.assertEqual(analysis["required_build_commands"], ["npm run build --workspace=frontend"])
            self.assertIn("npm run build --workspace=frontend", analysis["build_command_candidates"])

    def test_summarize_analysis_is_json_serializable_with_workspace_service_package(self) -> None:
        analysis = {
            "service_runtime": "node",
            "package_manager": "pnpm",
            "has_nextjs_ts_config": False,
            "python_entry_command": None,
            "node_entry_command": "pnpm --dir apps/portal start",
            "detected_port": 3000,
            "requires_python": None,
            "system_dependency_hints": [],
            "env_var_names": [],
            "config_file_hints": [],
            "database_file_hints": [],
            "storage_hints": [],
            "selected_service_package": {
                "name": "@agora-rest-api-debugger/portal",
                "relative_dir": "apps/portal",
                "package_dir": Path("/tmp/apps/portal"),
                "build_command": "pnpm --dir apps/portal build",
                "start_command": "pnpm --dir apps/portal start",
                "scripts": {"build": "next build", "start": "next start"},
                "dependencies": ["next", "react", "react-dom"],
            },
        }

        summary = runner.summarize_analysis(analysis, "git", "agora-rest-api-debugger")

        self.assertNotIn("package_dir", summary["selected_service_package"])
        json.dumps(summary)

    def test_validate_generated_files_accepts_monorepo_filtered_build(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "agora-rest-api-debugger",
                        "private": True,
                        "packageManager": "pnpm@9.15.4",
                        "scripts": {"build": "turbo build"},
                        "devDependencies": {"turbo": "^2.3.0"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (repo_dir / "pnpm-workspace.yaml").write_text('packages:\n  - "apps/*"\n', encoding="utf-8")
            portal_dir = repo_dir / "apps" / "portal"
            portal_dir.mkdir(parents=True)
            (portal_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "@agora-rest-api-debugger/portal",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "^15.1.0", "react": "^19.0.0", "react-dom": "^19.0.0"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (repo_dir / "README.md").write_text(
                "NEXT_PUBLIC_AGORA_APP_ID=demo\nAGORA_APP_CERTIFICATE=demo\n默认访问 http://localhost:3000\n",
                encoding="utf-8",
            )
            (repo_dir / "Dockerfile").write_text(
                "\n".join(
                    [
                        "FROM node:22-alpine AS deps",
                        "WORKDIR /app",
                        "COPY package.json ./",
                        "RUN pnpm install --frozen-lockfile",
                        "FROM node:22-alpine AS build",
                        "WORKDIR /app",
                        "COPY --from=deps /app /app",
                        "COPY . .",
                        "RUN pnpm --filter @agora-rest-api-debugger/portal build",
                        "FROM node:22-alpine AS runtime",
                        "WORKDIR /app",
                        'CMD ["pnpm", "--dir", "apps/portal", "start"]',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (repo_dir / "PROJECT_ONBOARDING.md").write_text(
                "\n".join(
                    [
                        "# PROJECT_ONBOARDING",
                        "## 1 项目基础信息",
                        "## 2 代码和版本信息",
                        "## 3 启动信息",
                        "## 4 运行参数",
                        "- 环境变量：`NEXT_PUBLIC_AGORA_APP_ID`",
                        "- 环境变量：`AGORA_APP_CERTIFICATE`",
                        "## 5 配置与密钥",
                        "## 6 存储信息",
                        "## 7 证据与判断说明",
                        "## 8 待确认问题",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            findings, warnings = runner.validate_generated_files(
                repo_dir,
                source_type="git",
                source="https://example.com/demo.git",
                ref="main",
            )

            self.assertEqual(findings, [])
            self.assertEqual(warnings, [])

    def test_validate_generated_files_does_not_require_build_step_without_runtime_build_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "api-only-service",
                        "private": True,
                        "scripts": {
                            "build": "tsc",
                            "start": "tsx src/index.ts",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (repo_dir / "package-lock.json").write_text("{}\n", encoding="utf-8")
            src_dir = repo_dir / "src"
            src_dir.mkdir()
            (src_dir / "index.ts").write_text(
                "console.log('hello')\n",
                encoding="utf-8",
            )
            (repo_dir / "Dockerfile").write_text(
                "\n".join(
                    [
                        "FROM node:22-alpine",
                        "WORKDIR /app",
                        "COPY package.json package-lock.json ./",
                        "RUN npm ci",
                        "COPY . .",
                        'CMD ["tsx", "src/index.ts"]',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (repo_dir / "PROJECT_ONBOARDING.md").write_text(
                "\n".join(
                    [
                        "# PROJECT_ONBOARDING",
                        "## 1 项目基础信息",
                        "## 2 代码和版本信息",
                        "## 3 启动信息",
                        "## 4 运行参数",
                        "## 5 配置与密钥",
                        "## 6 存储信息",
                        "## 7 证据与判断说明",
                        "## 8 待确认问题",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            findings, warnings = runner.validate_generated_files(
                repo_dir,
                source_type="git",
                source="https://example.com/demo.git",
                ref="main",
            )

            self.assertEqual(findings, [])
            self.assertEqual(warnings, [])

    def test_validate_generated_files_rejects_unknown_runtime_port_in_dockerfile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "demo-service",
                        "private": True,
                        "scripts": {"start": "node server.js"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (repo_dir / "server.js").write_text("console.log('ok')\n", encoding="utf-8")
            (repo_dir / "Dockerfile").write_text(
                "\n".join(
                    [
                        "FROM node:22-alpine",
                        "WORKDIR /app",
                        "COPY . .",
                        "ENV PORT=UNKNOWN",
                        "EXPOSE UNKNOWN",
                        'CMD ["node", "server.js"]',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (repo_dir / "PROJECT_ONBOARDING.md").write_text(
                "\n".join(
                    [
                        "# PROJECT_ONBOARDING",
                        "## 1 项目基础信息",
                        "## 2 代码和版本信息",
                        "## 3 启动信息",
                        "## 4 运行参数",
                        "## 5 配置与密钥",
                        "## 6 存储信息",
                        "## 7 证据与判断说明",
                        "## 8 待确认问题",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            findings, warnings = runner.validate_generated_files(
                repo_dir,
                source_type="git",
                source="https://example.com/demo.git",
                ref="main",
            )

            self.assertIn("Dockerfile uses UNKNOWN in EXPOSE for a runtime-critical port; omit EXPOSE when the port is not confirmed", findings)
            self.assertIn("Dockerfile sets PORT=UNKNOWN; omit the PORT default until a concrete port is confirmed", findings)
            self.assertIn("Dockerfile contains TODO/UNKNOWN markers", warnings)

    def test_validate_generated_files_accepts_chained_next_build_and_full_app_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "agora-rest-api-debugger",
                        "private": True,
                        "packageManager": "pnpm@9.15.4",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "^15.1.0", "react": "^19.0.0", "react-dom": "^19.0.0"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (repo_dir / "README.md").write_text(
                "NEXT_PUBLIC_AGORA_APP_ID=demo\nAGORA_APP_CERTIFICATE=demo\n默认访问 http://localhost:3000\n",
                encoding="utf-8",
            )
            (repo_dir / "Dockerfile").write_text(
                "\n".join(
                    [
                        "FROM node:22-alpine AS base",
                        "WORKDIR /app",
                        "RUN apk add --no-cache curl && corepack enable",
                        "FROM base AS deps",
                        "COPY package.json pnpm-lock.yaml ./",
                        "RUN corepack prepare pnpm@9.15.4 --activate && pnpm install --frozen-lockfile",
                        "FROM base AS builder",
                        "COPY --from=deps /app/node_modules ./node_modules",
                        "COPY . .",
                        "RUN corepack prepare pnpm@9.15.4 --activate && pnpm build",
                        "FROM node:22-alpine AS runner",
                        "WORKDIR /app",
                        'CMD ["./node_modules/.bin/next", "start", "-H", "0.0.0.0", "-p", "3000"]',
                        "COPY --from=builder /app /app",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (repo_dir / "PROJECT_ONBOARDING.md").write_text(
                "\n".join(
                    [
                        "# PROJECT_ONBOARDING",
                        "## 1 项目基础信息",
                        "## 2 代码和版本信息",
                        "## 3 启动信息",
                        "## 4 运行参数",
                        "- 环境变量：`NEXT_PUBLIC_AGORA_APP_ID`",
                        "- 环境变量：`AGORA_APP_CERTIFICATE`",
                        "## 5 配置与密钥",
                        "## 6 存储信息",
                        "## 7 证据与判断说明",
                        "## 8 待确认问题",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            findings, warnings = runner.validate_generated_files(
                repo_dir,
                source_type="git",
                source="https://example.com/demo.git",
                ref="main",
            )

            self.assertEqual(findings, [])
            self.assertEqual(warnings, [])

    def test_validate_generated_files_rejects_nextjs_ts_config_when_runtime_prunes_devdeps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "agora-rest-api-debugger",
                        "private": True,
                        "packageManager": "pnpm@9.15.4",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "^15.1.0", "react": "^19.0.0", "react-dom": "^19.0.0"},
                        "devDependencies": {"typescript": "^5.7.0"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (repo_dir / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
            (repo_dir / "next.config.ts").write_text(
                "import type { NextConfig } from 'next'\nconst nextConfig: NextConfig = {}\nexport default nextConfig\n",
                encoding="utf-8",
            )
            (repo_dir / "README.md").write_text("默认访问 http://localhost:3000\n", encoding="utf-8")
            (repo_dir / "Dockerfile").write_text(
                "\n".join(
                    [
                        "FROM node:22-alpine AS build",
                        "WORKDIR /app",
                        "RUN corepack enable",
                        "COPY package.json pnpm-lock.yaml ./",
                        "RUN pnpm install --frozen-lockfile",
                        "COPY . .",
                        "RUN pnpm build && pnpm prune --prod",
                        "FROM node:22-alpine AS runtime",
                        "WORKDIR /app",
                        "COPY --from=build /app /app",
                        'CMD ["./node_modules/.bin/next", "start", "--hostname", "0.0.0.0", "--port", "3000"]',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (repo_dir / "PROJECT_ONBOARDING.md").write_text(
                "\n".join(
                    [
                        "# PROJECT_ONBOARDING",
                        "## 1 项目基础信息",
                        "## 2 代码和版本信息",
                        "## 3 启动信息",
                        "## 4 运行参数",
                        "## 5 配置与密钥",
                        "## 6 存储信息",
                        "## 7 证据与判断说明",
                        "## 8 待确认问题",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            findings, warnings = runner.validate_generated_files(
                repo_dir,
                source_type="git",
                source="https://example.com/demo.git",
                ref="main",
            )

            self.assertIn(
                "Dockerfile prunes devDependencies even though next.config.ts requires TypeScript to remain available at runtime",
                findings,
            )

    def test_parse_onboarding_run_spec_ignores_unknown_placeholder_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            onboarding_path = Path(temp_dir) / "PROJECT_ONBOARDING.md"
            onboarding_path.write_text(
                "\n".join(
                    [
                        "# PROJECT_ONBOARDING",
                        "## 4 运行参数",
                        "- 启动命令：`node scripts/server.js`",
                        "## 5 配置与密钥",
                        "- 已确认环境变量：`UNKNOWN`",
                        "## 6 存储信息",
                        "- 持久化目录：`UNKNOWN`",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            spec = runner.parse_onboarding_run_spec(onboarding_path)

            self.assertEqual(spec["environment_variables"], [])
            self.assertEqual(spec["persistence_paths"], [])

    def test_parse_onboarding_run_spec_keeps_backward_compatibility_for_legacy_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            onboarding_path = Path(temp_dir) / "PROJECT_ONBOARDING.md"
            onboarding_path.write_text(
                "\n".join(
                    [
                        "# PROJECT_ONBOARDING",
                        "## 5 配置与密钥",
                        "- 已确认环境变量：`NONE`",
                        "## 6 存储信息",
                        "- 持久化目录：`NEEDS_CONFIRMATION, TBD - 由管理员构建, NONE`",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            spec = runner.parse_onboarding_run_spec(onboarding_path)

            self.assertEqual(spec["environment_variables"], [])
            self.assertEqual(spec["persistence_paths"], [])

    def test_build_runtime_rules_mentions_nextjs_ts_runtime_constraints(self) -> None:
        rules = runner.build_runtime_rules(
            {
                "service_runtime": "node",
                "python_entry_command": None,
                "node_entry_command": "next start",
                "requires_python": None,
                "package_scripts": ["build", "start"],
                "detected_port": 3000,
                "has_nextjs_ts_config": True,
                "package_manager": "pnpm",
            }
        )

        self.assertIn("next.config.ts", rules)
        self.assertIn("pnpm prune --prod", rules)
        self.assertIn("运行时阶段必须显式执行 `corepack enable`", rules)

    def test_append_warning_helpers_preserve_existing_entries(self) -> None:
        result = {"warnings": ["existing"]}

        runner.append_warning(result, "added")
        runner.extend_warnings(result, ["more"])

        self.assertEqual(result["warnings"], ["existing", "added", "more"])

    def test_detect_python_entrypoint_uses_readme_uvicorn_command_as_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "README.md").write_text(
                "\n".join(
                    [
                        "# Demo",
                        "```bash",
                        "uvicorn app.main:app --host 0.0.0.0 --port 8008",
                        "```",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            app_dir = repo_dir / "app"
            app_dir.mkdir()
            (app_dir / "main.py").write_text(
                "\n".join(
                    [
                        "def create_app():",
                        "    return object()",
                        "",
                        "app = create_app()",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            entry_path, entry_command, _entry_text = runner.detect_python_entrypoint(
                repo_dir,
                readme_text=runner.read_text(repo_dir / "README.md"),
                python_dependencies=["fastapi", "uvicorn"],
            )

            self.assertEqual(entry_path, repo_dir / "README.md")
            self.assertEqual(entry_command, "uvicorn app.main:app --host 0.0.0.0 --port 8008")

    def test_detect_python_entrypoint_prefers_source_over_readme_when_source_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "README.md").write_text(
                "\n".join(
                    [
                        "# Demo",
                        "```bash",
                        "uvicorn app.main:app --host 0.0.0.0 --port 8008",
                        "```",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            app_dir = repo_dir / "app"
            app_dir.mkdir()
            (app_dir / "main.py").write_text(
                "\n".join(
                    [
                        "from fastapi import FastAPI",
                        "",
                        "app = FastAPI()",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            entry_path, entry_command, _entry_text = runner.detect_python_entrypoint(
                repo_dir,
                readme_text=runner.read_text(repo_dir / "README.md"),
                python_dependencies=["fastapi", "uvicorn"],
            )

            self.assertEqual(entry_path, app_dir / "main.py")
            self.assertEqual(entry_command, "uvicorn app.main:app --host 0.0.0.0")

    def test_detect_python_entrypoint_uses_uvicorn_for_fastapi_app_without_main_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            app_dir = repo_dir / "app"
            app_dir.mkdir()
            (app_dir / "main.py").write_text(
                "\n".join(
                    [
                        "from fastapi import FastAPI",
                        "",
                        "app = FastAPI()",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            entry_path, entry_command, _entry_text = runner.detect_python_entrypoint(
                repo_dir,
                python_dependencies=["fastapi", "uvicorn"],
            )

            self.assertEqual(entry_path, app_dir / "main.py")
            self.assertEqual(entry_command, "uvicorn app.main:app --host 0.0.0.0")

    def test_detect_python_entrypoint_keeps_python_module_when_uvicorn_run_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            app_dir = repo_dir / "app"
            app_dir.mkdir()
            (app_dir / "main.py").write_text(
                "\n".join(
                    [
                        "import uvicorn",
                        "from fastapi import FastAPI",
                        "",
                        "app = FastAPI()",
                        "",
                        "if __name__ == \"__main__\":",
                        "    uvicorn.run(app, host=\"0.0.0.0\", port=8008)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            entry_path, entry_command, _entry_text = runner.detect_python_entrypoint(
                repo_dir,
                python_dependencies=["fastapi", "uvicorn"],
            )

            self.assertEqual(entry_path, app_dir / "main.py")
            self.assertEqual(entry_command, "python app/main.py")

    def test_detect_python_entrypoint_keeps_python_module_for_flask_main_program(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "server.py").write_text(
                "\n".join(
                    [
                        "from flask import Flask",
                        "",
                        "app = Flask(__name__)",
                        "",
                        "if __name__ == \"__main__\":",
                        "    app.run(host=\"0.0.0.0\", port=5000)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            entry_path, entry_command, _entry_text = runner.detect_python_entrypoint(
                repo_dir,
                python_dependencies=["flask"],
            )

            self.assertEqual(entry_path, repo_dir / "server.py")
            self.assertEqual(entry_command, "python server.py")

    def test_detect_python_entrypoint_prefers_script_for_flask_program_with_local_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            web_dir = repo_dir / "web"
            analyzer_dir = web_dir / "analyzer"
            analyzer_dir.mkdir(parents=True)
            (analyzer_dir / "__init__.py").write_text("def analyze():\n    return 'ok'\n", encoding="utf-8")
            app_path = web_dir / "app.py"
            app_path.write_text(
                "\n".join(
                    [
                        "from flask import Flask",
                        "from analyzer import analyze",
                        "",
                        "app = Flask(__name__)",
                        "",
                        "if __name__ == \"__main__\":",
                        "    app.run(host=\"0.0.0.0\", port=5050)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            entry_path, entry_command, _entry_text = runner.detect_python_entrypoint(
                repo_dir,
                python_dependencies=["flask"],
            )

            self.assertEqual(entry_path, app_path)
            self.assertEqual(entry_command, "python web/app.py")

    def test_collect_repo_analysis_prefers_source_entrypoint_for_fastapi_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "pyproject.toml").write_text(
                "\n".join(
                    [
                        "[project]",
                        'name = "demo-fastapi"',
                        'requires-python = ">=3.11,<3.12"',
                        "dependencies = [",
                        '  "fastapi>=0.115.0,<0.116",',
                        '  "uvicorn[standard]>=0.32.0,<0.33",',
                        "]",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (repo_dir / "README.md").write_text(
                "\n".join(
                    [
                        "# Demo",
                        "```bash",
                        "uvicorn app.main:app --host 0.0.0.0 --port 8008",
                        "```",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            app_dir = repo_dir / "app"
            app_dir.mkdir()
            (app_dir / "main.py").write_text(
                "\n".join(
                    [
                        "from fastapi import FastAPI",
                        "",
                        "app = FastAPI()",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            analysis = runner.collect_repo_analysis(repo_dir, "local", str(repo_dir), None)

            self.assertEqual(analysis["service_runtime"], "python")
            self.assertEqual(analysis["python_entry_command"], "uvicorn app.main:app --host 0.0.0.0")


if __name__ == "__main__":
    unittest.main()
