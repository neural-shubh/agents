"""Plain Gmail helpers for the Gmail agent (no MCP).

The agent imports these functions directly. When MCP is added later, it can simply
wrap these same functions, so nothing here needs to change.
"""
import base64
import json
import re
import time
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_FILE = Path(__file__).parent / "token.json"
SEND_LOG = Path(__file__).parent / ".send_log.json"
MAX_SENDS_PER_HOUR = 5
WRITE_SCOPES = {"https://www.googleapis.com/auth/gmail.modify", "https://mail.google.com/"}
# invisible padding that marketing/LinkedIn emails stuff into previews
_JUNK = re.compile("[\u034f\u200b-\u200f\u2060\ufeff\u00ad]")


# ------------------------------------------------------------------ auth
def get_service():
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))
    if not creds.valid:
        try:
            if not creds.refresh_token:
                raise RefreshError("no refresh token")
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
        except RefreshError:
            raise SystemExit(
                "Gmail token expired or revoked. Delete token.json and re-run gmail_auth.py."
            )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def token_scopes():
    try:
        return set(json.loads(TOKEN_FILE.read_text()).get("scopes", []))
    except Exception:
        return set()


def can_write():
    """True if the token allows drafts, sending and marking read."""
    return bool(token_scopes() & WRITE_SCOPES)


def my_address(service):
    return service.users().getProfile(userId="me").execute()["emailAddress"].lower()


# ------------------------------------------------------------------ parsing
def clean(text):
    return re.sub(r"\s+", " ", _JUNK.sub("", unescape(text))).strip()


def _header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            try:
                return str(make_header(decode_header(h["value"])))
            except Exception:
                return h["value"]
    return ""


class _Strip(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _html_to_text(html):
    p = _Strip()
    p.feed(html)
    return "".join(p.parts)


def _b64(data):
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def extract_text(payload):
    """Readable text body of a Gmail message payload (plain text preferred, HTML stripped)."""
    plain, html = [], []

    def walk(part):
        mime, body = part.get("mimeType", ""), part.get("body", {})
        if not part.get("filename"):
            if mime == "text/plain" and body.get("data"):
                plain.append(_b64(body["data"]))
            elif mime == "text/html" and body.get("data"):
                html.append(_b64(body["data"]))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    text = "\n".join(plain).strip() or _html_to_text("\n".join(html))
    text = _JUNK.sub("", text).replace("\r", "")
    lines = [re.sub(r"[ \t\f\v]+", " ", ln).strip() for ln in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ------------------------------------------------------------------ reading
def fetch_inbox(service, hours=24, max_results=100):
    """Inbox emails received in the last `hours` (excluding ones you sent), newest first.
    Lightweight dicts built from headers + snippet; no bodies."""
    since = int(time.time()) - hours * 3600
    query = f"in:inbox after:{since} -from:me"

    ids, token = [], None
    while len(ids) < max_results:
        res = service.users().messages().list(
            userId="me", q=query, maxResults=min(100, max_results - len(ids)),
            pageToken=token).execute()
        ids += [m["id"] for m in res.get("messages", [])]
        token = res.get("nextPageToken")
        if not token:
            break

    emails = []
    for mid in ids:
        msg = service.users().messages().get(
            userId="me", id=mid, format="metadata",
            metadataHeaders=["From", "Subject", "Date"]).execute()
        hs = msg.get("payload", {}).get("headers", [])
        name, addr = parseaddr(_header(hs, "From"))
        try:
            when = parsedate_to_datetime(_header(hs, "Date")).astimezone().strftime("%d %b %H:%M")
        except Exception:
            when = ""
        emails.append({
            "id": msg["id"],
            "thread_id": msg["threadId"],
            "sender_name": clean(name) or addr,
            "sender_addr": addr.lower(),
            "subject": clean(_header(hs, "Subject")) or "(no subject)",
            "snippet": clean(msg.get("snippet", "")),
            "labels": msg.get("labelIds", []),
            "time": when,
            "ts": int(msg.get("internalDate", "0")) / 1000,
        })
    return emails


def get_body_excerpt(service, message_id, limit=800):
    """First `limit` characters of one email's text body."""
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    return extract_text(msg["payload"])[:limit]


def get_thread_text(service, thread_id, max_msgs=4, per_msg=1500):
    """Last few messages of a conversation as [{from, date, text}], oldest first."""
    t = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    out = []
    for m in t.get("messages", [])[-max_msgs:]:
        hs = m["payload"]["headers"]
        out.append({"from": _header(hs, "From"), "date": _header(hs, "Date"),
                    "text": extract_text(m["payload"])[:per_msg]})
    return out


# ------------------------------------------------------------------ drafting
def _build_raw(to, subject, body, in_reply_to="", references=""):
    m = EmailMessage()  # raises ValueError if a header contains a newline (header injection)
    m["To"], m["Subject"] = to, subject
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
    if references:
        m["References"] = references
    m.set_content(body)
    return base64.urlsafe_b64encode(m.as_bytes()).decode()


def create_reply_draft(service, message_id, body):
    """Create a properly threaded reply draft in Gmail. Nothing is sent."""
    orig = service.users().messages().get(
        userId="me", id=message_id, format="metadata",
        metadataHeaders=["From", "Reply-To", "Subject", "Message-ID", "References"]).execute()
    hs = orig["payload"]["headers"]
    to = _header(hs, "Reply-To") or _header(hs, "From")
    subject = _header(hs, "Subject")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    msgid = _header(hs, "Message-ID")
    refs = f"{_header(hs, 'References')} {msgid}".strip()

    raw = _build_raw(to, subject, body, in_reply_to=msgid, references=refs)
    d = service.users().drafts().create(
        userId="me", body={"message": {"raw": raw, "threadId": orig["threadId"]}}).execute()
    return {"draft_id": d["id"], "to": to, "subject": subject}


def update_draft(service, draft_id, body):
    """Replace a draft's body, keeping recipient, subject and threading."""
    d = service.users().drafts().get(userId="me", id=draft_id, format="full").execute()
    msg = d["message"]
    hs = msg["payload"]["headers"]
    raw = _build_raw(_header(hs, "To"), _header(hs, "Subject"), body,
                     in_reply_to=_header(hs, "In-Reply-To"), references=_header(hs, "References"))
    payload = {"raw": raw}
    if msg.get("threadId"):
        payload["threadId"] = msg["threadId"]
    service.users().drafts().update(userId="me", id=draft_id, body={"message": payload}).execute()


def discard_draft(service, draft_id):
    service.users().drafts().delete(userId="me", id=draft_id).execute()


# ------------------------------------------------------------------ sending
def _recent_sends():
    try:
        stamps = json.loads(SEND_LOG.read_text())
    except Exception:
        stamps = []
    return [t for t in stamps if t > time.time() - 3600]


def send_draft(service, draft_id, live=False):
    """Send an existing draft. Without live=True this is a dry run and nothing is sent."""
    d = service.users().drafts().get(userId="me", id=draft_id, format="metadata").execute()
    hs = d["message"]["payload"]["headers"]
    info = {"to": _header(hs, "To"), "subject": _header(hs, "Subject")}

    if not live:
        return {"status": "dry_run", **info}
    recent = _recent_sends()
    if len(recent) >= MAX_SENDS_PER_HOUR:
        return {"status": "blocked", "reason": f"hourly send cap ({MAX_SENDS_PER_HOUR}) reached", **info}

    sent = service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
    SEND_LOG.write_text(json.dumps(recent + [time.time()]))
    return {"status": "sent", "message_id": sent.get("id"), **info}


def mark_read(service, message_ids):
    if message_ids:
        service.users().messages().batchModify(
            userId="me", body={"ids": message_ids, "removeLabelIds": ["UNREAD"]}).execute()