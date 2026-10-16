"""Developer cleanup: node_modules, venvs, build artifacts, stale caches (Tier 2, LLM-assisted for unknowns)."""
from __future__ import annotations

import time
from pathlib import Path

from core import config, db
from core.safety import is_protected

MODULE = "devclean"

DEV_PATTERNS = ["node_modules", ".venv", "venv", "dist", "build", ".next",
                "target", "__pycache__", ".gradle", "out"]
SCAN_ROOTS = [Path.home() / "Documents", Path.home() / "Desktop",
              Path.home() / "source", Path.home() / "projects", Path.home() / "dev"]

IDLE_DAYS = 30


def _size(path: Path) -> int:
    try:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except OSError:
        return 0


def scan() -> dict:
    cutoff = time.time() - IDLE_DAYS * 86400
    hits = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for pat in DEV_PATTERNS:
            for hit in root.rglob(pat):
                try:
                    if not hit.is_dir() or is_protected(hit):
                        continue
                    # Only surface deps of projects untouched for a while
                    parent_mtime = hit.parent.stat().st_mtime
                    if parent_mtime > cutoff:
                        continue
                    size = _size(hit)
                    if size > 10 * 1024 * 1024:  # ignore <10MB
                        hits.append({"path": str(hit), "size": size,
                                     "idle_days": int((time.time() - parent_mtime) / 86400)})
                except (PermissionError, OSError):
                    continue
    hits.sort(key=lambda h: -h["size"])
    for h in hits:
        db.add_pending(MODULE, f"Dev artifact: {h['path']}",
                       f"{h['size']/1e6:.0f} MB, project idle {h['idle_days']} days",
                       [h["path"]], h["size"])
    summary = {"count": len(hits), "total_gb": round(sum(h["size"] for h in hits) / 1e9, 2)}
    db.log_scan(MODULE, summary)
    return summary
