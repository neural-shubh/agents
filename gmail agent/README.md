# Gmail Agent

A personal Gmail assistant. It sorts your inbox into what needs a reply, what is worth reading and what is noise, prepares a digest before you open it, and helps you reply: an LLM drafts, you review and revise, and nothing is sent until you type `send`.

Local-first: it runs on your own machine with a local LLM (Ollama). Your email never goes to a third-party AI service.

```
Gmail  ->  fetch  ->  triage (rules first, local LLM for the rest)  ->  digest
                                                                          |
                              you: read / reply / write / skip / done  <--+
                                          |
                  LLM draft (or your own)  ->  you revise  ->  type "send"
```

## Safety design

- **Nothing is sent without you typing exactly `send`.** Email content can never trigger a send.
- **Safe mode by default.** `python agent.py` saves drafts to Gmail but `send` is a dry run. Sending for real needs `--live`.
- **Placeholders block sending.** The LLM is told never to invent your availability or decisions; it writes `[your availability]` instead, and `send` refuses until you fill it in.
- **Hourly send cap:** 5 per hour (`MAX_SENDS_PER_HOUR` in `gmail_tools.py`).
- **No delete tools.** The OAuth scope (`gmail.modify`) cannot permanently delete mail, and the code never trashes anything.
- **Untrusted content.** Email text is passed to the LLM as data with instructions to ignore any commands inside it; reply headers are built so a malicious address can't inject extra headers.
- **LinkedIn notifications and no-reply senders can't be replied to** (the agent says so instead of drafting).

## Requirements

- Python 3.10+
- A Google account and a Google Cloud project with the Gmail API enabled
- [Ollama](https://ollama.com) with a model pulled (optional: without it, triage uses rules only and `reply N` is unavailable; `write N` still works)

## Setup

1. **Google Cloud Console**
   - Create a project and enable the **Gmail API**.
   - **OAuth consent screen:** add yourself as a test user, then set publishing status to **In production**. If you leave it in *Testing*, your login expires every 7 days.
   - **Credentials:** create an *OAuth client ID* of type *Desktop app*, download the JSON and save it as `credentials.json` in this folder.
2. **Install**
   ```
   python -m venv venv
   venv\Scripts\activate            # Mac/Linux: source venv/bin/activate
   pip install -r requirements.txt
   ```
3. **Log in once:** run your auth script (`python gmail_auth.py`). A browser opens; approve access. This creates `token.json`. The script must request the scope `https://www.googleapis.com/auth/gmail.modify` (read-only access disables replying). If Google warns "hasn't verified this app", click *Advanced* then *Go to (app)*: it is your own app.
4. **Local LLM (optional but recommended):**
   ```
   ollama pull llama3.2:3b
   ```
   Use another model with `--model name` or the `OLLAMA_MODEL` setting (see *Settings: `.env`* below).
5. **Settings (optional):** `cp .env.example .env` (Windows: `copy .env.example .env`) and edit the copy.

## Usage

**Digest (read-only)**
```
python digest.py                     # last 24 hours
python digest.py --hours 168         # last week
python digest.py --no-llm            # rules only, no Ollama needed
python digest.py --show-skipped      # also list ignored emails (for tuning rules.json)
python digest.py --include-handled   # include emails you already replied to / marked done
```

**Interactive session**
```
python agent.py                      # safe mode: send is a dry run
python agent.py --live               # send really sends
```

| Command | What it does |
|---|---|
| `read N` | show the full email |
| `reply N` | LLM drafts a reply; you review it |
| `write N` | you write the reply (finish with a line containing only `.`) |
| `done N` | mark as read and hide from future digests |
| `skip N` | hide from future digests (changes nothing in Gmail) |
| `list` / `help` / `quit` | |

At the draft prompt: `send`, `discard`, `manual` (rewrite it yourself), or type what to change in plain English ("make it shorter", "say I'm free at 5pm"). Drafts are saved in Gmail's Drafts folder and threaded correctly. Greeting and sign-off are added by the code, not the model.

**Nightly digest (unattended)**
```
python nightly.py
```
Writes `digests/YYYY-MM-DD.md`, caches the LLM's results (so `agent.py` opens instantly afterwards) and logs to `logs/nightly.log` (counts only, never email content). If it fails, a `DIGEST FAILED` file is written instead and the exit code is 1. It never sends or changes anything.

Schedule it (the scripts find their own files, so the working directory doesn't matter):
- Linux (cron): `0 21 * * * /path/to/venv/bin/python /path/to/gmail-agent/nightly.py`
- Windows: `schtasks /Create /SC DAILY /ST 21:00 /TN "GmailDigest" /TR "\"C:\path\venv\Scripts\python.exe\" \"C:\path\nightly.py\""`

## Settings: `.env`

`.env` is optional and holds only machine-specific settings; **no secrets**: Gmail credentials live in `credentials.json` / `token.json`. Copy `.env.example` to `.env` and edit it. `.env` is git-ignored; `.env.example` is safe to commit. Real environment variables override `.env`, and empty values are ignored.

| Setting | Default | Purpose |
|---|---|---|
| `OLLAMA_MODEL` | `llama3.2:3b` | model for triage and drafting (also `--model`) |
| `OLLAMA_HOST` | `http://localhost:11434` | where Ollama runs (change if it is on another machine) |
| `DASHBOARD_TOKEN` | auto-generated | dashboard access token (paused feature) |
| `DASHBOARD_ALLOWED_HOSTS` | none | hostnames the dashboard may be reached by (paused feature) |

## Configuration: `rules.json`

Rules run before the LLM, in this order (first match wins):

1. `ignore_senders` always skipped
2. LinkedIn: invites and messages kept (section for a future LinkedIn agent), the rest skipped (`linkedin_signal_patterns`)
3. `vip_senders` never missed, go straight to *Needs a reply*
4. Gmail's own Promotions / Social / Forums tabs (`ignore_gmail_categories`) skipped
5. `important_keywords` go to *Worth reading*
6. `ignore_keywords` skipped
7. Anything left goes to the LLM, or to *Unsorted* if no LLM is available

Senders can be a full address (`a@b.com`) or a domain (`b.com`). **Never add a public provider like `gmail.com` as a domain**: it would match everyone using it. Other keys: `llm_enabled`, `my_name`, `reply_style`, `reply_signoff`. The file must stay valid JSON (double quotes, no trailing commas). Tune it with `python digest.py --hours 168 --no-llm --show-skipped`.

## Files

| File | Purpose |
|---|---|
| `agent.py` | interactive session: digest, read, draft, revise, send |
| `digest.py` | digest generation and display (also runnable on its own) |
| `nightly.py` | unattended nightly run for schedulers |
| `triage.py` | rules engine and LLM classification |
| `drafting.py` | LLM reply drafting, placeholder detection |
| `gmail_tools.py` | plain Gmail functions (fetch, draft, send, mark read) |
| `state.py` | local state: handled threads, LLM result cache, stats |
| `rules.json` | your settings and sender rules |
| `config.py` | loads `.env` into the environment (no extra dependency) |
| `.env.example` | demo settings file: copy to `.env` |
| `.gitignore` | keeps secrets and local data out of git |
| `gmail_auth.py` | one-time login (creates `token.json`) |
| `dashboard.py`, `web/` | **paused** optional web dashboard (needs `requirements-dashboard.txt`) |
| `gmail_mcp_server.py` | **paused** FastMCP version of the Gmail tools, to be wrapped around `gmail_tools.py` later |

## Local data and privacy

Everything stays on your machine: `token.json` and `credentials.json` (**these give access to your inbox: never share or commit them**), `state/` (handled threads, cached LLM results, stats), `digests/`, `logs/`, `.send_log.json`. Add all of these, plus `venv/`, `.env`, `client_secret*.json` and `__pycache__/`, to `.gitignore`. Don't keep the project in a cloud-synced folder (OneDrive, Dropbox).

## Troubleshooting

| Problem | Fix |
|---|---|
| `403 Gmail API has not been used in project...` | Enable the Gmail API in Google Cloud Console (the error contains the link), wait a few minutes. |
| "Your token is read-only" | Set the scope to `gmail.modify` in your auth script, delete `token.json`, log in again. |
| Token expired or revoked / stops working after ~7 days | Publish the consent screen (*In production*), remove the app at myaccount.google.com/permissions, delete `token.json`, log in again. |
| "Ollama is not running" | Start Ollama. Without it the digest uses rules only. |
| Draft says something wrong or unsure | Small models do this: tell it what to change, use `manual`, or try a larger model with `--model`. |
| Crash on emoji / non-English subjects | Already handled (unprintable characters become `?`); update to the latest `digest.py`. |

## Status and ideas

Working: digest, nightly runner, interactive replies (dry-run and live), handled-thread tracking, result caching.

Paused: the web dashboard, the MCP wrapper (to return when this joins the multi-agent setup).

Ideas: phone push of the nightly digest (e.g. Telegram), auto-replies for trusted low-risk senders, reply-all, attachment reading, calendar integration, a larger LLM once it runs on a dedicated server.
