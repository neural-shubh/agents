"""Non-push actions: create a repo, open an issue or PR, archive or delete a repo.

prepare_action   validates the request, applies policy, builds the confirmation
confirm_action   pauses with interrupt() until you confirm (level set in policy.py)
execute_action   only runs if you confirmed; calls the GitHub API

Deleting is deliberately hard: the repo must not be protected, ALLOW_DELETE=1
must be set in .env, and you must type the exact owner/name.
"""

from __future__ import annotations

from langgraph.types import interrupt

from github_api import (
    GitHubError, archive_repo, create_issue, create_pull_request, create_repo,
    delete_repo, get_repo, load_settings, log_event, split_repo,
)
from policy import LEVELS, YES_WORDS, delete_enabled, is_protected
from state import AgentState

NEEDS_REPO = {"issue", "pr", "repo_create", "repo_archive", "repo_delete"}


def prepare_action(state: AgentState) -> dict:
    intent = state["intent"]
    p = dict(state.get("params") or {})
    try:
        cfg = load_settings()
        if intent in NEEDS_REPO and not state.get("target_repo"):
            raise GitHubError('Which repo? Say "repo NAME".')
        owner, name = split_repo(state["target_repo"], cfg["username"])
        full = f"{owner}/{name}"
        phrase = None

        if intent == "issue":
            if not p.get("title"):
                raise GitHubError("The issue needs a title.")
            summary = f'Open an issue in {full}: "{p["title"]}"' + (f"\n  body: {p['body']}" if p.get("body") else "")
        elif intent == "pr":
            if not p.get("title") or not p.get("head"):
                raise GitHubError("The pull request needs a title and a source branch.")
            base = p.get("base") or "the default branch"
            summary = f'Open a pull request in {full}: "{p["title"]}"  ({p["head"]} -> {base})'
        elif intent == "repo_create":
            vis = state.get("visibility")
            summary = f"Create repo {full}" + (f" as {vis}" if vis else "") + " (empty, nothing is pushed)"
        elif intent in ("repo_archive", "repo_delete"):
            if is_protected(name):
                raise GitHubError(f"{full} is in PROTECTED_REPOS. The agent will not touch it.")
            if intent == "repo_delete" and not delete_enabled():
                raise GitHubError(
                    "Deleting is switched off. Set ALLOW_DELETE=1 in .env to enable it. "
                    "Archiving (read-only, reversible) is available without it."
                )
            phrase = full if intent == "repo_delete" else name
            verb = "PERMANENTLY DELETE" if intent == "repo_delete" else "Archive (make read-only)"
            summary = f"{verb} {full}"
        else:
            raise GitHubError(f"Unsupported action: {intent}")
    except (GitHubError, KeyError) as exc:
        return {"error": str(exc)}

    params = {**p, "owner": owner, "name": name}
    return {
        "params": params,
        "action_plan": {"intent": intent, "summary": summary, "level": LEVELS[intent], "phrase": phrase},
    }


def route_after_prepare(state: AgentState) -> str:
    return "end" if state.get("error") else "confirm"


def confirm_action(state: AgentState) -> dict:
    plan = state["action_plan"]
    answer = str(interrupt({
        "kind": "action_confirm",
        "summary": plan["summary"],
        "level": plan["level"],
        "phrase": plan["phrase"],
    })).strip()

    visibility = state.get("visibility")
    level = plan["level"]
    if level == "typed":
        approved = answer == plan["phrase"]
    elif level == "visibility":
        if answer.lower() in {"private", "public"}:
            visibility, approved = answer.lower(), True
        else:
            approved = answer.lower() in YES_WORDS and visibility in {"private", "public"}
    else:
        approved = answer.lower() in YES_WORDS

    log = list(state.get("op_log", []))
    log.append({"action": "confirm", "intent": plan["intent"], "approved": approved})
    return {
        "approved_actions": ["execute"] if approved else [],
        "visibility": visibility,
        "op_log": log,
    }


def route_after_confirm(state: AgentState) -> str:
    return "execute" if "execute" in state.get("approved_actions", []) else "end"


def execute_action(state: AgentState) -> dict:
    if "execute" not in state.get("approved_actions", []):
        return {}

    intent = state["intent"]
    p = state["params"]
    owner, name = p["owner"], p["name"]
    full = f"{owner}/{name}"
    log = list(state.get("op_log", []))

    try:
        cfg = load_settings()
        token = cfg["token"]

        if intent == "issue":
            r = create_issue(owner, name, token, p["title"], p.get("body", ""))
            result = {"summary": f"Issue #{r.get('number')} opened in {full}", "url": r.get("html_url")}
        elif intent == "pr":
            base = p.get("base")
            if not base:
                info = get_repo(owner, name, token)
                if info is None:
                    raise GitHubError(f"Could not find {full}.")
                base = info.get("default_branch", "main")
            r = create_pull_request(owner, name, token, p["title"], p["head"], base, p.get("body", ""))
            result = {"summary": f"Pull request #{r.get('number')} opened in {full}", "url": r.get("html_url")}
        elif intent == "repo_create":
            vis = state.get("visibility")
            if vis not in ("private", "public"):
                raise GitHubError("Visibility must be 'private' or 'public'.")
            r = create_repo(owner, name, vis == "private", token, cfg["username"])
            result = {"summary": f"Created {vis} repo {full}", "url": r.get("html_url")}
        elif intent == "repo_archive":
            archive_repo(owner, name, token)
            result = {"summary": f"Archived {full} (now read-only; you can unarchive it in the repo settings)", "url": f"https://github.com/{full}"}
        elif intent == "repo_delete":
            delete_repo(owner, name, token)
            result = {"summary": f"Deleted {full}", "url": None}
        else:
            raise GitHubError(f"Unsupported action: {intent}")

        event = {"action": intent, "ok": True, "repo": full, **{k: v for k, v in result.items() if k != "summary"}}
        log.append(event)
        log_event(event)
        return {"action_result": result, "op_log": log, "error": None}

    except (GitHubError, KeyError) as exc:
        log.append({"action": intent, "ok": False, "error": str(exc)})
        log_event({"action": intent, "ok": False, "repo": full, "error": str(exc)})
        return {"error": f"{intent} failed: {exc}", "op_log": log}
