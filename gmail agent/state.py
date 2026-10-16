"""Small local state, kept in ./state/ (never leaves your machine):
  handled.json       threads you replied to / marked done / skipped, so they stop reappearing
  triage_cache.json  LLM triage results per email, so the digest is instant after a nightly run
"""
import json
import os
import time
from pathlib import Path

STATE_DIR = Path(__file__).parent / "state"
HANDLED = STATE_DIR / "handled.json"
CACHE = STATE_DIR / "triage_cache.json"
CACHE_TTL = 14 * 24 * 3600
HANDLED_TTL = 60 * 24 * 3600


def _load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(path, data):
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a crash can't leave a half-written file


# ---- handled threads
def load_handled():
    return _load(HANDLED)


def mark_handled(thread_id):
    data = load_handled()
    data[thread_id] = time.time()
    cutoff = time.time() - HANDLED_TTL
    _save(HANDLED, {k: v for k, v in data.items() if v > cutoff})


def is_handled(email, handled):
    """Hidden if its thread was handled AFTER this email arrived. A newer message in the
    same thread (a fresh reply from the sender) is NOT hidden."""
    t = handled.get(email["thread_id"])
    return t is not None and email["ts"] <= t


# ---- LLM triage cache
def load_cache():
    cutoff = time.time() - CACHE_TTL
    return {k: v for k, v in _load(CACHE).items() if v.get("cached_at", 0) > cutoff}


def save_cache(cache):
    _save(CACHE, cache)