"""Junk detection & Tier-1 cleanup: temp files, safe caches, old crash dumps."""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from core import config, db
from core.safety import is_protected, quarantine

MODULE = "junk"

# Tier 1: these are always safe; apps regenerate them.
TIER1_DIRS = [
    Path(tempfile.gettempdir()),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Temp",
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Temp",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "INetCache",
    Path(os.environ.get("LOCALAPPDATA", "")) / "CrashDumps",
]

# Cache dirs are Tier 1 by pattern (regenerable).
TIER1_PATTERNS = ["__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                  "thumbs.db", ".ds_store"]

# Anything older than this in Tier-1 folders is cleaned.
MIN_AGE_DAYS = 2


def _iter_files(root: Path):
    try:
        for entry in root.rglob("*"):
            if entry.is_file():
                yield entry
    except (PermissionError, OSError):
        return


def find_junk() -> list[dict]:
    """Return list of {'path', 'size', 'age_days', 'tier'} candidates."""
    results, seen = [], set()
    cutoff = time.time() - MIN_AGE_DAYS * 86400
    for folder in TIER1_DIRS:
        if not folder.exists():
            continue
        for f in _iter_files(folder):
            try:
                if f.stat().st_mtime < cutoff and not is_protected(f):
                    key = str(f).lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    st = f.stat()
                    results.append({"path": str(f), "size": st.st_size,
                                    "age_days": (time.time() - st.st_mtime) / 86400,
                                    "tier": 1})
            except OSError:
                continue
    # Pattern-based junk anywhere under user profile (top two levels)
    for base in (Path.home() / "AppData",):
        if not base.exists():
            continue
        for pat in TIER1_PATTERNS:
            for hit in base.rglob(pat):
                if hit.is_dir() and not is_protected(hit):
                    size = sum(f.stat().st_size for f in hit.rglob("*") if f.is_file())
                    if size:
                        results.append({"path": str(hit), "size": size,
                                        "age_days": 0, "tier": 1})
    return results


def clean(dry_run: bool | None = None) -> dict:
    cfg = config.load()
    if dry_run is None:
        dry_run = cfg.get("dry_run", True)
    candidates = find_junk()
    auto, pending, freed = 0, 0, 0
    for item in candidates:
        if item["tier"] == 1 and cfg.get("tier1_auto_approve", True):
            try:
                if quarantine(item["path"], MODULE, 1, dry_run):
                    freed += item["size"]
                    auto += 1
            except (PermissionError, OSError) as e:
                db.log_action(MODULE, 1, "quarantine", item["path"], item["size"],
                              status="error", detail=str(e))
        else:
            db.add_pending(MODULE, f"Quarantine {Path(item['path']).name}",
                           f"Size: {item['size']/1e6:.1f} MB", [item["path"]],
                           item["size"])
            pending += 1
    summary = {"candidates": len(candidates), "auto_quarantined": auto,
               "pending_approval": pending, "bytes_recovered": freed,
               "dry_run": dry_run}
    db.log_scan(MODULE, summary)
    return summary
