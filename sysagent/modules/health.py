"""Health report: CPU, RAM, battery, top processes, recent errors."""
from __future__ import annotations

import psutil

from core import db

MODULE = "health"


def report() -> dict:
    battery = psutil.sensors_battery()
    disk_io = psutil.disk_io_counters()
    summary = {
        "cpu_percent": psutil.cpu_percent(interval=1),
        "ram_percent": psutil.virtual_memory().percent,
        "boot_time": psutil.boot_time(),
        "battery": ({
            "percent": battery.percent,
            "plugged": battery.power_plugged,
        } if battery else None),
        "disk_read_gb": round((disk_io.read_bytes if disk_io else 0) / 1e9, 1),
        "top_processes": _top_procs(),
    }
    db.log_scan(MODULE, summary)
    return summary


def _top_procs(n: int = 8) -> list[dict]:
    procs = []
    for p in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
        try:
            procs.append({"name": p.info["name"],
                          "cpu": p.info["cpu_percent"] or 0,
                          "ram_mb": round((p.info["memory_info"].rss or 0) / 1e6, 1)})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    procs.sort(key=lambda x: -x["ram_mb"])
    return procs[:n]
