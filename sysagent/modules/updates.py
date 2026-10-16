"""Update check via winget (Windows). Never auto-installs; always Tier 2.

Parses `winget upgrade` text table output (works with winget >= 1.4,
which removed/changed the --output json flag for some commands).
"""
from __future__ import annotations

import re
import subprocess

from core import db

MODULE = "updates"

# Name (may contain spaces)  Id  Version  Available  Source
ROW_RE = re.compile(r"^(?P<name>.+?)\s{2,}(?P<id>[^\s]+)\s+(?P<ver>[^\s]+)\s+(?P<avail>[^\s]+)\s+(?P<src>\S+)\s*$")


def scan() -> dict:
    raw = _winget(["upgrade", "--accept-source-agreements",
                   "--disable-interactivity"])
    if raw is None:
        summary = {"available": 0, "error": "winget unavailable"}
        db.log_scan(MODULE, summary)
        return summary
    pkgs = _parse_table(raw)
    for p in pkgs:
        db.add_pending(MODULE, f"Update {p['name']}",
                       f"{p['ver']} -> {p['avail']} (id: {p['id']}, source: {p['src']})",
                       [], 0)
    summary = {"available": len(pkgs),
               "packages": [{k: p[k] for k in ("name", "ver", "avail")} for p in pkgs]}
    db.log_scan(MODULE, summary)
    return summary


def _winget(args: list[str]) -> str | None:
    try:
        out = subprocess.run(["winget", *args], capture_output=True,
                             text=True, timeout=180, encoding="utf-8",
                             errors="replace")
        return out.stdout or ""
    except Exception:
        return None


def _parse_table(text: str) -> list[dict]:
    pkgs, in_body = [], False
    for line in text.splitlines():
        line = line.rstrip()
        if not in_body:
            # Body starts after the dashed separator line
            if set(line.strip()) <= {"-"} and line.strip():
                in_body = True
            continue
        m = ROW_RE.match(line)
        if m and "winget" in line.lower():
            pkgs.append({"name": m.group("name").strip(), "id": m.group("id"),
                         "ver": m.group("ver"), "avail": m.group("avail"),
                         "src": m.group("src")})
    return pkgs
