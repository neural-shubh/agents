"""Duplicate detection: size pre-filter, partial hash, then full hash. Tier 2."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

from core import config, db
from core.safety import is_protected

MODULE = "duplicates"

SCAN_ROOTS = [Path.home() / "Downloads", Path.home() / "Documents",
              Path.home() / "Desktop", Path.home() / "Pictures", Path.home() / "Videos"]


def _hash(path: Path, bytes_to_read: int | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        if bytes_to_read:
            h.update(f.read(bytes_to_read))
        else:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    return h.hexdigest()


def scan() -> dict:
    cfg = config.load()
    min_size = cfg["duplicate_min_size_mb"] * 1024 * 1024
    by_size: dict[int, list[Path]] = defaultdict(list)
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for f in root.rglob("*"):
            try:
                if f.is_file() and f.stat().st_size >= min_size and not is_protected(f):
                    by_size[f.stat().st_size].append(f)
            except (PermissionError, OSError):
                continue
    groups = []
    for size, files in by_size.items():
        if len(files) < 2:
            continue
        partial: dict[str, list[Path]] = defaultdict(list)
        for f in files:
            try:
                partial[_hash(f, 64 * 1024)].append(f)
            except OSError:
                continue
        for paths in partial.values():
            if len(paths) < 2:
                continue
            full: dict[str, list[Path]] = defaultdict(list)
            for f in paths:
                try:
                    full[_hash(f)].append(f)
                except OSError:
                    continue
            for digest, dupes in full.items():
                if len(dupes) >= 2:
                    groups.append({"hash": digest[:12], "size": size,
                                   "paths": [str(p) for p in dupes],
                                   "wasted": size * (len(dupes) - 1)})
    groups.sort(key=lambda g: -g["wasted"])
    for g in groups:
        # Keep first copy; propose quarantining the rest
        db.add_pending(MODULE, f"Duplicate set ({len(g['paths'])} copies, "
                       f"{g['wasted']/1e6:.0f} MB wasted)",
                       "Keep newest copy; quarantine duplicates. Hash: " + g["hash"],
                       g["paths"][1:], g["wasted"])
    summary = {"duplicate_sets": len(groups),
               "wasted_gb": round(sum(g["wasted"] for g in groups) / 1e9, 2)}
    db.log_scan(MODULE, summary)
    return summary
