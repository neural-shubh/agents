"""Background daemon: schedules scans, watches disk thresholds, runs approved actions."""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import schedule

from core import config, db
from core.notify import toast
from core.safety import purge_expired
from modules import (devclean, duplicates, health, junk, largefiles,
                     startup, storage, uninstall, updates)

LOG: logging.Logger


def _setup_logging() -> None:
    global LOG
    config.ensure_dirs()
    LOG = logging.getLogger("sysagent")
    LOG.setLevel(logging.INFO)
    fh = logging.FileHandler(config.LOG_DIR / "daemon.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(fh)
    LOG.addHandler(logging.StreamHandler())


def _run_safe(name: str, fn) -> None:
    try:
        LOG.info("running %s", name)
        result = fn()
        LOG.info("%s done: %s", name, str(result)[:300])
    except Exception:
        LOG.exception("%s failed", name)


def daily() -> None:
    cfg = config.load()
    junk_summary = _run_safe_ret("junk.clean", lambda: junk.clean(dry_run=cfg.get("dry_run", True)))
    _run_safe("updates.scan", updates.scan)
    _run_safe("quarantine.purge", purge_expired)
    _run_safe("health.report", health.report)
    pending = len(db.list_pending())
    if junk_summary:
        toast("SysAgent daily report",
              f"Cleaned {junk_summary['bytes_recovered']/1e6:.0f} MB junk "
              f"({'dry-run' if junk_summary['dry_run'] else 'quarantined'}). "
              f"{pending} items awaiting your approval.")


def weekly() -> None:
    _run_safe("largefiles.scan", largefiles.scan)
    _run_safe("duplicates.scan", duplicates.scan)
    _run_safe("devclean.scan", devclean.scan)
    _run_safe("startup.scan", startup.scan)
    _run_safe("uninstall.scan", uninstall.scan)
    toast("SysAgent weekly report",
          f"Weekly scan complete. {len(db.list_pending())} items await approval.")


def tick_storage() -> None:
    summary = _run_safe_ret("storage.scan", storage.scan)
    if summary and summary.get("threshold_breaches"):
        LOG.warning("disk threshold breached: %s — triggering junk cleanup", summary["threshold_breaches"])
        toast("SysAgent: low disk space",
              f"Free space below threshold on: "
              f"{', '.join(b['drive'] for b in summary['threshold_breaches'])}. Running cleanup.")
        cfg = config.load()
        _run_safe("junk.clean", lambda: junk.clean(dry_run=cfg.get("dry_run", True)))


def _run_safe_ret(name, fn):
    try:
        LOG.info("running %s", name)
        r = fn()
        LOG.info("%s done", name)
        return r
    except Exception:
        LOG.exception("%s failed", name)
        return None


def run() -> None:
    _setup_logging()
    db.init()
    cfg = config.load()
    LOG.info("sysagent daemon starting (dry_run=%s)", cfg.get("dry_run"))

    schedule.every(cfg["storage_check_interval_hours"]).hours.do(tick_storage)
    schedule.every().day.at(f"{cfg['junk_cleanup_daily_hour']:02d}:00").do(daily)
    schedule.every().day.at(f"{cfg['updates_check_daily_hour']:02d}:00").do(
        lambda: _run_safe("updates.scan", updates.scan))
    day = cfg.get("weekly_scan_day", "sunday")
    hour = f"{cfg['weekly_scan_hour']:02d}:00"
    getattr(schedule.every(), day).at(hour).do(weekly)

    # Catch-up run at startup
    _run_safe("startup.storage", storage.scan)
    _run_safe("startup.health", health.report)

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    run()
