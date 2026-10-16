"""Safety layer: hardcoded protected paths + quarantine (move before final delete)."""
from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from . import config, db

# These can NEVER be touched regardless of what the LLM or rules say.
PROTECTED_PREFIXES = [
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\ProgramData\\Microsoft\\Windows",
    "C:\\$Recycle.Bin",
    "C:\\System Volume Information",
    "C:\\Recovery",
    "C:\\Boot",
]

PROTECTED_NAMES = {
    "ntldr", "bootmgr", "pagefile.sys", "hiberfil.sys", "swapfile.sys",
    "desktop.ini", "ntuser.dat",
}


def is_protected(path: str | Path) -> bool:
    p = str(path)
    pl = p.lower()
    if Path(pl).name.lower() in PROTECTED_NAMES:
        return True
    # Normalise separator for prefix check
    norm = p.replace("/", "\\")
    norm_l = norm.lower()
    for pre in PROTECTED_PREFIXES:
        pre_l = pre.lower()
        if norm_l == pre_l or norm_l.startswith(pre_l + "\\"):
            return True
    return False


class SafetyError(PermissionError):
    pass


def quarantine(path: str | Path, module: str, tier: int, dry_run: bool | None = None) -> bool:
    """Move a file/folder into the quarantine area instead of deleting it."""
    if dry_run is None:
        dry_run = bool(config.load().get("dry_run", True))
    src = Path(path)
    if is_protected(src):
        raise SafetyError(f"Refusing to touch protected path: {src}")
    if not src.exists():
        return False
    size = _size_of(src)
    if dry_run:
        db.log_action(module, tier, "quarantine", str(src), size, status="dry_run")
        return True
    dest = config.QUARANTINE_DIR / f"{int(time.time())}_{src.name}_{uuid.uuid4().hex[:6]}"
    shutil.move(str(src), str(dest))
    db.log_action(module, tier, "quarantine", f"{src} -> {dest}", size, status="quarantined")
    return True


def purge_expired() -> int:
    """Permanently delete quarantined items older than the retention period. Returns bytes freed."""
    cfg = config.load()
    max_age = cfg.get("quarantine_retention_days", 7) * 86400
    freed = 0
    for item in config.QUARANTINE_DIR.iterdir():
        try:
            if time.time() - item.stat().st_mtime > max_age:
                freed += _size_of(item)
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
        except OSError:
            continue
    if freed:
        db.log_action("quarantine", 1, "purge_expired", str(config.QUARANTINE_DIR),
                      freed, status="done")
    return freed


def purge_module(module: str) -> int:
    """Immediately delete quarantined items belonging to a given module
    (e.g. 'junk'). Other modules' items remain restorable. Returns bytes freed."""
    freed = 0
    with db.connect() as c:
        rows = c.execute(
            "SELECT path FROM actions WHERE status='quarantined' AND module=?",
            (module,)).fetchall()
    for row in rows:
        try:
            dest = row["path"].split(" -> ")[-1]
            item = Path(dest)
            if not item.exists() or config.QUARANTINE_DIR not in item.parents:
                continue
            freed += _size_of(item)
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        except OSError:
            continue
    if freed:
        db.log_action(module, 1, "purge_module", f"module={module}", freed, status="done")
    return freed


def restore(quarantined_name: str, original_path: str) -> bool:
    src = config.QUARANTINE_DIR / quarantined_name
    if is_protected(original_path):
        raise SafetyError(f"Cannot restore to protected path: {original_path}")
    if not src.exists():
        return False
    Path(original_path).parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), original_path)
    db.log_action("quarantine", 1, "restore", f"{src} -> {original_path}", status="done")
    return True


def _size_of(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except OSError:
        return 0
