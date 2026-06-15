from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .collectors import collect_cpu, collect_disks, collect_memory, collect_network, collect_system
from .evaluators import compare_threshold, worst_status
from .models import CheckItem, CheckReport


def _format_kb_to_mb_gb(value_kb: float) -> str:
    value_mb = value_kb / 1024
    if value_mb >= 1024:
        return f"{value_mb / 1024:.2f} GB"
    return f"{value_mb:.2f} MB"


def _format_bytes_to_mb_gb(value_bytes: float) -> str:
    value_gb = value_bytes / (1024 ** 3)
    if value_gb >= 1:
        return f"{value_gb:.2f} GB"
    value_mb = value_bytes / (1024 ** 2)
    return f"{value_mb:.2f} MB"


def _format_probe_status(status: str) -> str:
    mapping = {
        "ok": "正常",
        "failed": "失败",
        "unavailable": "无法探测",
        "warning": "需关注",
        "critical": "异常",
    }
    return mapping.get(status, status)


def _summarize_interface_stats(interfaces: dict[str, dict[str, int]]) -> str:
    active_interfaces = [
        name
        for name, stats in interfaces.items()
        if stats["rx_bytes"] > 0 or stats["tx_bytes"] > 0
    ]
    error_interfaces = [
        name
        for name, stats in interfaces.items()
        if stats["rx_errs"] > 0
        or stats["tx_errs"] > 0
        or stats["rx_drop"] > 0
        or stats["tx_drop"] > 0
    ]

    if error_interfaces:
        return f"已采集，存在错误/丢包网卡: {', '.join(error_interfaces)}"
    if active_interfaces:
        return f"已采集，活跃网卡: {', '.join(active_interfaces)}"
    return "已采集，当前未发现活跃流量"


def _evaluate_cpu(cpu_data: dict[str, object], thresholds: dict[str, dict[str, float]]) -> CheckItem:
    cpu_status = compare_threshold(
        float(cpu_data["usage_percent"]),
        thresholds["cpu_usage_percent"]["warning"],
        thresholds["cpu_usage_percent"]["critical"],
    )
    load_status = compare_threshold(
        float(cpu_data["load_ratio_1m"]),
        thresholds["load_ratio"]["warning"],
        thresholds["load_ratio"]["critical"],
    )
    status = worst_status([cpu_status, load_status])
    details = dict(cpu_data)
    details["load_1m"] = cpu_data["loadavg"][0]
    details["load_5m"] = cpu_data["loadavg"][1]
    details["load_15m"] = cpu_data["loadavg"][2]
    details["load_ratio_1m_percent"] = round(float(cpu_data["load_ratio_1m"]) * 100, 2)
    return CheckItem(
        name="cpu",
        status=status,
        summary=(
            f"CPU 使用率 {cpu_data['usage_percent']}%，"
            f"1 分钟平均负载 {details['load_1m']}，"
            f"负载占核心比 {details['load_ratio_1m_percent']}%，"
            f"CPU 核心数 {cpu_data['cpu_count']}"
        ),
        details=details,
    )


def _evaluate_memory(memory_data: dict[str, object], thresholds: dict[str, dict[str, float]]) -> CheckItem:
    memory_status = compare_threshold(
        float(memory_data["memory_usage_percent"]),
        thresholds["memory_usage_percent"]["warning"],
        thresholds["memory_usage_percent"]["critical"],
    )
    swap_status = compare_threshold(
        float(memory_data["swap_usage_percent"]),
        thresholds["swap_usage_percent"]["warning"],
        thresholds["swap_usage_percent"]["critical"],
    )
    status = worst_status([memory_status, swap_status])
    details = dict(memory_data)
    details["mem_total_human"] = _format_kb_to_mb_gb(float(memory_data["mem_total_kb"]))
    details["mem_used_human"] = _format_kb_to_mb_gb(float(memory_data["mem_used_kb"]))
    details["mem_available_human"] = _format_kb_to_mb_gb(float(memory_data["mem_available_kb"]))
    details["swap_total_human"] = _format_kb_to_mb_gb(float(memory_data["swap_total_kb"]))
    details["swap_used_human"] = _format_kb_to_mb_gb(float(memory_data["swap_used_kb"]))
    return CheckItem(
        name="memory",
        status=status,
        summary=(
            f"内存使用率 {memory_data['memory_usage_percent']}%，"
            f"可用内存 {details['mem_available_human']}，"
            f"Swap 使用率 {memory_data['swap_usage_percent']}%"
        ),
        details=details,
    )


def _evaluate_disks(
    disk_data: list[dict[str, object]],
    thresholds: dict[str, dict[str, float]],
) -> CheckItem:
    statuses: list[str] = []
    mounts_with_human_sizes: list[dict[str, object]] = []
    for disk in disk_data:
        statuses.append(
            compare_threshold(
                float(disk["usage_percent"]),
                thresholds["disk_usage_percent"]["warning"],
                thresholds["disk_usage_percent"]["critical"],
            )
        )
        statuses.append(
            compare_threshold(
                float(disk["inode_usage_percent"]),
                thresholds["inode_usage_percent"]["warning"],
                thresholds["inode_usage_percent"]["critical"],
            )
        )
        disk_with_human_sizes = dict(disk)
        disk_with_human_sizes["total_human"] = _format_bytes_to_mb_gb(float(disk["total_bytes"]))
        disk_with_human_sizes["used_human"] = _format_bytes_to_mb_gb(float(disk["used_bytes"]))
        disk_with_human_sizes["available_human"] = _format_bytes_to_mb_gb(float(disk["available_bytes"]))
        mounts_with_human_sizes.append(disk_with_human_sizes)

    status = worst_status(statuses or ["ok"])
    summary = "；".join(
        f"挂载点 {disk['mount_point']}：已用 {disk['used_human']} / 总量 {disk['total_human']}，磁盘使用率 {disk['usage_percent']}%，inode 使用率 {disk['inode_usage_percent']}%"
        for disk in mounts_with_human_sizes
    )
    return CheckItem(name="disk", status=status, summary=summary, details={"mounts": mounts_with_human_sizes})


def _evaluate_network(network_data: dict[str, object]) -> CheckItem:
    ping_checks = network_data["ping_checks"]
    tcp_checks = network_data["tcp_checks"]
    interfaces = network_data["interfaces"]

    statuses: list[str] = []
    statuses.extend("critical" for item in ping_checks if item["probe_status"] == "failed")
    statuses.extend("critical" for item in tcp_checks if item["probe_status"] == "failed")
    statuses.extend("warning" for item in ping_checks if item["probe_status"] == "unavailable")
    statuses.extend("warning" for item in tcp_checks if item["probe_status"] == "unavailable")
    status = worst_status(statuses or ["ok"])

    loopback_ping = next((item for item in ping_checks if item["target"] == "127.0.0.1"), None)
    public_pings = [item for item in ping_checks if item["target"] != "127.0.0.1"]

    summary_parts: list[str] = []
    if loopback_ping:
        summary_parts.append(
            "本机回环连通性: "
            f"{_format_probe_status(str(loopback_ping['probe_status']))} "
            f"({loopback_ping['target']} ping)"
        )

    if public_pings:
        targets = ", ".join(str(item["target"]) for item in public_pings)
        public_status = worst_status(
            [
                "critical" if item["probe_status"] == "failed" else
                "warning" if item["probe_status"] == "unavailable" else
                "ok"
                for item in public_pings
            ]
        )
        summary_parts.append(
            "公网 ICMP 连通性: "
            f"{_format_probe_status(public_status)} "
            f"({targets} ping)"
        )

    if tcp_checks:
        tcp_targets = ", ".join(f"{item['host']}:{item['port']}" for item in tcp_checks)
        tcp_status = worst_status(
            [
                "critical" if item["probe_status"] == "failed" else
                "warning" if item["probe_status"] == "unavailable" else
                "ok"
                for item in tcp_checks
            ]
        )
        summary_parts.append(
            "公网 TCP 出站连通性: "
            f"{_format_probe_status(tcp_status)} "
            f"({tcp_targets})"
        )

    summary_parts.append(f"网卡统计: {_summarize_interface_stats(interfaces)}")
    summary = "；".join(summary_parts)

    return CheckItem(name="network", status=status, summary=summary, details=network_data)


def _evaluate_system(system_data: dict[str, object]) -> CheckItem:
    return CheckItem(
        name="system",
        status="ok",
        summary=(
            f"主机名 {system_data['hostname']}，"
            f"内核版本 {system_data['kernel']}，"
            f"运行时长 {system_data['uptime_seconds']} 秒"
        ),
        details=system_data,
    )


def generate_report(config: dict[str, object]) -> CheckReport:
    thresholds = config["thresholds"]
    system_data = collect_system()
    cpu_data = collect_cpu()
    memory_data = collect_memory()
    disk_data = collect_disks(list(config.get("mount_points", ["/"])))
    network_config = dict(config.get("network", {}))
    network_data = collect_network(
        ping_targets=list(network_config.get("ping_targets", [])),
        tcp_targets=list(network_config.get("tcp_targets", [])),
    )

    items = [
        _evaluate_system(system_data),
        _evaluate_cpu(cpu_data, thresholds),
        _evaluate_memory(memory_data, thresholds),
        _evaluate_disks(disk_data, thresholds),
        _evaluate_network(network_data),
    ]

    report = CheckReport(
        host=str(system_data["hostname"]),
        generated_at=str(system_data["generated_at"]),
        overall_status=worst_status([item.status for item in items]),
        items=items,
    )
    return report


def write_report(report: CheckReport, report_dir: str, history_dir: str) -> Path:
    report_root = Path(report_dir)
    history_root = Path(history_dir)
    report_root.mkdir(parents=True, exist_ok=True)
    history_root.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    latest_path = report_root / "latest.json"
    history_path = history_root / f"report-{now}.json"

    payload = json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
    latest_path.write_text(payload + "\n", encoding="utf-8")
    history_path.write_text(payload + "\n", encoding="utf-8")
    return latest_path
