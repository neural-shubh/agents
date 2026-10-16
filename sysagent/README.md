# SysAgent 🛡️

A semi-autonomous AI housekeeping agent for Windows. It runs quietly in the
background, keeps your disk healthy, and **always asks before touching
anything that matters** — with a Streamlit dashboard as your control room.

## What it does

| Capability | Details |
|---|---|
| 💾 Storage analysis | Disk snapshots every 2h; threshold alerts; trend graphs |
| 🧹 Junk cleanup | Temp files, caches, crash dumps — auto-quarantined (Tier 1) |
| 📦 Large-file detection | Big files idle for months → approval queue |
| 🔁 Duplicate detection | Size → partial-hash → full-hash, proposes keeping 1 copy |
| 🧑‍💻 Dev cleanup | `node_modules`, `.venv`, build artifacts of idle projects |
| 🚀 Startup analysis | Lists everything in your Run registry keys |
| 🗑️ Unused apps | Flags large installed apps with their uninstall commands |
| ⬆️ Update checks | `winget upgrade` scan with notify + approval |
| 🏥 Health reports | Daily CPU/RAM/battery/top-process reports |
| 🔔 Notifications | Windows toasts for events and digests |
| 🧠 LLM fallback | Unknown file types are classified by a local LLM ([opencode](https://github.com/sst/opencode)); answers become cached rules so it asks less over time |

## The safety model

Rules are the immune system; the LLM is only a consultant.

- **Protected paths** (`C:\Windows`, `Program Files`, pagefile, etc.) are
  hard-coded — the LLM *cannot* override them.
- **Nothing is ever deleted immediately.** Files go to a **quarantine**
  folder and are auto-purged after 7 days (configurable) — so every action
  is reversible. Junk (regenerable caches) can be purged instantly via the
  "Reclaim Space" button.
- **Two tiers:** Tier 1 (obvious junk) runs hands-free; Tier 2 (large files,
  duplicates, apps, updates) waits for your approval in the dashboard.
- **Every action is logged** to SQLite with timestamp, size, and status.

## Install & run

```powershell
git clone <your-repo-url>
cd sysagent
pip install -r requirements.txt
```

Start the background daemon:

```powershell
python run_agent.py          # or start_agent.bat (headless)
```

Open the dashboard:

```powershell
python -m streamlit run dashboard.py    # or start_dashboard.bat
```

Then visit **http://localhost:8501**.

> The agent ships with `"dry_run": true` in `config.json` — it simulates
> everything first. Flip it off in the dashboard's **Settings** page when
> you trust it.

## Manual commands

```powershell
python run_agent.py --scan-storage    # one-off storage analysis
python run_agent.py --scan-junk       # one-off junk cleanup
python run_agent.py --weekly          # large files, duplicates, dev artifacts, apps
python run_agent.py --health          # instant health report
```

## Default schedule

| Task | Frequency |
|---|---|
| Storage check | Every 2 hours |
| Junk cleanup + updates + health + quarantine purge | Daily |
| Large files / duplicates / dev cleanup / apps / startup | Weekly (Sunday) |
| Low-disk event trigger | Immediate cleanup when free space < 10% or < 20 GB |

All timings and thresholds live in `config.json` (editable in the dashboard).

## Project layout

```
sysagent/
├── run_agent.py          # entry point (daemon + manual scans)
├── dashboard.py          # Streamlit UI
├── config.json           # all settings/thresholds
├── core/                 # daemon, db (SQLite), safety/quarantine, notify
├── modules/              # storage, junk, duplicates, largefiles, devclean,
│                         # startup, uninstall, updates, health
├── brain/                # LLM classification + learned-rule cache
└── data/                 # gitignored: agent.db, quarantine/, logs/
```

## Requirements

- Windows 10/11
- Python 3.11+
- [winget](https://learn.microsoft.com/windows/package-manager/) (for update checks)
- Optional: `opencode` CLI for LLM classification of unknown files

## License

MIT — see [LICENSE](LICENSE).
