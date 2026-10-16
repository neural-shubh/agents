"""SQLite persistence layer: action log, approvals, scan results, learned rules."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

from . import config


@contextmanager
def connect():
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with connect() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                module TEXT NOT NULL,
                tier INTEGER NOT NULL,
                action TEXT NOT NULL,
                path TEXT,
                size_bytes INTEGER DEFAULT 0,
                status TEXT NOT NULL,          -- done | quarantined | pending | approved | rejected | error | dry_run
                detail TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS pending (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                module TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                paths_json TEXT DEFAULT '[]',
                size_bytes INTEGER DEFAULT 0,
                llm_reason TEXT DEFAULT '',
                status TEXT DEFAULT 'pending'  -- pending | approved | rejected
            );
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                module TEXT NOT NULL,
                summary TEXT
            );
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                pattern TEXT NOT NULL UNIQUE,
                tier INTEGER NOT NULL,
                source TEXT DEFAULT 'llm',
                hits INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS disk_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                drive TEXT NOT NULL,
                total_bytes INTEGER,
                used_bytes INTEGER,
                free_bytes INTEGER
            );
            """
        )


def log_action(module: str, tier: int, action: str, path: str = "",
               size_bytes: int = 0, status: str = "done", detail: str = "") -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO actions (ts, module, tier, action, path, size_bytes, status, detail) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (time.time(), module, tier, action, path, size_bytes, status, detail),
        )


def add_pending(module: str, title: str, description: str, paths: list[str],
                size_bytes: int = 0, llm_reason: str = "") -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO pending (ts, module, title, description, paths_json, size_bytes, llm_reason) "
            "VALUES (?,?,?,?,?,?,?)",
            (time.time(), module, title, description, json.dumps(paths), size_bytes, llm_reason),
        )
        return cur.lastrowid


def list_pending(status: str = "pending") -> list[sqlite3.Row]:
    with connect() as c:
        return c.execute(
            "SELECT * FROM pending WHERE status = ? ORDER BY ts DESC", (status,)
        ).fetchall()


def set_pending_status(item_id: int, status: str) -> None:
    with connect() as c:
        c.execute("UPDATE pending SET status = ? WHERE id = ?", (status, item_id))


def log_scan(module: str, summary: dict) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO scans (ts, module, summary) VALUES (?,?,?)",
            (time.time(), module, json.dumps(summary)),
        )


def snapshot_disks(rows: list[tuple]) -> None:
    with connect() as c:
        c.executemany(
            "INSERT INTO disk_snapshots (ts, drive, total_bytes, used_bytes, free_bytes) VALUES (?,?,?,?,?)",
            rows,
        )


def get_rule(pattern: str) -> sqlite3.Row | None:
    with connect() as c:
        return c.execute("SELECT * FROM rules WHERE pattern = ?", (pattern,)).fetchone()


def add_rule(pattern: str, tier: int, source: str = "llm") -> None:
    with connect() as c:
        c.execute(
            "INSERT OR IGNORE INTO rules (ts, pattern, tier, source) VALUES (?,?,?,?)",
            (time.time(), pattern, tier, source),
        )
