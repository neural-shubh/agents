"""Unattended nightly digest, meant to be run by a scheduler (cron / Task Scheduler).

    python nightly.py              # last 24h, saves digests/YYYY-MM-DD.md
    python nightly.py --no-llm

Read-only: it never sends, labels or modifies anything. LLM results are cached, so when you
open agent.py afterwards the digest is already sorted and appears instantly.
If something breaks (expired token, Gmail down...), a "DIGEST FAILED" file is written in place
of the digest, and details go to logs/nightly.log. Exit code is 0 on success, 1 on failure.
"""
import argparse
import logging
import os
import sys
from collections import Counter
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import digest
import gmail_tools as gt
import triage

BASE = Path(__file__).parent


def setup_logging():
    (BASE / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[RotatingFileHandler(BASE / "logs" / "nightly.log", maxBytes=500_000,
                                      backupCount=3, encoding="utf-8"),
                  logging.StreamHandler()])
    return logging.getLogger("nightly")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Unattended nightly Gmail digest")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--max", type=int, default=100)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--model", default=os.getenv("OLLAMA_MODEL", "llama3.2:3b"))
    args = ap.parse_args(argv)

    digest.safe_console()
    log = setup_logging()
    out_dir = BASE / "digests"
    out_dir.mkdir(exist_ok=True)
    day_file = out_dir / f"{datetime.now():%Y-%m-%d}.md"

    try:
        log.info("Nightly digest starting (last %sh)", args.hours)
        service = gt.get_service()
        rules = triage.load_rules()
        use_llm = digest.resolve_llm(rules, args.no_llm)
        log.info("LLM triage: %s", "on" if use_llm else "off (rules only)")

        emails, hidden = digest.run_triage(service, rules, args.hours, args.max, use_llm, args.model)
        text = digest.format_digest(emails, args.hours, hidden)
        day_file.write_text(text + "\n", encoding="utf-8")
        digest.save_digest(emails)

        # counts only: email content is never written to the log
        log.info("Done: %s, hidden=%d, saved %s", dict(Counter(e["category"] for e in emails)),
                 hidden, day_file.name)
        print(text)
        return 0
    except (Exception, SystemExit) as exc:  # get_service() exits with a message on a bad token
        log.exception("Nightly digest FAILED")
        day_file.write_text(f"DIGEST FAILED at {datetime.now():%d %b %Y %H:%M}: {exc}\n"
                            "See logs/nightly.log for details.\n", encoding="utf-8")
        return 1


if __name__ == "__main__":
    sys.exit(main())