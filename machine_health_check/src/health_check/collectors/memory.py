from __future__ import annotations


def _read_meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as file_obj:
        for line in file_obj:
            key, raw_value = line.split(":", 1)
            amount = int(raw_value.strip().split()[0])
            values[key] = amount
    return values


def collect_memory() -> dict[str, float | int]:
    meminfo = _read_meminfo()
    mem_total = meminfo.get("MemTotal", 0)
    mem_available = meminfo.get("MemAvailable", 0)
    mem_used = max(mem_total - mem_available, 0)
    swap_total = meminfo.get("SwapTotal", 0)
    swap_free = meminfo.get("SwapFree", 0)
    swap_used = max(swap_total - swap_free, 0)

    memory_usage_percent = round((mem_used / mem_total) * 100, 2) if mem_total else 0.0
    swap_usage_percent = round((swap_used / swap_total) * 100, 2) if swap_total else 0.0

    return {
        "mem_total_kb": mem_total,
        "mem_used_kb": mem_used,
        "mem_available_kb": mem_available,
        "memory_usage_percent": memory_usage_percent,
        "swap_total_kb": swap_total,
        "swap_used_kb": swap_used,
        "swap_usage_percent": swap_usage_percent,
    }
