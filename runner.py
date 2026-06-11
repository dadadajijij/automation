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
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse


ROOT_DIR = Path(__file__).resolve().parent.parent
AUTOMATION_DIR = ROOT_DIR / "automation"
JOBS_DIR = AUTOMATION_DIR / "jobs"
CODEX_HOME_CACHE_DIR = AUTOMATION_DIR / "codex-home-cache"
GENERATION_RULES_PATH = AUTOMATION_DIR / "generation_rules.md"
TEMPLATE_PATH = AUTOMATION_DIR / "templates" / "project_onboarding_template.md"
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
ATHENA_NGINX_CONFIG_PATH = Path(os.environ.get("ATHENA_NGINX_CONFIG_PATH", "/etc/nginx/sites-available/athena.conf"))
ATHENA_NGINX_SERVER_NAME = os.environ.get("ATHENA_NGINX_SERVER_NAME", DEFAULT_EXTERNAL_ACCESS_HOST)
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


def write_json(path: Path, payload: Dict[str, object]) -> None:
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def sorted_unique(items: Iterable[str]) -> List[str]:
    return sorted({item for item in items if item})


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
    return json.dumps(args, ensure_ascii=False)


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


def load_project_ports_map() -> Dict[str, str]:
    payload = read_json(PROJECT_PORTS_PATH)
    if not isinstance(payload, dict):
        return {}
    result: Dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, str) and re.fullmatch(r"\d{2,5}:\d{2,5}", value):
            result[key] = value
            continue
        if isinstance(value, dict):
            port_value = value.get("port")
            if isinstance(port_value, str) and re.fullmatch(r"\d{2,5}:\d{2,5}", port_value):
                result[key] = port_value
    return result


def save_project_ports_map(mapping: Dict[str, str]) -> None:
    payload = read_json(PROJECT_PORTS_PATH)
    existing = payload if isinstance(payload, dict) else {}
    updated: Dict[str, object] = dict(existing)
    for project_slug, port_mapping in mapping.items():
        current_value = updated.get(project_slug)
        if isinstance(current_value, dict):
            updated[project_slug] = {**current_value, "port": port_mapping}
        else:
            updated[project_slug] = port_mapping
    write_text(PROJECT_PORTS_PATH, json.dumps(updated, ensure_ascii=False, indent=2) + "\n")


def load_project_runtime_overrides(project_slug: str) -> Dict[str, object]:
    payload = read_json(PROJECT_PORTS_PATH)
    if not isinstance(payload, dict):
        return {}
    project_config = payload.get(project_slug)
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
    mapping = load_project_ports_map()
    if project_slug in mapping:
        host_text, container_text = mapping[project_slug].split(":", 1)
        return int(host_text), int(container_text)
    used_host_ports = {int(value.split(":", 1)[0]) for value in mapping.values()}
    candidate = DEFAULT_PROJECT_HOST_PORT_BASE
    while candidate in used_host_ports:
        candidate += 1
    mapping[project_slug] = f"{candidate}:{container_port}"
    save_project_ports_map(mapping)
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


def discover_workspace_packages(repo_dir: Path, root_package: Dict[str, object]) -> List[Dict[str, object]]:
    workspace_path = repo_dir / "pnpm-workspace.yaml"
    workspace_text = read_text_if_exists(workspace_path)
    if workspace_text is None:
        return []
    package_patterns = parse_pnpm_workspace_patterns(workspace_text)
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
        r'port\s*=\s*int\(os\.environ\.get\([^)]*["\'](\d{2,5})["\']\)\)',
        r'parser\.add_argument\(\s*["\']--port["\'][^)]*default\s*=\s*(\d{2,5})',
        r'process\.env\.PORT\s*\|\|\s*(\d{2,5})',
        r'listen\(\s*(\d{2,5})\s*[,)]',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def detect_python_entrypoint(repo_dir: Path) -> Tuple[Optional[Path], Optional[str], Optional[str]]:
    candidate_files = collect_matching_files(
        repo_dir,
        [
            "run_local.py",
            "main.py",
            "app.py",
            "server.py",
            "*/run_local.py",
            "*/main.py",
            "*/app.py",
            "*/server.py",
            "*/*/run_local.py",
            "*/*/main.py",
            "*/*/app.py",
            "*/*/server.py",
        ],
    )
    for file_path in candidate_files:
        text = read_text_if_exists(file_path)
        if text is None:
            continue
        if "uvicorn.run" in text or "FastAPI(" in text or "from fastapi import" in text:
            module_name = module_name_for_path(repo_dir, file_path)
            if module_name:
                return file_path, f"python -m {module_name}", text
    return None, None, None


def detect_node_entrypoint(repo_dir: Path, package_scripts: Dict[str, object]) -> Tuple[Optional[Path], Optional[str], Optional[str]]:
    start_script = package_scripts.get("start")
    if isinstance(start_script, str) and start_script.strip():
        return None, start_script.strip(), None
    for filename in ("server.js", "app.js", "main.js", "index.js"):
        candidate = repo_dir / filename
        text = read_text_if_exists(candidate)
        if text is None:
            continue
        return candidate, f"node {filename}", text
    return None, None, None


def extract_node_script_path_from_command(command: str) -> Optional[str]:
    match = re.search(r"\bnode\s+([^\s\"'`;&|]+\.js)\b", command)
    if not match:
        return None
    return match.group(1).strip()


def detect_node_entry_script_paths(repo_dir: Path, package_scripts: Dict[str, object]) -> List[Path]:
    candidates: List[Path] = []
    seen: Set[Path] = set()

    for script_name in ("start", "dev", "serve"):
        script_text = package_scripts.get(script_name)
        if not isinstance(script_text, str) or not script_text.strip():
            continue
        script_path_text = extract_node_script_path_from_command(script_text)
        if not script_path_text:
            continue
        candidate = (repo_dir / script_path_text).resolve()
        if candidate.is_file() and candidate not in seen:
            try:
                candidate.relative_to(repo_dir.resolve())
            except ValueError:
                continue
            candidates.append(candidate)
            seen.add(candidate)

    for filename in ("server.js", "app.js", "main.js", "index.js"):
        candidate = (repo_dir / filename).resolve()
        if candidate.is_file() and candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)

    return candidates


def resolve_entrypoint_relative_dir(entry_file: Path, args_text: str) -> Optional[Path]:
    quoted_parts = re.findall(r'["\']([^"\']+)["\']', args_text)
    if not quoted_parts:
        return None
    candidate = entry_file.parent
    for part in quoted_parts:
        candidate = candidate / part
    resolved = candidate.resolve()
    return resolved if resolved.exists() and resolved.is_dir() else None


def detect_static_root_hints_from_node_entry(repo_dir: Path, entry_file: Path) -> List[Path]:
    text = read_text_if_exists(entry_file)
    if not text:
        return []

    repo_root = repo_dir.resolve()
    variable_dirs: Dict[str, Path] = {}
    hints: List[Path] = []
    seen: Set[Path] = set()

    assignment_pattern = re.compile(
        r'(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*path\.(?:join|resolve)\(\s*__dirname\s*,\s*(.*?)\)\s*;'
    )
    for match in assignment_pattern.finditer(text):
        resolved = resolve_entrypoint_relative_dir(entry_file, match.group(2))
        if resolved is None:
            continue
        try:
            resolved.relative_to(repo_root)
        except ValueError:
            continue
        variable_dirs[match.group(1)] = resolved

    inline_pattern = re.compile(
        r'(?:express\.static|serveStatic|koaStatic)\(\s*path\.(?:join|resolve)\(\s*__dirname\s*,\s*(.*?)\)\s*\)'
    )
    for match in inline_pattern.finditer(text):
        resolved = resolve_entrypoint_relative_dir(entry_file, match.group(1))
        if resolved is None or resolved in seen:
            continue
        try:
            resolved.relative_to(repo_root)
        except ValueError:
            continue
        hints.append(resolved)
        seen.add(resolved)

    variable_pattern = re.compile(r'(?:express\.static|serveStatic|koaStatic)\(\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\)')
    for match in variable_pattern.finditer(text):
        resolved = variable_dirs.get(match.group(1))
        if resolved is None or resolved in seen:
            continue
        hints.append(resolved)
        seen.add(resolved)

    return hints


def detect_frontend_runtime_roots(repo_dir: Path) -> List[Path]:
    roots: List[Path] = []
    seen: Set[Path] = set()
    package = parse_package_json(repo_dir)
    package_scripts = package.get("scripts", {}) if isinstance(package.get("scripts"), dict) else {}

    for entry_file in detect_node_entry_script_paths(repo_dir, package_scripts):
        for runtime_root in detect_static_root_hints_from_node_entry(repo_dir, entry_file):
            if runtime_root not in seen:
                roots.append(runtime_root)
                seen.add(runtime_root)

    for anchor in ("src", "static", "public", "client", "web", "audioqas/web/static"):
        candidate = (repo_dir / anchor).resolve()
        if candidate.is_dir() and candidate not in seen:
            roots.append(candidate)
            seen.add(candidate)

    return roots


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

    python_entry_path, python_entry_command, python_entry_text = detect_python_entrypoint(repo_dir)
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
        package_manager_field = package.get("packageManager")
        if isinstance(package_manager_field, str) and package_manager_field.startswith("pnpm@"):
            package_manager = "pnpm"
        elif (repo_dir / "pnpm-lock.yaml").exists():
            package_manager = "pnpm"
        elif package_lock.exists():
            package_manager = "npm"
        elif (repo_dir / "yarn.lock").exists():
            package_manager = "yarn"
    elif service_runtime == "python":
        if pyproject_toml.exists():
            package_manager = "pip"
        elif requirements_txt.exists():
            package_manager = "pip"

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
    }


def summarize_analysis(analysis: Dict[str, object], source_type: str, project_slug: str) -> Dict[str, object]:
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
        "selected_service_package": analysis.get("selected_service_package"),
    }


def build_deployment_base_path(project_slug: str) -> str:
    return f"/tools2/{slugify(project_slug)}"


def render_athena_managed_tool_block(project_slug: str, host_port: int, proxy_mode: str = "strip_prefix", indent: str = "    ") -> str:
    managed_block = "\n".join(
        [
            f"# BEGIN {ATHENA_NGINX_TOOL_BLOCK_PREFIX} {project_slug}",
            render_nginx_add_conf(project_slug, host_port, proxy_mode=proxy_mode).strip(),
            f"# END {ATHENA_NGINX_TOOL_BLOCK_PREFIX} {project_slug}",
        ]
    )
    return textwrap.indent(managed_block, indent, lambda _line: True)


def find_matching_brace(text: str, opening_brace_index: int) -> Optional[int]:
    depth = 0
    for index in range(opening_brace_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def find_named_nginx_server_block(config_text: str, server_name: str) -> Optional[Tuple[int, int, str]]:
    for match in re.finditer(r"(^|\n)\s*server\s*\{", config_text):
        opening_brace_index = config_text.find("{", match.start())
        if opening_brace_index < 0:
            continue
        closing_brace_index = find_matching_brace(config_text, opening_brace_index)
        if closing_brace_index is None:
            continue
        block_start = match.start()
        block_end = closing_brace_index + 1
        block_text = config_text[block_start:block_end]
        if (
            re.search(r"(?m)^\s*listen\s+443\s+ssl\s+http2;\s*$", block_text)
            and re.search(rf"(?m)^\s*server_name\s+{re.escape(server_name)};\s*$", block_text)
        ):
            return block_start, block_end, block_text
    return None


def upsert_tool_nginx_into_athena_conf(config_text: str, project_slug: str, host_port: int, proxy_mode: str = "strip_prefix") -> Tuple[str, str]:
    server_block = find_named_nginx_server_block(config_text, ATHENA_NGINX_SERVER_NAME)
    if server_block is None:
        raise ValueError(f"Could not find HTTPS server block for {ATHENA_NGINX_SERVER_NAME} in {ATHENA_NGINX_CONFIG_PATH}")

    block_start, block_end, block_text = server_block
    anchor_match = re.search(r"(?m)^(?P<indent>\s*)client_max_body_size 100m;\s*(?:\n|$)", block_text)
    if anchor_match is None:
        raise ValueError("Could not find client_max_body_size 100m; anchor in Athena HTTPS server block")

    indent = anchor_match.group("indent")
    managed_block = render_athena_managed_tool_block(project_slug, host_port, proxy_mode, indent)
    managed_pattern = re.compile(
        rf"(?ms)^[ \t]*# BEGIN {re.escape(ATHENA_NGINX_TOOL_BLOCK_PREFIX)} {re.escape(project_slug)}\n.*?^[ \t]*# END {re.escape(ATHENA_NGINX_TOOL_BLOCK_PREFIX)} {re.escape(project_slug)}\s*\n?"
    )
    managed_match = managed_pattern.search(block_text)
    if managed_match is not None:
        existing_managed_block = managed_match.group(0).strip()
        desired_managed_block = managed_block.strip()
        if existing_managed_block == desired_managed_block:
            return config_text, "already_managed"
        updated_block_text = block_text[:managed_match.start()] + managed_block + block_text[managed_match.end():]
        return config_text[:block_start] + updated_block_text + config_text[block_end:], "updated_managed"

    base_path = build_deployment_base_path(project_slug)
    if (
        f"location = {base_path} " in block_text
        or f"location ^~ {base_path}/ " in block_text
        or f"proxy_set_header X-Forwarded-Prefix {base_path};" in block_text
    ):
        return config_text, "already_present_unmanaged"

    insert_at = anchor_match.end()
    updated_block_text = block_text[:insert_at] + "\n" + managed_block + "\n" + block_text[insert_at:]
    return config_text[:block_start] + updated_block_text + config_text[block_end:], "inserted_managed"


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
        updated_text, merge_status = upsert_tool_nginx_into_athena_conf(existing_text, project_slug, host_port, proxy_mode)
    except Exception as exc:  # noqa: BLE001
        result["status"] = "merge_failed"
        result["error"] = str(exc)
        return result

    result["merge_status"] = merge_status
    if updated_text == existing_text:
        result["status"] = merge_status
        return result

    backup_path = output_dir / "athena.conf.before"
    temp_config_path = output_dir / "athena.conf.updated"
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
SUBPATH_CLIENT_DIR_MARKERS = ("components", "client", "web")
SUBPATH_BROWSER_ATTRS = ("href", "src", "action")
SUBPATH_CLIENT_METHOD_PATTERNS = (
    re.compile(r'\bfetch\(\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1'),
    re.compile(r'\bnew\s+Request\(\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1'),
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


NEXTJS_RUNTIME_SHIM_BASENAME = "ka_tool_base_runtime"
NEXTJS_WINDOW_TYPES_BASENAME = "ka_tool_window"


@dataclass
class SubpathAuditFinding:
    file: str
    line: int
    severity: str
    code: str
    message: str


@dataclass
class HtmlUrlReference:
    tag: str
    attr: str
    url: str
    line: int


def find_frontend_rewrite_targets(repo_dir: Path) -> List[Path]:
    patterns = [
        "*.html",
        "src/**/*.html",
        "src/**/*.js",
        "src/**/*.mjs",
        "static/**/*.html",
        "static/**/*.js",
        "static/**/*.mjs",
        "public/**/*.html",
        "public/**/*.js",
        "public/**/*.mjs",
        "client/**/*.html",
        "client/**/*.js",
        "client/**/*.mjs",
        "web/**/*.html",
        "web/**/*.js",
        "web/**/*.mjs",
        "audioqas/web/static/**/*.html",
        "audioqas/web/static/**/*.js",
    ]
    targets: List[Path] = []
    for file_path in collect_matching_files(repo_dir, patterns):
        relative = file_path.relative_to(repo_dir).as_posix()
        basename = file_path.name
        if relative.startswith(("server/", "backend/", "api/")):
            continue
        if basename in {"server.js", "server.ts", "app.js", "app.ts", "api.py", "main.py"}:
            continue
        targets.append(file_path)
    targets.extend(discover_runtime_root_frontend_files(repo_dir, include_code_files=True))
    return sorted(set(targets))


def discover_runtime_root_frontend_files(repo_dir: Path, *, include_code_files: bool) -> List[Path]:
    patterns = ["**/*.html"]
    if include_code_files:
        patterns.extend(["**/*.js", "**/*.mjs"])
    targets: List[Path] = []
    seen: Set[Path] = set()
    for runtime_root in detect_frontend_runtime_roots(repo_dir):
        try:
            relative_root = runtime_root.relative_to(repo_dir.resolve())
        except ValueError:
            continue
        if relative_root == Path("."):
            continue
        for suffix_pattern in patterns:
            for file_path in collect_matching_files(repo_dir, [f"{relative_root.as_posix()}/{suffix_pattern}"]):
                relative = file_path.relative_to(repo_dir).as_posix()
                basename = file_path.name
                if any(part in relative.split("/") for part in ("node_modules", ".next", "dist", "build")):
                    continue
                if relative.startswith(("server/", "backend/", "api/")):
                    continue
                if basename in {"server.js", "server.ts", "app.js", "app.ts", "api.py", "main.py"}:
                    continue
                if file_path not in seen:
                    targets.append(file_path)
                    seen.add(file_path)
    return targets


def find_subpath_audit_targets(repo_dir: Path, strategy: Dict[str, str]) -> List[Path]:
    patterns = [
        "src/**/*.ts",
        "src/**/*.tsx",
        "src/**/*.js",
        "src/**/*.jsx",
        "app/**/*.ts",
        "app/**/*.tsx",
        "app/**/*.js",
        "app/**/*.jsx",
        "pages/**/*.ts",
        "pages/**/*.tsx",
        "pages/**/*.js",
        "pages/**/*.jsx",
        "components/**/*.ts",
        "components/**/*.tsx",
        "components/**/*.js",
        "components/**/*.jsx",
        "public/**/*.html",
        "static/**/*.html",
        "client/**/*.html",
        "web/**/*.html",
        "*.html",
    ]
    targets: List[Path] = []
    framework = strategy.get("framework", "")
    for file_path in collect_matching_files(repo_dir, patterns):
        relative = file_path.relative_to(repo_dir).as_posix()
        if any(part in relative.split("/") for part in ("node_modules", ".next", "dist", "build")):
            continue
        if relative.endswith((".test.ts", ".test.tsx", ".test.js", ".test.jsx", ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx")):
            continue
        if relative.startswith(("server/", "backend/", "api/")):
            continue
        if "/pages/api/" in f"/{relative}" or relative.endswith(("/route.ts", "/route.js", "/route.tsx", "/route.jsx")):
            continue
        if framework == "nextjs" and relative.endswith((".ts", ".tsx", ".js", ".jsx", ".html")):
            targets.append(file_path)
            continue
        if file_path.suffix == ".html":
            targets.append(file_path)
    targets.extend(discover_runtime_root_frontend_files(repo_dir, include_code_files=framework == "nextjs"))
    return sorted(set(targets))


class HtmlUrlExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: List[HtmlUrlReference] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        line, _offset = self.getpos()
        for attr, value in attrs:
            if attr in SUBPATH_BROWSER_ATTRS and isinstance(value, str):
                self.references.append(HtmlUrlReference(tag=tag, attr=attr, url=value, line=line))


def is_allowed_root_relative_url(url: str, base_path: str) -> bool:
    stripped = url.strip()
    if not stripped or not stripped.startswith("/"):
        return True
    if stripped.startswith("//"):
        return True
    if stripped.startswith("#"):
        return True
    lowered = stripped.lower()
    if lowered.startswith(("mailto:", "tel:", "data:", "javascript:")):
        return True
    if stripped == base_path or stripped.startswith(f"{base_path}/"):
        return True
    return False


def is_browser_facing_source(file_path: Path, repo_dir: Path, text: str) -> bool:
    relative = file_path.relative_to(repo_dir).as_posix()
    if file_path.suffix == ".html":
        return True
    if re.search(r"^\s*['\"]use client['\"]\s*;?\s*$", text, flags=re.MULTILINE):
        return True
    parts = set(relative.split("/"))
    return any(marker in parts for marker in SUBPATH_CLIENT_DIR_MARKERS)


def finding_to_error_text(finding: SubpathAuditFinding) -> str:
    return f"{finding.file}:{finding.line} {finding.message}"


def scan_subpath_findings(file_path: Path, framework: str, base_path: str, repo_dir: Path) -> List[SubpathAuditFinding]:
    text = read_text(file_path)
    if SUBPATH_ALLOWED_ROOT_COMMENT in text:
        return []
    relative = file_path.relative_to(repo_dir).as_posix()
    findings: List[SubpathAuditFinding] = []
    browser_facing = is_browser_facing_source(file_path, repo_dir, text)

    if file_path.suffix == ".html":
        parser = HtmlUrlExtractor()
        parser.feed(text)
        for ref in parser.references:
            if is_allowed_root_relative_url(ref.url, base_path):
                continue
            findings.append(
                SubpathAuditFinding(
                    file=relative,
                    line=ref.line,
                    severity="error",
                    code="root_relative_html_url",
                    message=f'Root-relative HTML attribute `{ref.attr}="{ref.url}"` is incompatible with deployment subpath `{base_path}`',
                )
            )
        return findings

    if framework == "nextjs":
        for match in re.finditer(r'<a\b[^>]*\bhref\s*=\s*(["\'])(?P<url>/[^"\']*)\1', text):
            url = match.group("url")
            if is_allowed_root_relative_url(url, base_path):
                continue
            line = text.count("\n", 0, match.start()) + 1
            findings.append(
                SubpathAuditFinding(
                    file=relative,
                    line=line,
                    severity="error",
                    code="nextjs_raw_anchor_root_href",
                    message=f'Use Next navigation instead of raw `<a href="{url}">` for subpath deployment `{base_path}`',
                )
            )
        for match in re.finditer(r'<form\b[^>]*\baction\s*=\s*(["\'])(?P<url>/[^"\']*)\1', text):
            url = match.group("url")
            if is_allowed_root_relative_url(url, base_path):
                continue
            line = text.count("\n", 0, match.start()) + 1
            findings.append(
                SubpathAuditFinding(
                    file=relative,
                    line=line,
                    severity="error",
                    code="nextjs_form_root_action",
                    message=f'Root-relative form action `{url}` is incompatible with deployment subpath `{base_path}`',
                )
            )
        for field_name in SUBPATH_TEMPLATE_ALLOWLIST_FIELDS:
            text = re.sub(rf'\b{field_name}\s*:\s*(["\'`])/(.*?)\1', "", text, flags=re.DOTALL)

    if browser_facing:
        for pattern in SUBPATH_CLIENT_METHOD_PATTERNS:
            for match in pattern.finditer(text):
                url = f"/{match.group('path')}"
                if is_allowed_root_relative_url(url, base_path):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                findings.append(
                    SubpathAuditFinding(
                        file=relative,
                        line=line,
                        severity="error",
                        code="root_relative_client_url",
                        message=f'Root-relative browser URL `{url}` is incompatible with deployment subpath `{base_path}`',
                    )
                )
    return findings


def run_static_subpath_audit(repo_dir: Path, project_slug: str, strategy: Dict[str, str]) -> Dict[str, object]:
    base_path = build_deployment_base_path(project_slug)
    targets = find_subpath_audit_targets(repo_dir, strategy)
    findings: List[SubpathAuditFinding] = []
    for file_path in targets:
        findings.extend(scan_subpath_findings(file_path, strategy.get("framework", ""), base_path, repo_dir))
    return {
        "framework": strategy.get("framework"),
        "proxy_mode": strategy.get("proxy_mode"),
        "scanned_files": [path.relative_to(repo_dir).as_posix() for path in targets],
        "findings": [
            {
                "file": item.file,
                "line": item.line,
                "severity": item.severity,
                "code": item.code,
                "message": item.message,
            }
            for item in findings
        ],
        "warnings": [],
    }


def auto_fix_subpath_issues(repo_dir: Path, project_slug: str, strategy: Dict[str, str]) -> List[str]:
    framework = strategy.get("framework")
    if framework == "nextjs":
        return auto_fix_nextjs_subpath_issues(repo_dir, project_slug)
    return []


def is_nextjs_project(repo_dir: Path) -> bool:
    package = parse_package_json(repo_dir)
    dependencies = package.get("dependencies") if isinstance(package.get("dependencies"), dict) else {}
    dev_dependencies = package.get("devDependencies") if isinstance(package.get("devDependencies"), dict) else {}
    if "next" in dependencies or "next" in dev_dependencies:
        return True
    return any((repo_dir / candidate).exists() for candidate in ("next.config.js", "next.config.mjs", "next.config.ts"))


def is_vite_project(repo_dir: Path) -> bool:
    package = parse_package_json(repo_dir)
    dependencies = package.get("dependencies") if isinstance(package.get("dependencies"), dict) else {}
    dev_dependencies = package.get("devDependencies") if isinstance(package.get("devDependencies"), dict) else {}
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    if "vite" in dependencies or "vite" in dev_dependencies:
        return True
    if any((repo_dir / candidate).exists() for candidate in ("vite.config.ts", "vite.config.js", "vite.config.mjs")):
        return True
    return any(isinstance(value, str) and "vite" in value for value in scripts.values())


def is_create_react_app_project(repo_dir: Path) -> bool:
    package = parse_package_json(repo_dir)
    dependencies = package.get("dependencies") if isinstance(package.get("dependencies"), dict) else {}
    dev_dependencies = package.get("devDependencies") if isinstance(package.get("devDependencies"), dict) else {}
    return "react-scripts" in dependencies or "react-scripts" in dev_dependencies


def is_vue_cli_project(repo_dir: Path) -> bool:
    package = parse_package_json(repo_dir)
    dependencies = package.get("dependencies") if isinstance(package.get("dependencies"), dict) else {}
    dev_dependencies = package.get("devDependencies") if isinstance(package.get("devDependencies"), dict) else {}
    if "@vue/cli-service" in dependencies or "@vue/cli-service" in dev_dependencies:
        return True
    return (repo_dir / "vue.config.js").exists()


def is_express_static_project(repo_dir: Path) -> bool:
    package = parse_package_json(repo_dir)
    package_scripts = package.get("scripts", {}) if isinstance(package.get("scripts"), dict) else {}
    return bool(detect_node_entry_script_paths(repo_dir, package_scripts) and detect_frontend_runtime_roots(repo_dir))


def is_static_html_project(repo_dir: Path) -> bool:
    return bool(collect_matching_files(repo_dir, ["*.html", "src/**/*.html", "public/**/*.html", "static/**/*.html", "web/**/*.html"]))


def detect_subpath_strategy(repo_dir: Path) -> Dict[str, str]:
    if is_nextjs_project(repo_dir):
        return {"framework": "nextjs", "proxy_mode": "preserve_prefix", "adapter": "nextjs"}
    if is_vite_project(repo_dir):
        return {"framework": "vite", "proxy_mode": "preserve_prefix", "adapter": "vite"}
    if is_vue_cli_project(repo_dir):
        return {"framework": "vue_cli", "proxy_mode": "preserve_prefix", "adapter": "vue_cli"}
    if is_create_react_app_project(repo_dir):
        return {"framework": "cra", "proxy_mode": "preserve_prefix", "adapter": "cra"}
    if is_express_static_project(repo_dir):
        return {"framework": "express_static", "proxy_mode": "strip_prefix", "adapter": "static_rewrite"}
    if is_static_html_project(repo_dir):
        return {"framework": "static_html", "proxy_mode": "strip_prefix", "adapter": "static_rewrite"}
    return {"framework": "generic", "proxy_mode": "strip_prefix", "adapter": "static_rewrite"}


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
    for candidate in ("vite.config.ts", "vite.config.js", "vite.config.mjs"):
        path = repo_dir / candidate
        if path.is_file():
            return path
    return None


def ensure_vite_base_config(repo_dir: Path, project_slug: str) -> List[str]:
    config_path = vite_config_file(repo_dir)
    if config_path is None:
        return []
    base_path = f"{build_deployment_base_path(project_slug)}/"
    original = read_text(config_path)
    rewritten = original
    if re.search(r"defineConfig\(\s*\{\s*\}", rewritten):
        rewritten = re.sub(
            r"defineConfig\(\s*\{\s*\}",
            f'defineConfig({{\n  base: "{base_path}",\n}}',
            rewritten,
            count=1,
        )
    if "base:" not in rewritten:
        rewritten = re.sub(
            r"(defineConfig\(\s*\{\n)",
            rf'\1  base: "{base_path}",' + "\n",
            rewritten,
            count=1,
        )
        rewritten = re.sub(
            r"(export\s+default\s+\{\n)",
            rf'\1  base: "{base_path}",' + "\n",
            rewritten,
            count=1,
        )
    if rewritten != original:
        write_text(config_path, rewritten)
        return [config_path.relative_to(repo_dir).as_posix()]
    return []


def ensure_vue_cli_public_path(repo_dir: Path, project_slug: str) -> List[str]:
    config_path = repo_dir / "vue.config.js"
    base_path = f"{build_deployment_base_path(project_slug)}/"
    if not config_path.exists():
        write_text(config_path, f"module.exports = {{\n  publicPath: '{base_path}',\n}}\n")
        return [config_path.relative_to(repo_dir).as_posix()]
    original = read_text(config_path)
    rewritten = original
    if re.search(r"module\.exports\s*=\s*\{\s*\}", rewritten):
        rewritten = re.sub(
            r"module\.exports\s*=\s*\{\s*\}",
            f"module.exports = {{\n  publicPath: '{base_path}',\n}}",
            rewritten,
            count=1,
        )
    if "publicPath:" not in rewritten:
        rewritten = re.sub(
            r"(module\.exports\s*=\s*\{\n)",
            rf"\1  publicPath: '{base_path}'," + "\n",
            rewritten,
            count=1,
        )
    if rewritten != original:
        write_text(config_path, rewritten)
        return [config_path.relative_to(repo_dir).as_posix()]
    return []


def ensure_cra_homepage(repo_dir: Path, project_slug: str) -> List[str]:
    package_path = repo_dir / "package.json"
    if not package_path.exists():
        return []
    package = parse_package_json(repo_dir)
    desired_homepage = build_deployment_base_path(project_slug)
    if package.get("homepage") == desired_homepage:
        return []
    package["homepage"] = desired_homepage
    write_text(package_path, json.dumps(package, ensure_ascii=False, indent=2) + "\n")
    return [package_path.relative_to(repo_dir).as_posix()]


def nextjs_runtime_import_path(entry_path: Path, shim_path: Path) -> str:
    relative = shim_path.relative_to(entry_path.parent).as_posix() if shim_path.parent == entry_path.parent else os.path.relpath(shim_path, entry_path.parent).replace("\\", "/")
    if relative.endswith(".ts"):
        relative = relative[:-3]
    if not relative.startswith("."):
        relative = f"./{relative}"
    return relative


def ensure_nextjs_runtime_import(entry_path: Path, shim_path: Path) -> bool:
    original = read_text(entry_path)
    import_path = nextjs_runtime_import_path(entry_path, shim_path)
    import_line = f'import "{import_path}"'
    if import_line in original or f"import '{import_path}'" in original:
        return False
    rewritten = f'{import_line}\n{original}'
    write_text(entry_path, rewritten)
    return True


def remove_nextjs_runtime_import(entry_path: Path) -> bool:
    original = read_text(entry_path)
    rewritten = re.sub(
        rf'^\s*import\s+["\'].*{re.escape(NEXTJS_RUNTIME_SHIM_BASENAME)}["\']\s*;?\s*\n?',
        "",
        original,
        flags=re.MULTILINE,
    )
    if rewritten != original:
        write_text(entry_path, rewritten)
        return True
    return False


def ensure_nextjs_basepath_config(repo_dir: Path, project_slug: str) -> List[str]:
    config_path = nextjs_config_file(repo_dir)
    if config_path is None:
        return []
    base_path = build_deployment_base_path(project_slug)
    original = read_text(config_path)
    rewritten = original
    if re.search(r"const\s+nextConfig(?:\s*:\s*NextConfig)?\s*=\s*\{\s*\}", rewritten):
        rewritten = re.sub(
            r"const\s+nextConfig(\s*:\s*NextConfig)?\s*=\s*\{\s*\}",
            f'const nextConfig\\1 = {{\n  basePath: "{base_path}",\n  assetPrefix: "{base_path}",\n}}',
            rewritten,
            count=1,
        )
    if "basePath:" not in rewritten:
        rewritten = re.sub(
            r"(const\s+nextConfig\s*:\s*NextConfig\s*=\s*\{\n)",
            rf'\1  basePath: "{base_path}",' + "\n",
            rewritten,
            count=1,
        )
        rewritten = re.sub(
            r"(const\s+nextConfig\s*=\s*\{\n)",
            rf'\1  basePath: "{base_path}",' + "\n",
            rewritten,
            count=1,
        )
    if "assetPrefix:" not in rewritten:
        rewritten = re.sub(
            r"(const\s+nextConfig\s*:\s*NextConfig\s*=\s*\{\n)",
            rf'\1  assetPrefix: "{base_path}",' + "\n",
            rewritten,
            count=1,
        )
        rewritten = re.sub(
            r"(const\s+nextConfig\s*=\s*\{\n)",
            rf'\1  assetPrefix: "{base_path}",' + "\n",
            rewritten,
            count=1,
        )
    if rewritten != original:
        write_text(config_path, rewritten)
        return [config_path.relative_to(repo_dir).as_posix()]
    return []


def rewrite_nextjs_source_calls(file_path: Path, repo_dir: Path) -> bool:
    return False


def ensure_next_link_import(text: str) -> str:
    if re.search(r"""^\s*import\s+Link\s+from\s+['"]next/link['"]\s*$""", text, flags=re.MULTILINE):
        return text
    return insert_import_after_directives(text, 'import Link from "next/link"')


def ensure_with_tool_base_import(text: str, entry_path: Path, helper_path: Path) -> str:
    import_path = nextjs_runtime_import_path(entry_path, helper_path)
    import_line = f'import {{ withToolBase }} from "{import_path}"'
    if import_line in text or f"import {{ withToolBase }} from '{import_path}'" in text:
        return text
    return insert_import_after_directives(text, import_line)


def insert_import_after_directives(text: str, import_line: str) -> str:
    lines = text.splitlines(keepends=True)
    insert_at = 0
    directive_pattern = re.compile(r'^\s*["\']use (client|server)["\']\s*;?\s*$')
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if directive_pattern.match(stripped):
            insert_at = index + 1
            continue
        break
    lines.insert(insert_at, f"{import_line}\n")
    return "".join(lines)


def rewrite_nextjs_jsx_anchor_hrefs(text: str) -> str:
    rewritten = text
    anchor_pattern = re.compile(
        r'<a(?P<attrs>\s+[^>]*?\bhref\s*=\s*(?P<quote>["\'])(?P<href>/[^"\']*)(?P=quote)[^>]*)>(?P<body>.*?)</a>',
        re.DOTALL,
    )

    def replace_anchor(match: re.Match[str]) -> str:
        attrs = match.group("attrs")
        href = match.group("href")
        body = match.group("body")
        rewritten_attrs = re.sub(
            r'\bhref\s*=\s*(["\'])/[^"\']*(\1)',
            f'href="{href}"',
            attrs,
            count=1,
        )
        return f"<Link{rewritten_attrs}>{body}</Link>"

    rewritten = anchor_pattern.sub(replace_anchor, rewritten)
    return rewritten


def rewrite_nextjs_fetch_calls(text: str) -> str:
    rewritten = text
    rewritten = re.sub(
        r'fetch\(\s*(["\'`])(/[^"\'`]*)(\1)\s*\)',
        r'fetch(withToolBase("\2"))',
        rewritten,
    )
    rewritten = re.sub(
        r'fetch\(\s*(["\'`])(/[^"\'`]*)(\1)\s*,',
        r'fetch(withToolBase("\2"),',
        rewritten,
    )
    rewritten = re.sub(
        r'axios\.(get|post|put|delete|patch)\(\s*(["\'`])(/[^"\'`]*)(\2)',
        r'axios.\1(withToolBase("\3")',
        rewritten,
    )
    rewritten = re.sub(
        r'url:\s*(["\'`])(/[^"\'`]*)(\1)',
        r'url: withToolBase("\2")',
        rewritten,
    )
    return rewritten


def ensure_nextjs_helper_module(entry_path: Path, repo_dir: Path, project_slug: str) -> List[str]:
    shim_path = repo_dir / f"{NEXTJS_RUNTIME_SHIM_BASENAME}.ts"
    base_path = build_deployment_base_path(project_slug)
    shim_text = (
        f'  const basePath = "{base_path}"\n'
        "\n"
        "export function withToolBase(path: string): string {\n"
        "    if (!path) return path\n"
        "    if (/^(https?:)?\\/\\//.test(path)) return path\n"
        "    if (path === basePath || path.startsWith(`${basePath}/`)) return path\n"
        "    if (path.startsWith('/')) return `${basePath}${path}`\n"
        "    return `${basePath}/${path.replace(/^\\/+/, '')}`\n"
        "}\n"
    )
    changed: List[str] = []
    original_shim = read_text_if_exists(shim_path)
    if original_shim != shim_text:
        write_text(shim_path, shim_text)
        changed.append(shim_path.relative_to(repo_dir).as_posix())
    return changed


def auto_fix_nextjs_subpath_issues(repo_dir: Path, project_slug: str) -> List[str]:
    changed: List[str] = []
    entry_path = nextjs_entry_file(repo_dir)
    if entry_path is not None:
        helper_changes = ensure_nextjs_helper_module(entry_path, repo_dir, project_slug)
        changed.extend(helper_changes)
    source_patterns = [
        "src/**/*.ts",
        "src/**/*.tsx",
        "src/**/*.jsx",
        "src/**/*.js",
        "pages/**/*.ts",
        "pages/**/*.tsx",
        "pages/**/*.jsx",
        "pages/**/*.js",
        "app/**/*.ts",
        "app/**/*.tsx",
        "app/**/*.jsx",
        "app/**/*.js",
        "components/**/*.ts",
        "components/**/*.tsx",
        "components/**/*.jsx",
        "components/**/*.js",
    ]
    helper_path = repo_dir / f"{NEXTJS_RUNTIME_SHIM_BASENAME}.ts"
    for file_path in collect_matching_files(repo_dir, source_patterns):
        original = read_text(file_path)
        rewritten = original
        if re.search(r'<a\b[^>]*\bhref\s*=\s*(["\'])/[^"\']*\1', rewritten):
            rewritten = ensure_next_link_import(rewritten)
            rewritten = rewrite_nextjs_jsx_anchor_hrefs(rewritten)
        request_rewritten = rewrite_nextjs_fetch_calls(rewritten)
        if request_rewritten != rewritten:
            rewritten = ensure_with_tool_base_import(request_rewritten, file_path, helper_path)
        else:
            rewritten = request_rewritten
        if rewritten != original:
            write_text(file_path, rewritten)
            changed.append(file_path.relative_to(repo_dir).as_posix())
    return sorted_unique(changed)


def apply_nextjs_subpath_adapter(repo_dir: Path, project_slug: str) -> List[str]:
    changed: List[str] = []
    changed.extend(ensure_nextjs_basepath_config(repo_dir, project_slug))
    legacy_types = repo_dir / f"{NEXTJS_WINDOW_TYPES_BASENAME}.d.ts"
    if legacy_types.exists():
        legacy_types.unlink()
        changed.append(legacy_types.relative_to(repo_dir).as_posix())
    changed.extend(auto_fix_nextjs_subpath_issues(repo_dir, project_slug))
    return sorted_unique(changed)


def inject_tool_base_runtime(text: str, base_path: str) -> str:
    marker = f'window.__TOOL_BASE_PATH__ = "{base_path}";'
    if marker in text:
        return text
    script = (
        "<script>\n"
        f'window.__TOOL_BASE_PATH__ = "{base_path}";\n'
        'window.__TOOL_ORIGIN_URL__ = window.__TOOL_BASE_PATH__ ? (window.location.origin + window.__TOOL_BASE_PATH__) : window.location.origin;\n'
        "window.withToolBase = function(path) {\n"
        "  if (!path) return path;\n"
        "  var base = window.__TOOL_BASE_PATH__ || \"\";\n"
        "  if (!base) return path;\n"
        "  if (/^(https?:)?\\/\\//.test(path)) return path;\n"
        "  if (path.startsWith(base + \"/\") || path === base) return path;\n"
        "  if (path.startsWith(\"/\")) return base + path;\n"
        "  return base + \"/\" + path.replace(/^\\/+/, \"\");\n"
        "};\n"
        "</script>\n"
    )
    if "</head>" in text:
        return text.replace("<script>", f"{script}<script>", 1) if "<script>" in text else text.replace("</head>", f"{script}</head>", 1)
    return script + text


def frontend_runtime_root(repo_dir: Path, file_path: Path, runtime_roots: Optional[List[Path]] = None) -> Path:
    if runtime_roots:
        file_resolved = file_path.resolve()
        matching_roots = []
        for root in runtime_roots:
            try:
                file_resolved.relative_to(root)
            except ValueError:
                continue
            matching_roots.append(root)
        if matching_roots:
            return max(matching_roots, key=lambda path: len(path.parts))

    relative = file_path.relative_to(repo_dir)
    parts = relative.parts
    if not parts:
        return repo_dir
    for anchor in ("src", "static", "public", "client", "web"):
        if anchor in parts:
            return repo_dir / anchor
    return repo_dir


def split_url_suffix(url: str) -> Tuple[str, str]:
    match = re.match(r"^([^?#]*)(.*)$", url)
    if not match:
        return url, ""
    return match.group(1), match.group(2)


def looks_like_templated_value(value: str) -> bool:
    return any(token in value for token in ("{{", "}}", "<%", "%>", "${"))


def should_rewrite_relative_html_asset(raw_url: str) -> bool:
    asset_path, _suffix = split_url_suffix(raw_url.strip())
    if not asset_path:
        return False
    lowered = asset_path.lower()
    if asset_path.startswith(("/", "#")):
        return False
    if re.match(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:)?//", asset_path):
        return False
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", asset_path):
        return False
    if looks_like_templated_value(asset_path):
        return False
    suffix = Path(asset_path).suffix.lower()
    return suffix in HTML_RELATIVE_ASSET_EXTENSIONS


def resolve_repo_relative_asset_url(
    repo_dir: Path,
    file_path: Path,
    raw_url: str,
    runtime_roots: Optional[List[Path]] = None,
) -> Optional[str]:
    if not should_rewrite_relative_html_asset(raw_url):
        return None
    asset_path, suffix = split_url_suffix(raw_url.strip())
    content_root = frontend_runtime_root(repo_dir, file_path, runtime_roots)
    candidate = (file_path.parent / asset_path).resolve()
    try:
        candidate.relative_to(content_root.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    relative_to_root = candidate.relative_to(content_root.resolve()).as_posix()
    return f"/{relative_to_root}{suffix}"


def rewrite_html_relative_asset_urls(
    text: str,
    repo_dir: Path,
    file_path: Path,
    runtime_roots: Optional[List[Path]] = None,
) -> str:
    attr_pattern = re.compile(r'(?P<prefix>\b(?:src|href)=["\'])(?P<url>[^"\']+)(?P<suffix>["\'])')

    def replace_attr(match: re.Match[str]) -> str:
        original_url = match.group("url")
        rewritten_url = resolve_repo_relative_asset_url(repo_dir, file_path, original_url, runtime_roots)
        if rewritten_url is None:
            return match.group(0)
        return f'{match.group("prefix")}{rewritten_url}{match.group("suffix")}'

    return attr_pattern.sub(replace_attr, text)


def rewrite_origin_based_subpath_logic(text: str) -> str:
    rewritten = text
    rewritten = re.sub(
        r'((?:const|let|var)\s+[A-Za-z_$][A-Za-z0-9_$]*ORIGIN[A-Za-z0-9_$]*\s*=\s*)(?!window\.__TOOL_ORIGIN_URL__ \|\| )([^;]+);',
        r'\1(window.__TOOL_ORIGIN_URL__ || (\2));',
        rewritten,
    )
    rewritten = rewritten.replace(
        "return origin;",
        "return window.__TOOL_ORIGIN_URL__ || origin;",
    )
    rewritten = rewritten.replace(
        "${origin}",
        "${window.__TOOL_ORIGIN_URL__ || origin}",
    )
    rewritten = rewritten.replace(
        "${window.location.origin}",
        "${window.__TOOL_ORIGIN_URL__ || window.location.origin}",
    )
    rewritten = rewritten.replace(
        'window.location.href = SETUP_PAGE_URL;',
        'window.location.href = window.withToolBase ? window.withToolBase(SETUP_PAGE_URL) : SETUP_PAGE_URL;',
    )
    rewritten = rewritten.replace(
        "window.location.href = $(e.target).attr('data-href');",
        "window.location.href = window.withToolBase ? window.withToolBase($(e.target).attr('data-href')) : $(e.target).attr('data-href');",
    )
    rewritten = rewritten.replace(
        "window.location.href = href;",
        "window.location.href = window.withToolBase ? window.withToolBase(href) : href;",
    )
    rewritten = rewritten.replace(
        'window.location.href = target;',
        'window.location.href = window.withToolBase ? window.withToolBase(target) : target;',
    )
    rewritten = rewritten.replace(
        'window.location.href = url;',
        'window.location.href = window.withToolBase ? window.withToolBase(url) : url;',
    )
    return rewritten


def rewrite_frontend_subpath_urls(file_path: Path, base_path: str, repo_dir: Path) -> bool:
    original = read_text(file_path)
    rewritten = original
    runtime_roots = detect_frontend_runtime_roots(repo_dir)
    if file_path.suffix in {".html", ".js", ".mjs", ".ts", ".tsx", ".jsx"}:
        # Make rewrites idempotent before applying any new replacements.
        rewritten = re.sub(
            r'window\.withToolBase\(\s*window\.withToolBase\((.*?)\)\s*\)',
            r'window.withToolBase(\1)',
            rewritten,
        )
        rewritten = re.sub(
            r'(?<!\.)withToolBase\(\s*window\.withToolBase\((.*?)\)\s*\)',
            r'withToolBase(\1)',
            rewritten,
        )
        rewritten = re.sub(
            r'fetch\(\s*window\.withToolBase\(\s*window\.withToolBase\((.*?)\)\s*\)\s*\)',
            r'fetch(window.withToolBase(\1))',
            rewritten,
        )
        rewritten = re.sub(
            r'url:\s*window\.withToolBase\(\s*window\.withToolBase\((.*?)\)\s*\)',
            r'url: window.withToolBase(\1)',
            rewritten,
        )
        rewritten = re.sub(
            r'axios\.(get|post|put|delete|patch)\(\s*window\.withToolBase\(\s*window\.withToolBase\((.*?)\)\s*\)',
            r'axios.\1(window.withToolBase(\2)',
            rewritten,
        )
        # Direct request calls using absolute root paths.
        rewritten = re.sub(
            r'fetch\(\s*(["\'`]/[^"\'`]*["\'`])\s*\)',
            r'fetch(window.withToolBase(\1))',
            rewritten,
        )
        rewritten = re.sub(
            r'fetch\(\s*(["\'`]/[^"\'`]*["\'`])\s*,',
            r'fetch(window.withToolBase(\1),',
            rewritten,
        )
        rewritten = re.sub(
            r'axios\.(get|post|put|delete|patch)\(\s*(["\'`]/[^"\'`]*["\'`])',
            r'axios.\1(window.withToolBase(\2)',
            rewritten,
        )
        rewritten = re.sub(
            r'uploadWithProgress\(\s*(["\'`]/[^"\'`]*["\'`])',
            r'uploadWithProgress(window.withToolBase(\1)',
            rewritten,
        )
        # Request-target assignments that are later fed into fetch/axios.
        rewritten = re.sub(
            r'(\b(?:endpoint|url|path|apiPath|requestPath)\s*=\s*)(["\'`]/[^"\'`]*["\'`])',
            r'\1window.withToolBase(\2)',
            rewritten,
        )
        # Object field request urls.
        rewritten = re.sub(
            r'url:\s*(["\'`]/[^"\'`]*["\'`])',
            r'url: window.withToolBase(\1)',
            rewritten,
        )
        rewritten = re.sub(
            r'(?<!window\.withToolBase\()(["\'`])/static-preview\1',
            r'window.withToolBase("/static-preview")',
            rewritten,
        )
        rewritten = rewrite_origin_based_subpath_logic(rewritten)
        if file_path.suffix == ".html":
            rewritten = rewrite_html_relative_asset_urls(rewritten, repo_dir, file_path, runtime_roots)
            rewritten = re.sub(r'(src|href|action)=["\']/(?!/)', rf'\1="{base_path}/', rewritten)
            rewritten = inject_tool_base_runtime(rewritten, base_path)
    if rewritten != original:
        write_text(file_path, rewritten)
        return True
    return False


def subpath_proxy_mode(repo_dir: Path) -> str:
    return detect_subpath_strategy(repo_dir)["proxy_mode"]


def apply_subpath_rewrites(repo_dir: Path, project_slug: Optional[str]) -> List[str]:
    if not isinstance(project_slug, str) or not project_slug.strip():
        return []
    strategy = detect_subpath_strategy(repo_dir)
    adapter = strategy["adapter"]
    if adapter == "nextjs":
        return apply_nextjs_subpath_adapter(repo_dir, project_slug)
    if adapter == "vite":
        return ensure_vite_base_config(repo_dir, project_slug)
    if adapter == "vue_cli":
        return ensure_vue_cli_public_path(repo_dir, project_slug)
    if adapter == "cra":
        return ensure_cra_homepage(repo_dir, project_slug)
    base_path = build_deployment_base_path(project_slug)
    changed: List[str] = []
    for file_path in find_frontend_rewrite_targets(repo_dir):
        if rewrite_frontend_subpath_urls(file_path, base_path, repo_dir):
            changed.append(file_path.relative_to(repo_dir).as_posix())
    return changed


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


def determine_generation_mode(existing_dockerfile: bool, existing_onboarding: bool) -> Tuple[bool, str]:
    if existing_dockerfile and existing_onboarding:
        return True, "both"
    if existing_dockerfile and not existing_onboarding:
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
    return prompt.strip()


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
    runner_log_path: Optional[Path] = None,
) -> None:
    if not command_exists("codex"):
        raise RuntimeError("codex CLI not found in PATH")
    prompt = build_codex_prompt(source_type, source, ref, repo_dir, generation_mode)
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
    if not re.search(r"^##?\s*4[.\s]+运行参数\b", onboarding_text, re.MULTILINE):
        findings.append("PROJECT_ONBOARDING.md is missing section 4")
    if re.search(r"\b(TODO|UNKNOWN|NEEDS_CONFIRMATION)\b", onboarding_text):
        warnings.append("PROJECT_ONBOARDING.md still contains unresolved confirmation items")
    if re.search(r"\b(TODO|UNKNOWN|NEEDS_CONFIRMATION)\b", docker_text):
        warnings.append("Dockerfile contains TODO/UNKNOWN/NEEDS_CONFIRMATION markers")

    if analysis:
        package_scripts = analysis.get("package_scripts", [])
        node_entry_command = analysis.get("node_entry_command")
        python_entry_command = analysis.get("python_entry_command")
        system_dependencies = analysis.get("system_dependency_hints", [])
        has_build_step = dockerfile_has_build_step(docker_text)
        package_manager = analysis.get("package_manager")
        has_nextjs_ts_config = bool(analysis.get("has_nextjs_ts_config"))

        if "build" in package_scripts:
            if not has_build_step:
                dockerfile_findings.append("Dockerfile is missing an application build step even though package.json.scripts.build exists")
        if isinstance(node_entry_command, str) and "dist/" in node_entry_command and not has_build_step:
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
    ignored_tokens = {"UNKNOWN", "NEEDS_CONFIRMATION", "TBD", "EXPOSE"}
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
            if normalized and normalized not in {"UNKNOWN", "NEEDS_CONFIRMATION", "TBD - 由管理员构建"}:
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
    if isinstance(env_file_hint, str) and env_file_hint:
        env_args.extend(["--env-file", env_file_hint])
    return env_args


def normalize_runtime_mount_specs(
    persistence_paths: Optional[List[str]],
    runtime_data_dir: Optional[Path],
) -> List[Tuple[Path, str]]:
    if not persistence_paths or runtime_data_dir is None:
        return []
    ensure_dir(runtime_data_dir)
    mounts: List[Tuple[Path, str]] = []
    for path_value in persistence_paths:
        normalized = path_value.strip()
        if not normalized:
            continue
        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", normalized.strip("/")) or "data"
        host_mount = runtime_data_dir / safe_name
        ensure_dir(host_mount)
        mounts.append((host_mount, normalized.rstrip("/")))
    return mounts


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
    mapping = load_project_ports_map()
    runtime_overrides = load_project_runtime_overrides(project_slug)
    if project_slug in mapping:
        host_text, container_text = mapping[project_slug].split(":", 1)
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
    persistence_paths: Optional[List[str]] = None,
    extra_volume_args: Optional[List[str]] = None,
    runtime_data_dir: Optional[Path] = None,
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
    for host_mount, container_mount in normalize_runtime_mount_specs(persistence_paths, runtime_data_dir):
        args.extend(["-v", f"{host_mount}:{container_mount}"])
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


def normalize_runtime_url_path(url: str, current_path: str, *, base_origin: Optional[str] = None) -> Optional[str]:
    stripped = url.strip()
    if not stripped or stripped.startswith(("#", "mailto:", "tel:", "data:", "javascript:")):
        return None
    if stripped.startswith("//"):
        parsed = urlparse(f"http:{stripped}")
        if base_origin:
            base_host = urlparse(base_origin).netloc.lower()
            if parsed.netloc and parsed.netloc.lower() != base_host:
                return None
        return parsed.path or None
    if re.match(r"^https?://", stripped):
        parsed = urlparse(stripped)
        if base_origin:
            base_host = urlparse(base_origin).netloc.lower()
            if parsed.netloc and parsed.netloc.lower() != base_host:
                return None
        return parsed.path or None
    if stripped.startswith("/"):
        return stripped
    base_url = f"{base_origin or 'http://local'}{current_path}"
    parsed = urlparse(urljoin(base_url, stripped))
    return parsed.path or "/"


def translate_external_to_upstream_path(external_path: str, base_path: str, proxy_mode: str) -> str:
    normalized = external_path if external_path.startswith("/") else f"/{external_path}"
    if proxy_mode != "strip_prefix":
        return normalized
    if normalized == base_path:
        return "/"
    if normalized.startswith(f"{base_path}/"):
        remainder = normalized[len(base_path):]
        return remainder if remainder.startswith("/") else f"/{remainder}"
    return normalized


def translate_upstream_to_external_path(upstream_path: str, base_path: str, proxy_mode: str) -> str:
    normalized = upstream_path if upstream_path.startswith("/") else f"/{upstream_path}"
    if proxy_mode != "strip_prefix":
        return normalized
    if normalized == "/":
        return f"{base_path}/"
    if normalized.startswith("/"):
        return f"{base_path}{normalized}"
    return f"{base_path}/{normalized}"


def extract_html_urls(html: str) -> List[HtmlUrlReference]:
    parser = HtmlUrlExtractor()
    parser.feed(html)
    return parser.references


def runtime_subpath_findings_for_html(html: str, current_path: str, base_path: str, *, base_origin: Optional[str] = None) -> List[Dict[str, object]]:
    findings: List[Dict[str, object]] = []
    for ref in extract_html_urls(html):
        normalized = normalize_runtime_url_path(ref.url, current_path, base_origin=base_origin)
        if normalized is None or is_allowed_root_relative_url(normalized, base_path):
            continue
        findings.append(
            {
                "path": current_path,
                "line": ref.line,
                "tag": ref.tag,
                "attr": ref.attr,
                "url": ref.url,
                "message": f'Runtime HTML emits root-relative `{ref.attr}="{ref.url}"` outside deployment subpath `{base_path}`',
            }
        )
    return findings


def collect_runtime_follow_links(html: str, current_path: str, base_path: str, limit: int = 5) -> List[str]:
    paths: List[str] = []
    for ref in extract_html_urls(html):
        if ref.attr != "href":
            continue
        normalized = normalize_runtime_url_path(ref.url, current_path, base_origin=f"https://{DEFAULT_EXTERNAL_ACCESS_HOST}")
        if normalized is None:
            continue
        if normalized == current_path:
            continue
        if normalized == base_path or normalized.startswith(f"{base_path}/"):
            paths.append(normalized)
        if len(paths) >= limit:
            break
    return sorted_unique(paths)


def run_runtime_subpath_audit(
    host_port: int,
    project_slug: str,
    proxy_mode: str,
) -> Dict[str, object]:
    base_path = build_deployment_base_path(project_slug)
    base_origin = f"https://{DEFAULT_EXTERNAL_ACCESS_HOST}"
    external_entry_path = base_path if proxy_mode == "preserve_prefix" else f"{base_path}/"
    entry_path = translate_external_to_upstream_path(external_entry_path, base_path, proxy_mode)
    checked_paths: List[str] = []
    findings: List[Dict[str, object]] = []
    warnings: List[str] = []

    status, headers, html, final_path = fetch_http_text(host_port, entry_path)
    external_final_path = translate_upstream_to_external_path(final_path, base_path, proxy_mode)
    checked_paths.append(external_final_path)
    if status >= 400:
        findings.append(
            {
                "path": external_final_path,
                "line": 1,
                "tag": "document",
                "attr": "status",
                "url": external_final_path,
                "message": f"Runtime subpath audit received HTTP {status} for {external_final_path}",
            }
        )
        return {"checked_paths": checked_paths, "findings": findings, "warnings": warnings}
    content_type = headers.get("content-type", "")
    if "html" not in content_type and "<html" not in html.lower():
        warnings.append(f"Runtime subpath audit skipped HTML extraction for {external_final_path} because response is not HTML.")
        return {"checked_paths": checked_paths, "findings": findings, "warnings": warnings}

    findings.extend(runtime_subpath_findings_for_html(html, external_final_path, base_path, base_origin=base_origin))
    for candidate in collect_runtime_follow_links(html, external_entry_path, base_path):
        upstream_candidate = translate_external_to_upstream_path(candidate, base_path, proxy_mode)
        status, headers, child_html, child_final_path = fetch_http_text(host_port, upstream_candidate)
        external_child_final_path = translate_upstream_to_external_path(child_final_path, base_path, proxy_mode)
        checked_paths.append(external_child_final_path)
        if status >= 400:
            findings.append(
                {
                    "path": external_child_final_path,
                    "line": 1,
                    "tag": "document",
                    "attr": "status",
                    "url": external_child_final_path,
                    "message": f"Runtime subpath audit received HTTP {status} for {external_child_final_path} (upstream {upstream_candidate})",
                }
            )
            continue
        child_content_type = headers.get("content-type", "")
        if "html" not in child_content_type and "<html" not in child_html.lower():
            continue
        findings.extend(runtime_subpath_findings_for_html(child_html, external_child_final_path, base_path, base_origin=base_origin))

    return {
        "checked_paths": sorted_unique(checked_paths),
        "findings": findings,
        "warnings": warnings,
    }


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
    tool_id = payload.get("tool_id")
    if not isinstance(tool_id, str) or not tool_id.strip():
        return None
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
            f"[{datetime.now(timezone.utc).isoformat()}] final_result_detail status={result.get('status')} analysis_summary={json.dumps(result.get('analysis_summary', {}), ensure_ascii=False)} warnings={json.dumps(result.get('warnings', []), ensure_ascii=False)}\n",
        )
    print(json.dumps(result["final_result"], ensure_ascii=False))


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
            f"[{datetime.now(timezone.utc).isoformat()}] raw_runner_invocation argv={json.dumps(sys.argv[1:], ensure_ascii=False)} cwd={Path.cwd()}\n",
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
                f"[{datetime.now(timezone.utc).isoformat()}] local_official_images images={json.dumps(local_official_images, ensure_ascii=False)}\n",
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
        repo_dir, reused_shared_repo, carried_forward_outputs = prepare_shared_repo(
            args.source_type,
            args.source,
            args.ref,
            fetch_log_path,
            shared_repo_dir,
            shared_repo_metadata_path,
        )
        if isinstance(args.tool_id, str) and args.tool_id.strip():
            repo_dir = prepare_job_repo_from_shared(shared_repo_dir, work_repo_dir)
            artifacts["job_work_repo"] = str(repo_dir)
            result["artifacts"] = artifacts
        if carried_forward_outputs:
            artifacts["carried_forward_shared_outputs"] = carried_forward_outputs
            result["artifacts"] = artifacts

        analysis = collect_repo_analysis(repo_dir, args.source_type, args.source, args.ref)
        result["analysis_summary"] = summarize_analysis(analysis, args.source_type, project_slug)
        result["repo_dir"] = str(repo_dir)
        deployment_project_slug = project_slug if isinstance(args.tool_id, str) and args.tool_id.strip() else None
        strategy = detect_subpath_strategy(repo_dir) if deployment_project_slug else {"framework": "generic", "proxy_mode": "strip_prefix", "adapter": "static_rewrite"}
        proxy_mode = strategy["proxy_mode"] if deployment_project_slug else "strip_prefix"
        rewritten_files = apply_subpath_rewrites(repo_dir, deployment_project_slug)
        if rewritten_files:
            artifacts["rewritten_frontend_files"] = rewritten_files
            artifacts["subpath_proxy_mode"] = proxy_mode
            result["artifacts"] = artifacts
            append_warning(result, f"Applied subpath rewrites for tool deployment path to {len(rewritten_files)} frontend files.")
        if deployment_project_slug:
            static_subpath_audit = run_static_subpath_audit(repo_dir, deployment_project_slug, strategy)
            static_audit_attempts: List[Dict[str, object]] = [static_subpath_audit]
            static_findings = static_subpath_audit.get("findings", [])
            if isinstance(static_findings, list) and static_findings:
                auto_fixed_files = auto_fix_subpath_issues(repo_dir, deployment_project_slug, strategy)
                if auto_fixed_files:
                    append_warning(result, f"Auto-fixed {len(auto_fixed_files)} subpath source files before deployment.")
                    static_subpath_audit = run_static_subpath_audit(repo_dir, deployment_project_slug, strategy)
                    static_audit_attempts.append(static_subpath_audit)
                    static_findings = static_subpath_audit.get("findings", [])
                    rewritten_files = sorted_unique(rewritten_files + auto_fixed_files)
                    artifacts["rewritten_frontend_files"] = rewritten_files
            artifacts["subpath_static_audit"] = static_subpath_audit
            artifacts["subpath_static_audit_attempts"] = static_audit_attempts
            result["artifacts"] = artifacts
            extend_warnings(result, static_subpath_audit.get("warnings", []))
            if isinstance(static_findings, list) and static_findings:
                result["status"] = "SUBPATH_STATIC_AUDIT_FAILED"
                result["errors"] = [
                    finding_to_error_text(
                        SubpathAuditFinding(
                            file=str(item.get("file", "")),
                            line=int(item.get("line", 1)),
                            severity=str(item.get("severity", "error")),
                            code=str(item.get("code", "")),
                            message=str(item.get("message", "")),
                        )
                    )
                    for item in static_findings
                    if isinstance(item, dict)
                ]
                emit_final_result(result, result_path)
                return 1
        append_text(
            runner_log_path,
            f"[{datetime.now(timezone.utc).isoformat()}] source_ready repo_dir={repo_dir} reused_shared_repo={reused_shared_repo}\n",
        )
        append_text(
            runner_log_path,
            f"[{datetime.now(timezone.utc).isoformat()}] analysis_summary summary={json.dumps(result['analysis_summary'], ensure_ascii=False)}\n",
        )
        result["status"] = "SOURCE_READY"
        write_json(result_path, result)

        existing_dockerfile = (repo_dir / "Dockerfile").exists()
        existing_onboarding = (repo_dir / "PROJECT_ONBOARDING.md").exists()
        prebuilt_outputs, generation_mode = determine_generation_mode(existing_dockerfile, existing_onboarding)
        if prebuilt_outputs:
            if reused_shared_repo:
                extend_warnings(result, [
                    f"Reused shared repo workspace under automation/jobs/{project_slug}/repo.",
                    "Reused existing Dockerfile and PROJECT_ONBOARDING.md from source repository; skipped codex generation.",
                ])
            else:
                extend_warnings(result, [
                    "Reused existing Dockerfile and PROJECT_ONBOARDING.md from source repository; skipped codex generation."
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
                append_warning(result, "Reused existing Dockerfile from source repository; only generated PROJECT_ONBOARDING.md.")
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
        extend_warnings(result, warnings)
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
                f"[{datetime.now(timezone.utc).isoformat()}] synced_outputs_to_shared_repo files={json.dumps(synced_outputs, ensure_ascii=False)}\n",
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
            persistence_paths=run_spec.get("persistence_paths") if isinstance(run_spec.get("persistence_paths"), list) else [],
            extra_volume_args=extra_volume_args,
            runtime_data_dir=runtime_data_dir,
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

        if deployment_project_slug:
            runtime_subpath_audit = run_runtime_subpath_audit(host_port, deployment_project_slug, proxy_mode)
            artifacts["subpath_runtime_audit"] = runtime_subpath_audit
            extend_warnings(result, runtime_subpath_audit.get("warnings", []))
            runtime_findings = runtime_subpath_audit.get("findings", [])
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
        if isinstance(args.tool_id, str) and args.tool_id.strip():
            nginx_add_conf_path = project_dir / "nginx-add.conf"
            write_text(nginx_add_conf_path, render_nginx_add_conf(project_slug, host_port, proxy_mode=proxy_mode))
            artifacts["nginx_add_conf"] = str(nginx_add_conf_path)
            athena_nginx_sync = sync_athena_nginx_config(project_slug, host_port, output_dir, runner_log_path, proxy_mode=proxy_mode)
            artifacts["athena_nginx_sync"] = athena_nginx_sync
            sync_status = athena_nginx_sync.get("status")
            if sync_status == "synced":
                append_warning(result, f"Synchronized {ATHENA_NGINX_CONFIG_PATH} for /tools2/{project_slug} and reloaded nginx.")
            elif sync_status == "already_managed":
                append_warning(result, f"{ATHENA_NGINX_CONFIG_PATH} already contains the managed nginx block for /tools2/{project_slug}.")
            elif sync_status == "already_present_unmanaged":
                append_warning(result, f"{ATHENA_NGINX_CONFIG_PATH} already contains nginx rules for /tools2/{project_slug}, so no managed block was injected.")
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
            print(json.dumps(final_payload, ensure_ascii=False))
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
            print(json.dumps(final_payload, ensure_ascii=False))
            return 1
        if args and args.source_type == "git" and result.get("status") in {"INITIALIZED", "FETCHING_SOURCE"}:
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
