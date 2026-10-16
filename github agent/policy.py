"""Which actions need which kind of confirmation, and which repos are off limits.

Levels:
  confirm     you answer yes/no after seeing what will happen
  visibility  you answer 'private' or 'public' (which also approves)
  typed       you must type the exact phrase shown (repo name) to proceed

Settings in .env (all optional):
  PROTECTED_REPOS=repo-one,repo-two   never archived or deleted, whatever you type
  ALLOW_DELETE=1                      deletion is OFF unless this is set
"""

from __future__ import annotations

import os

LEVELS = {
    "issue": "confirm",
    "pr": "confirm",
    "repo_create": "visibility",
    "repo_archive": "typed",
    "repo_delete": "typed",
}

YES_WORDS = {"y", "yes", "approve", "approved", "ok", "push"}


def protected_repos() -> set[str]:
    raw = os.getenv("PROTECTED_REPOS", "")
    return {r.strip().lower() for r in raw.split(",") if r.strip()}


def is_protected(name: str) -> bool:
    return name.lower() in protected_repos()


def delete_enabled() -> bool:
    return os.getenv("ALLOW_DELETE", "").strip().lower() in {"1", "true", "yes"}
