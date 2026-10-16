"""Startup program analysis (Windows registry Run keys)."""
from __future__ import annotations

import sys

from core import db

MODULE = "startup"


def scan() -> dict:
    if sys.platform != "win32":
        return {"items": [], "note": "unsupported platform"}
    import winreg
    items = []
    locations = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ]
    for hive, subkey in locations:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                i = 0
                while True:
                    try:
                        name, cmd, _ = winreg.EnumValue(key, i)
                        items.append({"name": name, "command": cmd,
                                      "scope": "user" if hive == winreg.HKEY_CURRENT_USER else "machine"})
                        i += 1
                    except OSError:
                        break
        except OSError:
            continue
    summary = {"count": len(items), "items": items}
    db.log_scan(MODULE, summary)
    return summary
