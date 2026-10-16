"""Plan commits, create the repo if needed, and push.

plan_node             groups files into commits and checks the target repo's state
route_after_plan      stops if the repo check failed
route_after_approval  sends the run to create_repo, push, or the end
create_repo_node      creates the repo (only after you chose private/public)
push_node             pushes. New or empty repo: commits go to main.
                      Existing repo with commits: commits go to a new branch
                      and a pull request is opened, main is never touched.

Safety rules built in:
  - Works in a temporary folder: the scanned project is never modified.
  - The token is sent via environment variables as an HTTP header, so it is
    never written into git config or the remote URL.
  - Error text is scrubbed of the token before it is shown.
  - Files deleted locally are NOT deleted from the remote; updates only add or change files.

Needs in .env (next to this file): GITHUB_TOKEN, GITHUB_USERNAME
Optional: GIT_AUTHOR_NAME, GIT_AUTHOR_EMAIL (commits only count toward your
contribution graph when the email belongs to your GitHub account).
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from github_api import (
    GitHubError, create_pull_request, create_repo, get_repo, load_settings, log_event, split_repo,
)
from state import AgentState, FileEntry

CONFIG_NAMES = {
    "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "pipfile",
    "environment.yml", ".gitignore", ".env.example", ".gitattributes",
}
CONFIG_EXTS = {".yaml", ".yml", ".toml", ".cfg", ".ini"}
DOC_EXTS = {".md", ".rst"}
CODE_EXTS = {".py", ".ipynb", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".sh", ".bat", ".ps1"}
DATA_EXTS = {".csv", ".tsv", ".json", ".xlsx", ".xls", ".parquet", ".db", ".sqlite", ".txt"}

# (category, commit message) in the order the commits are made
COMMIT_GROUPS = [
    ("config", "Add project configuration and dependencies"),
    ("code", "Add source code"),
    ("data", "Add data files"),
    ("other", "Add remaining project files"),
    ("docs", "Add documentation"),
]


class PushError(RuntimeError):
    pass


# ------------------------------------------------------------ planning ----

def _category(entry: FileEntry) -> str:
    name = Path(entry["path"]).name.lower()
    ext = entry["ext"]
    if name in CONFIG_NAMES or name.startswith("license") or ext in CONFIG_EXTS:
        return "config"
    if name.startswith("readme") or ext in DOC_EXTS:
        return "docs"
    if ext in CODE_EXTS:
        return "code"
    if ext in DATA_EXTS:
        return "data"
    return "other"


def plan_commits(manifest: list[FileEntry]) -> list[dict]:
    buckets: dict[str, list[str]] = {}
    for entry in manifest:
        buckets.setdefault(_category(entry), []).append(entry["path"])
    return [
        {"message": message, "files": sorted(buckets[cat])}
        for cat, message in COMMIT_GROUPS
        if buckets.get(cat)
    ]


# ------------------------------------------------------------ git helpers ----

def _remote_url(owner: str, name: str) -> str:
    base = os.environ.get("GITHUB_BASE_URL", "https://github.com").rstrip("/")
    return f"{base}/{owner}/{name}.git"


def _auth_header(username: str, token: str) -> str:
    return base64.b64encode(f"{username}:{token}".encode()).decode()


def _git_env(username: str, token: str) -> dict:
    env = dict(os.environ)
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {_auth_header(username, token)}",
    })
    return env


def _run_git(args: list[str], cwd: Path | None, env: dict, secrets: list[str]) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise PushError("git is not installed or not on PATH")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        for s in secrets:
            detail = detail.replace(s, "***")
        raise PushError(f"git {args[0]} failed: {detail}")
    return proc.stdout


def _remote_heads(url: str, env: dict, secrets: list[str]) -> str:
    """Branch list of the remote; retries briefly because a just-created repo can lag."""
    last: PushError | None = None
    for attempt in range(3):
        try:
            return _run_git(["ls-remote", "--heads", url], None, env, secrets)
        except PushError as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise last  # type: ignore[misc]


def _identity(username: str) -> tuple[str, str]:
    name = os.getenv("GIT_AUTHOR_NAME", "").strip() or username
    email = os.getenv("GIT_AUTHOR_EMAIL", "").strip()
    if not email:
        try:
            proc = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True)
            email = proc.stdout.strip() if proc.returncode == 0 else ""
        except FileNotFoundError:
            email = ""
    return name, email or f"{username}@users.noreply.github.com"


# ------------------------------------------------------------------ nodes ----

def plan_node(state: AgentState) -> dict:
    plan = plan_commits(state["file_manifest"])
    try:
        cfg = load_settings()
        owner, name = split_repo(state["target_repo"], cfg["username"])
        secrets = [cfg["token"], _auth_header(cfg["username"], cfg["token"])]
        info = get_repo(owner, name, cfg["token"])
        if info is None:
            status = "missing"
        else:
            env = _git_env(cfg["username"], cfg["token"])
            heads = _remote_heads(_remote_url(owner, name), env, secrets)
            status = "has_commits" if heads.strip() else "empty"
    except (GitHubError, PushError, KeyError) as exc:
        return {"error": f"Could not check the target repo: {exc}", "commit_plan": plan}

    params = dict(state.get("params") or {})
    params.update({
        "owner": owner,
        "name": name,
        "default_branch": (info or {}).get("default_branch", "main"),
    })
    if status == "has_commits":
        params["branch"] = f"agent/update-{datetime.now():%Y%m%d-%H%M%S}"
    return {"commit_plan": plan, "repo_status": status, "params": params}


def route_after_plan(state: AgentState) -> str:
    return "end" if state.get("error") else "approval"


def route_after_approval(state: AgentState) -> str:
    if "push" not in state.get("approved_actions", []):
        return "end"
    return "create_repo" if state.get("repo_status") == "missing" else "push"


def create_repo_node(state: AgentState) -> dict:
    log = list(state.get("op_log", []))
    try:
        cfg = load_settings()
        p = state["params"]
        vis = state.get("visibility")
        if vis not in ("private", "public"):
            raise GitHubError("Visibility must be 'private' or 'public'.")
        create_repo(p["owner"], p["name"], vis == "private", cfg["token"], cfg["username"])
        event = {"action": "create_repo", "ok": True, "repo": f"{p['owner']}/{p['name']}", "visibility": vis}
        log.append(event)
        log_event(event)
        return {"repo_status": "empty", "op_log": log, "error": None}
    except (GitHubError, KeyError) as exc:
        log.append({"action": "create_repo", "ok": False, "error": str(exc)})
        return {"error": f"Repo creation failed: {exc}", "op_log": log}


def route_after_create(state: AgentState) -> str:
    return "end" if state.get("error") else "push"


def push_node(state: AgentState) -> dict:
    if "push" not in state.get("approved_actions", []):
        return {}

    log = list(state.get("op_log", []))
    try:
        cfg = load_settings()
        secrets = [cfg["token"], _auth_header(cfg["username"], cfg["token"])]
        p = state["params"]
        owner, name = p["owner"], p["name"]
        url = _remote_url(owner, name)
        env = _git_env(cfg["username"], cfg["token"])
        root = Path(state["scan_summary"]["root"])
        plan = state["commit_plan"]
        author, email = _identity(cfg["username"])
        ident = ["-c", f"user.name={author}", "-c", f"user.email={email}"]

        try:
            heads = _remote_heads(url, env, secrets)
        except PushError as exc:
            raise PushError(f"Could not read {owner}/{name}. Check the repo name and the token's access. ({exc})")
        mode = "update" if heads.strip() else "new"
        expected = "update" if state.get("repo_status") == "has_commits" else "new"
        if mode != expected:
            raise PushError(f"{owner}/{name} changed since it was checked. Nothing was pushed; run again.")

        commits_made = 0
        pr_url = None
        branch = "main"

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            work = Path(tmp) / "repo"

            if mode == "new":
                work.mkdir()
                _run_git(["init"], work, env, secrets)
                _run_git(["symbolic-ref", "HEAD", "refs/heads/main"], work, env, secrets)
                _run_git(["remote", "add", "origin", url], work, env, secrets)
            else:
                branch = p["branch"]
                _run_git(["clone", "--depth", "1", url, str(work)], None, env, secrets)
                _run_git(["checkout", "-b", branch], work, env, secrets)

            for commit in plan:
                for rel in commit["files"]:
                    dest = work / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(root / rel, dest)
                _run_git(["add", "-A"], work, env, secrets)
                if not _run_git(["status", "--porcelain"], work, env, secrets).strip():
                    continue  # this group changed nothing
                message = commit["message"] if mode == "new" else commit["message"].replace("Add ", "Update ", 1)
                _run_git([*ident, "commit", "-m", message], work, env, secrets)
                commits_made += 1

            if commits_made:
                _run_git(["push", "-u", "origin", branch], work, env, secrets)

        result = {
            "repo_url": f"https://github.com/{owner}/{name}",
            "branch": branch,
            "commits": commits_made,
            "files": sum(len(c["files"]) for c in plan),
            "mode": mode,
            "pr_url": None,
        }

        if mode == "update":
            if commits_made == 0:
                result["note"] = "The repo already matches these files; nothing to update."
            else:
                body = "Opened by the GitHub agent.\n\n" + "\n".join(
                    f"- {c['message'].replace('Add ', 'Update ', 1)}: {', '.join(c['files'])}" for c in plan
                )
                try:
                    pr = create_pull_request(
                        owner, name, cfg["token"],
                        title=f"Update from agent ({commits_made} commit{'s' if commits_made != 1 else ''})",
                        head=branch, base=p.get("default_branch", "main"), body=body,
                    )
                    result["pr_url"] = pr.get("html_url")
                except GitHubError as exc:
                    result["note"] = (
                        f"Branch '{branch}' was pushed, but the pull request could not be opened "
                        f"(the token may need Pull requests: read and write). ({exc})"
                    )

        event = {"action": "push", "ok": True, "repo": f"{owner}/{name}", **result}
        log.append(event)
        log_event(event)
        return {"push_result": result, "op_log": log, "error": None}

    except (PushError, GitHubError, KeyError, OSError) as exc:
        log.append({"action": "push", "ok": False, "error": str(exc)})
        log_event({"action": "push", "ok": False, "error": str(exc)})
        return {"error": f"Push failed: {exc}", "op_log": log}