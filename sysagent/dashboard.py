"""Streamlit dashboard: approvals, health, storage trends, action log, settings."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import streamlit as st

from core import config, db
from core.safety import purge_expired, purge_module, quarantine

st.set_page_config(page_title="SysAgent", page_icon="🛡️", layout="wide")
db.init()

cfg = config.load()

st.title("🛡️ SysAgent Dashboard")

if cfg.get("dry_run"):
    st.warning("DRY-RUN mode is ON — no real deletion is happening. "
               "Toggle in Settings when ready.", icon="⚠️")

page = st.sidebar.radio("Page", ["Overview", "Approvals", "Health",
                                 "Storage Trends", "Action Log", "Settings"])


def latest_scan(module: str) -> dict | None:
    with db.connect() as c:
        row = c.execute(
            "SELECT summary FROM scans WHERE module=? ORDER BY ts DESC LIMIT 1",
            (module,)).fetchone()
    return json.loads(row["summary"]) if row else None


if page == "Overview":
    st.header("System Overview")
    st.caption("Live metrics — refreshed each time you open this page")
    cols = st.columns(4)
    store = latest_scan("storage")
    junk_s = latest_scan("junk")
    if store:
        total_free = sum(d["free_gb"] for d in store["drives"])
        cols[0].metric("Total Free Space", f"{total_free:.0f} GB")
        if store.get("threshold_breaches"):
            st.error(f"Low disk: {store['threshold_breaches']}")
    # Live CPU/RAM/battery straight from the OS, not a stale scan
    import psutil
    cols[1].metric("CPU", f"{psutil.cpu_percent(interval=0.5)}%")
    cols[2].metric("RAM", f"{psutil.virtual_memory().percent}%")
    batt = psutil.sensors_battery()
    if batt:
        cols[3].metric("Battery", f"{batt.percent}%"
                       + (" 🔌" if batt.power_plugged else ""))
    if junk_s:
        st.info(f"Last junk cleanup: {junk_s}")
    pend = db.list_pending()
    st.metric("Pending approvals", len(pend))

    st.divider()
    st.subheader("🚀 Reclaim Space Now")
    st.caption("Cleans junk and purges it from quarantine immediately — junk is "
               "regenerable, so it doesn't need the 7-day hold. "
               "Files from OTHER modules (large files, duplicates) stay restorable.")
    if st.button("Reclaim Space", type="primary"):
        with st.spinner("Cleaning... (can take a few minutes)"):
            from modules import junk, storage as stg
            junk_summary = junk.clean(dry_run=cfg.get("dry_run", True))
            junk_purged = purge_module("junk")
            expired_purged = purge_expired()
            stg.scan()
        freed_gb = (junk_purged + expired_purged) / 1e9
        st.success(f"✅ {junk_summary['auto_quarantined']} junk items cleaned, "
                   f"**{freed_gb:.2f} GB actually freed** "
                   f"({'dry-run' if junk_summary['dry_run'] else 'space is back on your drive'})")
        if junk_summary["pending_approval"]:
            st.info(f"{junk_summary['pending_approval']} items need approval (see Approvals)")
        st.rerun()

elif page == "Approvals":
    st.header("Pending Approvals")
    items = db.list_pending()
    if not items:
        st.success("Nothing waiting for review 🎉")
    for it in items:
        with st.expander(f"[{it['module']}] {it['title']} — {it['size_bytes']/1e6:.1f} MB"):
            st.write(it["description"])
            paths = json.loads(it["paths_json"])
            if paths:
                st.code("\n".join(paths))
            if it["llm_reason"]:
                st.caption(f"LLM: {it['llm_reason']}")
            c1, c2 = st.columns(2)
            if c1.button("✅ Approve (quarantine)", key=f"a{it['id']}"):
                ok = 0
                for p in paths:
                    try:
                        ok += quarantine(p, it["module"], 2,
                                         dry_run=cfg.get("dry_run", True))
                    except Exception as e:
                        st.error(f"{p}: {e}")
                db.set_pending_status(it["id"], "approved")
                st.success(f"Quarantined {ok} item(s)")
                if hasattr(st, "rerun"):
                    st.rerun()
            if c2.button("❌ Reject", key=f"r{it['id']}"):
                db.set_pending_status(it["id"], "rejected")
                if hasattr(st, "rerun"):
                    st.rerun()

elif page == "Health":
    st.header("Latest Health Report")
    hl = latest_scan("health")
    if hl:
        st.json(hl)
    else:
        st.info("No health report yet — the daemon generates one daily.")

elif page == "Storage Trends":
    st.header("Disk Usage Over Time")
    with db.connect() as c:
        rows = c.execute(
            "SELECT ts, drive, free_bytes, used_bytes FROM disk_snapshots ORDER BY ts"
        ).fetchall()
    if rows:
        df = pd.DataFrame([dict(r) for r in rows])
        df["time"] = pd.to_datetime(df["ts"], unit="s")
        df["free_gb"] = df["free_bytes"] / 1e9
        for drive in df["drive"].unique():
            d = df[df["drive"] == drive]
            st.line_chart(d, x="time", y="free_gb")
            st.caption(f"Free GB over time — {drive}")
    else:
        st.info("No snapshots yet.")

elif page == "Action Log":
    st.header("Action Log")
    with db.connect() as c:
        rows = c.execute(
            "SELECT ts, module, tier, action, path, size_bytes, status FROM actions "
            "ORDER BY ts DESC LIMIT 200").fetchall()
    if rows:
        df = pd.DataFrame([dict(r) for r in rows])
        df["time"] = pd.to_datetime(df["ts"], unit="s")
        st.dataframe(df.drop(columns=["ts"]), use_container_width=True)
    else:
        st.info("No actions logged yet.")

elif page == "Settings":
    st.header("Settings")
    nc = dict(cfg)
    nc["dry_run"] = st.toggle("Dry-run mode (nothing is actually deleted)",
                              value=cfg.get("dry_run", True))
    nc["storage_check_interval_hours"] = st.number_input(
        "Storage check interval (hours)", 1, 24, cfg["storage_check_interval_hours"])
    nc["disk_free_threshold_percent"] = st.slider(
        "Low-disk alert threshold (%)", 5, 25, cfg["disk_free_threshold_percent"])
    nc["quarantine_retention_days"] = st.slider(
        "Quarantine retention (days)", 1, 30, cfg["quarantine_retention_days"])
    nc["large_file_min_mb"] = st.number_input(
        "Large-file threshold (MB)", 100, 10000, cfg["large_file_min_mb"])
    nc["duplicate_min_size_mb"] = st.number_input(
        "Duplicate min size (MB)", 10, 5000, cfg["duplicate_min_size_mb"])
    if st.button("Save settings"):
        config.save(nc)
        st.success("Saved. Restart the daemon to apply scheduling changes.")
    if st.button("Purge expired quarantine now"):
        freed = purge_expired()
        st.info(f"Freed {freed/1e6:.1f} MB")
