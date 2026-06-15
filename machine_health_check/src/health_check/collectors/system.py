from __future__ import annotations

import os
import platform
import socket
from datetime import datetime, timezone


def _read_uptime_seconds() -> float:
    with open("/proc/uptime", "r", encoding="utf-8") as file_obj:
        return float(file_obj.read().split()[0])


def collect_system() -> dict[str, str | float | int]:
    return {
        "hostname": socket.gethostname(),
        "kernel": platform.release(),
        "platform": platform.platform(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": round(_read_uptime_seconds(), 2),
        "cpu_count": os.cpu_count() or 1,
    }
