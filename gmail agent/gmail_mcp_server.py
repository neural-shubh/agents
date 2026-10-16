"""Gmail MCP server built on FastMCP.

Tools: list_emails, read_email, read_thread, daily_digest,
       create_draft, reply_to_email, list_drafts, send_draft

Safety: DRY_RUN (default true) blocks all sending. Replies are saved as
drafts until you set DRY_RUN=false in .env.

Run:  python gmail_mcp_server.py        (stdio transport)
"""
import base64
import os
import re
import sys
import time
from email.message import EmailMessage
from functools import lru_cache
from html import unescape
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import FastMCP

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))          # so `gmail_auth` imports from any cwd
load_dotenv(BASE / ".env")

from gmail_auth import get_service  # noqa: E402

DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() != "false"
MAX_DIGEST = int(os.getenv("MAX_EMAILS_PER_DIGEST", "50"))
BODY_LIMIT = 6000                       # chars of body returned per message

AUTOMATED_HINTS = ("noreply", "no-reply", "donotreply", "do-not-reply",
                   "notifications@", "notification@", "mailer-daemon",
                   "newsletter", "updates@")

mcp = FastMCP("gmail")


# ----------------------------------------------------------------- helpers
@lru_cache(maxsize=1)
def _svc():
    return get_service()


def _clean(value: str) -> str:
    """Collapse whitespace/newlines so values are safe to use as headers."""
    return " ".join((value or "").split())


def _headers(msg: dict) -> dict:
    return {h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])}


def _summary(msg: dict) -> dict:
    h = _headers(msg)
    sender = h.get("from", "")
    return {
        "id": msg["id"],
        "thread_id": msg["threadId"],
        "from": sender,
        "subject": h.get("subject", "(no subject)"),
        "date": h.get("date", ""),
        "unread": "UNREAD" in msg.get("labelIds", []),
        "likely_automated": any(k in sender.lower() for k in AUTOMATED_HINTS),
        "snippet": unescape(msg.get("snippet", "")),
    }


def _fetch_summaries(query: str, max_results: int) -> list[dict]:
    svc = _svc()
    resp = svc.users().messages().list(
        userId="me", q=query, maxResults=max(1, min(max_results, 100))
    ).execute()
    out = []
    for m in resp.get("messages", []):
        msg = svc.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        out.append(_summary(msg))
    return out


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
        "utf-8", errors="replace")


def _extract_body(payload: dict) -> tuple[str, list[str]]:
    """Return (plain-text body, attachment filenames)."""
    plain, html, files = [], [], []

    def walk(part: dict):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if part.get("filename"):
            files.append(part["filename"])
        elif data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            html.append(_decode(data))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    if plain:
        return "\n".join(plain).strip(), files
    if html:
        text = "\n".join(html)
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", text)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
        text = unescape(re.sub(r"<[^>]+>", "", text))
        return re.sub(r"\n{3,}", "\n\n", text).strip(), files
    return "", files


def _full_message(message_id: str) -> dict:
    msg = _svc().users().messages().get(
        userId="me", id=message_id, format="full").execute()
    body, files = _extract_body(msg.get("payload", {}))
    out = _summary(msg)
    out["to"] = _headers(msg).get("to", "")
    out["cc"] = _headers(msg).get("cc", "")
    out["attachments"] = files
    out["body"] = body[:BODY_LIMIT] + (
        "\n...[truncated]" if len(body) > BODY_LIMIT else "")
    return out


def _to_raw(msg: EmailMessage) -> str:
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _build_message(to: str, subject: str, body: str, cc: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = _clean(to)
    if cc:
        msg["Cc"] = _clean(cc)
    msg["Subject"] = _clean(subject)
    msg.set_content(body)
    return msg


def _create_draft(msg: EmailMessage, thread_id: str | None = None) -> dict:
    message = {"raw": _to_raw(msg)}
    if thread_id:
        message["threadId"] = thread_id
    return _svc().users().drafts().create(
        userId="me", body={"message": message}).execute()


def _blocked() -> dict:
    return {"status": "blocked_dry_run",
            "detail": "DRY_RUN is on, so nothing was sent. "
                      "Set DRY_RUN=false in .env and restart the server to allow sending."}


# ------------------------------------------------------------------- tools
@mcp.tool
def list_emails(query: str = "in:inbox", max_results: int = 20) -> list[dict]:
    """List emails matching a Gmail search query (same syntax as the Gmail
    search box), newest first. Examples: 'is:unread', 'from:alice newer_than:2d',
    'in:inbox -category:promotions'. Returns id, thread_id, from, subject, date,
    unread flag, likely_automated flag and a snippet. Use read_email for bodies."""
    return _fetch_summaries(query, max_results)


@mcp.tool
def read_email(message_id: str) -> dict:
    """Read one email in full: headers, plain-text body (truncated to ~6000
    chars) and attachment filenames."""
    return _full_message(message_id)


@mcp.tool
def read_thread(thread_id: str) -> list[dict]:
    """Read every message in a thread, oldest first. Use this before drafting
    a reply so the draft has the full conversation context."""
    thread = _svc().users().threads().get(
        userId="me", id=thread_id, format="minimal").execute()
    return [_full_message(m["id"]) for m in thread.get("messages", [])]


@mcp.tool
def daily_digest(hours: int = 24, include_read: bool = True) -> dict:
    """Collect inbox emails from the last N hours for an end-of-day review.
    Skips Promotions/Social/Forums tabs and mail you sent. Each item has a
    likely_automated flag (noreply/notification senders) so you can rank real
    people above bots. Rank the items by importance and say which need a reply."""
    since = int(time.time() - hours * 3600)
    query = (f"in:inbox after:{since} -from:me "
             "-category:promotions -category:social -category:forums")
    if not include_read:
        query += " is:unread"
    emails = _fetch_summaries(query, MAX_DIGEST)
    return {
        "window_hours": hours,
        "count": len(emails),
        "from_people": sum(not e["likely_automated"] for e in emails),
        "emails": emails,
    }


@mcp.tool
def create_draft(to: str, subject: str, body: str, cc: str = "") -> dict:
    """Save a new email as a Gmail draft (does NOT send). Returns the draft id."""
    draft = _create_draft(_build_message(to, subject, body, cc))
    return {"status": "draft_created", "draft_id": draft["id"]}


@mcp.tool
def reply_to_email(message_id: str, body: str, send: bool = False) -> dict:
    """Reply to an email, keeping it in the same thread with correct
    In-Reply-To/References headers. By default saves a draft. With send=True it
    sends immediately, but only if DRY_RUN is off; otherwise it falls back to
    saving a draft."""
    original = _svc().users().messages().get(
        userId="me", id=message_id, format="metadata",
        metadataHeaders=["From", "Reply-To", "Subject", "Message-ID", "References"],
    ).execute()
    h = _headers(original)

    subject = _clean(h.get("subject", ""))
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    msg = _build_message(h.get("reply-to") or h.get("from", ""), subject, body)
    mid = _clean(h.get("message-id", ""))
    if mid:
        msg["In-Reply-To"] = mid
        msg["References"] = _clean(f"{h.get('references', '')} {mid}")

    draft = _create_draft(msg, thread_id=original["threadId"])
    if not send:
        return {"status": "draft_created", "draft_id": draft["id"], "to": msg["To"]}
    if DRY_RUN:
        return {**_blocked(), "status": "saved_as_draft_dry_run",
                "draft_id": draft["id"], "to": msg["To"]}
    sent = _svc().users().drafts().send(
        userId="me", body={"id": draft["id"]}).execute()
    return {"status": "sent", "message_id": sent.get("id"), "to": msg["To"]}


@mcp.tool
def list_drafts(max_results: int = 10) -> list[dict]:
    """List current drafts (id, to, subject, snippet) so they can be reviewed
    before sending."""
    svc = _svc()
    resp = svc.users().drafts().list(
        userId="me", maxResults=max(1, min(max_results, 50))).execute()
    out = []
    for d in resp.get("drafts", []):
        msg = svc.users().drafts().get(
            userId="me", id=d["id"], format="metadata").execute()["message"]
        h = _headers(msg)
        out.append({"draft_id": d["id"], "to": h.get("to", ""),
                    "subject": h.get("subject", "(no subject)"),
                    "snippet": unescape(msg.get("snippet", ""))})
    return out


@mcp.tool
def send_draft(draft_id: str) -> dict:
    """Send an existing draft. Blocked while DRY_RUN is on."""
    if DRY_RUN:
        return _blocked()
    sent = _svc().users().drafts().send(
        userId="me", body={"id": draft_id}).execute()
    return {"status": "sent", "message_id": sent.get("id")}


if __name__ == "__main__":
    mcp.run()