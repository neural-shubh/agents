"""Gmail digest (read-only: nothing is sent, labelled or modified).

Usage:
    python digest.py                   # last 24 hours
    python digest.py --hours 48
    python digest.py --no-llm          # rules only, no Ollama needed
    python digest.py --show-skipped    # also list ignored emails (handy for tuning rules.json)
    python digest.py --include-handled # also show emails you already replied to / marked done
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import gmail_tools as gt
import state
import triage

SECTIONS = [
    ("needs_reply", "NEEDS A REPLY"),
    ("worth_reading", "WORTH READING"),
    ("linkedin", "LINKEDIN (invites and messages)"),
    ("unsorted", "UNSORTED (could not classify, check manually)"),
]


def safe_console():
    """Windows consoles (cp1252) crash on emoji/Hindi subjects; replace unprintable chars."""
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass


def resolve_llm(rules, no_llm=False):
    if no_llm or not rules.get("llm_enabled", True):
        return False
    if not triage.llm_available():
        print("Note: Ollama is not running, so using rules only. Uncertain emails go to UNSORTED.")
        return False
    return True


def run_triage(service, rules, hours, max_n, use_llm, model, include_handled=False):
    """Fetch + triage. Returns (emails, hidden_count). Emails you've already handled are
    hidden; LLM results are cached so repeat runs are instant."""
    emails = gt.fetch_inbox(service, hours, max_n)

    hidden = 0
    if not include_handled:
        handled = state.load_handled()
        kept = [e for e in emails if not state.is_handled(e, handled)]
        hidden, emails = len(emails) - len(kept), kept

    cache, dirty = state.load_cache(), False
    for e in emails:
        res = triage.rule_triage(e, rules)
        if res is None and e["id"] in cache:
            c = cache[e["id"]]
            res = {"category": c["category"], "reason": c["reason"], "summary": c["summary"]}
        elif res is None and use_llm:
            try:
                res = triage.llm_triage(e, gt.get_body_excerpt(service, e["id"]), model)
                cache[e["id"]] = {**res, "cached_at": time.time()}
                dirty = True
            except Exception as exc:
                res = {"category": "unsorted", "reason": f"LLM failed: {exc}", "summary": None}
        if res is None:
            res = {"category": "unsorted", "reason": "no rule matched", "summary": None}
        e.update(res)
    if dirty:
        state.save_cache(cache)
    return emails, hidden


def group_and_number(emails):
    """Group by category and give every shown email a number (used by 'reply 3' etc.)."""
    groups = {key: [e for e in emails if e["category"] == key] for key, _ in SECTIONS}
    n = 0
    for key, _ in SECTIONS:
        for e in groups[key]:
            n += 1
            e["number"] = n
    return groups


def format_digest(emails, hours, hidden=0, show_skipped=False):
    groups = group_and_number(emails)
    skipped = [e for e in emails if e["category"] == "ignore"]

    out = [f"\n=== Gmail digest, {datetime.now():%d %b %Y %H:%M} "
           f"(last {hours}h, {len(emails)} emails) ==="]
    for key, title in SECTIONS:
        if not groups[key]:
            continue
        out.append(f"\n{title} ({len(groups[key])})")
        for e in groups[key]:
            tag = "  [handled]" if e.get("handled") else ""
            out.append(f" {e['number']:>2}. {e['sender_name']} - {e['subject'][:70]}  [{e['time']}]{tag}")
            out.append(f"      {e['summary'] or e['snippet'][:110]}")
            out.append(f"      ({e['reason']})")

    reasons = Counter(e["reason"] for e in skipped)
    detail = ", ".join(f"{r} {c}" for r, c in reasons.most_common(4))
    out.append(f"\nSKIPPED: {len(skipped)}" + (f" ({detail})" if detail else ""))
    if show_skipped:
        out += [f"   - {e['sender_name']} | {e['subject'][:60]}  ({e['reason']})" for e in skipped]
    if hidden:
        out.append(f"HIDDEN: {hidden} already handled (use --include-handled to show them)")
    if not emails:
        out.append("Nothing new. Quiet inbox.")
    return "\n".join(out)


def print_digest(emails, hours, hidden=0, show_skipped=False):
    print(format_digest(emails, hours, hidden, show_skipped))


def save_digest(emails):
    out = Path(__file__).parent / "digests"
    out.mkdir(exist_ok=True)
    (out / "last_digest.json").write_text(json.dumps(emails, indent=2), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Gmail digest (read-only)")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--max", type=int, default=100)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--show-skipped", action="store_true")
    ap.add_argument("--include-handled", action="store_true")
    ap.add_argument("--model", default=os.getenv("OLLAMA_MODEL", "llama3.2:3b"))
    args = ap.parse_args(argv)
    safe_console()

    service = gt.get_service()
    rules = triage.load_rules()
    use_llm = resolve_llm(rules, args.no_llm)

    print(f"Fetching emails from the last {args.hours}h...")
    emails, hidden = run_triage(service, rules, args.hours, args.max, use_llm, args.model,
                                args.include_handled)
    print_digest(emails, args.hours, hidden, args.show_skipped)
    save_digest(emails)


if __name__ == "__main__":
    main()