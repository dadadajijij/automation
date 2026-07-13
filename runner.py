#!/usr/bin/env python3
import argparse
import hashlib
import http.client
import json
import os
import re
import shlex
import selectors
import signal
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from subpath import (
    apply_nextjs_subpath_adapter as subpath_apply_nextjs_subpath_adapter,
    apply_subpath_rewrites as subpath_apply_subpath_rewrites,
    apply_vite_subpath_adapter as subpath_apply_vite_subpath_adapter,
    apply_framework_config_adapters as subpath_apply_framework_config_adapters,
    auto_fix_nextjs_subpath_issues as subpath_auto_fix_nextjs_subpath_issues,
    auto_fix_subpath_issues as subpath_auto_fix_subpath_issues,
    build_subpath_plan as subpath_build_subpath_plan,
    auto_fix_findings as subpath_auto_fix_findings,
    run_build_output_subpath_audit as subpath_run_build_output_subpath_audit,
    detect_runtime_root_evidence as subpath_detect_runtime_root_evidence,
    ensure_cra_homepage as subpath_ensure_cra_homepage,
    ensure_next_link_import as subpath_ensure_next_link_import,
    ensure_nextjs_basepath_config as subpath_ensure_nextjs_basepath_config,
    ensure_nextjs_helper_module as subpath_ensure_nextjs_helper_module,
    ensure_vite_base_config as subpath_ensure_vite_base_config,
    ensure_vue_cli_public_path as subpath_ensure_vue_cli_public_path,
    insert_import_after_directives as subpath_insert_import_after_directives,
    NEXTJS_RUNTIME_SHIM_BASENAME as SUBPATH_NEXTJS_RUNTIME_SHIM_BASENAME,
    NEXTJS_WINDOW_TYPES_BASENAME as SUBPATH_NEXTJS_WINDOW_TYPES_BASENAME,
    SubpathAuditFinding,
    SUBPATH_ALLOWED_ROOT_COMMENT,
    SUBPATH_BROWSER_ATTRS,
    SUBPATH_CLIENT_METHOD_PATTERNS,
    SUBPATH_TEMPLATE_ALLOWLIST_FIELDS,
    build_deployment_base_path,
    collect_python_frontend_hint_files as subpath_collect_python_frontend_hint_files,
    detect_frontend_root_hints_from_python_file as subpath_detect_frontend_root_hints_from_python_file,
    detect_frontend_runtime_roots as subpath_detect_frontend_runtime_roots,
    detect_frontend_runtime_root_groups as subpath_detect_frontend_runtime_root_groups,
    detect_static_root_hints_from_node_entry as subpath_detect_static_root_hints_from_node_entry,
    discover_runtime_root_frontend_files as subpath_discover_runtime_root_frontend_files,
    find_subpath_audit_targets as subpath_find_subpath_audit_targets,
    find_plan_rewrite_targets as subpath_find_plan_rewrite_targets,
    findings_to_error_texts as subpath_findings_to_error_texts,
    frontend_runtime_root as subpath_frontend_runtime_root,
    is_create_react_app_project as subpath_is_create_react_app_project,
    is_allowed_vite_root_html_url as subpath_is_allowed_vite_root_html_url,
    is_browser_facing_source as subpath_is_browser_facing_source,
    is_express_static_project as subpath_is_express_static_project,
    is_nextjs_project as subpath_is_nextjs_project,
    is_runtime_root_relative_file as subpath_is_runtime_root_relative_file,
    is_server_side_code_file as subpath_is_server_side_code_file,
    is_static_html_project as subpath_is_static_html_project,
    is_vite_project as subpath_is_vite_project,
    is_vue_cli_project as subpath_is_vue_cli_project,
    normalize_python_hint_path as subpath_normalize_python_hint_path,
    prepare_subpath_sources as subpath_prepare_subpath_sources,
    resolve_entrypoint_relative_dir as subpath_resolve_entrypoint_relative_dir,
    resolve_python_hint_directory as subpath_resolve_python_hint_directory,
    rewrite_frontend_subpath_urls as subpath_rewrite_frontend_subpath_urls,
    rewrite_frontend_html_attribute_urls as subpath_rewrite_frontend_html_attribute_urls,
    rewrite_frontend_html_link_urls as subpath_rewrite_frontend_html_link_urls,
    rewrite_frontend_html_script_urls as subpath_rewrite_frontend_html_script_urls,
    rewrite_frontend_html_form_action_urls as subpath_rewrite_frontend_html_form_action_urls,
    rewrite_frontend_client_request_urls as subpath_rewrite_frontend_client_request_urls,
    rewrite_frontend_request_api_urls as subpath_rewrite_frontend_request_api_urls,
    rewrite_frontend_navigation_urls as subpath_rewrite_frontend_navigation_urls,
    rewrite_frontend_eventsource_urls as subpath_rewrite_frontend_eventsource_urls,
    rewrite_frontend_return_value_urls as subpath_rewrite_frontend_return_value_urls,
    rewrite_origin_based_subpath_logic as subpath_rewrite_origin_based_subpath_logic,
    run_runtime_subpath_audit as subpath_run_runtime_subpath_audit,
    run_static_subpath_audit as subpath_run_static_subpath_audit,
    runtime_subpath_phase as subpath_runtime_subpath_phase,
    scan_subpath_findings as subpath_scan_subpath_findings,
    vite_project_roots as subpath_vite_project_roots,
    workspace_frontend_package_dirs as subpath_workspace_frontend_package_dirs,
)


ROOT_DIR = Path(__file__).resolve().parent.parent
AUTOMATION_DIR = ROOT_DIR / "automation"
JOBS_DIR = AUTOMATION_DIR / "jobs"
CODEX_HOME_CACHE_DIR = AUTOMATION_DIR / "codex-home-cache"
GENERATION_RULES_PATH = AUTOMATION_DIR / "generation_rules.md"
PROJECT_PORTS_PATH = AUTOMATION_DIR / "project-ports.json"
LOCAL_OFFICIAL_IMAGES_CACHE_PATH = CODEX_HOME_CACHE_DIR / "local-official-images.txt"
DEFAULT_CODEX_HOME_SEED = Path.home() / ".codex"
SANDBOX_PODMAN_ROOT = Path("/tmp/cdx")
DEFAULT_PROJECT_HOST_PORT_BASE = 8003
DEFAULT_PROJECT_BIND_HOST = "127.0.0.1"

CODEX_GENERATION_TIMEOUT_SECONDS = 600
CODEX_GENERATION_HEARTBEAT_SECONDS = 10
CODEX_GENERATION_MAX_ATTEMPTS = 2
CODEX_GENERATION_RETRY_DELAY_SECONDS = 2
GIT_CLONE_TIMEOUT_SECONDS = 180
GIT_CLONE_MAX_ATTEMPTS = 3
GIT_CLONE_RETRY_DELAY_SECONDS = 2
RUN_READY_TIMEOUT_SECONDS = 60
RUN_READY_POLL_INTERVAL_SECONDS = 2
DEFAULT_EXTERNAL_ACCESS_HOST = "athena.agoralab.co"
ATHENA_NGINX_CONFIG_PATH = Path(os.environ.get("ATHENA_NGINX_CONFIG_PATH", "/etc/nginx/sites-available/tools.conf"))
ATHENA_NGINX_TOOL_BLOCK_PREFIX = "KA TOOL"
ATHENA_NGINX_SUDO_PREFIX = ["sudo", "-n"]


class RunnerArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


@dataclass
class CommandResult:
    args: List[str]
    returncode: int
    stdout: str


def utc_now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def default_job_id() -> str:
    return str(int(datetime.now(timezone.utc).timestamp()))


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "project"


def normalize_local_copy_slug(value: str) -> str:
    slug = slugify(value)
    match = re.fullmatch(r"(?P<base>[a-z0-9]+(?:-[a-z0-9]+)+)-(?P<copy_number>\d+)", slug)
    if not match:
        return slug
    base = match.group("base")
    if base.count("-") < 2:
        return slug
    return base


def read_package_json_project_name(source_dir: Path) -> Optional[str]:
    package_json_path = source_dir / "package.json"
    if not package_json_path.is_file():
        return None
    try:
        payload = json.loads(read_text(package_json_path))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    return name.strip()


def read_pyproject_project_name(source_dir: Path) -> Optional[str]:
    pyproject_path = source_dir / "pyproject.toml"
    if not pyproject_path.is_file():
        return None
    try:
        content = read_text(pyproject_path)
    except OSError:
        return None

    in_project_section = False
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project_section = stripped == "[project]"
            continue
        if not in_project_section:
            continue
        match = re.match(r'name\s*=\s*["\']([^"\']+)["\']', stripped)
        if match:
            name = match.group(1).strip()
            return name or None
    return None


def derive_local_project_slug(source: str) -> str:
    source_dir = Path(source)
    for reader in (read_package_json_project_name, read_pyproject_project_name):
        project_name = reader(source_dir)
        if project_name:
            return slugify(project_name)
    return normalize_local_copy_slug(source_dir.name)


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def append_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_json(path: Path) -> Optional[Dict[str, object]]:
    if not path.exists():
        return None
    payload = json.loads(read_text(path))
    return payload if isinstance(payload, dict) else None


def read_subpath_declaration(repo_dir: Path) -> Optional[Dict[str, object]]:
    declaration_path = repo_dir / ".ka" / "subpath.json"
    try:
        return read_json(declaration_path)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def summarize_subpath_declaration(declaration: Optional[Dict[str, object]]) -> Optional[Dict[str, object]]:
    if not isinstance(declaration, dict):
        return None
    projects = declaration.get("projects", [])
    build_output_policy = declaration.get("build_output_policy", {})
    default_project = declaration.get("default_project")
    return {
        "project_count": len(projects) if isinstance(projects, list) else 0,
        "has_build_output_policy": isinstance(build_output_policy, dict) and bool(build_output_policy),
        "declared_default_project": default_project if isinstance(default_project, str) and default_project.strip() else None,
    }


def subpath_plan_from_payload(plan_payload: object, repo_dir: Path) -> Optional[object]:
    if not isinstance(plan_payload, dict):
        return None
    try:
        from subpath.models import FrontendProjectStrategy, SubpathPlan

        projects = tuple(
            FrontendProjectStrategy(
                project_id=str(item.get("project_id", "")),
                framework=str(item.get("framework", "")),
                proxy_mode=str(item.get("proxy_mode", "")),
                adapter=str(item.get("adapter", "")),
                source_adapter=str(item.get("source_adapter", "static_rewrite")),
                project_root=Path(str(item.get("project_root", repo_dir))),
                source_roots=tuple(Path(str(path)) for path in item.get("source_roots", [])),
                runtime_roots=tuple(Path(str(path)) for path in item.get("runtime_roots", [])),
                config_files=tuple(Path(str(path)) for path in item.get("config_files", [])),
                capabilities=tuple(str(item2) for item2 in item.get("capabilities", [])),
                evidence=tuple(str(item2) for item2 in item.get("evidence", [])),
                runtime_entry_hint=str(item.get("runtime_entry_hint")) if item.get("runtime_entry_hint") is not None else None,
                runtime_entry_candidates=tuple(str(item2) for item2 in item.get("runtime_entry_candidates", [])),
            )
            for item in plan_payload.get("projects", [])
            if isinstance(item, dict)
        )
        return SubpathPlan(
            projects=projects,
            default_project=plan_payload.get("default_project"),
            notes=tuple(str(item) for item in plan_payload.get("notes", [])),
        )
    except Exception:
        return None


def build_runtime_subpath_plan(repo_dir: Path):
    return subpath_build_subpath_plan(
        repo_dir,
        parse_package_json=parse_package_json,
        detect_node_entry_script_paths=detect_node_entry_script_paths,
        read_text_if_exists=read_text_if_exists,
        collect_python_frontend_hint_files_fn=collect_python_frontend_hint_files,
        workspace_frontend_package_dirs_fn=workspace_frontend_package_dirs,
        vite_project_roots_fn=vite_project_roots,
        collect_matching_files=collect_matching_files,
        discover_workspace_packages=discover_workspace_packages,
    )


def build_runtime_root_evidence(repo_dir: Path):
    return subpath_detect_runtime_root_evidence(
        repo_dir,
        parse_package_json=parse_package_json,
        detect_node_entry_script_paths=detect_node_entry_script_paths,
        read_text_if_exists=read_text_if_exists,
        collect_python_frontend_hint_files_fn=collect_python_frontend_hint_files,
        workspace_frontend_package_dirs_fn=workspace_frontend_package_dirs,
        collect_matching_files=collect_matching_files,
    )


def json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, set):
        return [json_safe(item) for item in sorted(value, key=lambda item: str(item))]
    if hasattr(value, "__dataclass_fields__"):
        return json_safe(vars(value))
    return str(value)


def json_dumps_safe(value: object, *, indent: Optional[int] = None) -> str:
    return json.dumps(json_safe(value), ensure_ascii=False, indent=indent)


def write_json(path: Path, payload: Dict[str, object]) -> None:
    write_text(path, json_dumps_safe(payload, indent=2) + "\n")


def sorted_unique(items: Iterable[str]) -> List[str]:
    return sorted({item for item in items if item})


def ordered_unique(items: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def append_warning(result: Dict[str, object], warning: str) -> None:
    if not warning:
        return
    warnings = result.setdefault("warnings", [])
    if not isinstance(warnings, list):
        warnings = []
        result["warnings"] = warnings
    warnings.append(warning)


def extend_warnings(result: Dict[str, object], warnings_to_add: Iterable[str]) -> None:
    for warning in warnings_to_add:
        append_warning(result, warning)


def summarize_command_args(args: List[str]) -> str:
    if len(args) >= 2 and args[0] == "codex" and args[1] == "exec":
        return '["codex","exec","<prompt omitted>"]'
    return json_dumps_safe(args)


def build_sudo_command(args: List[str]) -> List[str]:
    return [*ATHENA_NGINX_SUDO_PREFIX, *args]


def run_sudo_command(
    args: List[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    log_path: Optional[Path] = None,
    runner_log_path: Optional[Path] = None,
    timeout: Optional[int] = None,
    heartbeat_seconds: Optional[int] = None,
    stream_log_label: Optional[str] = None,
) -> CommandResult:
    if not command_exists("sudo"):
        raise OSError("sudo not found in PATH")
    return run_command(
        build_sudo_command(args),
        cwd=cwd,
        env=env,
        log_path=log_path,
        runner_log_path=runner_log_path,
        timeout=timeout,
        heartbeat_seconds=heartbeat_seconds,
        stream_log_label=stream_log_label,
    )


def project_jobs_dir(project_slug: str) -> Path:
    return JOBS_DIR / project_slug


def project_shared_repo_dir(project_slug: str) -> Path:
    return project_jobs_dir(project_slug) / "repo"


def project_shared_repo_metadata_path(project_slug: str) -> Path:
    return project_jobs_dir(project_slug) / "repo-state.json"


def project_runtime_data_dir(project_slug: str, job_id: str) -> Path:
    return project_jobs_dir(project_slug) / job_id / "runtime-data"


def project_job_work_repo_dir(project_slug: str, job_id: str) -> Path:
    return project_jobs_dir(project_slug) / job_id / "work-repo"


def derive_project_slug(source_type: str, source: str) -> str:
    if source_type == "git":
        parsed = urlparse(source)
        name = Path(parsed.path).name
        if name.endswith(".git"):
            name = name[:-4]
        return slugify(name)
    return derive_local_project_slug(source)


def copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def is_git_tracked_file(repo_dir: Path, path: Path) -> bool:
    try:
        relative_path = path.relative_to(repo_dir).as_posix()
    except ValueError:
        return False
    if not relative_path:
        return False
    result = run_command(
        ["git", "ls-files", "--error-unmatch", "--", relative_path],
        cwd=repo_dir,
    )
    return result.returncode == 0


def copytree_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        shutil.copytree(src, dst, dirs_exist_ok=True)


def append_stream_chunk(path: Path, label: str, chunk: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    lines = chunk.splitlines(keepends=True)
    if not lines:
        lines = [chunk]
    for line in lines:
        suffix = "" if line.endswith("\n") else "\n"
        append_text(path, f"[{timestamp}] {label} {line}{suffix}")


def run_command(
    args: List[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    log_path: Optional[Path] = None,
    runner_log_path: Optional[Path] = None,
    timeout: Optional[int] = None,
    heartbeat_seconds: Optional[int] = None,
    stream_log_label: Optional[str] = None,
) -> CommandResult:
    started_at = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    next_heartbeat_at = (
        started_monotonic + heartbeat_seconds
        if heartbeat_seconds is not None and heartbeat_seconds > 0
        else None
    )
    if runner_log_path is not None:
        append_text(
            runner_log_path,
            f"[{started_at.isoformat()}] command_start cwd={cwd or Path.cwd()} args={json.dumps(args, ensure_ascii=False)}\n",
        )

    log_handle = None
    try:
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = log_path.open("w", encoding="utf-8")

        process = subprocess.Popen(
            args,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

        selector = selectors.DefaultSelector()
        assert process.stdout is not None
        assert process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ)
        selector.register(process.stderr, selectors.EVENT_READ)

        output_chunks: List[str] = []
        deadline = started_monotonic + timeout if timeout is not None else None

        while selector.get_map():
            now = time.monotonic()
            wait_until: List[float] = []
            if deadline is not None:
                wait_until.append(max(0.0, deadline - now))
            if next_heartbeat_at is not None:
                wait_until.append(max(0.0, next_heartbeat_at - now))
            select_timeout = min(wait_until) if wait_until else None

            events = selector.select(select_timeout)
            now = time.monotonic()

            if deadline is not None and now >= deadline and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                remaining_output = "".join(output_chunks)
                if log_handle is not None:
                    log_handle.write(f"\n[timeout] command exceeded {timeout}s\n")
                    log_handle.flush()
                if runner_log_path is not None:
                    append_text(
                        runner_log_path,
                        f"[{datetime.now(timezone.utc).isoformat()}] command_timeout args={summarize_command_args(args)} timeout_seconds={timeout}\n",
                    )
                raise subprocess.TimeoutExpired(args, timeout, output=remaining_output)

            if next_heartbeat_at is not None and now >= next_heartbeat_at and process.poll() is None:
                if runner_log_path is not None:
                    elapsed_seconds = int(now - started_monotonic)
                    append_text(
                        runner_log_path,
                        f"[{datetime.now(timezone.utc).isoformat()}] command_heartbeat elapsed_seconds={elapsed_seconds} args={summarize_command_args(args)}\n",
                    )
                next_heartbeat_at = now + (heartbeat_seconds or 0)

            for key, _ in events:
                chunk = key.fileobj.readline()
                if chunk == "":
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                output_chunks.append(chunk)
                if log_handle is not None:
                    log_handle.write(chunk)
                    log_handle.flush()
                if runner_log_path is not None and stream_log_label is not None:
                    stream_name = "stdout" if key.fileobj is process.stdout else "stderr"
                    append_stream_chunk(runner_log_path, f"{stream_log_label}_{stream_name}", chunk)

        returncode = process.wait()
        completed = CommandResult(args=args, returncode=returncode, stdout="".join(output_chunks))
    finally:
        if log_handle is not None:
            log_handle.close()

    if runner_log_path is not None:
        append_text(
            runner_log_path,
            f"[{datetime.now(timezone.utc).isoformat()}] command_end returncode={completed.returncode} args={summarize_command_args(args)}\n",
        )
    return completed


def list_local_official_docker_images_from_env() -> List[str]:
    raw = os.environ.get("AUTOMATION_LOCAL_OFFICIAL_IMAGES", "").strip()
    if not raw:
        return []
    return sorted_unique(item.strip() for item in raw.split(","))


def list_local_official_docker_images_from_cache() -> List[str]:
    if not LOCAL_OFFICIAL_IMAGES_CACHE_PATH.exists():
        return []
    raw = read_text(LOCAL_OFFICIAL_IMAGES_CACHE_PATH).strip()
    if not raw:
        return []
    return sorted_unique(item.strip() for item in raw.split(","))


def list_local_official_docker_images_fallback() -> List[str]:
    if not command_exists("podman"):
        return []
    result = run_command(["podman", "images", "--format", "{{.Repository}}:{{.Tag}}"])
    if result.returncode != 0:
        return []
    images = []
    for line in result.stdout.splitlines():
        item = line.strip()
        if item.startswith("docker.io/library/") and not item.endswith(":<none>"):
            images.append(item)
    return sorted_unique(images)


def list_local_official_docker_images() -> List[str]:
    from_cache = list_local_official_docker_images_from_cache()
    if from_cache:
        return from_cache
    from_env = list_local_official_docker_images_from_env()
    if from_env:
        return from_env
    return list_local_official_docker_images_fallback()


def is_valid_port_mapping(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"\d{2,5}:\d{2,5}", value))


def load_project_port_configs() -> Dict[str, Dict[str, object]]:
    payload = read_json(PROJECT_PORTS_PATH)
    if not isinstance(payload, dict):
        return {}
    result: Dict[str, Dict[str, object]] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, dict):
            port_value = value.get("port")
            if is_valid_port_mapping(port_value):
                result[key] = dict(value)
    return result


def save_project_port_configs(configs: Dict[str, Dict[str, object]]) -> None:
    updated: Dict[str, object] = {}
    for project_slug, config in configs.items():
        if not isinstance(project_slug, str) or not project_slug:
            continue
        if not isinstance(config, dict):
            continue
        port_value = config.get("port")
        if not is_valid_port_mapping(port_value):
            continue
        normalized: Dict[str, object] = {"port": str(port_value)}
        volume_specs = config.get("volumes")
        if isinstance(volume_specs, list):
            volumes = [item for item in volume_specs if isinstance(item, str) and item.strip()]
            if volumes:
                normalized["volumes"] = volumes
        env_config = config.get("env")
        if isinstance(env_config, dict):
            env_map = {
                key: value
                for key, value in env_config.items()
                if isinstance(key, str) and key and isinstance(value, str)
            }
            if env_map:
                normalized["env"] = env_map
        env_file = config.get("env_file")
        if isinstance(env_file, str) and env_file.strip():
            normalized["env_file"] = env_file.strip()
        updated[project_slug] = normalized
    write_text(PROJECT_PORTS_PATH, json.dumps(updated, ensure_ascii=False, indent=2) + "\n")


def load_project_runtime_overrides(project_slug: str) -> Dict[str, object]:
    project_config = load_project_port_configs().get(project_slug)
    if not isinstance(project_config, dict):
        return {}

    overrides: Dict[str, object] = {}
    port_value = project_config.get("port")
    if isinstance(port_value, str) and re.fullmatch(r"\d{2,5}:\d{2,5}", port_value):
        overrides["port"] = port_value

    volume_specs = project_config.get("volumes")
    if isinstance(volume_specs, list):
        volumes = [
            item
            for item in volume_specs
            if isinstance(item, str) and item.strip()
        ]
        if volumes:
            overrides["volumes"] = volumes

    env_config = project_config.get("env")
    if isinstance(env_config, dict):
        env_map = {
            key: value
            for key, value in env_config.items()
            if isinstance(key, str) and key and isinstance(value, str)
        }
        if env_map:
            overrides["env"] = env_map

    env_file = project_config.get("env_file")
    if isinstance(env_file, str) and env_file.strip():
        overrides["env_file"] = env_file.strip()

    return overrides


def resolve_project_port_mapping(project_slug: str, container_port: int) -> Tuple[int, int]:
    port_configs = load_project_port_configs()
    existing_config = port_configs.get(project_slug)
    if isinstance(existing_config, dict) and is_valid_port_mapping(existing_config.get("port")):
        host_text, container_text = str(existing_config["port"]).split(":", 1)
        return int(host_text), int(container_text)
    used_host_ports = {
        int(str(value.get("port")).split(":", 1)[0])
        for value in port_configs.values()
        if isinstance(value, dict) and is_valid_port_mapping(value.get("port"))
    }
    candidate = DEFAULT_PROJECT_HOST_PORT_BASE
    while candidate in used_host_ports:
        candidate += 1
    updated_config = dict(existing_config) if isinstance(existing_config, dict) else {}
    updated_config["port"] = f"{candidate}:{container_port}"
    port_configs[project_slug] = updated_config
    save_project_port_configs(port_configs)
    return candidate, container_port


def path_within_limit(base: Path, *parts: str, limit: int = 50) -> Path:
    candidate = base.joinpath(*parts)
    if len(str(candidate)) > limit:
        raise ValueError(f"Path exceeds Podman limit of {limit} characters: {candidate}")
    return candidate


def build_podman_workspace_name(project_slug: str, job_id: str) -> str:
    workspace_name = f"{project_slug}{job_id}"
    runroot_candidate = SANDBOX_PODMAN_ROOT / workspace_name / "runroot"
    if len(str(runroot_candidate)) <= 50:
        return workspace_name

    max_project_len = 50 - len(str(SANDBOX_PODMAN_ROOT / job_id / "runroot"))
    if max_project_len <= 0:
        raise ValueError("Job ID is too long for Podman runroot path constraints")

    shortened_project = project_slug[:max_project_len]
    workspace_name = f"{shortened_project}{job_id}"
    path_within_limit(SANDBOX_PODMAN_ROOT / workspace_name, "runroot")
    return workspace_name


def prepare_podman_environment(project_slug: str, job_id: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    workspace_name = build_podman_workspace_name(project_slug, job_id)
    job_root = SANDBOX_PODMAN_ROOT / workspace_name
    runtime_dir = job_root / "xdg"
    runroot = path_within_limit(job_root, "runroot")
    graphroot = job_root / "graphroot"
    tmpdir = job_root / "tmp"
    config_dir = job_root / "config"
    storage_conf = config_dir / "storage.conf"

    for path in (runtime_dir, runroot, graphroot, tmpdir, config_dir):
        ensure_dir(path)

    write_text(
        storage_conf,
        textwrap.dedent(
            f"""
            [storage]
            driver = "overlay"
            runroot = "{runroot}"
            graphroot = "{graphroot}"

            [storage.options]
            """
        ).strip()
        + "\n",
    )

    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = str(runtime_dir)
    env["TMPDIR"] = str(tmpdir)
    env["CONTAINERS_STORAGE_CONF"] = str(storage_conf)
    env["AUTOMATION_PODMAN_MODE"] = "isolated"
    env["AUTOMATION_PODMAN_GRAPHROOT"] = str(graphroot)
    env["AUTOMATION_PODMAN_RUNROOT"] = str(runroot)
    env["AUTOMATION_PODMAN_TMPDIR"] = str(tmpdir)

    metadata = {
        "podman_runtime_dir": str(runtime_dir),
        "podman_runroot": str(runroot),
        "podman_graphroot": str(graphroot),
        "podman_tmpdir": str(tmpdir),
        "podman_storage_conf": str(storage_conf),
        "podman_mode": "isolated",
    }
    return env, metadata


def prepare_default_podman_environment() -> Tuple[Dict[str, str], Dict[str, str]]:
    env = dict(os.environ)
    env["AUTOMATION_PODMAN_MODE"] = "default"
    return env, {"podman_mode": "default"}


def build_podman_command(base_args: List[str], podman_env: Dict[str, str]) -> List[str]:
    command = ["podman"]
    if podman_env.get("AUTOMATION_PODMAN_MODE") == "isolated":
        graphroot = podman_env.get("AUTOMATION_PODMAN_GRAPHROOT")
        runroot = podman_env.get("AUTOMATION_PODMAN_RUNROOT")
        tmpdir = podman_env.get("AUTOMATION_PODMAN_TMPDIR")
        if graphroot:
            command.extend(["--root", graphroot])
        if runroot:
            command.extend(["--runroot", runroot])
        if tmpdir:
            command.extend(["--tmpdir", tmpdir])
    command.extend(base_args)
    return command


def refresh_codex_home_cache() -> None:
    ensure_dir(CODEX_HOME_CACHE_DIR)
    for relative in ("auth.json", "config.toml", "installation_id"):
        copy_if_exists(DEFAULT_CODEX_HOME_SEED / relative, CODEX_HOME_CACHE_DIR / relative)
    copytree_if_exists(DEFAULT_CODEX_HOME_SEED / "rules", CODEX_HOME_CACHE_DIR / "rules")
    copytree_if_exists(DEFAULT_CODEX_HOME_SEED / "skills", CODEX_HOME_CACHE_DIR / "skills")
    if not LOCAL_OFFICIAL_IMAGES_CACHE_PATH.exists():
        write_text(LOCAL_OFFICIAL_IMAGES_CACHE_PATH, "")


def prepare_codex_home(job_dir: Path) -> Path:
    refresh_codex_home_cache()
    codex_home = job_dir / "codex-home"
    ensure_dir(codex_home / "tmp")
    ensure_dir(codex_home / "memories")
    for relative in ("auth.json", "config.toml", "installation_id"):
        copy_if_exists(CODEX_HOME_CACHE_DIR / relative, codex_home / relative)
    copytree_if_exists(CODEX_HOME_CACHE_DIR / "rules", codex_home / "rules")
    copytree_if_exists(CODEX_HOME_CACHE_DIR / "skills", codex_home / "skills")
    return codex_home


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def iter_local_source_files(src: Path, dst: Path) -> List[Path]:
    files: List[Path] = []
    for root, dirs, filenames in os.walk(src):
        current = Path(root)
        names = list(dirs) + list(filenames)
        ignored = {".git", "node_modules", "__pycache__", ".DS_Store", ".codex", ".agents"}
        for name in names:
            candidate = (current / name).resolve()
            if candidate == dst or _is_relative_to(dst, candidate):
                ignored.add(name)
        dirs[:] = sorted(name for name in dirs if name not in ignored)
        for filename in sorted(filenames):
            if filename in ignored:
                continue
            files.append(current / filename)
    return files


def local_source_signature(source: Path, dst: Path) -> Dict[str, object]:
    hasher = hashlib.sha256()
    for file_path in iter_local_source_files(source.resolve(), dst):
        relative = file_path.relative_to(source.resolve()).as_posix()
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0")
        if file_path.is_symlink():
            hasher.update(b"symlink\0")
            hasher.update(os.readlink(file_path).encode("utf-8"))
        else:
            hasher.update(b"file\0")
            hasher.update(file_path.read_bytes())
        hasher.update(b"\0")
    return {
        "source_type": "local",
        "fingerprint": hasher.hexdigest(),
    }


def git_source_signature(source: str, ref: Optional[str]) -> Dict[str, object]:
    return {
        "source_type": "git",
        "source": source,
        "ref": ref,
    }


def shared_repo_matches(
    signature: Dict[str, object],
    shared_repo_dir: Path,
    shared_repo_metadata_path: Path,
) -> bool:
    existing = read_json(shared_repo_metadata_path)
    if existing != signature:
        return False
    if not shared_repo_dir.exists():
        return False
    return True


def sync_local_source(src: Path, dst: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(f"Local source path does not exist: {src}")
    if not src.is_dir():
        raise ValueError("Local source path must be a directory")

    def ignore(path: str, names: List[str]) -> List[str]:
        ignored = {".git", "node_modules", "__pycache__"}
        current = Path(path)
        for name in names:
            candidate = current / name
            if candidate.resolve() == dst.resolve() or _is_relative_to(dst, candidate):
                ignored.add(name)
        if current.resolve() == src.resolve():
            ignored.update({"automation", "jobs"})
        return sorted(ignored.intersection(names))

    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=ignore)


def github_https_to_ssh_url(source: str) -> Optional[str]:
    if not source.startswith("https://github.com/"):
        return None
    parsed = urlparse(source)
    repo_path = parsed.path.lstrip("/")
    if not repo_path:
        return None
    return f"git@github.com:{repo_path}"


def should_retry_git_failure(output: str) -> bool:
    lowered = output.lower()
    non_retryable_markers = [
        "repository not found",
        "remote branch",
        "authentication failed",
        "invalid username or token",
        "could not read username",
        "terminal prompts disabled",
        "permission denied",
    ]
    if any(marker in lowered for marker in non_retryable_markers):
        return False
    retryable_markers = [
        "could not resolve host",
        "failed to connect to github.com port 443",
        "connection timed out",
        "timed out",
        "http/2 stream",
        "http2 framing layer",
        "expected flush after ref listing",
        "rpc failed",
        "connection reset",
        "tls",
        "internal error",
        "502 bad gateway",
        "upstream request failed",
    ]
    return any(marker in lowered for marker in retryable_markers)


def diagnose_git_failure(output: str) -> Optional[str]:
    lowered = output.lower()
    if "could not read username" in lowered or "terminal prompts disabled" in lowered:
        return "Git fetch failed because HTTPS authentication is required but no non-interactive credentials were provided."
    if "invalid username or token" in lowered or "authentication failed" in lowered:
        return "Git fetch failed because the provided GitHub token is invalid or expired."
    if "permission denied (publickey)" in lowered:
        return "Git fetch failed because SSH authentication was attempted but the configured SSH key was not accepted."
    if "could not resolve host" in lowered:
        return "Git fetch failed because DNS could not resolve github.com from the current environment."
    if "failed to connect to github.com port 443" in lowered or "connection timed out" in lowered:
        return "Git fetch failed because the current environment could not establish an HTTPS connection to GitHub."
    if "http/2 stream" in lowered or "http2 framing layer" in lowered or "expected flush after ref listing" in lowered:
        return "Git fetch hit an unstable HTTP/2 transport error; retrying with HTTP/1.1 is appropriate."
    if "rpc failed" in lowered:
        return "Git fetch failed during remote transfer; this is often a transient network or transport-layer issue."
    if "repository not found" in lowered and "github.com" in lowered:
        return "Git fetch failed because the repository is private or unavailable to the current credentials."
    return None


def clone_git_source(source: str, ref: Optional[str], dst: Path, log_path: Path, runner_log_path: Optional[Path]) -> None:
    remove_path(dst)
    last_output = ""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    https_askpass_path: Optional[Path] = None

    def build_clone_cmd(source_url: str, transport: str) -> List[str]:
        clone_cmd = ["git"]
        if transport == "https":
            clone_cmd.extend(["-c", "http.version=HTTP/1.1"])
        clone_cmd.extend(["clone", "--depth", "1"])
        if ref:
            clone_cmd.extend(["--branch", ref])
        clone_cmd.extend([source_url, str(dst)])
        return clone_cmd

    def build_env(source_url: str, transport: str) -> Dict[str, str]:
        nonlocal https_askpass_path
        git_env = dict(os.environ)
        git_env["GIT_TERMINAL_PROMPT"] = "0"
        if transport == "https" and source_url.startswith("https://github.com/") and token:
            https_askpass_path = Path(tempfile.mkdtemp(prefix="git-askpass-", dir="/tmp")) / "askpass.sh"
            write_text(
                https_askpass_path,
                textwrap.dedent(
                    """\
                    #!/bin/sh
                    case "$1" in
                      *Username*) printf '%s\n' "x-access-token" ;;
                      *Password*) printf '%s\n' "$GITHUB_TOKEN" ;;
                      *) printf '%s\n' "" ;;
                    esac
                    """
                ),
            )
            https_askpass_path.chmod(0o700)
            git_env["GIT_ASKPASS"] = str(https_askpass_path)
            git_env["GITHUB_TOKEN"] = token
        return git_env

    source_attempts: List[Tuple[str, str]] = []
    ssh_url = github_https_to_ssh_url(source)
    if ssh_url:
        source_attempts.append((ssh_url, "ssh"))
    source_attempts.append((source, "https" if source.startswith("https://") else "ssh"))

    try:
        for source_url, transport in source_attempts:
            append_text(log_path, f"\n[git_clone_strategy] transport={transport} source={source_url}\n")
            for attempt in range(1, GIT_CLONE_MAX_ATTEMPTS + 1):
                if https_askpass_path is not None:
                    remove_path(https_askpass_path.parent)
                    https_askpass_path = None
                clone_cmd = build_clone_cmd(source_url, transport)
                git_env = build_env(source_url, transport)
                append_text(
                    log_path,
                    f"[git_clone_attempt] transport={transport} attempt={attempt} max_attempts={GIT_CLONE_MAX_ATTEMPTS} args={json.dumps(clone_cmd, ensure_ascii=False)}\n",
                )
                result = run_command(
                    clone_cmd,
                    env=git_env,
                    log_path=log_path,
                    timeout=GIT_CLONE_TIMEOUT_SECONDS,
                )
                if result.returncode == 0:
                    append_text(log_path, f"[git_clone_succeeded] transport={transport} source={source_url}\n")
                    return
                last_output = result.stdout
                remove_path(dst)
                if attempt >= GIT_CLONE_MAX_ATTEMPTS or not should_retry_git_failure(last_output):
                    break
                append_text(
                    log_path,
                    f"[git_clone_retry] transport={transport} attempt={attempt} sleeping_seconds={GIT_CLONE_RETRY_DELAY_SECONDS}\n",
                )
                time.sleep(GIT_CLONE_RETRY_DELAY_SECONDS)
            if last_output:
                append_text(
                    log_path,
                    f"[git_clone_transport_failed] transport={transport} diagnosis={diagnose_git_failure(last_output) or 'UNKNOWN'}\n",
                )
    finally:
        if https_askpass_path is not None:
            remove_path(https_askpass_path.parent)

    raise RuntimeError(f"git clone failed:\n{last_output}")


def parse_package_json(repo_dir: Path) -> Dict[str, object]:
    package_json = repo_dir / "package.json"
    if not package_json.exists():
        return {}
    try:
        payload = json.loads(read_text(package_json))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def read_text_if_exists(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    return read_text(path)


def normalize_requirement_name(requirement: str) -> str:
    token = re.split(r"[<>=!~\[\];\s]", requirement.strip(), maxsplit=1)[0]
    return token.strip().lower()


def parse_pyproject_dependencies(pyproject_text: str) -> List[str]:
    dependencies: List[str] = []
    in_dependencies = False
    for line in pyproject_text.splitlines():
        stripped = line.strip()
        if stripped == "dependencies = [":
            in_dependencies = True
            continue
        if not in_dependencies:
            continue
        if stripped == "]":
            break
        match = re.match(r'"([^"]+)"', stripped.rstrip(","))
        if not match:
            continue
        normalized = normalize_requirement_name(match.group(1))
        if normalized:
            dependencies.append(normalized)
    return dependencies


def parse_requirements_dependencies(requirements_text: str) -> List[str]:
    dependencies: List[str] = []
    for line in requirements_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        normalized = normalize_requirement_name(stripped)
        if normalized:
            dependencies.append(normalized)
    return dependencies


def parse_pyproject_requires_python(pyproject_text: str) -> Optional[str]:
    match = re.search(r'^requires-python\s*=\s*"([^"]+)"', pyproject_text, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()


def module_name_for_path(repo_dir: Path, file_path: Path) -> Optional[str]:
    try:
        relative = file_path.relative_to(repo_dir)
    except ValueError:
        return None
    if file_path.suffix != ".py":
        return None
    without_suffix = relative.with_suffix("")
    if without_suffix.name == "__init__":
        return None
    return ".".join(without_suffix.parts)


def collect_matching_files(repo_dir: Path, patterns: List[str]) -> List[Path]:
    matches: Set[Path] = set()
    for pattern in patterns:
        matches.update(path for path in repo_dir.glob(pattern) if path.is_file())
    return sorted(matches)


def parse_pnpm_workspace_patterns(workspace_text: str) -> List[str]:
    patterns: List[str] = []
    in_packages = False
    for raw_line in workspace_text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "packages:":
            in_packages = True
            continue
        if not in_packages:
            continue
        if not raw_line.startswith((" ", "\t", "-")) and ":" in stripped:
            break
        match = re.match(r'-\s*["\']?([^"\']+)["\']?\s*$', stripped)
        if match:
            patterns.append(match.group(1).strip())
    return patterns


def package_dependency_names(package_payload: Dict[str, object]) -> List[str]:
    names: Set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        value = package_payload.get(key)
        if isinstance(value, dict):
            names.update(str(name).lower() for name in value.keys())
    return sorted(names)


def detect_repo_package_manager(repo_dir: Path, package_payload: Dict[str, object]) -> str:
    package_manager_field = package_payload.get("packageManager")
    if isinstance(package_manager_field, str):
        if package_manager_field.startswith("pnpm@"):
            return "pnpm"
        if package_manager_field.startswith("yarn@"):
            return "yarn"
        if package_manager_field.startswith("npm@"):
            return "npm"
    if (repo_dir / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (repo_dir / "package-lock.json").exists():
        return "npm"
    if (repo_dir / "yarn.lock").exists():
        return "yarn"
    return "unknown"


def build_root_script_command(package_manager: str, script_name: str) -> str:
    if package_manager == "pnpm":
        return f"pnpm {script_name}"
    if package_manager == "yarn":
        return f"yarn {script_name}"
    return f"npm run {script_name}"


def build_workspace_script_command(
    package_manager: str,
    script_name: str,
    relative_dir: str,
    package_name: Optional[str] = None,
) -> Optional[str]:
    normalized_dir = relative_dir.strip()
    if not normalized_dir:
        return None
    if package_manager == "pnpm":
        return f"pnpm --dir {normalized_dir} {script_name}"
    if package_manager == "npm":
        return f"npm run {script_name} --workspace={normalized_dir}"
    if package_manager == "yarn" and package_name:
        return f"yarn workspace {package_name} {script_name}"
    return None


def looks_like_generated_artifact_path(path_text: str) -> bool:
    generated_markers = {"dist", "build", ".next", "out", ".output"}
    return any(part in generated_markers for part in Path(path_text).parts if part not in (".", "/"))


def command_mentions_generated_artifact(command: str) -> bool:
    lowered = command.lower()
    return bool(re.search(r"(?:^|[\s/'\"`])(?:dist|build|\.next|out|\.output)(?:[\s/'\"`]|$)", lowered))


def command_requires_prior_build(command: str) -> bool:
    lowered = command.lower()
    return any(
        marker in lowered
        for marker in (
            "next start",
            "vite preview",
            "nuxt start",
            "nuxt preview",
            "gatsby serve",
        )
    )


def discover_workspace_packages(repo_dir: Path, root_package: Dict[str, object]) -> List[Dict[str, object]]:
    workspace_members = root_package.get("workspaces")
    package_patterns: List[str] = []
    if isinstance(workspace_members, list):
        for item in workspace_members:
            if isinstance(item, str) and item.strip():
                package_patterns.append(item.strip())
    elif isinstance(workspace_members, dict):
        packages = workspace_members.get("packages")
        if isinstance(packages, list):
            for item in packages:
                if isinstance(item, str) and item.strip():
                    package_patterns.append(item.strip())

    workspace_path = repo_dir / "pnpm-workspace.yaml"
    workspace_text = read_text_if_exists(workspace_path)
    if workspace_text is not None:
        package_patterns.extend(parse_pnpm_workspace_patterns(workspace_text))
    package_patterns = sorted_unique(package_patterns)
    if not package_patterns:
        return []
    discovered: List[Dict[str, object]] = []
    seen_dirs: Set[Path] = set()
    for pattern in package_patterns:
        for package_json_path in sorted(repo_dir.glob(f"{pattern}/package.json")):
            package_dir = package_json_path.parent.resolve()
            if package_dir in seen_dirs:
                continue
            payload = parse_package_json(package_dir)
            if not payload:
                continue
            scripts = payload.get("scripts", {}) if isinstance(payload.get("scripts"), dict) else {}
            discovered.append(
                {
                    "name": payload.get("name"),
                    "relative_dir": package_dir.relative_to(repo_dir.resolve()).as_posix(),
                    "package_dir": package_dir,
                    "scripts": scripts,
                    "dependencies": package_dependency_names(payload),
                }
            )
            seen_dirs.add(package_dir)
    return discovered


def workspace_package_service_score(package_info: Dict[str, object]) -> int:
    score = 0
    scripts = package_info.get("scripts", {})
    if isinstance(scripts, dict):
        start_script = scripts.get("start")
        build_script = scripts.get("build")
        if isinstance(start_script, str) and start_script.strip():
            score += 4
            if "next start" in start_script:
                score += 2
        if isinstance(build_script, str) and build_script.strip():
            score += 2
            if "next build" in build_script:
                score += 2
    dependencies = package_info.get("dependencies", [])
    if isinstance(dependencies, list):
        dep_set = {str(dep).lower() for dep in dependencies}
        if "next" in dep_set:
            score += 3
        if {"react", "react-dom"}.issubset(dep_set):
            score += 1
    relative_dir = str(package_info.get("relative_dir") or "")
    if relative_dir.startswith(("apps/", "packages/")):
        score += 1
    return score


def select_service_package(
    repo_dir: Path,
    root_package: Dict[str, object],
    workspace_packages: List[Dict[str, object]],
) -> Optional[Dict[str, object]]:
    candidates = [pkg for pkg in workspace_packages if workspace_package_service_score(pkg) > 0]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            workspace_package_service_score(item),
            str(item.get("relative_dir") or ""),
        ),
        reverse=True,
    )
    selected = dict(candidates[0])
    scripts = selected.get("scripts", {})
    relative_dir = str(selected.get("relative_dir") or "")
    if isinstance(scripts, dict):
        build_script = scripts.get("build")
        start_script = scripts.get("start")
        if isinstance(build_script, str) and build_script.strip():
            if build_script.strip() == "next build":
                selected["build_command"] = f"pnpm --dir {relative_dir} build"
            else:
                selected["build_command"] = build_script.strip()
        if isinstance(start_script, str) and start_script.strip():
            if start_script.strip() == "next start":
                selected["start_command"] = f"pnpm --dir {relative_dir} start"
            else:
                selected["start_command"] = start_script.strip()
    return selected


def extract_env_var_names(text: str) -> List[str]:
    matches: Set[str] = set()
    matches.update(re.findall(r'os\.environ\.get\(\s*["\']([A-Z0-9_]+)["\']', text))
    matches.update(re.findall(r'os\.getenv\(\s*["\']([A-Z0-9_]+)["\']', text))
    matches.update(re.findall(r'process\.env\.([A-Z0-9_]+)', text))
    matches.update(re.findall(r'process\.env\[\s*["\']([A-Z0-9_]+)["\']\s*\]', text))
    return sorted(matches)


def detect_port_from_text(text: str) -> Optional[int]:
    patterns = [
        r'uvicorn\.run\([^)]*port\s*=\s*(\d{2,5})',
        r'\b(?:app|application|server)\.run\([^)]*port\s*=\s*(\d{2,5})',
        r'--port(?:=|\s+)(\d{2,5})',
        r'port\s*=\s*int\(os\.environ\.get\([^)]*["\'](\d{2,5})["\']\)\)',
        r'\b(?:const|let|var)\s+PORT\s*=\s*process\.env\.PORT\s*\|\|\s*(\d{2,5})',
        r'\b(?:const|let|var)\s+PORT\s*=\s*process\.env\.PORT\s*\?\?\s*(\d{2,5})',
        r'process\.env\.PORT\s*\|\|\s*(\d{2,5})',
        r'process\.env\.PORT\s*\?\?\s*(\d{2,5})',
        r'parser\.add_argument\(\s*["\']--port["\'][^)]*default\s*=\s*(\d{2,5})',
        r'listen\(\s*(\d{2,5})\s*[,)]',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def normalize_shell_command_line(line: str) -> str:
    command = line.strip().strip("`").strip()
    command = re.sub(r"^(?:\(.+?\)\s*)?[$#]\s*", "", command)
    command = re.sub(r"^(?:[-*]|\d+[.)])\s+", "", command)
    return command.strip()


def score_python_readme_command(command: str, *, in_code_block: bool) -> Optional[int]:
    lowered = command.lower()
    if not command:
        return None
    if lowered.startswith(
        (
            "cd ",
            "source ",
            "export ",
            "set ",
            "pip ",
            "pip3 ",
            "pytest ",
            "poetry install",
            "uv sync",
            "uv pip ",
        )
    ):
        return None
    if re.match(r"^python(?:\d+(?:\.\d+)?)?\s+-m\s+(?:pip|venv|pytest|unittest)\b", lowered):
        return None

    score: Optional[int] = None
    if re.match(r"^(?:uvicorn|gunicorn|hypercorn|daphne)\b", lowered):
        score = 90
    elif re.match(r"^streamlit\s+run\b", lowered):
        score = 85
    elif re.match(r"^flask\b.*\brun\b", lowered):
        score = 80
    elif re.match(r"^python(?:\d+(?:\.\d+)?)?\s+-m\s+(?:uvicorn|gunicorn|hypercorn|daphne|flask)\b", lowered):
        score = 88
    elif re.match(r"^python(?:\d+(?:\.\d+)?)?\s+-m\s+[a-z_][a-z0-9_\.]*\b", lowered):
        score = 70
    elif re.match(r"^python(?:\d+(?:\.\d+)?)?\s+[^\s]+\.py\b", lowered):
        score = 65
    if score is None:
        return None

    if in_code_block:
        score += 10
    if "0.0.0.0" in lowered:
        score += 20
    if "127.0.0.1" in lowered or "localhost" in lowered:
        score -= 20
    if "--port" in lowered:
        score += 5
    return score


def extract_python_start_command_from_readme(readme_text: str) -> Optional[str]:
    candidates: List[Tuple[int, int, str]] = []
    in_code_block = False
    for index, raw_line in enumerate(readme_text.splitlines()):
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        command = normalize_shell_command_line(raw_line)
        score = score_python_readme_command(command, in_code_block=in_code_block)
        if score is None:
            continue
        candidates.append((score, -index, command))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def has_python_main_guard(text: str) -> bool:
    return bool(re.search(r'if\s+__name__\s*==\s*["\']__main__["\']\s*:', text))


def detect_assigned_framework_app_name(text: str, factory_names: Tuple[str, ...]) -> Optional[str]:
    factory_pattern = "|".join(re.escape(name) for name in factory_names)
    match = re.search(
        rf"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]+)?=\s*(?:[A-Za-z_][A-Za-z0-9_]*\.)?(?:{factory_pattern})\s*\(",
        text,
        re.MULTILINE,
    )
    if not match:
        return None
    return match.group(1)


def build_uvicorn_entry_command(module_name: str, app_name: str, port: Optional[int]) -> str:
    command = f"uvicorn {module_name}:{app_name} --host 0.0.0.0"
    if isinstance(port, int):
        command += f" --port {port}"
    return command


def build_flask_entry_command(module_name: str, app_name: str, port: Optional[int]) -> str:
    command = f"flask --app {module_name}:{app_name} run --host 0.0.0.0"
    if isinstance(port, int):
        command += f" --port {port}"
    return command


def build_python_script_command(repo_dir: Path, file_path: Path) -> Optional[str]:
    try:
        relative_path = file_path.relative_to(repo_dir).as_posix()
    except ValueError:
        return None
    if not relative_path:
        return None
    return f"python {relative_path}"


def has_local_python_imports(text: str) -> bool:
    for imported_module in re.findall(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_]*)\s+import\b", text, re.MULTILINE):
        if imported_module not in {"__future__"}:
            return True
    return False


def python_command_matches_repo(repo_dir: Path, command: str) -> bool:
    lowered = command.lower()
    uvicorn_match = re.search(
        r'\buvicorn\s+([A-Za-z_][A-Za-z0-9_\.]*):([A-Za-z_][A-Za-z0-9_]*)',
        lowered,
    )
    if uvicorn_match:
        module_name = uvicorn_match.group(1)
        app_name = uvicorn_match.group(2)
        module_path = repo_dir / Path(*module_name.split(".")).with_suffix(".py")
        module_text = read_text_if_exists(module_path)
        return bool(module_text and re.search(rf"^\s*{re.escape(app_name)}\s*=", module_text, re.MULTILINE))

    flask_match = re.search(
        r'\bflask\s+--app\s+([A-Za-z_][A-Za-z0-9_\.]*):([A-Za-z_][A-Za-z0-9_]*)\b',
        lowered,
    )
    if flask_match:
        module_name = flask_match.group(1)
        app_name = flask_match.group(2)
        module_path = repo_dir / Path(*module_name.split(".")).with_suffix(".py")
        module_text = read_text_if_exists(module_path)
        return bool(module_text and re.search(rf"^\s*{re.escape(app_name)}\s*=", module_text, re.MULTILINE))

    python_module_match = re.search(r'^python(?:\d+(?:\.\d+)?)?\s+-m\s+([a-z_][a-z0-9_\.]*)\b', lowered)
    if python_module_match:
        module_name = python_module_match.group(1)
        module_path = repo_dir / Path(*module_name.split(".")).with_suffix(".py")
        return module_path.is_file()

    python_script_match = re.search(r'^python(?:\d+(?:\.\d+)?)?\s+([^\s]+\.py)\b', lowered)
    if python_script_match:
        script_path = (repo_dir / python_script_match.group(1)).resolve()
        try:
            script_path.relative_to(repo_dir.resolve())
        except ValueError:
            return False
        return script_path.is_file()

    return False


def python_entry_file_priority(repo_dir: Path, file_path: Path) -> int:
    try:
        relative = file_path.relative_to(repo_dir)
    except ValueError:
        relative = file_path
    filename_scores = {
        "server.py": 40,
        "app.py": 35,
        "main.py": 30,
        "api.py": 25,
        "asgi.py": 25,
        "wsgi.py": 20,
        "run.py": 10,
        "run_local.py": -20,
    }
    return filename_scores.get(relative.name, 0) - len(relative.parts)


def detect_python_entrypoint_from_file(
    repo_dir: Path,
    file_path: Path,
    text: str,
    python_dependencies: Set[str],
) -> Optional[Tuple[int, str]]:
    module_name = module_name_for_path(repo_dir, file_path)
    if not module_name:
        return None

    priority = python_entry_file_priority(repo_dir, file_path)
    port = detect_port_from_text(text)
    has_main = has_python_main_guard(text)
    script_command = build_python_script_command(repo_dir, file_path)
    has_local_imports = has_local_python_imports(text)
    imports_uvicorn = "uvicorn" in python_dependencies or bool(
        re.search(r"^\s*(?:from\s+uvicorn\s+import|import\s+uvicorn\b)", text, re.MULTILINE)
    )
    imports_flask = "flask" in python_dependencies or bool(
        re.search(r"^\s*(?:from\s+flask\s+import|import\s+flask\b)", text, re.MULTILINE)
    )
    asgi_app_name = detect_assigned_framework_app_name(text, ("FastAPI", "Starlette", "Quart"))
    flask_app_name = detect_assigned_framework_app_name(text, ("Flask",))

    if has_main and has_local_imports and script_command:
        return 245 + priority, script_command
    if has_main and re.search(r"\buvicorn\.run\s*\(", text):
        return 240 + priority, f"python -m {module_name}"
    if has_main and re.search(r"\b(?:app|application|server)\.run\s*\(", text):
        return 230 + priority, f"python -m {module_name}"
    if has_main and (asgi_app_name or flask_app_name):
        return 220 + priority, f"python -m {module_name}"
    if re.search(r"\buvicorn\.run\s*\(", text):
        return 210 + priority, f"python -m {module_name}"
    if asgi_app_name and imports_uvicorn:
        return 180 + priority, build_uvicorn_entry_command(module_name, asgi_app_name, port)
    if flask_app_name and imports_flask:
        return 170 + priority, build_flask_entry_command(module_name, flask_app_name, port)
    return None


def detect_python_entrypoint(
    repo_dir: Path,
    readme_text: Optional[str] = None,
    python_dependencies: Optional[List[str]] = None,
) -> Tuple[Optional[Path], Optional[str], Optional[str]]:
    candidates: List[Tuple[int, Path, str, str]] = []
    dependency_names = {item.lower() for item in (python_dependencies or [])}
    candidate_files = collect_matching_files(
        repo_dir,
        [
            "server.py",
            "app.py",
            "main.py",
            "api.py",
            "asgi.py",
            "wsgi.py",
            "run.py",
            "run_local.py",
            "*/server.py",
            "*/app.py",
            "*/main.py",
            "*/api.py",
            "*/asgi.py",
            "*/wsgi.py",
            "*/run.py",
            "*/run_local.py",
            "*/*/server.py",
            "*/*/app.py",
            "*/*/main.py",
            "*/*/api.py",
            "*/*/asgi.py",
            "*/*/wsgi.py",
            "*/*/run.py",
            "*/*/run_local.py",
        ],
    )
    for file_path in candidate_files:
        text = read_text_if_exists(file_path)
        if text is None:
            continue
        candidate = detect_python_entrypoint_from_file(repo_dir, file_path, text, dependency_names)
        if candidate is None:
            continue
        score, command = candidate
        candidates.append((score, file_path, command, text))
    if readme_text:
        readme_command = extract_python_start_command_from_readme(readme_text)
        if readme_command and python_command_matches_repo(repo_dir, readme_command):
            readme_score = 190 if candidates else 1000
            candidates.append((readme_score, repo_dir / "README.md", readme_command, readme_text))
    if not candidates:
        return None, None, None
    candidates.sort(key=lambda item: (-item[0], item[1].as_posix(), item[2]))
    _score, file_path, command, text = candidates[0]
    return file_path, command, text


def detect_node_entrypoint(repo_dir: Path, package_scripts: Dict[str, object]) -> Tuple[Optional[Path], Optional[str], Optional[str]]:
    start_script = package_scripts.get("start")
    if isinstance(start_script, str) and start_script.strip():
        command = start_script.strip()
        script_path = extract_node_script_path_from_command(command)
        if script_path:
            candidate = (repo_dir / script_path).resolve()
            try:
                candidate.relative_to(repo_dir.resolve())
            except ValueError:
                candidate = None
            if candidate and candidate.is_file():
                text = read_text_if_exists(candidate)
                return candidate, command, text
        return None, command, None
    for filename in ("server.js", "app.js", "main.js", "index.js"):
        candidate = repo_dir / filename
        text = read_text_if_exists(candidate)
        if text is None:
            continue
        return candidate, f"node {filename}", text
    return None, None, None


def extract_node_script_path_from_command(command: str) -> Optional[str]:
    match = re.search(
        r"\b(?:node|tsx|ts-node(?:-esm)?)\b(?:\s+--[^\s\"'`;&|]+(?:[=\s][^\s\"'`;&|]+)?)*\s+(?:watch\s+)?([^\s\"'`;&|]+\.(?:js|mjs|cjs|ts|mts|cts))\b",
        command,
    )
    if not match:
        return None
    return match.group(1).strip()


def extract_workspace_script_reference(command: str) -> Optional[Tuple[str, str]]:
    patterns = [
        r"\bnpm\s+run\s+([A-Za-z0-9:_-]+)\s+--workspace(?:=|\s+)([^\s\"'`;&|]+)",
        r"\bnpm\s+--workspace(?:=|\s+)([^\s\"'`;&|]+)\s+run\s+([A-Za-z0-9:_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, command)
        if not match:
            continue
        if pattern.startswith(r"\bnpm\s+run"):
            script_name = match.group(1).strip()
            workspace_ref = match.group(2).strip()
        else:
            workspace_ref = match.group(1).strip()
            script_name = match.group(2).strip()
        if script_name and workspace_ref:
            return script_name, workspace_ref
    return None


def detect_node_entry_script_paths(repo_dir: Path, package_scripts: Dict[str, object]) -> List[Path]:
    candidates: List[Path] = []
    seen: Set[Path] = set()
    root_package = parse_package_json(repo_dir)
    workspace_packages = discover_workspace_packages(repo_dir, root_package)
    workspace_lookup: Dict[str, Dict[str, object]] = {}
    for package_info in workspace_packages:
        package_dir = package_info.get("package_dir")
        if not isinstance(package_dir, Path):
            continue
        relative_dir = str(package_info.get("relative_dir") or "").strip()
        package_name = str(package_info.get("name") or "").strip()
        if relative_dir:
            workspace_lookup[relative_dir] = package_info
        if package_name:
            workspace_lookup[package_name] = package_info

    for script_name in ("start", "dev", "serve"):
        script_text = package_scripts.get(script_name)
        if not isinstance(script_text, str) or not script_text.strip():
            continue
        script_path_text = extract_node_script_path_from_command(script_text)
        if script_path_text:
            candidate = (repo_dir / script_path_text).resolve()
            if candidate.is_file() and candidate not in seen:
                try:
                    candidate.relative_to(repo_dir.resolve())
                except ValueError:
                    pass
                else:
                    candidates.append(candidate)
                    seen.add(candidate)

        workspace_ref = extract_workspace_script_reference(script_text)
        if workspace_ref is None:
            continue
        nested_script_name, workspace_key = workspace_ref
        workspace_package = workspace_lookup.get(workspace_key)
        if not isinstance(workspace_package, dict):
            continue
        nested_scripts = workspace_package.get("scripts", {})
        package_dir = workspace_package.get("package_dir")
        if not isinstance(nested_scripts, dict) or not isinstance(package_dir, Path):
            continue
        nested_script_text = nested_scripts.get(nested_script_name)
        if not isinstance(nested_script_text, str) or not nested_script_text.strip():
            continue
        nested_script_path_text = extract_node_script_path_from_command(nested_script_text)
        if not nested_script_path_text:
            continue
        candidate = (package_dir / nested_script_path_text).resolve()
        if candidate.is_file() and candidate not in seen:
            try:
                candidate.relative_to(repo_dir.resolve())
            except ValueError:
                continue
            candidates.append(candidate)
            seen.add(candidate)

    for filename in ("server.js", "app.js", "main.js", "index.js", "server.ts", "app.ts", "main.ts", "index.ts"):
        candidate = (repo_dir / filename).resolve()
        if candidate.is_file() and candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)

    return candidates


def resolve_entrypoint_relative_dir_hint(entry_file: Path, args_text: str) -> Optional[Path]:
    quoted_parts = re.findall(r'["\']([^"\']+)["\']', args_text)
    if not quoted_parts:
        return None
    candidate = entry_file.parent
    for part in quoted_parts:
        candidate = candidate / Path(part)
    return candidate.resolve()


def detect_static_root_reference_paths_from_node_entry(repo_dir: Path, entry_file: Path) -> List[Path]:
    text = read_text_if_exists(entry_file)
    if not text:
        return []

    repo_root = repo_dir.resolve()
    variable_dirs: Dict[str, Path] = {}
    hints: List[Path] = []
    seen: Set[Path] = set()

    assignment_pattern = re.compile(r'(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*path\.(?:join|resolve)\(\s*__dirname\s*,\s*(.*?)\)\s*;?')
    for match in assignment_pattern.finditer(text):
        resolved = resolve_entrypoint_relative_dir_hint(entry_file, match.group(2))
        if resolved is None:
            continue
        try:
            resolved.relative_to(repo_root)
        except ValueError:
            continue
        variable_dirs[match.group(1)] = resolved

    inline_pattern = re.compile(r'(?:express\.static|serveStatic|koaStatic|root\s*:)\s*[\(\s]*path\.(?:join|resolve)\(\s*__dirname\s*,\s*(.*?)\)\s*\)?')
    for match in inline_pattern.finditer(text):
        resolved = resolve_entrypoint_relative_dir_hint(entry_file, match.group(1))
        if resolved is None or resolved in seen:
            continue
        try:
            resolved.relative_to(repo_root)
        except ValueError:
            continue
        hints.append(resolved)
        seen.add(resolved)

    variable_pattern = re.compile(r'(?:express\.static|serveStatic|koaStatic)\(\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\)|root\s*:\s*([A-Za-z_$][A-Za-z0-9_$]*)')
    for match in variable_pattern.finditer(text):
        variable_name = match.group(1) or match.group(2)
        if not variable_name:
            continue
        resolved = variable_dirs.get(variable_name)
        if resolved is None or resolved in seen:
            continue
        hints.append(resolved)
        seen.add(resolved)

    return hints


def resolve_entrypoint_relative_dir(entry_file: Path, args_text: str) -> Optional[Path]:
    return subpath_resolve_entrypoint_relative_dir(entry_file, args_text)


def detect_static_root_hints_from_node_entry(repo_dir: Path, entry_file: Path) -> List[Path]:
    return subpath_detect_static_root_hints_from_node_entry(
        repo_dir,
        entry_file,
        read_text_if_exists=read_text_if_exists,
    )


def workspace_frontend_package_dirs(repo_dir: Path) -> List[Path]:
    return subpath_workspace_frontend_package_dirs(
        repo_dir,
        parse_package_json=parse_package_json,
        discover_workspace_packages=discover_workspace_packages,
    )


def vite_project_roots(repo_dir: Path) -> List[Path]:
    return subpath_vite_project_roots(
        repo_dir,
        parse_package_json=parse_package_json,
        workspace_frontend_package_dirs_fn=workspace_frontend_package_dirs,
    )


def collect_python_frontend_hint_files(repo_dir: Path) -> List[Path]:
    return subpath_collect_python_frontend_hint_files(
        repo_dir,
        collect_matching_files=collect_matching_files,
    )


def normalize_python_hint_path(expr: str) -> Optional[str]:
    return subpath_normalize_python_hint_path(expr)


def resolve_python_hint_directory(repo_dir: Path, source_file: Path, path_text: str) -> List[Path]:
    return subpath_resolve_python_hint_directory(repo_dir, source_file, path_text)


def detect_frontend_root_hints_from_python_file(repo_dir: Path, source_file: Path) -> List[Path]:
    return subpath_detect_frontend_root_hints_from_python_file(
        repo_dir,
        source_file,
        read_text_if_exists=read_text_if_exists,
    )


def detect_frontend_runtime_roots(repo_dir: Path) -> List[Path]:
    return subpath_detect_frontend_runtime_roots(
        repo_dir,
        parse_package_json=parse_package_json,
        detect_node_entry_script_paths=detect_node_entry_script_paths,
        detect_static_root_hints_from_node_entry_fn=detect_static_root_hints_from_node_entry,
        collect_python_frontend_hint_files_fn=collect_python_frontend_hint_files,
        detect_frontend_root_hints_from_python_file_fn=detect_frontend_root_hints_from_python_file,
        workspace_frontend_package_dirs_fn=workspace_frontend_package_dirs,
        read_text_if_exists=read_text_if_exists,
        collect_matching_files=collect_matching_files,
    )


def detect_frontend_runtime_root_groups(repo_dir: Path) -> Dict[str, tuple[Path, ...]]:
    return subpath_detect_frontend_runtime_root_groups(
        repo_dir,
        parse_package_json=parse_package_json,
        detect_node_entry_script_paths=detect_node_entry_script_paths,
        detect_static_root_hints_from_node_entry_fn=detect_static_root_hints_from_node_entry,
        collect_python_frontend_hint_files_fn=collect_python_frontend_hint_files,
        detect_frontend_root_hints_from_python_file_fn=detect_frontend_root_hints_from_python_file,
        workspace_frontend_package_dirs_fn=workspace_frontend_package_dirs,
        read_text_if_exists=read_text_if_exists,
        collect_matching_files=collect_matching_files,
    )


def detect_system_dependency_hints(repo_dir: Path, package_scripts: Dict[str, object], readme_text: Optional[str]) -> List[str]:
    hints: Set[str] = set()
    evidence_files = collect_matching_files(
        repo_dir,
        [
            "README.md",
            ".env.example",
            "package.json",
            "server/**/*.ts",
            "server/**/*.js",
            "**/*.service",
            "deploy/**/*",
        ],
    )

    keyword_map = {
        "sqlite3": [r"\bsqlite3\b", r"\bSQLITE_BIN\b"],
        "ffmpeg": [r"\bffmpeg\b"],
        "ffprobe": [r"\bffprobe\b"],
        "curl": [r"\bcurl\b"],
        "git": [r"\bgit\b"],
    }

    for file_path in evidence_files:
        text = read_text_if_exists(file_path)
        if not text:
            continue
        for dependency, patterns in keyword_map.items():
            if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
                hints.add(dependency)

    if package_scripts:
        for script_text in package_scripts.values():
            if not isinstance(script_text, str):
                continue
            if "sqlite3" in script_text:
                hints.add("sqlite3")
            if "ffmpeg" in script_text:
                hints.add("ffmpeg")
            if "ffprobe" in script_text:
                hints.add("ffprobe")

    if readme_text:
        for dependency in ("sqlite3", "ffmpeg", "ffprobe", "curl", "git"):
            if re.search(rf"\b{re.escape(dependency)}\b", readme_text, re.IGNORECASE):
                hints.add(dependency)

    return sorted(hints)


def detect_database_file_hints(repo_dir: Path, readme_text: Optional[str]) -> List[str]:
    hints: Set[str] = set()
    for file_path in collect_matching_files(repo_dir, ["**/*.db", "*.db", "**/*.sqlite", "*.sqlite"]):
        hints.add(file_path.name)

    evidence_files = collect_matching_files(
        repo_dir,
        [
            "README.md",
            ".env.example",
            "server/**/*.ts",
            "server/**/*.js",
            "deploy/**/*",
        ],
    )
    for file_path in evidence_files:
        text = read_text_if_exists(file_path)
        if not text:
            continue
        for match in re.findall(r"\b([A-Za-z0-9_.-]+\.(?:db|sqlite))\b", text):
            hints.add(match)

    if readme_text:
        for match in re.findall(r"\b([A-Za-z0-9_.-]+\.(?:db|sqlite))\b", readme_text):
            hints.add(match)

    return sorted(hints)


def detect_storage_path_hints(repo_dir: Path, readme_text: Optional[str]) -> List[str]:
    hints: Set[str] = set()
    evidence_files = collect_matching_files(
        repo_dir,
        [
            "README.md",
            ".env.example",
            "server/**/*.ts",
            "server/**/*.js",
            "deploy/**/*",
        ],
    )

    directory_patterns = [
        r"\b([A-Za-z0-9_./-]+/data/?[A-Za-z0-9_./-]*)\b",
        r"\b([A-Za-z0-9_./-]+/uploads/?[A-Za-z0-9_./-]*)\b",
        r"\b([A-Za-z0-9_./-]+/storage/?[A-Za-z0-9_./-]*)\b",
        r"\b([A-Za-z0-9_./-]+/state/?[A-Za-z0-9_./-]*)\b",
        r"\b([A-Za-z0-9_./-]+/cache/?[A-Za-z0-9_./-]*)\b",
    ]

    for file_path in evidence_files:
        text = read_text_if_exists(file_path)
        if not text:
            continue
        for pattern in directory_patterns:
            for match in re.findall(pattern, text):
                hints.add(match.strip())

    if readme_text:
        for literal in ("data/", "forum/data/", "uploads/", "storage/", "state/", "cache/"):
            if literal in readme_text:
                hints.add(literal)

    return sorted(hints)


def normalize_storage_hints(hints: Iterable[str]) -> List[str]:
    normalized: Set[str] = set()
    for hint in hints:
        item = hint.strip().replace("\\", "/")
        if not item:
            continue
        if item.endswith(".json") or item.endswith(".db") or item.endswith(".sqlite"):
            parent = str(Path(item).parent).replace("\\", "/")
            if parent and parent != ".":
                item = f"{parent}/"
        if item.endswith("/"):
            normalized.add(item)
        elif "/" in item and not Path(item).suffix:
            normalized.add(f"{item}/")
        else:
            normalized.add(item)
    return sorted(normalized)


def normalize_config_file_hints(hints: Iterable[str]) -> List[str]:
    normalized: Set[str] = set()
    for hint in hints:
        item = hint.strip().replace("\\", "/")
        if not item:
            continue
        if item.endswith(".env.example") or item.endswith(".env.local") or item.endswith(".service"):
            normalized.add(item)
    return sorted(normalized)


def path_exists_in_repo(repo_dir: Path, path_text: str) -> bool:
    normalized = path_text.strip().lstrip("/").rstrip("/")
    if not normalized:
        return False
    return (repo_dir / normalized).exists()


def dockerfile_has_build_step(docker_text: str) -> bool:
    normalized_text = re.sub(r"\\\s*\n\s*", " ", docker_text)
    patterns = [
        r"^\s*RUN\s+.*\bnpm\s+run\s+build\b",
        r"^\s*RUN\s+.*\bpnpm(?:\s+--[^\s]+(?:[=\s][^\s]+)?)*\s+build\b",
        r"^\s*RUN\s+.*\byarn(?:\s+--[^\s]+(?:[=\s][^\s]+)?)*\s+build\b",
        r"^\s*RUN\s+.*\bbun\s+run\s+build\b",
        r"^\s*RUN\s+.*\bturbo\s+build\b",
        r"^\s*RUN\s+.*\bnext\s+build\b",
        r"^\s*RUN\s+.*\bvite\s+build\b",
        r"^\s*RUN\s+.*\bpython\s+-m\s+build\b",
    ]
    return any(re.search(pattern, normalized_text, re.MULTILINE | re.IGNORECASE) for pattern in patterns)


def detect_builder_output_hints(docker_text: str) -> Set[str]:
    hints: Set[str] = set()
    for line in docker_text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("RUN "):
            lowered = stripped.lower()
            if any(token in lowered for token in ("pnpm install", "npm install", "npm ci", "yarn install", "bun install")):
                hints.add("app/node_modules")
                hints.add("node_modules")
            if "next build" in lowered:
                hints.add("app/.next")
                hints.add(".next")
            if any(token in lowered for token in ("pnpm build", "npm run build", "yarn build", "vite build", "turbo build")):
                hints.add("app/dist")
                hints.add("dist")
            if "tsc" in lowered:
                hints.add("app/build")
                hints.add("build")
            for match in re.findall(r"\b(?:mkdir\s+-p|mkdir)\s+([^\s&;]+)", stripped):
                hints.add(match.strip().rstrip("/"))
            for match in re.findall(r"\b(?:cp|mv)\s+[^\s]+\s+([^\s&;]+)", stripped):
                hints.add(match.strip().rstrip("/"))
            for match in re.findall(r"--outdir=([^\s&;]+)", stripped):
                hints.add(match.strip().rstrip("/"))
            continue
        if stripped.upper().startswith("COPY . ."):
            hints.add("app")
    return hints


def collect_multistage_copy_sources(docker_text: str) -> List[Tuple[str, str]]:
    results: List[Tuple[str, str]] = []
    for line in docker_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not stripped.upper().startswith("COPY "):
            continue
        match = re.search(r"--from=([^\s]+)", stripped)
        if not match:
            continue
        source_stage = match.group(1)
        sanitized = re.sub(r"--from=[^\s]+\s*", "", stripped, count=1)
        sanitized = re.sub(r"--chown=[^\s]+\s*", "", sanitized)
        sanitized = re.sub(r"--chmod=[^\s]+\s*", "", sanitized)
        try:
            parts = shlex.split(sanitized)
        except ValueError:
            continue
        if len(parts) < 3 or parts[0].upper() != "COPY":
            continue
        for source_path in parts[1:-1]:
            results.append((source_stage, source_path))
    return results


def validate_multistage_copy_sources(repo_dir: Path, docker_text: str) -> List[str]:
    findings: List[str] = []
    builder_hints = detect_builder_output_hints(docker_text)
    for source_stage, source_path in collect_multistage_copy_sources(docker_text):
        normalized = source_path.strip().rstrip("/")
        if not normalized.startswith("/"):
            continue
        repo_relative = normalized.lstrip("/")
        if path_exists_in_repo(repo_dir, repo_relative):
            continue
        if repo_relative in builder_hints:
            continue
        if any(repo_relative.startswith(f"{hint.strip('/')}/") or repo_relative == hint.strip("/") for hint in builder_hints):
            continue
        findings.append(
            f'Dockerfile copies "{normalized}" from stage "{source_stage}", but there is no stable evidence that this path exists in the build stage'
        )
    return findings


def collect_repo_analysis(repo_dir: Path, source_type: str, source: str, ref: Optional[str]) -> Dict[str, object]:
    package_json = repo_dir / "package.json"
    pyproject_toml = repo_dir / "pyproject.toml"
    requirements_txt = repo_dir / "requirements.txt"
    readme = repo_dir / "README.md"
    run_sh = repo_dir / "run.sh"
    package_lock = repo_dir / "package-lock.json"

    package = parse_package_json(repo_dir)
    package_scripts = package.get("scripts", {}) if isinstance(package.get("scripts"), dict) else {}
    package_dependencies = sorted_unique([str(name).lower() for name in (package.get("dependencies") or {}).keys()])
    workspace_packages = discover_workspace_packages(repo_dir, package)
    selected_service_package = select_service_package(repo_dir, package, workspace_packages)
    pyproject_text = read_text_if_exists(pyproject_toml)
    requirements_text = read_text_if_exists(requirements_txt)
    readme_text = read_text_if_exists(readme)
    detected_node_package_manager = detect_repo_package_manager(repo_dir, package)
    python_dependencies = sorted_unique(
        (parse_pyproject_dependencies(pyproject_text) if pyproject_text else [])
        + (parse_requirements_dependencies(requirements_text) if requirements_text else [])
    )
    requires_python = parse_pyproject_requires_python(pyproject_text) if pyproject_text else None

    facts: List[str] = []
    if source_type == "git":
        facts.append(f'- input repository URL = "{source}"')
        facts.append(f'- requested git ref = "{ref or "default branch"}"')
    else:
        facts.append(f'- local source path = "{Path(source).resolve()}"')

    if pyproject_text:
        facts.append("- pyproject.toml exists")
    if requirements_text:
        facts.append("- requirements.txt exists")
    if requires_python:
        facts.append(f'- pyproject.toml requires-python = "{requires_python}"')
    if python_dependencies:
        facts.append(f'- Python runtime dependencies detected = {", ".join(python_dependencies)}')

    if package_json.exists():
        package_name = package.get("name", "UNKNOWN")
        package_version = package.get("version", "UNKNOWN")
        facts.append(f'- package.json.name = "{package_name}"')
        facts.append(f'- package.json.version = "{package_version}"')
        if package_dependencies:
            facts.append(f'- package.json dependencies = {", ".join(package_dependencies)}')
    if isinstance(selected_service_package, dict):
        facts.append(
            f'- selected workspace service package = "{selected_service_package.get("name")}" in "{selected_service_package.get("relative_dir")}"'
        )
    if (repo_dir / "next.config.ts").exists():
        facts.append("- next.config.ts exists")

    python_entry_path, python_entry_command, python_entry_text = detect_python_entrypoint(
        repo_dir,
        readme_text=readme_text,
        python_dependencies=python_dependencies,
    )
    node_entry_path, node_entry_command, node_entry_text = detect_node_entrypoint(repo_dir, package_scripts)
    if isinstance(selected_service_package, dict):
        selected_start_command = selected_service_package.get("start_command")
        if isinstance(selected_start_command, str) and selected_start_command.strip():
            node_entry_path = None
            node_entry_command = selected_start_command.strip()
            node_entry_text = None

    python_signals = 0
    node_signals = 0
    if pyproject_text or requirements_text:
        python_signals += 2
    if python_entry_command:
        python_signals += 3
    if "fastapi" in python_dependencies or "uvicorn" in python_dependencies:
        python_signals += 2
    if package_json.exists():
        node_signals += 1
    if node_entry_command:
        node_signals += 3
    if (repo_dir / "server.js").exists():
        node_signals += 2

    service_runtime = "unknown"
    if python_signals > node_signals:
        service_runtime = "python"
    elif node_signals > python_signals:
        service_runtime = "node"
    elif python_signals:
        service_runtime = "python"

    facts.append(f'- detected service runtime = "{service_runtime}"')

    if python_entry_path and python_entry_command:
        facts.append(
            f'- Python service entrypoint candidate = "{python_entry_command}" from "{python_entry_path.relative_to(repo_dir).as_posix()}"'
        )
    if node_entry_path and node_entry_command:
        facts.append(
            f'- Node service entrypoint candidate = "{node_entry_command}" from "{node_entry_path.relative_to(repo_dir).as_posix()}"'
        )
    elif node_entry_command:
        if isinstance(package_scripts.get("start"), str) and package_scripts.get("start", "").strip():
            facts.append(f'- Node service entrypoint candidate = "{node_entry_command}" from "package.json scripts.start"')
        else:
            facts.append(f'- Node service entrypoint candidate = "{node_entry_command}" from package.json')

    port = None
    if python_entry_text:
        port = detect_port_from_text(python_entry_text)
    if port is None and node_entry_text:
        port = detect_port_from_text(node_entry_text)
    if port is not None:
        facts.append(f'- detected service port = "{port}"')

    env_var_names: Set[str] = set()
    analysis_files = collect_matching_files(
        repo_dir,
        [
            "README.md",
            "run.sh",
            "server.js",
            "app.js",
            "main.js",
            "index.js",
            "run_local.py",
            "*/run_local.py",
            "*/*/run_local.py",
            "main.py",
            "*/main.py",
            "*/*/main.py",
            "app.py",
            "*/app.py",
            "*/*/app.py",
            "server.py",
            "*/server.py",
            "*/*/server.py",
            "api.py",
            "*/api.py",
            "*/*/api.py",
            "*settings*.py",
            "*/settings*.py",
            "*/*/settings*.py",
            "*history*.py",
            "*/history*.py",
            "*/*/history*.py",
        ],
    )
    storage_facts: List[str] = []
    for file_path in analysis_files:
        text = read_text_if_exists(file_path)
        if text is None:
            continue
        env_var_names.update(extract_env_var_names(text))
        relative_path = file_path.relative_to(repo_dir).as_posix()
        if (
            any(token in text for token in ("write_text(", ".open(", "mkdir(", "json.dumps("))
            and any(name.endswith("_DIR") or name.endswith("_PATH") for name in extract_env_var_names(text))
        ):
            for env_var_name in extract_env_var_names(text):
                if env_var_name.endswith("_DIR") or env_var_name.endswith("_PATH"):
                    storage_facts.append(
                        f'- {relative_path} uses "{env_var_name}" to control writable state location'
                    )

    if env_var_names:
        facts.append(f'- detected environment variables = {", ".join(sorted(env_var_names))}')
    for hint in sorted_unique(storage_facts):
        facts.append(hint)

    if readme_text:
        if readme_text and "npm install" in readme_text and "node server.js" in readme_text:
            facts.append('- README.md confirms manual startup uses "npm install" then "node server.js"')
        if re.search(r"\bffmpeg\b", readme_text, re.IGNORECASE):
            facts.append("- README.md mentions ffmpeg as a runtime dependency")
        if re.search(r"\bffprobe\b", readme_text, re.IGNORECASE):
            facts.append("- README.md mentions ffprobe as a runtime dependency")
        if "localhost:8000" in readme_text:
            facts.append('- README.md references a local web endpoint at "http://localhost:8000"')

    if python_entry_text and "127.0.0.1" in python_entry_text:
        facts.append("- Python runtime defaults to binding 127.0.0.1; container startup must override to 0.0.0.0")

    if run_sh.exists():
        facts.append("- run.sh is a local-dev helper that kills old processes and opens a browser; do not use it as container ENTRYPOINT")

    if package_lock.exists() and service_runtime == "node":
        facts.append("- package-lock.json exists, so npm ci is preferred over npm install")

    if source_type == "local":
        facts.append("- this workspace is a local source delivery copy, not a usable Git working tree for metadata discovery")

    system_dependency_hints = detect_system_dependency_hints(repo_dir, package_scripts, readme_text)
    if system_dependency_hints:
        facts.append(f'- runtime system dependency hints = {", ".join(system_dependency_hints)}')

    local_official_images = list_local_official_docker_images()
    if local_official_images:
        relevant_images = local_official_images
        if service_runtime == "node":
            relevant_images = [image for image in local_official_images if image.startswith("docker.io/library/node:")]
        elif service_runtime == "python":
            relevant_images = [image for image in local_official_images if image.startswith("docker.io/library/python:")]
        if relevant_images:
            facts.append(f'- current host docker.io official images = {", ".join(relevant_images)}')

    package_manager = "unknown"
    if service_runtime == "node":
        package_manager = detected_node_package_manager
    elif service_runtime == "python":
        if pyproject_toml.exists():
            package_manager = "pip"
        elif requirements_txt.exists():
            package_manager = "pip"

    frontend_runtime_roots: List[Path] = []
    if service_runtime == "node" or package_json.exists():
        frontend_runtime_roots = detect_frontend_runtime_roots(repo_dir)
    frontend_runtime_root_paths = sorted_unique(
        runtime_root.relative_to(repo_dir.resolve()).as_posix()
        for runtime_root in frontend_runtime_roots
        if runtime_root.exists()
    )
    referenced_runtime_root_paths: List[str] = []
    if service_runtime == "node" or package_json.exists():
        for entry_path in detect_node_entry_script_paths(repo_dir, package_scripts):
            for runtime_root in detect_static_root_reference_paths_from_node_entry(repo_dir, entry_path):
                try:
                    referenced_runtime_root_paths.append(runtime_root.relative_to(repo_dir.resolve()).as_posix())
                except ValueError:
                    continue
    referenced_runtime_root_paths = sorted_unique(referenced_runtime_root_paths)
    combined_runtime_root_paths = sorted_unique(frontend_runtime_root_paths + referenced_runtime_root_paths)
    runtime_build_artifact_paths = [
        path_text for path_text in combined_runtime_root_paths if looks_like_generated_artifact_path(path_text)
    ]

    workspace_package_by_dir: Dict[str, Dict[str, object]] = {}
    for workspace_package in workspace_packages:
        relative_dir = str(workspace_package.get("relative_dir") or "").strip()
        if relative_dir:
            workspace_package_by_dir[relative_dir] = workspace_package

    build_command_candidates: List[str] = []
    required_build_commands: List[str] = []
    root_build_script = package_scripts.get("build")
    if isinstance(root_build_script, str) and root_build_script.strip():
        build_command_candidates.append(build_root_script_command(detected_node_package_manager, "build"))

    for runtime_root in runtime_build_artifact_paths:
        for relative_dir, workspace_package in workspace_package_by_dir.items():
            if runtime_root == relative_dir or runtime_root.startswith(f"{relative_dir}/"):
                scripts = workspace_package.get("scripts", {})
                if not isinstance(scripts, dict):
                    continue
                build_script = scripts.get("build")
                if not isinstance(build_script, str) or not build_script.strip():
                    continue
                build_command = build_workspace_script_command(
                    detected_node_package_manager,
                    "build",
                    relative_dir,
                    package_name=str(workspace_package.get("name") or "").strip() or None,
                )
                if build_command:
                    build_command_candidates.append(build_command)
                    required_build_commands.append(build_command)

    selected_service_build_command: Optional[str] = None
    selected_service_has_build_script = False
    if isinstance(selected_service_package, dict):
        scripts = selected_service_package.get("scripts", {})
        relative_dir = str(selected_service_package.get("relative_dir") or "").strip()
        if isinstance(scripts, dict):
            selected_build_script = scripts.get("build")
            if isinstance(selected_build_script, str) and selected_build_script.strip():
                selected_service_has_build_script = True
                build_command = build_workspace_script_command(
                    detected_node_package_manager,
                    "build",
                    relative_dir,
                    package_name=str(selected_service_package.get("name") or "").strip() or None,
                )
                if build_command:
                    selected_service_build_command = build_command
                    build_command_candidates.append(build_command)

    requires_build_step = False
    build_requirement_reasons: List[str] = []
    if runtime_build_artifact_paths:
        requires_build_step = True
        joined_paths = ", ".join(f"`{path_text}`" for path_text in runtime_build_artifact_paths)
        build_requirement_reasons.append(f"runtime depends on generated assets at {joined_paths}")

    start_script_texts: List[str] = []
    root_start_script = package_scripts.get("start")
    if isinstance(root_start_script, str) and root_start_script.strip():
        start_script_texts.append(root_start_script.strip())
    if isinstance(selected_service_package, dict):
        selected_scripts = selected_service_package.get("scripts", {})
        if isinstance(selected_scripts, dict):
            selected_start_script = selected_scripts.get("start")
            if isinstance(selected_start_script, str) and selected_start_script.strip():
                start_script_texts.append(selected_start_script.strip())

    if isinstance(node_entry_command, str) and node_entry_command.strip():
        if command_requires_prior_build(node_entry_command):
            requires_build_step = True
            build_requirement_reasons.append(f'entry command `{node_entry_command}` requires built artifacts')
        elif command_mentions_generated_artifact(node_entry_command):
            requires_build_step = True
            build_requirement_reasons.append(f'entry command `{node_entry_command}` references generated artifact paths')

    for script_text in start_script_texts:
        if command_requires_prior_build(script_text):
            requires_build_step = True
            build_requirement_reasons.append(f'start script `{script_text}` requires a prior build')
        elif command_mentions_generated_artifact(script_text):
            requires_build_step = True
            build_requirement_reasons.append(f'start script `{script_text}` references generated artifact paths')

    if selected_service_build_command and selected_service_has_build_script:
        selected_service_requires_build = False
        if isinstance(node_entry_command, str) and node_entry_command.strip():
            selected_service_requires_build = (
                command_requires_prior_build(node_entry_command)
                or command_mentions_generated_artifact(node_entry_command)
            )
        if not selected_service_requires_build:
            for script_text in start_script_texts:
                if command_requires_prior_build(script_text) or command_mentions_generated_artifact(script_text):
                    selected_service_requires_build = True
                    break
        if selected_service_requires_build:
            required_build_commands.append(selected_service_build_command)

    build_command_candidates = sorted_unique(build_command_candidates)
    required_build_commands = ordered_unique(required_build_commands)
    if requires_build_step and not required_build_commands:
        if build_command_candidates:
            required_build_commands = ordered_unique(build_command_candidates)
        elif isinstance(root_build_script, str) and root_build_script.strip():
            required_build_commands = [build_root_script_command(detected_node_package_manager, "build")]
    build_requirement_reasons = sorted_unique(build_requirement_reasons)
    if referenced_runtime_root_paths:
        facts.append(f'- referenced runtime asset paths = {", ".join(referenced_runtime_root_paths)}')
    if runtime_build_artifact_paths:
        facts.append(f'- runtime build artifact paths = {", ".join(runtime_build_artifact_paths)}')
    if required_build_commands:
        facts.append(f'- minimum required build commands = {", ".join(required_build_commands)}')
    if build_command_candidates:
        facts.append(f'- build command candidates = {", ".join(build_command_candidates)}')
    if requires_build_step and build_requirement_reasons:
        facts.append(f'- runtime requires an application build step because {", ".join(build_requirement_reasons)}')

    config_file_hints: List[str] = []
    for candidate in collect_matching_files(repo_dir, [".env.example", "**/*.service", "deploy/**/*"]):
        if candidate.is_file():
            config_file_hints.append(candidate.relative_to(repo_dir).as_posix())

    database_file_hints = detect_database_file_hints(repo_dir, readme_text)
    storage_path_hints = detect_storage_path_hints(repo_dir, readme_text)

    normalized_config_file_hints = normalize_config_file_hints(config_file_hints)
    normalized_database_file_hints = sorted_unique(database_file_hints)
    normalized_storage_hints = normalize_storage_hints(storage_path_hints)

    if normalized_config_file_hints:
        facts.append(f'- config file hints = {", ".join(normalized_config_file_hints)}')
    if normalized_database_file_hints:
        facts.append(f'- database file hints = {", ".join(normalized_database_file_hints)}')
    if normalized_storage_hints:
        facts.append(f'- storage path hints = {", ".join(normalized_storage_hints)}')

    return {
        "service_runtime": service_runtime,
        "facts": facts,
        "python_dependencies": python_dependencies,
        "package_scripts": sorted(str(name) for name in package_scripts.keys()),
        "python_entry_command": python_entry_command,
        "node_entry_command": node_entry_command,
        "selected_service_package": selected_service_package,
        "workspace_packages": workspace_packages,
        "detected_port": port,
        "requires_python": requires_python,
        "env_var_names": sorted(env_var_names),
        "has_package_lock": package_lock.exists(),
        "package_manager": package_manager,
        "has_nextjs_ts_config": any((repo_dir / candidate).exists() for candidate in ("next.config.ts",)),
        "system_dependency_hints": system_dependency_hints,
        "config_file_hints": normalized_config_file_hints,
        "database_file_hints": normalized_database_file_hints,
        "storage_hints": normalized_storage_hints,
        "frontend_runtime_root_paths": combined_runtime_root_paths,
        "runtime_build_artifact_paths": runtime_build_artifact_paths,
        "required_build_commands": required_build_commands,
        "build_command_candidates": build_command_candidates,
        "requires_build_step": requires_build_step,
        "build_requirement_reasons": build_requirement_reasons,
    }


def summarize_analysis(analysis: Dict[str, object], source_type: str, project_slug: str) -> Dict[str, object]:
    selected_service_package = analysis.get("selected_service_package")
    summarized_service_package: Optional[Dict[str, object]] = None
    if isinstance(selected_service_package, dict):
        summarized_service_package = {}
        for key in ("name", "relative_dir", "build_command", "start_command", "scripts", "dependencies"):
            value = selected_service_package.get(key)
            if isinstance(value, (str, int, float, bool)) or value is None:
                summarized_service_package[key] = value
            elif isinstance(value, list):
                summarized_service_package[key] = value
            elif isinstance(value, dict):
                summarized_service_package[key] = value
    return {
        "source_type": source_type,
        "project_slug": project_slug,
        "service_runtime": analysis.get("service_runtime"),
        "package_manager": analysis.get("package_manager"),
        "has_nextjs_ts_config": analysis.get("has_nextjs_ts_config"),
        "entrypoint_python": analysis.get("python_entry_command"),
        "entrypoint_node": analysis.get("node_entry_command"),
        "detected_port": analysis.get("detected_port"),
        "requires_python": analysis.get("requires_python"),
        "system_dependencies": analysis.get("system_dependency_hints", []),
        "environment_variables": analysis.get("env_var_names", []),
        "config_file_hints": analysis.get("config_file_hints", []),
        "database_file_hints": analysis.get("database_file_hints", []),
        "storage_hints": analysis.get("storage_hints", []),
        "selected_service_package": summarized_service_package,
    }


def render_athena_managed_tool_block(project_slug: str, host_port: int, proxy_mode: str = "strip_prefix", indent: str = "    ") -> str:
    managed_block = "\n".join(
        [
            f"# BEGIN {ATHENA_NGINX_TOOL_BLOCK_PREFIX} {project_slug}",
            render_nginx_add_conf(project_slug, host_port, proxy_mode=proxy_mode).strip(),
            f"# END {ATHENA_NGINX_TOOL_BLOCK_PREFIX} {project_slug}",
        ]
    )
    return textwrap.indent(managed_block, indent, lambda _line: True)


def tool_block_pattern(project_slug: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?ms)^[ \t]*# BEGIN {re.escape(ATHENA_NGINX_TOOL_BLOCK_PREFIX)} {re.escape(project_slug)}\n.*?^[ \t]*# END {re.escape(ATHENA_NGINX_TOOL_BLOCK_PREFIX)} {re.escape(project_slug)}\s*\n?"
    )


def upsert_tool_nginx_into_tools_conf(config_text: str, project_slug: str, host_port: int, proxy_mode: str = "strip_prefix") -> Tuple[str, str]:
    managed_block = render_athena_managed_tool_block(project_slug, host_port, proxy_mode=proxy_mode, indent="")
    existing_match = tool_block_pattern(project_slug).search(config_text)
    if existing_match is not None:
        existing_managed_block = existing_match.group(0).strip()
        desired_managed_block = managed_block.strip()
        if existing_managed_block == desired_managed_block:
            normalized_existing = config_text if config_text.endswith("\n") or not config_text else config_text + "\n"
            return normalized_existing, "already_managed"
        updated_text = config_text[:existing_match.start()] + managed_block + "\n" + config_text[existing_match.end():]
        updated_text = re.sub(r"\n{3,}", "\n\n", updated_text).lstrip("\n")
        return updated_text if updated_text.endswith("\n") else updated_text + "\n", "updated_managed"

    updated_text = config_text
    if updated_text and not updated_text.endswith("\n"):
        updated_text += "\n"
    if updated_text.strip():
        updated_text += "\n"
    updated_text += managed_block + "\n"
    return updated_text, "inserted_managed"


def reload_nginx(runner_log_path: Optional[Path]) -> Tuple[str, str]:
    if not command_exists("nginx"):
        return "nginx_missing", "nginx command not found in PATH"

    try:
        test_result = run_sudo_command(["nginx", "-t"], runner_log_path=runner_log_path, timeout=30)
    except OSError as exc:
        return "sudo_unavailable", str(exc)
    if test_result.returncode != 0:
        return "nginx_test_failed", test_result.stdout.strip() or "nginx -t failed"

    reload_attempts: List[List[str]] = [["nginx", "-s", "reload"]]
    if command_exists("systemctl"):
        reload_attempts.append(["systemctl", "reload", "nginx"])

    reload_outputs: List[str] = []
    for args in reload_attempts:
        try:
            reload_result = run_sudo_command(args, runner_log_path=runner_log_path, timeout=30)
        except OSError as exc:
            reload_outputs.append(f"{' '.join(args)}: {exc}")
            continue
        if reload_result.returncode == 0:
            return "reloaded", reload_result.stdout.strip()
        reload_outputs.append(f"{' '.join(args)}: {reload_result.stdout.strip()}")

    output = "\n".join(chunk for chunk in reload_outputs if chunk).strip()
    return "nginx_reload_failed", output or "failed to reload nginx"


def sync_athena_nginx_config(project_slug: str, host_port: int, output_dir: Path, runner_log_path: Optional[Path], proxy_mode: str = "strip_prefix") -> Dict[str, object]:
    result: Dict[str, object] = {
        "path": str(ATHENA_NGINX_CONFIG_PATH),
        "project_slug": project_slug,
        "host_port": host_port,
        "proxy_mode": proxy_mode,
        "changed": False,
    }
    try:
        read_result = run_sudo_command(["cat", str(ATHENA_NGINX_CONFIG_PATH)], runner_log_path=runner_log_path, timeout=30)
    except OSError as exc:
        result["status"] = "sudo_unavailable"
        result["error"] = str(exc)
        return result
    if read_result.returncode != 0:
        result["status"] = "read_failed"
        result["error"] = read_result.stdout.strip() or f"failed to read {ATHENA_NGINX_CONFIG_PATH} with sudo"
        return result
    existing_text = read_result.stdout

    try:
        updated_text, merge_status = upsert_tool_nginx_into_tools_conf(existing_text, project_slug, host_port, proxy_mode=proxy_mode)
    except Exception as exc:  # noqa: BLE001
        result["status"] = "merge_failed"
        result["error"] = str(exc)
        return result

    result["merge_status"] = merge_status
    if updated_text == existing_text:
        result["status"] = merge_status
        return result

    backup_path = output_dir / "tools.conf.before"
    temp_config_path = output_dir / "tools.conf.updated"
    athena_backup_path = ATHENA_NGINX_CONFIG_PATH.with_name(f"{ATHENA_NGINX_CONFIG_PATH.stem}_{utc_now_stamp()}{ATHENA_NGINX_CONFIG_PATH.suffix}")
    result["backup_path"] = str(backup_path)
    result["athena_backup_path"] = str(athena_backup_path)
    result["temp_config_path"] = str(temp_config_path)
    try:
        write_text(backup_path, existing_text)
        write_text(temp_config_path, updated_text)
        backup_result = run_sudo_command(
            ["cp", str(ATHENA_NGINX_CONFIG_PATH), str(athena_backup_path)],
            runner_log_path=runner_log_path,
            timeout=30,
        )
        if backup_result.returncode != 0:
            result["status"] = "backup_failed"
            result["error"] = backup_result.stdout.strip() or f"failed to create backup {athena_backup_path}"
            return result
        write_result = run_sudo_command(["cp", str(temp_config_path), str(ATHENA_NGINX_CONFIG_PATH)], runner_log_path=runner_log_path, timeout=30)
        if write_result.returncode != 0:
            result["status"] = "write_failed"
            result["error"] = write_result.stdout.strip() or f"failed to copy updated config into {ATHENA_NGINX_CONFIG_PATH}"
            return result
    except OSError as exc:
        result["status"] = "write_failed"
        result["error"] = str(exc)
        return result

    result["changed"] = True
    reload_status, reload_output = reload_nginx(runner_log_path)
    result["reload_status"] = reload_status
    if reload_output:
        result["reload_output"] = reload_output
    if reload_status == "reloaded":
        result["status"] = "synced"
        return result

    try:
        write_result = run_sudo_command(["cp", str(backup_path), str(ATHENA_NGINX_CONFIG_PATH)], runner_log_path=runner_log_path, timeout=30)
        result["reverted"] = write_result.returncode == 0
        if write_result.returncode != 0:
            result["revert_error"] = write_result.stdout.strip() or f"failed to restore {ATHENA_NGINX_CONFIG_PATH}"
    except OSError as exc:
        result["reverted"] = False
        result["revert_error"] = str(exc)
    result["status"] = reload_status
    return result


HTML_RELATIVE_ASSET_EXTENSIONS = {
    ".js",
    ".mjs",
    ".css",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".json",
    ".wasm",
    ".map",
    ".mp3",
    ".wav",
    ".mp4",
    ".webm",
}


SUBPATH_ALLOWED_ROOT_COMMENT = "ka-subpath-allow-root"
SUBPATH_CLIENT_DIR_MARKERS = ("components", "client", "web", "static", "public", "views", "templates")
SUBPATH_BROWSER_ATTRS = ("href", "src", "action")
SUBPATH_CLIENT_METHOD_PATTERNS = (
    re.compile(r'\bfetch\(\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1'),
    re.compile(r'\bnew\s+Request\(\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1'),
    re.compile(r'\bnew\s+EventSource\(\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1'),
    re.compile(r'\baxios\.(?:get|post|put|delete|patch)\(\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1'),
    re.compile(r'\baxios\(\s*\{\s*[^}]*\burl\s*:\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1', re.DOTALL),
    re.compile(r'\bwindow\.location\.href\s*=\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1'),
    re.compile(r'\bwindow\.location\.assign\(\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1\s*\)'),
    re.compile(r'\bwindow\.open\(\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1'),
)
SUBPATH_TEMPLATE_ALLOWLIST_FIELDS = ("pathTemplate",)


NEXTJS_ENTRY_CANDIDATES = [
    "src/app/layout.tsx",
    "app/layout.tsx",
    "src/pages/_app.tsx",
    "pages/_app.tsx",
    "src/pages/_document.tsx",
    "pages/_document.tsx",
]


NEXTJS_RUNTIME_SHIM_BASENAME = SUBPATH_NEXTJS_RUNTIME_SHIM_BASENAME
NEXTJS_WINDOW_TYPES_BASENAME = SUBPATH_NEXTJS_WINDOW_TYPES_BASENAME


def is_runtime_root_relative_file(file_path: Path, repo_dir: Path) -> bool:
    return subpath_is_runtime_root_relative_file(
        file_path,
        repo_dir,
        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
    )


def is_server_side_code_file(file_path: Path, repo_dir: Path) -> bool:
    return subpath_is_server_side_code_file(
        file_path,
        repo_dir,
        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
    )


def find_frontend_rewrite_targets(repo_dir: Path) -> List[Path]:
    plan = subpath_build_subpath_plan(
        repo_dir,
        parse_package_json=parse_package_json,
        detect_node_entry_script_paths=detect_node_entry_script_paths,
        read_text_if_exists=read_text_if_exists,
        collect_python_frontend_hint_files_fn=collect_python_frontend_hint_files,
        workspace_frontend_package_dirs_fn=workspace_frontend_package_dirs,
        vite_project_roots_fn=vite_project_roots,
        collect_matching_files=collect_matching_files,
        discover_workspace_packages=discover_workspace_packages,
    )
    return subpath_find_plan_rewrite_targets(
        repo_dir,
        plan,
        collect_matching_files=collect_matching_files,
        discover_runtime_root_frontend_files=lambda path: discover_runtime_root_frontend_files(path, include_code_files=True),
        is_server_side_code_file=is_server_side_code_file,
        vite_project_roots=vite_project_roots,
    )


def discover_runtime_root_frontend_files(repo_dir: Path, *, include_code_files: bool) -> List[Path]:
    return subpath_discover_runtime_root_frontend_files(
        repo_dir,
        include_code_files=include_code_files,
        collect_matching_files=collect_matching_files,
        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
    )


def is_allowed_vite_root_html_url(url: str, file_path: Path, repo_dir: Path) -> bool:
    return subpath_is_allowed_vite_root_html_url(
        url,
        file_path,
        repo_dir,
        vite_project_roots=vite_project_roots,
    )


def is_browser_facing_source(file_path: Path, repo_dir: Path, text: str) -> bool:
    return subpath_is_browser_facing_source(
        file_path,
        repo_dir,
        text,
        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
    )


def scan_subpath_findings(file_path: Path, framework: str, base_path: str, repo_dir: Path) -> List[SubpathAuditFinding]:
    return subpath_scan_subpath_findings(
        file_path,
        framework,
        base_path,
        repo_dir,
        read_text=read_text,
        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
        vite_project_roots=vite_project_roots,
    )


def is_nextjs_project(repo_dir: Path) -> bool:
    return subpath_is_nextjs_project(repo_dir, parse_package_json=parse_package_json)


def is_vite_project(repo_dir: Path) -> bool:
    return subpath_is_vite_project(repo_dir, vite_project_roots_fn=vite_project_roots)


def is_create_react_app_project(repo_dir: Path) -> bool:
    return subpath_is_create_react_app_project(repo_dir, parse_package_json=parse_package_json)


def is_vue_cli_project(repo_dir: Path) -> bool:
    return subpath_is_vue_cli_project(repo_dir, parse_package_json=parse_package_json)


def is_express_static_project(repo_dir: Path) -> bool:
    return subpath_is_express_static_project(
        repo_dir,
        parse_package_json=parse_package_json,
        detect_node_entry_script_paths=detect_node_entry_script_paths,
        detect_frontend_runtime_roots_fn=detect_frontend_runtime_roots,
    )


def is_static_html_project(repo_dir: Path) -> bool:
    return subpath_is_static_html_project(repo_dir, collect_matching_files=collect_matching_files)


def nextjs_entry_file(repo_dir: Path) -> Optional[Path]:
    for candidate in NEXTJS_ENTRY_CANDIDATES:
        path = repo_dir / candidate
        if path.is_file():
            return path
    return None


def nextjs_config_file(repo_dir: Path) -> Optional[Path]:
    for candidate in ("next.config.ts", "next.config.mjs", "next.config.js"):
        path = repo_dir / candidate
        if path.is_file():
            return path
    return None


def vite_config_file(repo_dir: Path) -> Optional[Path]:
    for package_dir in vite_project_roots(repo_dir):
        for candidate in ("vite.config.ts", "vite.config.js", "vite.config.mjs"):
            path = package_dir / candidate
            if path.is_file():
                return path
    return None


def ensure_vite_base_config(repo_dir: Path, project_slug: str) -> List[str]:
    return subpath_ensure_vite_base_config(
        repo_dir,
        project_slug,
        vite_config_file=vite_config_file,
        read_text=read_text,
        write_text=write_text,
    )


def apply_vite_subpath_adapter(repo_dir: Path, project_slug: str) -> List[str]:
    return subpath_apply_vite_subpath_adapter(
        repo_dir,
        project_slug,
        ensure_vite_base_config_fn=ensure_vite_base_config,
        find_frontend_rewrite_targets=find_frontend_rewrite_targets,
        rewrite_frontend_subpath_urls=rewrite_frontend_subpath_urls,
        sorted_unique=sorted_unique,
    )


def ensure_vue_cli_public_path(repo_dir: Path, project_slug: str) -> List[str]:
    return subpath_ensure_vue_cli_public_path(
        repo_dir,
        project_slug,
        read_text=read_text,
        write_text=write_text,
    )


def ensure_cra_homepage(repo_dir: Path, project_slug: str) -> List[str]:
    return subpath_ensure_cra_homepage(
        repo_dir,
        project_slug,
        parse_package_json=parse_package_json,
        write_text=write_text,
    )


def ensure_nextjs_basepath_config(repo_dir: Path, project_slug: str) -> List[str]:
    return subpath_ensure_nextjs_basepath_config(
        repo_dir,
        project_slug,
        nextjs_config_file=nextjs_config_file,
        read_text=read_text,
        write_text=write_text,
    )


def ensure_next_link_import(text: str) -> str:
    return subpath_ensure_next_link_import(text)


def insert_import_after_directives(text: str, import_line: str) -> str:
    return subpath_insert_import_after_directives(text, import_line)


def ensure_nextjs_helper_module(entry_path: Path, repo_dir: Path, project_slug: str) -> List[str]:
    return subpath_ensure_nextjs_helper_module(
        entry_path,
        repo_dir,
        project_slug,
        read_text_if_exists=read_text_if_exists,
        write_text=write_text,
    )


def auto_fix_nextjs_subpath_issues(repo_dir: Path, project_slug: str) -> List[str]:
    return subpath_auto_fix_nextjs_subpath_issues(
        repo_dir,
        project_slug,
        nextjs_entry_file=nextjs_entry_file,
        collect_matching_files=collect_matching_files,
        read_text=read_text,
        read_text_if_exists=read_text_if_exists,
        write_text=write_text,
        sorted_unique=sorted_unique,
    )


def apply_nextjs_subpath_adapter(repo_dir: Path, project_slug: str) -> List[str]:
    return subpath_apply_nextjs_subpath_adapter(
        repo_dir,
        project_slug,
        ensure_nextjs_basepath_config_fn=ensure_nextjs_basepath_config,
        auto_fix_nextjs_subpath_issues_fn=auto_fix_nextjs_subpath_issues,
    )


def frontend_runtime_root(repo_dir: Path, file_path: Path, runtime_roots: Optional[List[Path]] = None) -> Path:
    return subpath_frontend_runtime_root(repo_dir, file_path, runtime_roots)


def rewrite_origin_based_subpath_logic(text: str) -> str:
    return subpath_rewrite_origin_based_subpath_logic(text)


def rewrite_frontend_subpath_urls(file_path: Path, base_path: str, repo_dir: Path) -> bool:
    return subpath_rewrite_frontend_subpath_urls(
        file_path,
        base_path,
        repo_dir,
        read_text=read_text,
        write_text=write_text,
        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
        detect_frontend_runtime_root_groups=detect_frontend_runtime_root_groups,
        vite_project_roots=vite_project_roots,
    )


def rewrite_frontend_html_attribute_urls(file_path: Path, base_path: str, repo_dir: Path) -> bool:
    return subpath_rewrite_frontend_html_attribute_urls(
        file_path,
        base_path,
        repo_dir,
        read_text=read_text,
        write_text=write_text,
        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
        detect_frontend_runtime_root_groups=detect_frontend_runtime_root_groups,
        vite_project_roots=vite_project_roots,
    )


def rewrite_frontend_html_link_urls(file_path: Path, base_path: str, repo_dir: Path) -> bool:
    return subpath_rewrite_frontend_html_link_urls(
        file_path,
        base_path,
        repo_dir,
        read_text=read_text,
        write_text=write_text,
        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
        detect_frontend_runtime_root_groups=detect_frontend_runtime_root_groups,
        vite_project_roots=vite_project_roots,
    )


def rewrite_frontend_html_script_urls(file_path: Path, base_path: str, repo_dir: Path) -> bool:
    return subpath_rewrite_frontend_html_script_urls(
        file_path,
        base_path,
        repo_dir,
        read_text=read_text,
        write_text=write_text,
        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
        detect_frontend_runtime_root_groups=detect_frontend_runtime_root_groups,
        vite_project_roots=vite_project_roots,
    )


def rewrite_frontend_html_form_action_urls(file_path: Path, base_path: str, repo_dir: Path) -> bool:
    return subpath_rewrite_frontend_html_form_action_urls(
        file_path,
        base_path,
        repo_dir,
        read_text=read_text,
        write_text=write_text,
        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
        detect_frontend_runtime_root_groups=detect_frontend_runtime_root_groups,
        vite_project_roots=vite_project_roots,
    )


def rewrite_frontend_client_request_urls(file_path: Path, base_path: str, repo_dir: Path) -> bool:
    return subpath_rewrite_frontend_client_request_urls(
        file_path,
        base_path,
        repo_dir,
        read_text=read_text,
        write_text=write_text,
    )


def render_nginx_add_conf(project_slug: str, host_port: int, proxy_mode: str = "strip_prefix") -> str:
    base_path = build_deployment_base_path(project_slug)
    if proxy_mode == "preserve_prefix":
        exact_location = textwrap.dedent(
            f"""
            location = {base_path} {{
                auth_request /_ka_auth;
                error_page 401 = @ka_oauth_login;

                proxy_pass http://127.0.0.1:{host_port}{base_path};
                proxy_http_version 1.1;

                proxy_buffering off;
                proxy_cache off;
                proxy_read_timeout 3600s;
                proxy_send_timeout 3600s;

                proxy_set_header Host $host;
                proxy_set_header X-Real-IP $remote_addr;
                proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                proxy_set_header X-Forwarded-Proto https;
                proxy_set_header X-Forwarded-Host $host;
                proxy_set_header X-Forwarded-Port 443;
                proxy_set_header X-Forwarded-Prefix {base_path};
            }}
            """
        ).strip()
        prefixed_proxy_pass = f"http://127.0.0.1:{host_port}{base_path}/"
    else:
        exact_location = textwrap.dedent(
            f"""
            location = {base_path} {{
                return 301 {base_path}/;
            }}
            """
        ).strip()
        prefixed_proxy_pass = f"http://127.0.0.1:{host_port}/"
    return textwrap.dedent(
        f"""
        # Additional nginx rules for {project_slug}
        {exact_location}

        location ^~ {base_path}/ {{
            auth_request /_ka_auth;
            error_page 401 = @ka_oauth_login;

            proxy_pass {prefixed_proxy_pass};
            proxy_http_version 1.1;

            proxy_buffering off;
            proxy_cache off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;

            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
            proxy_set_header X-Forwarded-Host $host;
            proxy_set_header X-Forwarded-Port 443;
            proxy_set_header X-Forwarded-Prefix {base_path};
        }}
        """
    ).strip() + "\n"


def prepare_shared_repo(
    source_type: str,
    source: str,
    ref: Optional[str],
    fetch_log_path: Optional[Path],
    shared_repo_dir: Path,
    shared_repo_metadata_path: Path,
) -> Tuple[Path, bool, List[str]]:
    ensure_dir(shared_repo_dir.parent)
    for stale_dir in shared_repo_dir.parent.glob(f"{shared_repo_dir.name}.tmp-*"):
        remove_path(stale_dir)
    carried_forward_outputs: List[str] = []

    if source_type == "local":
        signature = local_source_signature(Path(source), shared_repo_dir)
        if shared_repo_matches(signature, shared_repo_dir, shared_repo_metadata_path):
            return shared_repo_dir, True, carried_forward_outputs
        staging_dir = shared_repo_dir.parent / f"{shared_repo_dir.name}.tmp-{utc_now_stamp()}"
        remove_path(staging_dir)
        sync_local_source(Path(source).resolve(), staging_dir)
        shared_dockerfile = shared_repo_dir / "Dockerfile"
        staging_dockerfile = staging_dir / "Dockerfile"
        if shared_dockerfile.exists() and not staging_dockerfile.exists():
            copy_if_exists(shared_dockerfile, staging_dockerfile)
            carried_forward_outputs.append("Dockerfile")
        remove_path(shared_repo_dir)
        staging_dir.replace(shared_repo_dir)
    else:
        signature = git_source_signature(source, ref)
        if fetch_log_path is None:
            raise ValueError("fetch_log_path is required for git sources")
        if shared_repo_matches(signature, shared_repo_dir, shared_repo_metadata_path):
            return shared_repo_dir, True, carried_forward_outputs
        staging_dir = shared_repo_dir.parent / f"{shared_repo_dir.name}.tmp-{utc_now_stamp()}"
        remove_path(staging_dir)
        clone_git_source(source, ref, staging_dir, fetch_log_path, None)
        for generated_name in ("Dockerfile", "PROJECT_ONBOARDING.md"):
            generated_path = staging_dir / generated_name
            if generated_path.exists() and not is_git_tracked_file(staging_dir, generated_path):
                remove_path(generated_path)
        remove_path(shared_repo_dir)
        staging_dir.replace(shared_repo_dir)

    write_json(shared_repo_metadata_path, signature)
    return shared_repo_dir, False, carried_forward_outputs


def prepare_job_repo_from_shared(shared_repo_dir: Path, job_repo_dir: Path) -> Path:
    remove_path(job_repo_dir)
    shutil.copytree(shared_repo_dir, job_repo_dir, dirs_exist_ok=True)
    return job_repo_dir


def sync_generated_outputs_to_shared_repo(source_repo_dir: Path, shared_repo_dir: Path) -> List[str]:
    synced: List[str] = []
    for name in ("Dockerfile", "PROJECT_ONBOARDING.md"):
        src = source_repo_dir / name
        dst = shared_repo_dir / name
        if src.exists():
            copy_if_exists(src, dst)
            synced.append(name)
    return synced


def determine_generation_mode(
    existing_dockerfile: bool,
    existing_onboarding: bool,
    *,
    existing_dockerfile_tracked: bool = True,
    existing_onboarding_tracked: bool = True,
) -> Tuple[bool, str]:
    reusable_dockerfile = existing_dockerfile and existing_dockerfile_tracked
    reusable_onboarding = existing_onboarding and existing_onboarding_tracked
    if reusable_dockerfile and reusable_onboarding:
        return True, "both"
    if reusable_dockerfile and not reusable_onboarding:
        return False, "onboarding_only"
    return False, "both"


def build_runtime_rules(analysis: Dict[str, object]) -> str:
    service_runtime = analysis["service_runtime"]
    python_entry_command = analysis["python_entry_command"]
    node_entry_command = analysis["node_entry_command"]
    requires_python = analysis["requires_python"]
    package_scripts = analysis["package_scripts"]
    port = analysis["detected_port"]
    has_nextjs_ts_config = bool(analysis.get("has_nextjs_ts_config"))
    package_manager = analysis.get("package_manager")
    required_build_commands = analysis.get("required_build_commands", [])
    build_command_candidates = analysis.get("build_command_candidates", [])
    requires_build_step = bool(analysis.get("requires_build_step"))
    runtime_build_artifact_paths = analysis.get("runtime_build_artifact_paths", [])
    local_official_images = list_local_official_docker_images()

    runtime_rules: List[str] = [
        "- 若当前环境已存在满足项目要求的兼容 `docker.io` 官方基础镜像，优先直接复用；否则再选择兼容的 `docker.io` 官方基础镜像",
    ]
    if service_runtime == "python":
        runtime_rules.extend(
            [
                "- 这是 Python 运行时项目时，优先选择兼容的官方 Python 基础镜像",
                "- 存在 `pyproject.toml` 时优先使用 `pip install .`；只有 `requirements.txt` 时使用 `pip install -r requirements.txt`",
                "- 已知 Python 启动命令时，使用 JSON-form CMD，优先采用已识别的入口命令",
                "- 已知端口时写 `EXPOSE`",
            ]
        )
        python_images = [image for image in local_official_images if image.startswith("docker.io/library/python:")]
        if python_images:
            runtime_rules.append(
                f'- 当前环境已存在可复用的 Python 官方基础镜像：`{"`, `".join(python_images)}`；如满足项目要求，优先从这些镜像中选择'
            )
        if python_entry_command:
            runtime_rules.append(f'- 当前优先入口命令候选是 `{python_entry_command}`')
        if requires_python:
            runtime_rules.append(f'- 当前仓库声明的 Python 版本范围是 `{requires_python}`')
    elif service_runtime == "node":
        runtime_rules.extend(
            [
                "- 这是 Node 运行时项目时，优先选择兼容的官方 Node 基础镜像",
                "- 如果存在 `package-lock.json`，使用 `npm ci`",
                "- 已知 Node 启动命令时，使用 JSON-form CMD，优先采用已识别的入口命令",
                "- 不要使用 run.sh 之类的本地开发辅助脚本作为容器入口",
                "- 已知端口时写 `EXPOSE`",
            ]
        )
        node_images = [image for image in local_official_images if image.startswith("docker.io/library/node:")]
        if node_images:
            runtime_rules.append(
                f'- 当前环境已存在可复用的 Node 官方基础镜像：`{"`, `".join(node_images)}`；如满足项目要求，优先从这些镜像中选择'
            )
        if node_entry_command:
            runtime_rules.append(f'- 当前优先入口命令候选是 `{node_entry_command}`')
        if isinstance(required_build_commands, list) and required_build_commands:
            runtime_rules.append(
                f'- 当前最低必需构建命令是：`{"`, `".join(str(item) for item in required_build_commands)}`'
            )
        if isinstance(build_command_candidates, list) and build_command_candidates:
            runtime_rules.append(f'- 当前可直接采用的构建命令候选是：`{"`, `".join(str(item) for item in build_command_candidates)}`')
        if isinstance(required_build_commands, list) and required_build_commands:
            runtime_rules.append("- 对 workspace / monorepo，只执行生成运行时必需产物的最小构建集合；不要为了保险把所有候选 build 命令都用 `&&` 串起来全部执行，除非已有直接证据证明它们都必需")
        if requires_build_step:
            runtime_rules.append("- 当前项目运行时依赖预构建产物，Dockerfile 必须包含明确的应用构建步骤，不能只安装依赖后直接启动")
        if isinstance(runtime_build_artifact_paths, list) and runtime_build_artifact_paths:
            runtime_rules.append(
                f'- 已识别到运行时依赖这些构建产物路径：`{"`, `".join(str(item) for item in runtime_build_artifact_paths)}`；必须确保这些路径在容器启动前已生成'
            )
        if has_nextjs_ts_config:
            runtime_rules.extend(
                [
                    "- 当前仓库存在 `next.config.ts`；若运行时使用 `next start`，运行时镜像必须能加载 TypeScript 配置",
                    "- 存在 `next.config.ts` 时，不要在构建后用 `pnpm prune --prod`、`npm prune --omit=dev` 或等价方式移除 `typescript`，除非你同时将运行时配置改成 `next.config.js`/`next.config.mjs`",
                    "- 存在 `next.config.ts` 时，优先让运行时入口直接执行 Next 二进制，例如 `./node_modules/.bin/next start`，不要让运行时依赖 `pnpm start`，除非你已在运行时镜像中显式提供 `pnpm`",
                ]
            )
        if package_manager == "pnpm":
            runtime_rules.append("- 使用 `pnpm` 的项目在多阶段 Dockerfile 中，若任何运行时命令仍依赖 `pnpm`，运行时阶段必须显式执行 `corepack enable` 或以其他稳定方式提供 `pnpm` 可执行文件")
    else:
        runtime_rules.extend(
            [
                "- 未能明确识别唯一运行时时，优先选择更像真实服务运行时的一侧，不要把仅用于测试的 package.json 误判为服务入口",
                "- 必须根据仓库中的实际启动证据生成 Dockerfile，不要硬写 `node server.js` 或其他默认命令",
                "- 已知端口时写 `EXPOSE`",
            ]
        )
        if package_scripts:
            runtime_rules.append(
                f'- 已检测到 package.json scripts = `{", ".join(package_scripts)}`，请判断这些脚本是否只是测试用途'
            )
    if isinstance(port, int):
        runtime_rules.append(f"- 已确认服务端口时，在 Dockerfile 中写 `EXPOSE {port}`")
    return "\n".join(runtime_rules)


def build_source_context_rules(source_type: str, source: str, ref: Optional[str]) -> str:
    source_context_rules: List[str] = []
    if source_type == "local":
        source_context_rules.append("- 本地源码输入时：代码仓库地址写 `第一版为本地源码交付`，分支/标签写 `UNKNOWN`")
    else:
        source_context_rules.extend(
            [
                f'- Git 仓库输入时：代码仓库地址写 `{source}`',
                f'- Git 仓库输入时：分支/标签写 `{ref or "default branch"}`',
            ]
        )
    return "\n".join(source_context_rules)


def build_validation_feedback_block(findings: List[str], warnings: Optional[List[str]] = None) -> str:
    lines = [
        "## 上一次生成未通过校验",
        "",
        "你必须修复以下问题后再输出最终文件：",
    ]
    for item in findings:
        lines.append(f"- {item}")
    warning_items = [item for item in (warnings or []) if item]
    if warning_items:
        lines.extend(["", "同时尽量消除这些告警："])
        for item in warning_items:
            lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "不要解释原因，不要输出 diff；直接修改仓库中的目标文件，使其通过这些校验。",
        ]
    )
    return "\n".join(lines)


def read_prompt_template() -> str:
    content = read_text(GENERATION_RULES_PATH)
    marker = "<!-- PROMPT_BODY_START -->"
    if marker in content:
        content = content.split(marker, 1)[1]
    return content.strip()


def build_codex_prompt(
    source_type: str,
    source: str,
    ref: Optional[str],
    repo_dir: Path,
    generation_mode: str = "both",
    validation_findings: Optional[List[str]] = None,
    validation_warnings: Optional[List[str]] = None,
) -> str:
    analysis = collect_repo_analysis(repo_dir, source_type, source, ref)
    template = read_prompt_template()
    task_text = "只生成 `PROJECT_ONBOARDING.md`。" if generation_mode == "onboarding_only" else "只生成 `Dockerfile` 和 `PROJECT_ONBOARDING.md`。"
    dockerfile_rules = build_runtime_rules(analysis)
    if generation_mode == "onboarding_only":
        dockerfile_rules = "- 保留仓库中现有 `Dockerfile`，不要重写、重命名或补生成新的 `Dockerfile`"
    replacements = {
        "## 任务\n\n只生成 `Dockerfile` 和 `PROJECT_ONBOARDING.md`。": f"## 任务\n\n{task_text}",
        "{{RUNTIME_RULES}}": dockerfile_rules,
        "{{SOURCE_CONTEXT_RULES}}": build_source_context_rules(source_type, source, ref),
        "{{REPO_FACTS}}": "\n".join(analysis["facts"]),
    }
    prompt = template
    for placeholder, value in replacements.items():
        prompt = prompt.replace(placeholder, value)
    feedback_block = ""
    if validation_findings:
        feedback_block = "\n\n" + build_validation_feedback_block(validation_findings, validation_warnings)
    return (prompt.strip() + feedback_block).strip()


def diagnose_codex_failure(output: str) -> Optional[str]:
    lowered = output.lower()
    if "502 bad gateway" in lowered or "upstream request failed" in lowered:
        return "Upstream Codex gateway failed while streaming the response."
    if "codex cli not found" in lowered:
        return "Codex CLI is not available in PATH."
    if "timed out" in lowered:
        return "codex exec timed out before producing final output."
    if "reconnecting..." in lowered and "stream disconnected before completion" in lowered:
        return "Codex generation lost its upstream connection while streaming the response."
    if "stream disconnected before completion" in lowered or "error sending request for url" in lowered:
        return "Codex generation failed because the upstream API connection dropped before completion."
    if "rate limit" in lowered or "too many requests" in lowered:
        return "Codex generation failed because the upstream API rate-limited the request."
    if "unauthorized" in lowered or "not authenticated" in lowered or "authentication" in lowered:
        return "Codex generation failed because the current codex session is not authenticated."
    return None


def should_retry_codex_failure(output: str) -> bool:
    lowered = output.lower()
    retryable_markers = [
        "reconnecting...",
        "stream disconnected before completion",
        "error sending request for url",
        "502 bad gateway",
        "upstream request failed",
        "connection reset",
        "timed out",
    ]
    non_retryable_markers = [
        "codex cli not found",
        "unauthorized",
        "not authenticated",
        "authentication",
        "context length",
        "prompt is too long",
    ]
    if any(marker in lowered for marker in non_retryable_markers):
        return False
    return any(marker in lowered for marker in retryable_markers)


def invoke_codex_generation(
    repo_dir: Path,
    codex_home: Path,
    log_path: Path,
    summary_path: Path,
    source_type: str,
    source: str,
    ref: Optional[str],
    generation_mode: str = "both",
    validation_findings: Optional[List[str]] = None,
    validation_warnings: Optional[List[str]] = None,
    runner_log_path: Optional[Path] = None,
) -> None:
    if not command_exists("codex"):
        raise RuntimeError("codex CLI not found in PATH")
    prompt = build_codex_prompt(
        source_type,
        source,
        ref,
        repo_dir,
        generation_mode,
        validation_findings=validation_findings,
        validation_warnings=validation_warnings,
    )
    env = dict(os.environ)
    env["CODEX_HOME"] = str(codex_home)
    args = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "workspace-write",
        "--cd",
        str(repo_dir),
        "--output-last-message",
        str(summary_path),
        prompt,
    ]

    last_output = ""
    for attempt in range(1, CODEX_GENERATION_MAX_ATTEMPTS + 1):
        write_text(
            log_path,
            f"[started] generation_mode={generation_mode} attempt={attempt} at {datetime.now(timezone.utc).isoformat()}\n",
        )
        result = run_command(
            args,
            cwd=repo_dir,
            env=env,
            log_path=log_path,
            runner_log_path=runner_log_path,
            timeout=CODEX_GENERATION_TIMEOUT_SECONDS,
            heartbeat_seconds=CODEX_GENERATION_HEARTBEAT_SECONDS,
            stream_log_label="codex_output",
        )
        last_output = result.stdout
        if result.returncode == 0:
            return
        if attempt >= CODEX_GENERATION_MAX_ATTEMPTS or not should_retry_codex_failure(last_output):
            break
        if runner_log_path is not None:
            append_text(
                runner_log_path,
                f"[{datetime.now(timezone.utc).isoformat()}] codex_retry attempt={attempt} sleeping_seconds={CODEX_GENERATION_RETRY_DELAY_SECONDS}\n",
            )
        time.sleep(CODEX_GENERATION_RETRY_DELAY_SECONDS)
    raise RuntimeError(f"codex exec failed:\n{last_output}")


def validate_generated_files(
    repo_dir: Path,
    source_type: Optional[str] = None,
    source: Optional[str] = None,
    ref: Optional[str] = None,
    generation_mode: str = "both",
) -> Tuple[List[str], List[str]]:
    findings: List[str] = []
    warnings: List[str] = []
    dockerfile = repo_dir / "Dockerfile"
    onboarding = repo_dir / "PROJECT_ONBOARDING.md"
    if not dockerfile.exists():
        findings.append("Missing Dockerfile")
        return findings, warnings
    if not onboarding.exists():
        findings.append("Missing PROJECT_ONBOARDING.md")
        return findings, warnings
    docker_text = read_text(dockerfile)
    onboarding_text = read_text(onboarding)
    analysis: Optional[Dict[str, object]] = None
    if source_type and source:
        analysis = collect_repo_analysis(repo_dir, source_type, source, ref)
    dockerfile_findings: List[str] = []

    if "CMD [" not in docker_text and "ENTRYPOINT [" not in docker_text:
        dockerfile_findings.append("Dockerfile is missing JSON-form CMD or ENTRYPOINT")
    dockerfile_findings.extend(validate_multistage_copy_sources(repo_dir, docker_text))
    if re.search(r"^\s*EXPOSE\s+UNKNOWN\b", docker_text, re.MULTILINE):
        dockerfile_findings.append("Dockerfile uses UNKNOWN in EXPOSE for a runtime-critical port; omit EXPOSE when the port is not confirmed")
    if re.search(r"^\s*ENV\s+PORT\s*=\s*UNKNOWN\b", docker_text, re.MULTILINE):
        dockerfile_findings.append("Dockerfile sets PORT=UNKNOWN; omit the PORT default until a concrete port is confirmed")
    if not re.search(r"^##?\s*4[.\s]+运行参数\b", onboarding_text, re.MULTILINE):
        findings.append("PROJECT_ONBOARDING.md is missing section 4")
    if re.search(r"\b(TODO|UNKNOWN)\b", onboarding_text):
        warnings.append("PROJECT_ONBOARDING.md still contains unresolved confirmation items")
    if re.search(r"\b(TODO|UNKNOWN)\b", docker_text):
        warnings.append("Dockerfile contains TODO/UNKNOWN markers")

    if analysis:
        package_scripts = analysis.get("package_scripts", [])
        node_entry_command = analysis.get("node_entry_command")
        python_entry_command = analysis.get("python_entry_command")
        system_dependencies = analysis.get("system_dependency_hints", [])
        has_build_step = dockerfile_has_build_step(docker_text)
        package_manager = analysis.get("package_manager")
        has_nextjs_ts_config = bool(analysis.get("has_nextjs_ts_config"))
        requires_build_step = bool(analysis.get("requires_build_step"))

        if requires_build_step and "build" in package_scripts:
            if not has_build_step:
                dockerfile_findings.append("Dockerfile is missing an application build step even though package.json.scripts.build exists")
        if requires_build_step and not has_build_step:
            dockerfile_findings.append("Dockerfile start command appears to require built artifacts, but no build step was detected")
        elif isinstance(node_entry_command, str) and "dist/" in node_entry_command and not has_build_step:
            dockerfile_findings.append("Dockerfile start command appears to require built artifacts, but no build step was detected")
        if package_manager == "pnpm" and "pnpm" in docker_text and "corepack enable" not in docker_text and "pnpm install" not in docker_text:
            warnings.append("Dockerfile references pnpm but does not clearly enable or install pnpm in the image")
        if has_nextjs_ts_config:
            runs_next_start = bool(re.search(r'next["\s,-]+start|\bnext start\b', docker_text))
            if runs_next_start:
                if "typescript" not in docker_text and "pnpm install" in docker_text and "pnpm prune --prod" in docker_text:
                    dockerfile_findings.append("Dockerfile prunes devDependencies even though next.config.ts requires TypeScript to remain available at runtime")
                if "corepack enable" not in docker_text and '"pnpm"' not in docker_text and "pnpm " not in docker_text:
                    dockerfile_findings.append("Dockerfile may not provide pnpm at runtime even though next.config.ts can trigger pnpm-based TypeScript installation during next start")
        if "sqlite3" in system_dependencies and "sqlite3" not in docker_text and "sqlite" not in docker_text:
            dockerfile_findings.append("Dockerfile does not install sqlite3 even though runtime evidence indicates sqlite3 is required")
        if "ffmpeg" in system_dependencies and "ffmpeg" not in docker_text:
            dockerfile_findings.append("Dockerfile does not install ffmpeg even though runtime evidence indicates ffmpeg is required")
        env_var_names = analysis.get("env_var_names", [])
        if isinstance(env_var_names, list):
            for env_name in env_var_names:
                if isinstance(env_name, str) and env_name not in onboarding_text:
                    warnings.append(f"PROJECT_ONBOARDING.md does not mention detected environment variable {env_name}")
        if isinstance(node_entry_command, str) and node_entry_command.strip():
            if "CMD [" in docker_text and "node" in node_entry_command and "server.js" in node_entry_command and '"server.js"' not in docker_text:
                warnings.append("Dockerfile CMD may not match the detected Node entrypoint")
        if isinstance(python_entry_command, str) and python_entry_command.strip():
            if "CMD [" in docker_text and "python -m" in python_entry_command and python_entry_command.split()[-1] not in docker_text:
                warnings.append("Dockerfile CMD may not match the detected Python entrypoint")

    if generation_mode == "onboarding_only":
        warnings.extend(dockerfile_findings)
    else:
        findings.extend(dockerfile_findings)
    return findings, warnings


def parse_confirmed_port(onboarding_path: Path) -> Optional[int]:
    text = read_text(onboarding_path)
    patterns = [
        r"服务端口[:：]\s*`?(\d{2,5})`?",
        r"对外端口[:：]\s*`?(\d{2,5})/tcp`?",
        r"对外端口[:：]\s*`?(\d{2,5})`?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def parse_onboarding_run_spec(onboarding_path: Path) -> Dict[str, object]:
    text = read_text(onboarding_path)
    spec: Dict[str, object] = {
        "container_port": parse_confirmed_port(onboarding_path),
        "environment_variables": [],
        "env_file_hint": None,
        "start_command": None,
        "persistence_paths": [],
        "health_path_hint": "/",
    }
    ignored_tokens = {"UNKNOWN", "NEEDS_CONFIRMATION", "TBD", "EXPOSE", "NONE"}
    spec["environment_variables"] = sorted_unique(
        token
        for token in re.findall(r"`([A-Z][A-Z0-9_]+)`", text)
        if token not in ignored_tokens
    )
    env_file_match = re.search(r"环境文件[:：]\s*`([^`]+)`", text)
    if env_file_match:
        spec["env_file_hint"] = env_file_match.group(1).strip()
    start_match = re.search(r"运行入口[:：]\s*`([^`]+)`", text)
    if not start_match:
        start_match = re.search(r"启动命令[:：]\s*`([^`]+)`", text)
    if start_match:
        spec["start_command"] = start_match.group(1).strip()
    persistence_matches = re.findall(r"持久化目录[:：]\s*`([^`]+)`", text)
    persistence_paths: List[str] = []
    for value in persistence_matches:
        for item in re.split(r"[，,]", value):
            normalized = item.strip()
            if normalized and normalized not in {"UNKNOWN", "NEEDS_CONFIRMATION", "TBD - 由管理员构建", "NONE"}:
                persistence_paths.append(normalized)
    spec["persistence_paths"] = sorted_unique(persistence_paths)
    health_match = re.search(r"health[^`\s]*[:：]?\s*`?(/[^`\s]*)`?", text, re.IGNORECASE)
    if health_match:
        spec["health_path_hint"] = health_match.group(1)
    return spec


def derive_runtime_env_vars(
    run_spec: Dict[str, object],
    host_port: int,
    container_port: int,
) -> List[str]:
    env_args: List[str] = []
    env_names = run_spec.get("environment_variables", [])
    if isinstance(env_names, list) and "PORT" in env_names:
        env_args.extend(["-e", f"PORT={container_port}"])
    env_file_hint = run_spec.get("env_file_hint")
    project_env_file = run_spec.get("project_env_file")
    if not (isinstance(project_env_file, str) and project_env_file) and isinstance(env_file_hint, str) and env_file_hint:
        env_args.extend(["--env-file", env_file_hint])
    return env_args


def inspect_image_id(image_name: str, repo_dir: Path, podman_env: Dict[str, str]) -> Optional[str]:
    result = run_command(
        build_podman_command(["image", "inspect", "--format", "{{.Id}}", image_name], podman_env),
        cwd=repo_dir,
        env=podman_env,
    )
    if result.returncode != 0:
        return None
    image_id = result.stdout.strip()
    return image_id or None


def inspect_image_runtime_spec(
    image_name: str,
    repo_dir: Path,
    podman_env: Dict[str, str],
    runner_log_path: Optional[Path] = None,
) -> Dict[str, object]:
    result = run_command(
        build_podman_command(["image", "inspect", image_name], podman_env),
        cwd=repo_dir,
        env=podman_env,
        runner_log_path=runner_log_path,
    )
    if result.returncode != 0:
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return {}
    image_info = payload[0]
    config = image_info.get("Config") if isinstance(image_info.get("Config"), dict) else {}
    exposed_ports = config.get("ExposedPorts") if isinstance(config.get("ExposedPorts"), dict) else {}
    exposed_port = None
    for port_name in exposed_ports:
        if isinstance(port_name, str):
            match = re.match(r"(\d{2,5})/(tcp|udp)", port_name)
            if match:
                exposed_port = int(match.group(1))
                break
    env_values = config.get("Env") if isinstance(config.get("Env"), list) else []
    return {
        "exposed_port": exposed_port,
        "env": [item for item in env_values if isinstance(item, str)],
        "cmd": config.get("Cmd"),
        "entrypoint": config.get("Entrypoint"),
        "working_dir": config.get("WorkingDir"),
    }


def merge_run_spec(
    *,
    project_slug: str,
    user_host_port: Optional[int],
    onboarding_spec: Dict[str, object],
    image_spec: Dict[str, object],
) -> Dict[str, object]:
    mapped_host_port: Optional[int] = None
    mapped_container_port: Optional[int] = None
    port_configs = load_project_port_configs()
    runtime_overrides = load_project_runtime_overrides(project_slug)
    project_port_config = port_configs.get(project_slug)
    if isinstance(project_port_config, dict) and is_valid_port_mapping(project_port_config.get("port")):
        host_text, container_text = str(project_port_config["port"]).split(":", 1)
        mapped_host_port = int(host_text)
        mapped_container_port = int(container_text)

    container_port = mapped_container_port
    if not isinstance(container_port, int):
        onboarding_port = onboarding_spec.get("container_port")
        if isinstance(onboarding_port, int):
            container_port = onboarding_port
    if not isinstance(container_port, int):
        exposed = image_spec.get("exposed_port")
        if isinstance(exposed, int):
            container_port = exposed
    host_port = user_host_port
    if isinstance(container_port, int):
        if mapped_host_port is None or mapped_container_port is None:
            mapped_host_port, mapped_container_port = resolve_project_port_mapping(project_slug, container_port)
        if host_port is None:
            host_port = mapped_host_port
        container_port = mapped_container_port
    return {
        "container_port": container_port,
        "host_port": host_port,
        "environment_variables": onboarding_spec.get("environment_variables", []),
        "env_file_hint": onboarding_spec.get("env_file_hint"),
        "start_command": onboarding_spec.get("start_command"),
        "persistence_paths": onboarding_spec.get("persistence_paths", []),
        "health_path_hint": onboarding_spec.get("health_path_hint") or "/",
        "image_env": image_spec.get("env", []),
        "project_env": runtime_overrides.get("env", {}),
        "project_env_file": runtime_overrides.get("env_file"),
        "project_volumes": runtime_overrides.get("volumes", []),
    }


def build_image(repo_dir: Path, image_name: str, log_path: Path, podman_env: Dict[str, str], runner_log_path: Optional[Path]) -> CommandResult:
    return run_command(
        build_podman_command(["build", "-t", image_name, "-f", "Dockerfile", "."], podman_env),
        cwd=repo_dir,
        env=podman_env,
        log_path=log_path,
        runner_log_path=runner_log_path,
    )


def diagnose_podman_failure(output: str) -> Optional[str]:
    lowered = output.lower()
    if "/run/user/" in output and "read-only file system" in lowered:
        return "Podman tried to use the default runtime directory under /run/user, which is read-only in this environment."
    if "bolt_state.db" in output and "read-only file system" in lowered:
        return "Podman tried to use the default image store under ~/.local/share/containers/storage, which is read-only in this environment."
    if "newuidmap" in lowered or "user namespace" in lowered:
        return "Rootless Podman is blocked by the current sandbox or user namespace configuration."
    if "permission denied" in lowered:
        return "Podman hit a permission problem while building or running the container."
    return None


def should_fallback_to_isolated_podman(output: str) -> bool:
    lowered = output.lower()
    return (
        ("/run/user/" in output and "read-only file system" in lowered)
        or ("bolt_state.db" in output and "read-only file system" in lowered)
    )


def run_container(
    repo_dir: Path,
    image_name: str,
    host_port: int,
    container_port: int,
    container_name: str,
    log_path: Path,
    podman_env: Dict[str, str],
    env_args: Optional[List[str]] = None,
    extra_volume_args: Optional[List[str]] = None,
    runner_log_path: Optional[Path] = None,
) -> CommandResult:
    args = [
        "run",
        "-d",
        "--name",
        container_name,
        "-p",
        f"{DEFAULT_PROJECT_BIND_HOST}:{host_port}:{container_port}",
        "--restart",
        "always",
    ]
    if env_args:
        args.extend(env_args)
    if extra_volume_args:
        for volume_arg in extra_volume_args:
            args.extend(["-v", volume_arg])
    args.append(image_name)
    return run_command(
        build_podman_command(args, podman_env),
        cwd=repo_dir,
        env=podman_env,
        log_path=log_path,
        runner_log_path=runner_log_path,
    )


def collect_container_logs(
    container_name: str,
    repo_dir: Path,
    podman_env: Dict[str, str],
    runner_log_path: Optional[Path] = None,
) -> str:
    result = run_command(
        build_podman_command(["logs", container_name], podman_env),
        cwd=repo_dir,
        env=podman_env,
        runner_log_path=runner_log_path,
    )
    return result.stdout


def cleanup_container(
    container_name: str,
    repo_dir: Path,
    podman_env: Dict[str, str],
    runner_log_path: Optional[Path] = None,
) -> None:
    run_command(
        build_podman_command(["rm", "-f", container_name], podman_env),
        cwd=repo_dir,
        env=podman_env,
        runner_log_path=runner_log_path,
    )


def wait_for_container_ready(
    container_name: str,
    host_port: int,
    health_path: str,
    repo_dir: Path,
    podman_env: Dict[str, str],
    runner_log_path: Optional[Path] = None,
    timeout_seconds: int = RUN_READY_TIMEOUT_SECONDS,
    poll_interval_seconds: int = RUN_READY_POLL_INTERVAL_SECONDS,
) -> Tuple[bool, str]:
    deadline = time.monotonic() + timeout_seconds
    path = health_path if health_path.startswith("/") else f"/{health_path}"
    last_error = "container did not become ready"
    while time.monotonic() < deadline:
        ps_result = run_command(
            build_podman_command(["ps", "--filter", f"name={container_name}", "--format", "{{.Status}}"], podman_env),
            cwd=repo_dir,
            env=podman_env,
            runner_log_path=runner_log_path,
        )
        if not ps_result.stdout.strip():
            logs = collect_container_logs(container_name, repo_dir, podman_env, runner_log_path)
            return False, logs or "container exited before becoming ready"
        try:
            conn = http.client.HTTPConnection("127.0.0.1", host_port, timeout=2)
            conn.request("GET", path)
            response = conn.getresponse()
            body = response.read()
            conn.close()
            if 200 <= response.status < 500:
                return True, f"http {response.status} {len(body)} bytes"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(poll_interval_seconds)
    logs = collect_container_logs(container_name, repo_dir, podman_env, runner_log_path)
    return False, logs or last_error


def fetch_http_text(
    host_port: int,
    path: str,
    *,
    timeout_seconds: int = 5,
    max_redirects: int = 5,
) -> Tuple[int, Dict[str, str], str, str]:
    current_path = path if path.startswith("/") else f"/{path}"
    for _attempt in range(max_redirects + 1):
        conn = http.client.HTTPConnection("127.0.0.1", host_port, timeout=timeout_seconds)
        conn.request("GET", current_path)
        response = conn.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        headers = {key.lower(): value for key, value in response.getheaders()}
        conn.close()
        if response.status in {301, 302, 307, 308} and "location" in headers:
            location = headers["location"]
            parsed = urlparse(location)
            current_path = parsed.path or current_path
            if parsed.query:
                current_path = f"{current_path}?{parsed.query}"
            continue
        return response.status, headers, body, current_path
    raise RuntimeError(f"too many redirects while requesting {path}")


def run_runtime_subpath_audit(
    host_port: int,
    project_slug: str,
    proxy_mode: str,
    entry_path_hint: Optional[str] = None,
    entry_path_candidates: Optional[List[str]] = None,
) -> Dict[str, object]:
    return subpath_run_runtime_subpath_audit(
        host_port,
        project_slug,
        proxy_mode,
        entry_path_hint,
        entry_path_candidates,
        external_access_host=DEFAULT_EXTERNAL_ACCESS_HOST,
        fetch_http_text=fetch_http_text,
    )


def derive_image_name(source_type: str, source: str, job_id: str) -> str:
    if source_type == "git":
        parsed = urlparse(source)
        base = Path(parsed.path).name or "repo"
        name = re.sub(r"\.git$", "", base)
    else:
        name = Path(source).name
    return f"{slugify(name)}:{job_id.lower()}"


def make_result_skeleton(
    job_id: str,
    args: argparse.Namespace,
    project_slug: str,
    job_dir: Path,
    repo_dir: Path,
) -> Dict[str, object]:
    return {
        "job_id": job_id,
        "project_slug": project_slug,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_type": args.source_type,
        "source": args.source,
        "ref": args.ref,
        "tool_id": args.tool_id,
        "job_dir": str(job_dir),
        "repo_dir": str(repo_dir),
        "status": "INITIALIZED",
        "generated_files": [],
        "image": None,
        "image_id": None,
        "container": None,
        "container_id": None,
        "confirmed_port": None,
        "build_requested": args.build,
        "run_requested": args.run,
        "warnings": [],
        "errors": [],
        "analysis_summary": {},
        "artifacts": {},
        "final_result": {},
    }


def build_access_url(payload: Dict[str, object]) -> Optional[str]:
    project_slug = payload.get("project_slug")
    if not isinstance(project_slug, str) or not project_slug.strip():
        return None
    return f"https://{DEFAULT_EXTERNAL_ACCESS_HOST}{build_deployment_base_path(project_slug)}"


def build_final_result(payload: Dict[str, object]) -> Dict[str, object]:
    status = payload.get("status")
    if status in {"BUILD_SUCCEEDED", "COMPLETED_WITH_BUILD", "RUN_SUCCEEDED", "COMPLETED_WITHOUT_BUILD"}:
        result = {
            "ok": True,
            "status": status,
            "job_id": payload.get("job_id"),
        }
        if status == "COMPLETED_WITHOUT_BUILD":
            result["generated_files"] = payload.get("generated_files", [])
            result["confirmed_port"] = payload.get("confirmed_port")
            return result
        final_result = {
            **result,
            "image": payload.get("image"),
            "image_id": payload.get("image_id"),
        }
        if status == "RUN_SUCCEEDED":
            container_id = payload.get("container_id")
            if isinstance(container_id, str) and container_id.strip():
                final_result["container_id"] = container_id.strip()
            artifacts = payload.get("artifacts")
            if isinstance(artifacts, dict):
                run_result = artifacts.get("run_result")
                if isinstance(run_result, dict):
                    host_port = run_result.get("host_port")
                    container_port = run_result.get("container_port")
                    if isinstance(host_port, int) and isinstance(container_port, int):
                        final_result["port"] = f"{host_port}:{container_port}"
            access_url = build_access_url(payload)
            if access_url:
                final_result["url"] = access_url
        return final_result
    return {
        "ok": False,
        "status": status,
        "job_id": payload.get("job_id"),
        "errors": payload.get("errors", []),
    }


def emit_final_result(result: Dict[str, object], result_path: Path) -> None:
    result["final_result"] = build_final_result(result)
    write_json(result_path, result)
    runner_log = None
    artifacts = result.get("artifacts")
    if isinstance(artifacts, dict):
        runner_log = artifacts.get("runner_log")
    if isinstance(runner_log, str) and runner_log:
        append_text(
            Path(runner_log),
            f"[{datetime.now(timezone.utc).isoformat()}] final_result_detail status={result.get('status')} analysis_summary={json_dumps_safe(result.get('analysis_summary', {}))} warnings={json_dumps_safe(result.get('warnings', []))}\n",
        )
    print(json_dumps_safe(result["final_result"]))


def main() -> int:
    parser = RunnerArgumentParser(description="Phase 1 onboarding runner")
    parser.add_argument("--source-type", choices=["local", "git"], required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--ref")
    parser.add_argument("--tool-id")
    parser.add_argument("--job-id")
    parser.add_argument("--image-name")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--host-port", type=int)
    args: Optional[argparse.Namespace] = None
    project_slug = "unknown-project"
    job_id: Optional[str] = None
    project_dir = project_jobs_dir(project_slug)
    shared_repo_dir = project_shared_repo_dir(project_slug)
    shared_repo_metadata_path = project_shared_repo_metadata_path(project_slug)
    job_dir = project_dir / "unknown-job"
    output_dir = job_dir / "output"
    repo_dir = shared_repo_dir
    work_repo_dir = project_job_work_repo_dir(project_slug, "unknown-job")
    result_path = output_dir / "result.json"
    runner_log_path = output_dir / "runner.log"
    result: Optional[Dict[str, object]] = None
    artifacts: Dict[str, object] = {}
    failure_stage = "init"
    local_official_images = list_local_official_docker_images_from_env()
    podman_env = dict(os.environ)

    try:
        args = parser.parse_args()
        if args.run and not args.build:
            raise ValueError("--run requires --build")

        project_slug = derive_project_slug(args.source_type, args.source)
        project_dir = project_jobs_dir(project_slug)
        shared_repo_dir = project_shared_repo_dir(project_slug)
        shared_repo_metadata_path = project_shared_repo_metadata_path(project_slug)
        job_id = args.job_id or default_job_id()
        job_dir = project_dir / job_id
        output_dir = job_dir / "output"
        repo_dir = shared_repo_dir
        work_repo_dir = project_job_work_repo_dir(project_slug, job_id)
        result_path = output_dir / "result.json"
        runner_log_path = output_dir / "runner.log"

        ensure_dir(project_dir)
        ensure_dir(job_dir)
        ensure_dir(output_dir)

        result = make_result_skeleton(job_id, args, project_slug, job_dir, repo_dir)
        artifacts = {
            "runner_log": str(runner_log_path),
        }
        if local_official_images:
            artifacts["local_official_images"] = local_official_images
        if args.source_type == "git":
            artifacts["fetch_log"] = str(output_dir / "fetch.log")
        if args.build:
            artifacts["build_log"] = str(output_dir / "build.log")
        if args.run:
            artifacts["run_log"] = str(output_dir / "run.log")
        result["artifacts"] = artifacts
        write_json(result_path, result)
        append_text(
            runner_log_path,
            f"[{datetime.now(timezone.utc).isoformat()}] raw_runner_invocation argv={json_dumps_safe(sys.argv[1:])} cwd={Path.cwd()}\n",
        )
        append_text(
            runner_log_path,
            f"[{datetime.now(timezone.utc).isoformat()}] runtime_environment path={os.environ.get('PATH', '')} codex_path={shutil.which('codex') or ''}\n",
        )
        append_text(
            runner_log_path,
            f"[{datetime.now(timezone.utc).isoformat()}] job_initialized job_id={job_id} source_type={args.source_type} source={args.source}\n",
        )
        if local_official_images:
            append_text(
                runner_log_path,
                f"[{datetime.now(timezone.utc).isoformat()}] local_official_images images={json_dumps_safe(local_official_images)}\n",
            )

        codex_home = prepare_codex_home(job_dir)
        podman_env, podman_artifacts = prepare_default_podman_environment()
        artifacts["codex_home"] = str(codex_home)
        artifacts.update(podman_artifacts)
        result["artifacts"] = artifacts
        write_json(result_path, result)

        fetch_log_path = Path(artifacts["fetch_log"]) if "fetch_log" in artifacts else None
        if args.source_type == "git":
            result["status"] = "FETCHING_SOURCE"
            write_json(result_path, result)
            failure_stage = "fetch"
        repo_dir, reused_shared_repo, carried_forward_outputs = prepare_shared_repo(
            args.source_type,
            args.source,
            args.ref,
            fetch_log_path,
            shared_repo_dir,
            shared_repo_metadata_path,
        )
        failure_stage = "post_fetch"
        repo_dir = prepare_job_repo_from_shared(shared_repo_dir, work_repo_dir)
        artifacts["job_work_repo"] = str(repo_dir)
        result["artifacts"] = artifacts
        if carried_forward_outputs:
            artifacts["carried_forward_shared_outputs"] = carried_forward_outputs
            result["artifacts"] = artifacts

        analysis = collect_repo_analysis(repo_dir, args.source_type, args.source, args.ref)
        result["analysis_summary"] = summarize_analysis(analysis, args.source_type, project_slug)
        result["repo_dir"] = str(repo_dir)
        subpath_prepare = subpath_prepare_subpath_sources(
            repo_dir,
            project_slug,
            build_subpath_plan=build_runtime_subpath_plan,
            detect_runtime_root_evidence=build_runtime_root_evidence,
            apply_subpath_rewrites=lambda repo_dir_arg, slug_arg, plan_arg: subpath_apply_subpath_rewrites(
                repo_dir_arg,
                slug_arg,
                plan=plan_arg,
                apply_framework_config_adapters=lambda plan, slug: subpath_apply_framework_config_adapters(
                    plan,
                    slug,
                    parse_package_json=parse_package_json,
                    read_text=read_text,
                    write_text=write_text,
                ),
                apply_nextjs_subpath_adapter_fn=apply_nextjs_subpath_adapter,
                apply_vite_subpath_adapter_fn=apply_vite_subpath_adapter,
                ensure_vue_cli_public_path_fn=ensure_vue_cli_public_path,
                ensure_cra_homepage_fn=ensure_cra_homepage,
                find_frontend_rewrite_targets=find_frontend_rewrite_targets,
                rewrite_frontend_subpath_urls=rewrite_frontend_subpath_urls,
            ),
            run_static_subpath_audit=lambda repo_dir_arg, slug_arg, plan_arg: subpath_run_static_subpath_audit(
                repo_dir_arg,
                slug_arg,
                plan_arg,
                collect_matching_files=collect_matching_files,
                detect_frontend_runtime_roots=detect_frontend_runtime_roots,
                vite_project_roots=vite_project_roots,
                read_text=read_text,
            ),
            auto_fix_subpath_issues=lambda repo_dir_arg, slug_arg, plan_arg: subpath_auto_fix_subpath_issues(
                repo_dir_arg,
                slug_arg,
                plan_arg,
                auto_fix_nextjs_subpath_issues_fn=auto_fix_nextjs_subpath_issues,
                find_subpath_audit_targets=lambda repo_dir_inner, plan_inner: subpath_find_subpath_audit_targets(
                    repo_dir_inner,
                    plan_inner,
                    collect_matching_files=collect_matching_files,
                    detect_frontend_runtime_roots=detect_frontend_runtime_roots,
                    vite_project_roots=vite_project_roots,
                ),
                rewrite_frontend_subpath_urls=rewrite_frontend_subpath_urls,
                sorted_unique=sorted_unique,
            ),
            auto_fix_findings=lambda repo_dir_arg, slug_arg, findings_arg, plan_arg: subpath_auto_fix_findings(
                repo_dir_arg,
                slug_arg,
                findings_arg,
                plan_arg,
                auto_fix_nextjs_subpath_issues_fn=auto_fix_nextjs_subpath_issues,
                find_subpath_audit_targets=lambda repo_dir_inner, plan_inner: subpath_find_subpath_audit_targets(
                    repo_dir_inner,
                    plan_inner,
                    collect_matching_files=collect_matching_files,
                    detect_frontend_runtime_roots=detect_frontend_runtime_roots,
                    vite_project_roots=vite_project_roots,
                ),
                rewrite_frontend_subpath_urls=rewrite_frontend_subpath_urls,
                rewrite_frontend_html_attribute_urls=rewrite_frontend_html_attribute_urls,
                rewrite_frontend_html_link_urls=rewrite_frontend_html_link_urls,
                rewrite_frontend_html_script_urls=rewrite_frontend_html_script_urls,
                rewrite_frontend_html_form_action_urls=rewrite_frontend_html_form_action_urls,
                rewrite_frontend_client_request_urls=rewrite_frontend_client_request_urls,
                rewrite_frontend_request_api_urls=lambda file_path, base_path, repo_dir: subpath_rewrite_frontend_request_api_urls(
                    file_path,
                    base_path,
                    repo_dir,
                    read_text=read_text,
                    write_text=write_text,
                ),
                rewrite_frontend_navigation_urls=lambda file_path, base_path, repo_dir: subpath_rewrite_frontend_navigation_urls(
                    file_path,
                    base_path,
                    repo_dir,
                    read_text=read_text,
                    write_text=write_text,
                ),
                rewrite_frontend_eventsource_urls=lambda file_path, base_path, repo_dir: subpath_rewrite_frontend_eventsource_urls(
                    file_path,
                    base_path,
                    repo_dir,
                    read_text=read_text,
                    write_text=write_text,
                ),
                rewrite_frontend_return_value_urls=lambda file_path, base_path, repo_dir: subpath_rewrite_frontend_return_value_urls(
                    file_path,
                    base_path,
                    repo_dir,
                    read_text=read_text,
                    write_text=write_text,
                ),
                sorted_unique=sorted_unique,
            ),
            sorted_unique=sorted_unique,
        )
        proxy_mode = str(subpath_prepare["proxy_mode"])
        rewritten_files = list(subpath_prepare["rewritten_files"])
        if rewritten_files:
            artifacts["rewritten_frontend_files"] = rewritten_files
            artifacts["subpath_proxy_mode"] = proxy_mode
            result["artifacts"] = artifacts
            append_warning(result, f"Applied subpath rewrites for tool deployment path to {len(rewritten_files)} frontend files.")
        auto_fixed_files = list(subpath_prepare["auto_fixed_files"])
        if auto_fixed_files:
            append_warning(result, f"Auto-fixed {len(auto_fixed_files)} subpath source files before deployment.")
        static_subpath_audit = subpath_prepare["static_subpath_audit"]
        static_audit_attempts: List[Dict[str, object]] = list(subpath_prepare["static_audit_attempts"])
        static_findings = list(subpath_prepare["static_findings"])
        subpath_declaration = read_subpath_declaration(repo_dir)
        if auto_fixed_files:
            artifacts["rewritten_frontend_files"] = rewritten_files
        rewrite_report = subpath_prepare.get("rewrite_report")
        if isinstance(rewrite_report, dict):
            artifacts["subpath_rewrite_report"] = rewrite_report
        if subpath_prepare.get("plan") is not None:
            artifacts["subpath_plan"] = json_safe(subpath_prepare["plan"])
        if subpath_prepare.get("detection_evidence") is not None:
            artifacts["subpath_detection_evidence"] = json_safe(subpath_prepare["detection_evidence"])
        if subpath_declaration is not None:
            artifacts["subpath_declaration"] = json_safe(subpath_declaration)
            declaration_summary = summarize_subpath_declaration(subpath_declaration)
            if declaration_summary is not None:
                artifacts["subpath_declaration_summary"] = declaration_summary
        summary = result.get("analysis_summary")
        if isinstance(summary, dict):
            if subpath_declaration is not None:
                summary["subpath_declaration"] = subpath_declaration
                declaration_summary = summarize_subpath_declaration(subpath_declaration)
                if declaration_summary is not None:
                    summary["subpath_declaration_summary"] = declaration_summary
            plan_payload = subpath_prepare.get("plan")
            if plan_payload is not None and hasattr(plan_payload, "projects"):
                summary["subpath_plan"] = {
                    "project_count": len(getattr(plan_payload, "projects", ()) or ()),
                    "default_project": getattr(plan_payload, "default_project", None),
                }
                default_project = next(
                    (
                        project
                        for project in getattr(plan_payload, "projects", ())
                        if getattr(project, "project_id", None) == getattr(plan_payload, "default_project", None)
                    ),
                    (getattr(plan_payload, "projects", ()) or [None])[0],
                )
                if default_project is not None:
                    summary["subpath_default_project"] = {
                        "project_id": getattr(default_project, "project_id", None),
                        "framework": getattr(default_project, "framework", None),
                        "proxy_mode": getattr(default_project, "proxy_mode", None),
                        "adapter": getattr(default_project, "adapter", None),
                        "source_adapter": getattr(default_project, "source_adapter", None),
                    }
            elif isinstance(plan_payload, dict):
                projects = plan_payload.get("projects", [])
                default_project_id = plan_payload.get("default_project")
                if isinstance(projects, list):
                    summary["subpath_plan"] = {
                        "project_count": len(projects),
                        "default_project": default_project_id,
                    }
                    default_project = next(
                        (
                            project
                            for project in projects
                            if isinstance(project, dict) and project.get("project_id") == default_project_id
                        ),
                        projects[0] if projects and isinstance(projects[0], dict) else None,
                    )
                    if isinstance(default_project, dict):
                        summary["subpath_default_project"] = {
                            "project_id": default_project.get("project_id"),
                            "framework": default_project.get("framework"),
                            "proxy_mode": default_project.get("proxy_mode"),
                            "adapter": default_project.get("adapter"),
                            "source_adapter": default_project.get("source_adapter"),
                        }
            detection_evidence = subpath_prepare.get("detection_evidence")
            if isinstance(detection_evidence, list):
                summary["subpath_detection_evidence"] = {
                    "count": len(detection_evidence),
                    "sources": sorted({str(item.source) for item in detection_evidence if hasattr(item, "source")}),
                }
            summary["subpath_static_audit_summary"] = {
                "scanned_files_count": len(static_subpath_audit.get("scanned_files", [])) if isinstance(static_subpath_audit, dict) else 0,
                "findings_count": len(static_subpath_audit.get("findings", [])) if isinstance(static_subpath_audit, dict) else 0,
                "findings_by_code": {
                    code: len([item for item in static_subpath_audit.get("findings", []) if isinstance(item, dict) and item.get("code") == code])
                    for code in sorted({str(item.get("code", "")) for item in static_subpath_audit.get("findings", []) if isinstance(item, dict)})
                } if isinstance(static_subpath_audit, dict) else {},
            }
            if isinstance(rewrite_report, dict):
                applied_codes_by_reason = rewrite_report.get("applied_codes_by_reason", {})
                unchanged_codes_by_reason = rewrite_report.get("unchanged_codes_by_reason", {})
                unchanged_codes_by_reason_and_file = rewrite_report.get("unchanged_codes_by_reason_and_file", {})
                top_unchanged_files = []
                if isinstance(unchanged_codes_by_reason_and_file, dict):
                    file_counts: Dict[str, int] = {}
                    for _reason, file_map in unchanged_codes_by_reason_and_file.items():
                        if not isinstance(file_map, dict):
                            continue
                        for file_name, codes in file_map.items():
                            if isinstance(file_name, str) and isinstance(codes, list):
                                file_counts[file_name] = file_counts.get(file_name, 0) + len(codes)
                    top_unchanged_files = [
                        {"file": file_name, "count": count}
                        for file_name, count in sorted(file_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
                    ]
                summary["subpath_rewrite_report_summary"] = {
                    "changed_files_count": len(rewrite_report.get("changed_files", [])),
                    "applied_codes": rewrite_report.get("applied_codes", []),
                    "applied_reason_counts": {
                        reason: len(codes)
                        for reason, codes in applied_codes_by_reason.items()
                    } if isinstance(applied_codes_by_reason, dict) else {},
                    "applied_files_count_by_reason": {
                        reason: len(
                            {
                                file_name
                                for file_name, codes in rewrite_report.get("applied_codes_by_file", {}).items()
                                if isinstance(file_name, str)
                                and isinstance(codes, list)
                                and any(code in codes for code in (applied_codes_by_reason.get(reason, []) if isinstance(applied_codes_by_reason, dict) else []))
                            }
                        )
                        for reason in applied_codes_by_reason.keys()
                    } if isinstance(applied_codes_by_reason, dict) else {},
                    "unchanged_codes_by_reason": unchanged_codes_by_reason,
                    "unchanged_reason_counts": {
                        reason: len(codes)
                        for reason, codes in unchanged_codes_by_reason.items()
                    } if isinstance(unchanged_codes_by_reason, dict) else {},
                    "top_unchanged_files": top_unchanged_files,
                }
        artifacts["subpath_static_audit"] = static_subpath_audit
        artifacts["subpath_static_audit_attempts"] = static_audit_attempts
        result["artifacts"] = artifacts
        extend_warnings(result, static_subpath_audit.get("warnings", []))
        if isinstance(static_findings, list) and static_findings:
            result["status"] = "SUBPATH_STATIC_AUDIT_FAILED"
            result["errors"] = subpath_findings_to_error_texts(static_findings)
            emit_final_result(result, result_path)
            return 1
        append_text(
            runner_log_path,
            f"[{datetime.now(timezone.utc).isoformat()}] source_ready repo_dir={repo_dir} reused_shared_repo={reused_shared_repo}\n",
        )
        append_text(
            runner_log_path,
            f"[{datetime.now(timezone.utc).isoformat()}] analysis_summary summary={json_dumps_safe(result['analysis_summary'])}\n",
        )
        result["status"] = "SOURCE_READY"
        write_json(result_path, result)

        existing_dockerfile_path = repo_dir / "Dockerfile"
        existing_onboarding_path = repo_dir / "PROJECT_ONBOARDING.md"
        existing_dockerfile = existing_dockerfile_path.exists()
        existing_onboarding = existing_onboarding_path.exists()
        existing_dockerfile_tracked = args.source_type != "git" or (
            existing_dockerfile and is_git_tracked_file(repo_dir, existing_dockerfile_path)
        )
        existing_onboarding_tracked = args.source_type != "git" or (
            existing_onboarding and is_git_tracked_file(repo_dir, existing_onboarding_path)
        )
        prebuilt_outputs, generation_mode = determine_generation_mode(
            existing_dockerfile,
            existing_onboarding,
            existing_dockerfile_tracked=existing_dockerfile_tracked,
            existing_onboarding_tracked=existing_onboarding_tracked,
        )
        if prebuilt_outputs:
            if reused_shared_repo:
                extend_warnings(result, [
                    f"Reused shared repo workspace under automation/jobs/{project_slug}/repo.",
                    "Reused tracked Dockerfile and PROJECT_ONBOARDING.md from source repository; skipped codex generation.",
                ])
            else:
                extend_warnings(result, [
                    "Reused tracked Dockerfile and PROJECT_ONBOARDING.md from source repository; skipped codex generation."
                ])
        else:
            if reused_shared_repo:
                extend_warnings(result, [f"Reused shared repo workspace under automation/jobs/{project_slug}/repo."])
            elif carried_forward_outputs:
                extend_warnings(result, [
                    f"Carried forward {', '.join(carried_forward_outputs)} from automation/jobs/{project_slug}/repo into refreshed shared source."
                ])
            result["status"] = "GENERATING_FILES"
            artifacts["codex_log"] = str(output_dir / "codex.log")
            artifacts["codex_summary"] = str(output_dir / "codex-summary.txt")
            result["artifacts"] = artifacts
            write_json(result_path, result)
            if generation_mode == "onboarding_only":
                append_warning(result, "Reused tracked Dockerfile from source repository; only generated PROJECT_ONBOARDING.md.")
            invoke_codex_generation(
                repo_dir=repo_dir,
                codex_home=codex_home,
                log_path=Path(artifacts["codex_log"]),
                summary_path=Path(artifacts["codex_summary"]),
                source_type=args.source_type,
                source=args.source,
                ref=args.ref,
                generation_mode=generation_mode,
                runner_log_path=runner_log_path,
            )

        generated_files = []
        for name in ("Dockerfile", "PROJECT_ONBOARDING.md"):
            if (repo_dir / name).exists():
                generated_files.append(str(repo_dir / name))
        result["generated_files"] = generated_files

        findings, warnings = validate_generated_files(
            repo_dir,
            args.source_type,
            args.source,
            args.ref,
            generation_mode,
        )
        retried_after_validation = False
        if findings and not prebuilt_outputs:
            retried_after_validation = True
            append_text(
                runner_log_path,
                f"[{datetime.now(timezone.utc).isoformat()}] validation_retry findings={json_dumps_safe(findings)} warnings={json_dumps_safe(warnings)}\n",
            )
            invoke_codex_generation(
                repo_dir=repo_dir,
                codex_home=codex_home,
                log_path=Path(artifacts["codex_log"]),
                summary_path=Path(artifacts["codex_summary"]),
                source_type=args.source_type,
                source=args.source,
                ref=args.ref,
                generation_mode=generation_mode,
                validation_findings=findings,
                validation_warnings=warnings,
                runner_log_path=runner_log_path,
            )
            generated_files = []
            for name in ("Dockerfile", "PROJECT_ONBOARDING.md"):
                if (repo_dir / name).exists():
                    generated_files.append(str(repo_dir / name))
            result["generated_files"] = generated_files
            findings, warnings = validate_generated_files(
                repo_dir,
                args.source_type,
                args.source,
                args.ref,
                generation_mode,
            )
        extend_warnings(result, warnings)
        if retried_after_validation and not findings:
            append_warning(result, "Initial generated files failed local validation once; regenerated with validation feedback and recovered.")
        if findings:
            result["errors"] = findings
            result["status"] = "VALIDATION_FAILED"
            emit_final_result(result, result_path)
            return 1

        synced_outputs = sync_generated_outputs_to_shared_repo(repo_dir, shared_repo_dir)
        if synced_outputs:
            artifacts["synced_outputs_to_shared_repo"] = synced_outputs
            result["artifacts"] = artifacts
            append_text(
                runner_log_path,
                f"[{datetime.now(timezone.utc).isoformat()}] synced_outputs_to_shared_repo files={json_dumps_safe(synced_outputs)}\n",
            )

        result["status"] = "FILES_GENERATED"
        onboarding_path = repo_dir / "PROJECT_ONBOARDING.md"
        confirmed_port = parse_confirmed_port(onboarding_path)
        result["confirmed_port"] = confirmed_port
        append_text(
            runner_log_path,
            f"[{datetime.now(timezone.utc).isoformat()}] files_generated confirmed_port={confirmed_port}\n",
        )
        write_json(result_path, result)

        if not args.build:
            result["status"] = "COMPLETED_WITHOUT_BUILD"
            emit_final_result(result, result_path)
            return 0

        if not command_exists("podman"):
            result["status"] = "BUILD_SKIPPED"
            result["errors"] = ["podman not found in PATH"]
            emit_final_result(result, result_path)
            return 1

        image_name = args.image_name or derive_image_name(args.source_type, args.source, job_id)
        result["image"] = image_name
        result["status"] = "BUILDING_IMAGE"
        append_text(
            runner_log_path,
            f"[{datetime.now(timezone.utc).isoformat()}] build_started image={image_name}\n",
        )
        write_json(result_path, result)
        build_result = build_image(repo_dir, image_name, Path(artifacts["build_log"]), podman_env, runner_log_path)
        if build_result.returncode != 0 and podman_artifacts.get("podman_mode") == "default" and should_fallback_to_isolated_podman(build_result.stdout):
            append_warning(result, "Default Podman mode hit a read-only runtime/storage path; retrying build with isolated Podman storage.")
            podman_env, podman_artifacts = prepare_podman_environment(project_slug, job_id)
            artifacts.update(podman_artifacts)
            result["artifacts"] = artifacts
            write_json(result_path, result)
            build_result = build_image(repo_dir, image_name, Path(artifacts["build_log"]), podman_env, runner_log_path)
        if build_result.returncode != 0:
            result["status"] = "BUILD_FAILED"
            result["errors"] = ["podman build failed"]
            diagnosis = diagnose_podman_failure(build_result.stdout)
            if diagnosis:
                append_warning(result, diagnosis)
            emit_final_result(result, result_path)
            return build_result.returncode or 1

        result["image_id"] = inspect_image_id(image_name, repo_dir, podman_env)
        result["status"] = "BUILD_SUCCEEDED"
        append_text(
            runner_log_path,
            f"[{datetime.now(timezone.utc).isoformat()}] build_succeeded image={image_name}\n",
        )
        plan_payload = artifacts.get("subpath_plan")
        if isinstance(plan_payload, dict):
            try:
                subpath_plan_obj = subpath_plan_from_payload(plan_payload, repo_dir)
                if subpath_plan_obj is None:
                    raise ValueError("invalid subpath plan payload")
                build_output_audit = subpath_run_build_output_subpath_audit(
                    repo_dir,
                    project_slug,
                    subpath_plan_obj,
                    policy_overrides=subpath_declaration.get("build_output_policy") if isinstance(subpath_declaration, dict) else None,
                    collect_matching_files=collect_matching_files,
                    read_text=read_text,
                )
                artifacts["subpath_build_output_audit"] = build_output_audit
                summary = result.get("analysis_summary")
                if isinstance(summary, dict):
                    build_summary = build_output_audit.get("summary")
                    if isinstance(build_summary, dict):
                        summary["subpath_build_output_audit_summary"] = build_summary
                build_summary = build_output_audit.get("summary", {})
                enforcement_candidates = build_summary.get("enforcement_candidates", {}) if isinstance(build_summary, dict) else {}
                enforce_codes = {
                    str(item.get("code", "")).strip()
                    for item in build_output_audit.get("findings", [])
                    if isinstance(item, dict)
                    and isinstance(enforcement_candidates, dict)
                    and enforcement_candidates.get(str(item.get("code", "")).strip()) == "enforce"
                }
                if enforce_codes:
                    result["status"] = "SUBPATH_BUILD_OUTPUT_AUDIT_FAILED"
                    result["errors"] = [
                        str(item.get("message", ""))
                        for item in build_output_audit.get("findings", [])
                        if isinstance(item, dict) and str(item.get("code", "")).strip() in enforce_codes
                    ]
                    result["artifacts"] = artifacts
                    emit_final_result(result, result_path)
                    return 1
                if build_output_audit.get("findings"):
                    findings_by_code = build_summary.get("findings_by_code", {}) if isinstance(build_summary, dict) else {}
                    code_summary = ", ".join(f"{key}={value}" for key, value in findings_by_code.items()) if isinstance(findings_by_code, dict) else ""
                    suffix = f" ({code_summary})" if code_summary else ""
                    append_warning(result, f"Build output subpath audit found {len(build_output_audit['findings'])} findings after build{suffix}.")
                result["artifacts"] = artifacts
            except Exception as exc:
                append_warning(result, f"Build output subpath audit skipped due to error: {exc}")
        write_json(result_path, result)

        if not args.run:
            result["status"] = "COMPLETED_WITH_BUILD"
            emit_final_result(result, result_path)
            return 0

        onboarding_spec = parse_onboarding_run_spec(onboarding_path)
        image_spec = inspect_image_runtime_spec(image_name, repo_dir, podman_env, runner_log_path)
        run_spec = merge_run_spec(
            project_slug=project_slug,
            user_host_port=args.host_port,
            onboarding_spec=onboarding_spec,
            image_spec=image_spec,
        )
        artifacts["run_spec"] = run_spec
        result["artifacts"] = artifacts
        write_json(result_path, result)

        container_port = run_spec.get("container_port")
        host_port = run_spec.get("host_port")
        if not isinstance(container_port, int):
            result["status"] = "RUN_SKIPPED"
            result["errors"] = ["No confirmed or inspectable service port found for container run"]
            emit_final_result(result, result_path)
            return 1
        if not isinstance(host_port, int):
            result["status"] = "RUN_SKIPPED"
            result["errors"] = ["No host port could be assigned for container run"]
            emit_final_result(result, result_path)
            return 1

        container_name = project_slug
        runtime_data_dir = project_runtime_data_dir(project_slug, job_id)
        env_args = derive_runtime_env_vars(run_spec, host_port, container_port)
        project_env = run_spec.get("project_env")
        if isinstance(project_env, dict):
            for key, value in project_env.items():
                if isinstance(key, str) and key and isinstance(value, str):
                    env_args.extend(["-e", f"{key}={value}"])
        project_env_file = run_spec.get("project_env_file")
        if isinstance(project_env_file, str) and project_env_file:
            env_args.extend(["--env-file", project_env_file])
        extra_volume_args = run_spec.get("project_volumes")
        if not isinstance(extra_volume_args, list):
            extra_volume_args = []
        result["container"] = container_name
        result["status"] = "STARTING_CONTAINER"
        artifacts["run_spec"] = {
            **run_spec,
            "resolved_host_port": host_port,
            "resolved_container_port": container_port,
            "container_name": container_name,
            "runtime_data_dir": str(runtime_data_dir),
            "env_args": env_args,
            "extra_volume_args": extra_volume_args,
        }
        result["artifacts"] = artifacts
        append_text(
            runner_log_path,
            f"[{datetime.now(timezone.utc).isoformat()}] run_started container={container_name} host_port={host_port} container_port={container_port}\n",
        )
        write_json(result_path, result)
        cleanup_container(container_name, repo_dir, podman_env, runner_log_path)
        run_result = run_container(
            repo_dir=repo_dir,
            image_name=image_name,
            host_port=host_port,
            container_port=container_port,
            container_name=container_name,
            log_path=Path(artifacts["run_log"]),
            podman_env=podman_env,
            env_args=env_args,
            extra_volume_args=extra_volume_args,
            runner_log_path=runner_log_path,
        )
        if run_result.returncode != 0:
            result["status"] = "RUN_FAILED"
            result["errors"] = ["podman run failed"]
            diagnosis = diagnose_podman_failure(run_result.stdout)
            if diagnosis:
                append_warning(result, diagnosis)
            emit_final_result(result, result_path)
            return run_result.returncode or 1

        result["container_id"] = run_result.stdout.strip() or None
        result["status"] = "WAITING_FOR_HEALTHCHECK"
        write_json(result_path, result)
        health_ok, health_detail = wait_for_container_ready(
            container_name=container_name,
            host_port=host_port,
            health_path=str(run_spec.get("health_path_hint") or "/"),
            repo_dir=repo_dir,
            podman_env=podman_env,
            runner_log_path=runner_log_path,
        )
        if not health_ok:
            result["status"] = "RUN_FAILED"
            result["errors"] = ["container did not become ready"]
            if health_detail:
                append_warning(result, health_detail)
            cleanup_container(container_name, repo_dir, podman_env, runner_log_path)
            emit_final_result(result, result_path)
            return 1

        runtime_plan = None
        if isinstance(plan_payload, dict):
            runtime_plan = subpath_plan_from_payload(plan_payload, repo_dir)
        runtime_phase = subpath_runtime_subpath_phase(
            host_port,
            project_slug,
            proxy_mode,
            plan=runtime_plan,
            run_runtime_subpath_audit=run_runtime_subpath_audit,
        )
        runtime_subpath_audit = runtime_phase["runtime_subpath_audit"]
        artifacts["subpath_runtime_audit"] = runtime_subpath_audit
        summary = result.get("analysis_summary")
        if isinstance(summary, dict):
            summary["subpath_runtime_audit_summary"] = {
                "checked_paths": len(runtime_subpath_audit.get("checked_paths", [])) if isinstance(runtime_subpath_audit, dict) else 0,
                "findings": len(runtime_subpath_audit.get("findings", [])) if isinstance(runtime_subpath_audit, dict) else 0,
                "warnings": len(runtime_subpath_audit.get("warnings", [])) if isinstance(runtime_subpath_audit, dict) else 0,
                "skipped_by_capability": bool(runtime_subpath_audit.get("skipped_by_capability")) if isinstance(runtime_subpath_audit, dict) else False,
                "default_runtime_project": runtime_subpath_audit.get("default_runtime_project") if isinstance(runtime_subpath_audit, dict) else None,
                "entry_path_candidates": len(runtime_subpath_audit.get("entry_path_candidates", [])) if isinstance(runtime_subpath_audit, dict) else 0,
                "audited_projects": len(runtime_subpath_audit.get("audited_projects", [])) if isinstance(runtime_subpath_audit, dict) else 0,
                "projects_with_findings": len(runtime_subpath_audit.get("projects_with_findings", [])) if isinstance(runtime_subpath_audit, dict) else 0,
                "project_summaries": runtime_subpath_audit.get("project_summaries", []) if isinstance(runtime_subpath_audit, dict) else [],
            }
        extend_warnings(result, runtime_subpath_audit.get("warnings", []))
        runtime_findings = runtime_phase["runtime_findings"]
        if isinstance(runtime_findings, list) and runtime_findings:
            result["status"] = "SUBPATH_RUNTIME_AUDIT_FAILED"
            result["errors"] = [str(item.get("message", "")) for item in runtime_findings if isinstance(item, dict)]
            result["artifacts"] = artifacts
            cleanup_container(container_name, repo_dir, podman_env, runner_log_path)
            emit_final_result(result, result_path)
            return 1

        result["status"] = "RUN_SUCCEEDED"
        result["confirmed_port"] = container_port
        artifacts["run_result"] = {
            "host_port": host_port,
            "container_port": container_port,
            "port": f"{host_port}:{container_port}",
            "healthcheck_path": run_spec.get("health_path_hint") or "/",
            "container_name": container_name,
        }
        nginx_add_conf_path = project_dir / "nginx-add.conf"
        write_text(nginx_add_conf_path, render_nginx_add_conf(project_slug, host_port, proxy_mode=proxy_mode))
        artifacts["nginx_add_conf"] = str(nginx_add_conf_path)
        athena_nginx_sync = sync_athena_nginx_config(project_slug, host_port, output_dir, runner_log_path, proxy_mode=proxy_mode)
        artifacts["athena_nginx_sync"] = athena_nginx_sync
        sync_status = athena_nginx_sync.get("status")
        if sync_status == "synced":
            append_warning(
                result,
                f"Synchronized {ATHENA_NGINX_CONFIG_PATH} for /tools2/{project_slug} and reloaded nginx.",
            )
        elif sync_status == "already_managed":
            append_warning(
                result,
                f"{ATHENA_NGINX_CONFIG_PATH} already contains the managed nginx block for /tools2/{project_slug}.",
            )
        else:
            detail = athena_nginx_sync.get("error") or athena_nginx_sync.get("reload_output") or sync_status
            append_warning(result, f"Automatic Athena nginx sync for /tools2/{project_slug} did not complete: {detail}")
        result["artifacts"] = artifacts
        append_text(
            runner_log_path,
            f"[{datetime.now(timezone.utc).isoformat()}] run_succeeded container={container_name}\n",
        )
        emit_final_result(result, result_path)
        return 0

    except subprocess.TimeoutExpired as exc:
        error_text = str(exc)
        if result is None:
            final_payload = {
                "ok": False,
                "status": "FAILED",
                "job_id": job_id,
                "errors": [error_text],
            }
            print(json_dumps_safe(final_payload))
            return 1
        result["status"] = "FAILED"
        result["errors"] = [f"command timed out: {summarize_command_args(list(exc.cmd) if isinstance(exc.cmd, list) else [str(exc.cmd)])}"]
        emit_final_result(result, result_path)
        return 1
    except Exception as exc:  # noqa: BLE001
        error_text = str(exc)
        if result is None:
            status = "ARGUMENT_ERROR" if isinstance(exc, ValueError) else "FAILED"
            final_payload = {
                "ok": False,
                "status": status,
                "job_id": job_id,
                "errors": [error_text],
            }
            print(json_dumps_safe(final_payload))
            return 1
        if args and args.source_type == "git" and failure_stage == "fetch":
            result["status"] = "FETCH_FAILED"
            result["errors"] = ["git clone failed"]
            diagnosis = diagnose_git_failure(error_text)
            if diagnosis:
                append_warning(result, diagnosis)
            if "fetch_log" in artifacts:
                append_warning(result, "See fetch_log artifact for full git output.")
        elif result.get("status") == "GENERATING_FILES":
            result["status"] = "GENERATION_FAILED"
            if error_text.startswith("codex exec timed out"):
                result["errors"] = ["codex exec timed out"]
            elif error_text.startswith("codex exec failed"):
                result["errors"] = ["codex exec failed"]
            else:
                result["errors"] = ["file generation failed"]
            diagnosis = diagnose_codex_failure(error_text)
            if diagnosis:
                append_warning(result, diagnosis)
            if "codex_log" in artifacts:
                append_warning(result, "See codex_log artifact for full codex output.")
        elif result.get("status") in {"STARTING_CONTAINER", "WAITING_FOR_HEALTHCHECK"}:
            result["status"] = "RUN_FAILED"
            result["errors"] = ["container run failed"]
            container_name = result.get("container")
            if isinstance(container_name, str) and container_name:
                try:
                    cleanup_container(container_name, repo_dir, podman_env, runner_log_path)
                except Exception:
                    pass
        else:
            result["status"] = "FAILED"
            result["errors"] = [error_text]
        append_text(
            runner_log_path,
            f"[{datetime.now(timezone.utc).isoformat()}] job_failed error={error_text}\n",
        )
        emit_final_result(result, result_path)
        return 1


if __name__ == "__main__":
    sys.exit(main())
