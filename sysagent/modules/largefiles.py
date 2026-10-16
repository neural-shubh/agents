"""Large, idle file detection (Tier 2 -> approval)."""
from __future__ import annotations

import time
from pathlib import Path

from core import config, db
from core.safety import is_protected

MODULE = "largefiles"

SCAN_ROOTS = [
    Path.home() / "Downloads",
    Path.home() / "Documents",
    Path.home() / "Desktop",
    Path.home() / "Videos",
    Path.home() / "Pictures",
]


def scan() -> dict:
    cfg = config.load()
    min_bytes = cfg["large_file_min_mb"] * 1024 * 1024
    idle_cutoff = time.time() - cfg["large_file_idle_days"] * 86400
    hits = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for f in root.rglob("*"):
            try:
                if not f.is_file():
                    continue
                st = f.stat()
                if st.st_size >= min_bytes and st.st_atime < idle_cutoff and not is_protected(f):
                    hits.append({"path": str(f), "size": st.st_size,
                                 "idle_days": int((time.time() - st.st_atime) / 86400)})
            except (PermissionError, OSError):
                continue
    hits.sort(key=lambda h: -h["size"])
    for h in hits:
        db.add_pending(MODULE, f"Large idle file: {Path(h['path']).name}",
                       f"{h['size']/1e9:.2f} GB, untouched {h['idle_days']} days",
                       [h["path"]], h["size"])
    summary = {"count": len(hits), "total_gb": round(sum(h["size"] for h in hits) / 1e9, 2)}
    db.log_scan(MODULE, summary)
    return summary
