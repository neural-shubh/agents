"""LLM fallback for ambiguous classification + learned-rule cache.

Deterministic rules are authoritative. The LLM is a consultant for unknowns,
and its verdicts become cached rules so it is asked less over time.
The LLM can NEVER mark something tier 0/safe if it matches a protected path —
that enforcement lives in core.safety.
"""
from __future__ import annotations

import os
from pathlib import Path

from core import config, db
from core.safety import is_protected

# How to talk to the local model: by default uses `opencode run` CLI.
# Falls back to a conservative default (Tier 2 = ask human) if unavailable.


def classify(path: str, context: dict) -> int:
    """Return tier 1/2 for an unknown path. Checks the learned rule cache first."""
    p = Path(path)
    if is_protected(p):
        return 2  # protected paths always escalate to human
    pattern = f"ext:{p.suffix.lower()}" if p.is_file() else f"dir:{p.name.lower()}"
    rule = db.get_rule(pattern)
    if rule:
        with db.connect() as c:
            c.execute("UPDATE rules SET hits = hits + 1 WHERE id = ?", (rule["id"],))
        return int(rule["tier"])

    tier = _ask_llm(path, context)
    db.add_rule(pattern, tier, source="llm")
    return tier


def _ask_llm(path: str, context: dict) -> int:
    prompt = (
        "You are a cautious Windows sys-admin AI. Classify this path into a tier:\n"
        "1 = safe to auto-quarantine (regenerable junk/cache/temp)\n"
        "2 = needs explicit human approval before any action\n"
        f"Path: {path}\nContext: {context}\n"
        "When unsure, answer 2. Reply with ONLY the digit 1 or 2."
    )
    try:
        import subprocess
        out = subprocess.run(["opencode", "run", prompt], capture_output=True,
                             text=True, timeout=60)
        text = (out.stdout or "").strip()
        if "1" in text and "2" not in text:
            return 1
    except Exception:
        pass
    return 2  # conservative default
