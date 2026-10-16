"""Thin GitHub REST client plus settings and an action log.

Uses only the standard library. The token is sent as a Bearer header and is
scrubbed from any error message before it is shown.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).with_name(".env")
LOG_PATH = Path(__file__).with_name("agent_log.jsonl")


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def load_settings() -> dict:
    load_dotenv(ENV_PATH)
    token = os.getenv("GITHUB_TOKEN", "").strip()
    username = os.getenv("GITHUB_USERNAME", "").strip()
    if not token or not username:
        raise GitHubError(f"GITHUB_TOKEN and GITHUB_USERNAME must both be set in {ENV_PATH}")
    return {"token": token, "username": username}


def split_repo(target: str, username: str) -> tuple[str, str]:
    """'name' -> (username, name); 'owner/name' -> (owner, name)."""
    target = target.strip().strip("/")
    if "/" in target:
        owner, name = target.split("/", 1)
    else:
        owner, name = username, target
    return owner, name.removesuffix(".git")


def log_event(event: dict) -> None:
    """Append one line to agent_log.jsonl. Never contains secrets."""
    record = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
    try:
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass


def gh(method: str, path: str, token: str, body: dict | None = None):
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-agent",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            message = json.loads(raw).get("message", raw)
        except ValueError:
            message = raw
        raise GitHubError(f"GitHub API {exc.code}: {message}".replace(token, "***"), status=exc.code)
    except urllib.error.URLError as exc:
        raise GitHubError(f"Could not reach GitHub: {exc.reason}")


# ---- operations -------------------------------------------------------------

def get_repo(owner: str, name: str, token: str) -> dict | None:
    """Return repo info, or None if it does not exist (or the token cannot see it)."""
    try:
        return gh("GET", f"/repos/{owner}/{name}", token)
    except GitHubError as exc:
        if exc.status == 404:
            return None
        raise


def create_repo(owner: str, name: str, private: bool, token: str, username: str) -> dict:
    body = {"name": name, "private": private, "auto_init": False}
    path = "/user/repos" if owner.lower() == username.lower() else f"/orgs/{owner}/repos"
    try:
        return gh("POST", path, token, body)
    except GitHubError as exc:
        if exc.status == 422:
            raise GitHubError(
                f"Could not create {owner}/{name}: the name may already exist (the token may not be "
                f"able to see it), or the name is invalid. ({exc})", status=422)
        if exc.status in (403, 404):
            raise GitHubError(
                f"Could not create {owner}/{name}: the token probably lacks the Administration "
                f"(read and write) permission, or doesn't cover all repositories. ({exc})", status=exc.status)
        raise


def create_issue(owner: str, name: str, token: str, title: str, body: str = "") -> dict:
    return gh("POST", f"/repos/{owner}/{name}/issues", token, {"title": title, "body": body})


def create_pull_request(owner: str, name: str, token: str, title: str, head: str, base: str, body: str = "") -> dict:
    return gh("POST", f"/repos/{owner}/{name}/pulls", token,
              {"title": title, "head": head, "base": base, "body": body})


def archive_repo(owner: str, name: str, token: str) -> dict:
    return gh("PATCH", f"/repos/{owner}/{name}", token, {"archived": True})


def delete_repo(owner: str, name: str, token: str) -> None:
    gh("DELETE", f"/repos/{owner}/{name}", token)
