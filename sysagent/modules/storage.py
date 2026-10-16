"""Storage analysis: disk usage snapshots + threshold breach detection."""
from __future__ import annotations

import time

import psutil

from core import config, db

MODULE = "storage"


def scan() -> dict:
    cfg = config.load()
    snapshots = []
    breaches = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        snapshots.append((time.time(), part.mountpoint, usage.total, usage.used, usage.free))
        free_pct = 100 * usage.free / usage.total if usage.total else 0
        free_gb = usage.free / (1024 ** 3)
        if free_pct < cfg["disk_free_threshold_percent"] or free_gb < cfg["disk_free_threshold_gb"]:
            breaches.append({
                "drive": part.mountpoint,
                "free_gb": round(free_gb, 2),
                "free_percent": round(free_pct, 1),
            })
    db.snapshot_disks(snapshots)
    summary = {
        "drives": [
            {"drive": s[1], "total_gb": round(s[2] / 1073741824, 1),
             "used_gb": round(s[3] / 1073741824, 1), "free_gb": round(s[4] / 1073741824, 1)}
            for s in snapshots
        ],
        "threshold_breaches": breaches,
    }
    db.log_scan(MODULE, summary)
    if breaches:
        for b in breaches:
            db.log_action(MODULE, 2, "threshold_breach", b["drive"], status="done",
                          detail=f"free {b['free_gb']}GB ({b['free_percent']}%)")
    return summary


def low_disk() -> bool:
    """Event-trigger: is any drive below configured thresholds?"""
    return bool(scan()["threshold_breaches"])
