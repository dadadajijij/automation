from __future__ import annotations

import socket
import subprocess
import time


def _read_net_dev() -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = {}
    with open("/proc/net/dev", "r", encoding="utf-8") as file_obj:
        lines = file_obj.readlines()[2:]

    for line in lines:
        iface, raw_values = line.split(":", 1)
        values = raw_values.split()
        stats[iface.strip()] = {
            "rx_bytes": int(values[0]),
            "rx_packets": int(values[1]),
            "rx_errs": int(values[2]),
            "rx_drop": int(values[3]),
            "tx_bytes": int(values[8]),
            "tx_packets": int(values[9]),
            "tx_errs": int(values[10]),
            "tx_drop": int(values[11]),
        }
    return stats


def _ping_target(host: str) -> dict[str, str | bool | float]:
    start = time.time()
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "2", host],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return {
            "target": host,
            "reachable": False,
            "probe_status": "unavailable",
            "latency_ms": round((time.time() - start) * 1000, 2),
            "message": "ping command not found",
        }

    latency_ms = round((time.time() - start) * 1000, 2)
    message = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else result.stderr.strip()

    if result.returncode == 0:
        probe_status = "ok"
        reachable = True
    elif result.returncode == 2:
        probe_status = "unavailable"
        reachable = False
        message = message or "ping probe unavailable"
    else:
        probe_status = "failed"
        reachable = False

    return {
        "target": host,
        "reachable": reachable,
        "probe_status": probe_status,
        "latency_ms": latency_ms,
        "message": message,
    }


def _tcp_probe(host: str, port: int, timeout_seconds: float) -> dict[str, str | bool | float | int]:
    start = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            reachable = True
            probe_status = "ok"
            message = "connected"
    except PermissionError as exc:
        reachable = False
        probe_status = "unavailable"
        message = str(exc)
    except OSError as exc:
        reachable = False
        probe_status = "failed"
        message = str(exc)

    latency_ms = round((time.time() - start) * 1000, 2)
    return {
        "host": host,
        "port": port,
        "reachable": reachable,
        "probe_status": probe_status,
        "latency_ms": latency_ms,
        "message": message,
    }


def collect_network(ping_targets: list[str], tcp_targets: list[dict[str, str | int | float]]) -> dict[str, object]:
    return {
        "interfaces": _read_net_dev(),
        "ping_checks": [_ping_target(target) for target in ping_targets],
        "tcp_checks": [
            _tcp_probe(
                host=str(target["host"]),
                port=int(target["port"]),
                timeout_seconds=float(target.get("timeout_seconds", 2)),
            )
            for target in tcp_targets
        ],
    }
