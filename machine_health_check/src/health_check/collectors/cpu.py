from __future__ import annotations

import os
import time


def _read_cpu_times() -> tuple[int, int]:
    with open("/proc/stat", "r", encoding="utf-8") as file_obj:
        first_line = file_obj.readline().strip().split()

    values = [int(value) for value in first_line[1:]]
    idle = values[3] + values[4]
    total = sum(values)
    return idle, total


def _sample_cpu_usage(sample_seconds: float = 0.2) -> float:
    idle_1, total_1 = _read_cpu_times()
    time.sleep(sample_seconds)
    idle_2, total_2 = _read_cpu_times()
    idle_delta = idle_2 - idle_1
    total_delta = total_2 - total_1
    if total_delta <= 0:
        return 0.0
    busy = 1 - (idle_delta / total_delta)
    return round(busy * 100, 2)


def collect_cpu() -> dict[str, float | int | tuple[float, float, float]]:
    loadavg = os.getloadavg()
    cpu_count = os.cpu_count() or 1
    usage_percent = _sample_cpu_usage()
    return {
        "usage_percent": usage_percent,
        "loadavg": loadavg,
        "cpu_count": cpu_count,
        "load_ratio_1m": round(loadavg[0] / cpu_count, 2),
    }
