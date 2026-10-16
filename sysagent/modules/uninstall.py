"""Unused/large installed-app detection (Tier 2). Reads Windows uninstall registry."""
from __future__ import annotations

import sys

from core import db

MODULE = "uninstall"

MIN_SIZE_MB = 300  # only flag apps bigger than this


def scan() -> dict:
    if sys.platform != "win32":
        return {"apps": [], "note": "unsupported platform"}
    import winreg
    apps = []
    locations = [
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, sub in locations:
        try:
            with winreg.OpenKey(hive, sub) as root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root, i)) as k:
                            name = _get(k, "DisplayName")
                            if not name:
                                continue
                            size_kb = _get(k, "EstimatedSize") or 0
                            size_mb = int(size_kb) / 1024
                            if size_mb < MIN_SIZE_MB:
                                continue
                            if _get(k, "SystemComponent") or _get(k, "ParentKeyName"):
                                continue
                            apps.append({
                                "name": name,
                                "size_mb": round(size_mb),
                                "publisher": _get(k, "Publisher") or "",
                                "uninstall_cmd": _get(k, "UninstallString") or "",
                            })
                    except OSError:
                        continue
        except OSError:
            continue
    apps.sort(key=lambda a: -a["size_mb"])
    for a in apps[:20]:
        db.add_pending(MODULE, f"Large app: {a['name']} ({a['size_mb']} MB)",
                       f"Publisher: {a['publisher']}. Uninstall runs: {a['uninstall_cmd']}",
                       [], a["size_mb"] * 1024 * 1024)
    summary = {"flagged_apps": len(apps),
               "total_gb": round(sum(a["size_mb"] for a in apps) / 1024, 2)}
    db.log_scan(MODULE, summary)
    return summary


def _get(key, name):
    import winreg
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None
