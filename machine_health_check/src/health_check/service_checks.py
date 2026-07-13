from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request

from .evaluators import worst_status


@dataclass
class ServiceTarget:
    name: str
    display_name: str
    deploy_type: str
    host: str
    port: int
    protocol: str
    health_paths: list[str]
    timeout_seconds: float
    success_status_codes: list[int]
    container_name: str | None = None


def _normalize_service_name(name: str) -> str:
    return name.replace("_", "-").strip().lower()


def _display_status(status: str) -> str:
    mapping = {
        "ok": "正常",
        "warning": "需关注",
        "critical": "异常",
        "unavailable": "无法探测",
    }
    return mapping.get(status, status)


def _status_from_bool(ok: bool) -> str:
    return "ok" if ok else "critical"


def _dedupe_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for path in paths:
        normalized = path if path.startswith("/") else f"/{path}"
        if normalized not in seen:
            seen.add(normalized)
            results.append(normalized)
    return results


def _parse_host_port(port_mapping: str) -> int | None:
    try:
        host_part = str(port_mapping).split(":", 1)[0]
        return int(host_part)
    except (IndexError, ValueError):
        return None


def _load_project_ports(project_ports_file: str) -> dict[str, object]:
    path = Path(project_ports_file)
    with path.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def _run_command(command: list[str], env: dict[str, str] | None = None) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    except FileNotFoundError as exc:
        return False, str(exc)

    if result.returncode == 0:
        return True, result.stdout.strip()
    message = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
    return False, message


def _tcp_probe(host: str, port: int, timeout_seconds: float) -> dict[str, object]:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return {
                "status": "ok",
                "message": f"{host}:{port} TCP 连接成功",
            }
    except PermissionError as exc:
        return {
            "status": "warning",
            "message": f"{host}:{port} TCP 无法探测: {exc}",
        }
    except OSError as exc:
        return {
            "status": "critical",
            "message": f"{host}:{port} TCP 连接失败: {exc}",
        }


def _http_probe(
    host: str,
    port: int,
    health_paths: list[str],
    timeout_seconds: float,
    success_status_codes: list[int],
) -> dict[str, object]:
    last_warning: dict[str, object] | None = None
    last_error = "未配置 HTTP 健康检查路径"
    detected_http = False
    for path in health_paths:
        url = f"http://{host}:{port}{path}"
        try:
            with request.urlopen(url, timeout=timeout_seconds) as response:
                status_code = response.getcode()
                detected_http = True
                if status_code in success_status_codes or 200 <= status_code < 400:
                    return {
                        "status": "ok",
                        "message": f"{url} 返回 {status_code}",
                        "url": url,
                        "status_code": status_code,
                        "detected_http": True,
                    }
                if 400 <= status_code < 500:
                    last_warning = {
                        "status": "warning",
                        "message": f"{url} 返回 {status_code}",
                        "url": url,
                        "status_code": status_code,
                        "detected_http": True,
                    }
                    continue
                last_error = f"{url} 返回 {status_code}"
        except error.HTTPError as exc:
            detected_http = True
            if exc.code in success_status_codes:
                return {
                    "status": "ok",
                    "message": f"{url} 返回 {exc.code}",
                    "url": url,
                    "status_code": exc.code,
                    "detected_http": True,
                }
            if 400 <= exc.code < 500:
                last_warning = {
                    "status": "warning",
                    "message": f"{url} 返回 {exc.code}",
                    "url": url,
                    "status_code": exc.code,
                    "detected_http": True,
                }
                continue
            last_error = f"{url} 返回 {exc.code}"
        except error.URLError as exc:
            reason = str(exc.reason)
            if "Operation not permitted" in reason:
                return {
                    "status": "warning",
                    "message": f"{url} 无法探测: {reason}",
                    "detected_http": False,
                }
            last_error = f"{url} 请求失败: {reason}"

    if last_warning is not None:
        return last_warning

    return {
        "status": "critical",
        "message": last_error,
        "detected_http": detected_http,
    }


def _host_process_probe(target: ServiceTarget) -> dict[str, object]:
    command = ["ss", "-ltn"]
    ok, output = _run_command(command)
    if not ok:
        return {
            "status": "warning",
            "message": f"无法执行端口监听检查: {output}",
        }

    expected = f":{target.port}"
    listening = expected in output
    return {
        "status": _status_from_bool(listening),
        "message": f"宿主机端口 {target.port} {'正在监听' if listening else '未监听'}",
    }


def _list_podman_containers() -> tuple[dict[str, dict[str, object]], str | None]:
    env = os.environ.copy()
    env.setdefault("HOME", str(Path.home()))
    env.setdefault("USER", os.environ.get("USER", "devops"))
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")

    command = ["podman", "ps", "--format", "json"]
    ok, output = _run_command(command, env=env)
    if not ok:
        return {}, output

    try:
        raw_containers = json.loads(output)
    except json.JSONDecodeError as exc:
        return {}, f"Podman JSON 解析失败: {exc}"

    containers: dict[str, dict[str, object]] = {}
    for raw_container in raw_containers:
        names = raw_container.get("Names", [])
        if not names:
            continue
        container_name = str(names[0])
        ports = list(raw_container.get("Ports", []))
        host_ports = [int(port["hostPort"]) for port in ports if "hostPort" in port]
        containers[container_name] = {
            "status": str(raw_container.get("Status", "")),
            "state": str(raw_container.get("State", "")),
            "ports": ports,
            "host_ports": host_ports,
            "id": str(raw_container.get("Id", "")),
            "image": str(raw_container.get("Image", "")),
        }
    return containers, None


def _find_container_match(
    target: ServiceTarget,
    containers: dict[str, dict[str, object]],
) -> tuple[str | None, dict[str, object] | None, str | None]:
    if target.container_name and target.container_name in containers:
        return target.container_name, containers[target.container_name], "name"

    normalized_target = _normalize_service_name(target.name)
    for container_name, container in containers.items():
        if _normalize_service_name(container_name) == normalized_target:
            return container_name, container, "normalized_name"

    for container_name, container in containers.items():
        host_ports = list(container.get("host_ports", []))
        if target.port in host_ports:
            return container_name, container, "host_port"

    return None, None, None


def _container_probe(target: ServiceTarget, containers: dict[str, dict[str, object]], podman_error: str | None) -> dict[str, object]:
    if podman_error:
        return {
            "status": "warning",
            "message": f"无法获取 Podman 容器状态: {podman_error}",
        }

    matched_name, container, matched_by = _find_container_match(target, containers)
    if not container:
        return {
            "status": "critical",
            "message": f"未找到与端口 {target.port} 或名称 {target.container_name or target.name} 匹配的容器",
        }

    status_text = str(container["status"])
    if not status_text.lower().startswith("up"):
        return {
            "status": "critical",
            "message": f"容器 {matched_name} 状态异常: {status_text}",
        }

    return {
        "status": "ok",
        "message": f"容器 {matched_name} 运行中: {status_text}（匹配方式: {matched_by}）",
        "matched_container_name": matched_name,
        "matched_by": matched_by,
        "container_id": str(container.get("id", "")),
        "image": str(container.get("image", "")),
    }


def _build_host_targets(service_config: dict[str, object]) -> list[ServiceTarget]:
    host_services = list(service_config.get("host_services", []))
    targets: list[ServiceTarget] = []
    for raw_service in host_services:
        service = dict(raw_service)
        targets.append(
            ServiceTarget(
                name=str(service["name"]),
                display_name=str(service.get("display_name", service["name"])),
                deploy_type="host",
                host=str(service.get("host", "127.0.0.1")),
                port=int(service["port"]),
                protocol=str(service.get("protocol", "tcp")),
                health_paths=list(service.get("health_paths", [])),
                timeout_seconds=float(service.get("timeout_seconds", 3)),
                success_status_codes=[int(code) for code in service.get("success_status_codes", [200, 204, 301, 302, 307, 308])],
            )
        )
    return targets


def _build_container_targets(service_config: dict[str, object]) -> list[ServiceTarget]:
    project_ports_file = str(service_config.get("project_ports_file", "../project-ports.json"))
    project_ports = _load_project_ports(project_ports_file)
    container_config = dict(service_config.get("container_services", {}))
    exclude_names = {str(name) for name in container_config.get("exclude_names", [])}
    default_host = str(container_config.get("default_host", "127.0.0.1"))
    default_timeout = float(container_config.get("default_timeout_seconds", 3))
    default_protocol = str(container_config.get("default_protocol", "auto"))
    default_health_paths = list(container_config.get("default_health_paths", ["/api/health", "/health", "/"]))
    base_path_templates = list(
        container_config.get(
            "base_path_templates",
            ["/tools2/{service_name}", "/tools2/{service_name}/"],
        )
    )
    default_success_status_codes = [
        int(code)
        for code in container_config.get("default_success_status_codes", [200, 204, 301, 302, 307, 308])
    ]
    overrides = dict(container_config.get("per_service_overrides", {}))

    targets: list[ServiceTarget] = []
    for name, raw_config in project_ports.items():
        if name in exclude_names:
            continue
        service_ports = dict(raw_config)
        host_port = _parse_host_port(str(service_ports.get("port", "")))
        if host_port is None:
            continue

        override = dict(overrides.get(name, {}))
        derived_paths = [template.format(service_name=name) for template in base_path_templates]
        merged_health_paths = _dedupe_paths(
            list(override.get("health_paths", [])) + derived_paths + default_health_paths
        )
        targets.append(
            ServiceTarget(
                name=str(name),
                display_name=str(override.get("display_name", name)),
                deploy_type="podman",
                host=str(override.get("host", default_host)),
                port=host_port,
                protocol=str(override.get("protocol", default_protocol)),
                health_paths=merged_health_paths,
                timeout_seconds=float(override.get("timeout_seconds", default_timeout)),
                success_status_codes=[
                    int(code)
                    for code in override.get("success_status_codes", default_success_status_codes)
                ],
                container_name=str(override.get("container_name", name)),
            )
        )
    return targets


def _summarize_service(target: ServiceTarget, checks: dict[str, dict[str, object]], status: str) -> str:
    if target.deploy_type == "host":
        return (
            f"宿主机服务 {target.display_name}："
            f"端口 {target.port}，"
            f"进程检查 {_display_status(str(checks['process']['status']))}，"
            f"TCP 检查 {_display_status(str(checks['tcp']['status']))}，"
            f"{'HTTP 检查 ' + _display_status(str(checks['http']['status'])) if 'http' in checks else '未配置 HTTP 检查'}"
        )

    container_message = str(checks["container"]["message"])
    container_summary = (
        "容器状态 已识别"
        if str(checks["container"]["status"]) == "ok"
        else "容器状态信息未匹配"
    )
    return (
        f"容器服务 {target.display_name}："
        f"端口 {target.port}，"
        f"TCP 检查 {_display_status(str(checks['tcp']['status']))}，"
        f"{'HTTP 检查 ' + _display_status(str(checks['http']['status'])) if 'http' in checks else '按 TCP 服务处理'}，"
        f"{container_summary}"
    )


def _collect_host_service(target: ServiceTarget) -> dict[str, object]:
    checks: dict[str, dict[str, object]] = {}
    checks["process"] = _host_process_probe(target)
    checks["tcp"] = _tcp_probe(target.host, target.port, target.timeout_seconds)
    if target.protocol == "http":
        checks["http"] = _http_probe(
            target.host,
            target.port,
            target.health_paths,
            target.timeout_seconds,
            target.success_status_codes,
        )

    status = worst_status([str(check["status"]) for check in checks.values()])
    return {
        "name": target.name,
        "display_name": target.display_name,
        "deploy_type": target.deploy_type,
        "host": target.host,
        "port": target.port,
        "status": status,
        "checks": checks,
        "summary": _summarize_service(target, checks, status),
    }


def _collect_container_service(
    target: ServiceTarget,
    containers: dict[str, dict[str, str]],
    podman_error: str | None,
) -> dict[str, object]:
    checks: dict[str, dict[str, object]] = {}
    checks["container"] = _container_probe(target, containers, podman_error)
    checks["tcp"] = _tcp_probe(target.host, target.port, target.timeout_seconds)
    if target.protocol in {"http", "auto"}:
        http_result = _http_probe(
            target.host,
            target.port,
            target.health_paths,
            target.timeout_seconds,
            target.success_status_codes,
        )
        if target.protocol == "http" or bool(http_result.get("detected_http")) or str(http_result["status"]) == "warning":
            checks["http"] = http_result

    primary_statuses = [str(checks["tcp"]["status"])]
    if "http" in checks:
        primary_statuses.append(str(checks["http"]["status"]))
    status = worst_status(primary_statuses)

    return {
        "name": target.name,
        "display_name": target.display_name,
        "deploy_type": target.deploy_type,
        "container_name": target.container_name,
        "host": target.host,
        "port": target.port,
        "status": status,
        "checks": checks,
        "summary": _summarize_service(target, checks, status),
    }


def collect_service_checks(service_config: dict[str, object]) -> dict[str, object]:
    if not service_config.get("enabled", False):
        return {
            "enabled": False,
            "services": [],
            "summary": "服务健康检查未启用",
        }

    host_targets = _build_host_targets(service_config)
    container_targets = _build_container_targets(service_config)
    containers, podman_error = _list_podman_containers()

    services: list[dict[str, object]] = []
    for target in host_targets:
        services.append(_collect_host_service(target))
    for target in container_targets:
        services.append(_collect_container_service(target, containers, podman_error))

    total = len(services)
    ok_count = sum(1 for service in services if service["status"] == "ok")
    warning_count = sum(1 for service in services if service["status"] == "warning")
    critical_count = sum(1 for service in services if service["status"] == "critical")

    return {
        "enabled": True,
        "services": services,
        "summary": f"服务总数 {total}，正常 {ok_count}，需关注 {warning_count}，异常 {critical_count}",
        "status": worst_status([str(service["status"]) for service in services] or ["ok"]),
        "podman_error": podman_error,
    }
