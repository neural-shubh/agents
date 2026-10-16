"""Loads ./.env into the environment. No extra dependency.

Real environment variables always win over .env, and empty values count as "not set".
Imported first by triage.py, so every script picks the settings up automatically.
"""
import os
from pathlib import Path


def load_env(path=None):
    path = Path(path) if path else Path(__file__).parent / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]
        else:
            value = value.split(" #")[0].strip()  # trailing comment
        if key and value:
            os.environ.setdefault(key, value)


load_env()