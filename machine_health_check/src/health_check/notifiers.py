from __future__ import annotations

import json
from urllib import error, request

from .models import CheckReport


def _display_name(name: str) -> str:
    mapping = {
        "system": "系统",
        "cpu": "CPU",
        "memory": "内存",
        "disk": "磁盘",
        "network": "网络",
    }
    return mapping.get(name, name)


def _display_status(status: str) -> str:
    mapping = {
        "ok": "正常",
        "warning": "需关注",
        "critical": "异常",
        "failed": "失败",
        "unavailable": "无法探测",
    }
    return mapping.get(status, status)


def _build_wecom_message(report: CheckReport) -> str:
    lines = [
        "服务器健康巡检结果",
        f"主机: {report.host}",
        f"时间: {report.generated_at}",
        f"总状态: {_display_status(report.overall_status)}",
    ]

    lines.append("巡检项:")
    for item in report.items:
        lines.append(f"- {_display_name(item.name)}: {_display_status(item.status)} | {item.summary}")

    return "\n".join(lines)


def send_wecom_message(webhook_url: str, report: CheckReport) -> tuple[bool, str]:
    payload = {
        "msgtype": "text",
        "text": {
            "content": _build_wecom_message(report),
        },
    }

    req = request.Request(
        webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=10) as response:
            response_body = response.read().decode("utf-8")
        return True, response_body
    except error.URLError as exc:
        return False, str(exc)
