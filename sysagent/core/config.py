"""Central configuration loader."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
QUARANTINE_DIR = DATA_DIR / "quarantine"
DB_PATH = DATA_DIR / "agent.db"
LOG_DIR = DATA_DIR / "logs"

_cache: dict | None = None


def load() -> dict:
    global _cache
    if _cache is None:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache


def save(cfg: dict) -> None:
    global _cache
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    _cache = cfg


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
