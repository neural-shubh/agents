"""Human-in-the-loop step for pushes: the graph pauses here until you answer.

approval_node pauses with interrupt(). When you answer, the graph resumes with
Command(resume=answer) (same thread_id) and this node restarts from the top,
this time with interrupt() returning your answer. Keep everything before
interrupt() free of side effects.

What the answer means depends on the repo:
  existing repo  -> 'yes' approves
  new repo       -> 'private' or 'public' approves AND picks the visibility
                    ('yes' also works if you already said which in your request)
"""

from __future__ import annotations

from langgraph.types import interrupt

from policy import YES_WORDS
from state import AgentState


def route_after_audit(state: AgentState) -> str:
    """Secrets found -> stop. Nothing may be pushed until they are fixed."""
    blockers = [f for f in state.get("findings", []) if f["severity"] == "block"]
    return "end" if blockers else "plan"


MODE_TEXT = {
    "missing": "NEW repo (will be created, then pushed to main)",
    "empty": "empty repo (commits go to main)",
    "has_commits": "existing repo (commits go to a new branch and a pull request is opened; main is not touched)",
}


def approval_node(state: AgentState) -> dict:
    summary = state["scan_summary"]
    findings = state.get("findings", [])
    status = state.get("repo_status", "empty")
    params = state.get("params") or {}

    payload = {
        "kind": "push_approval",
        "question": "Push these files?",
        "repo": state.get("target_repo"),
        "mode": MODE_TEXT.get(status, status),
        "branch": params.get("branch"),
        "needs_visibility": status == "missing",
        "visibility": state.get("visibility"),
        "root": summary["root"],
        "files_to_push": summary["files_to_push"],
        "total_size": summary["total_size"],
        "skipped_files": summary["skipped_files"],
        "commit_plan": [
            {"message": c["message"], "files": len(c["files"])} for c in state.get("commit_plan", [])
        ],
        "must_fix": [f["message"] for f in findings if f["severity"] == "must"],
        "should_fix_count": sum(1 for f in findings if f["severity"] == "should"),
    }

    answer = str(interrupt(payload)).strip().lower()  # <- the graph pauses here

    visibility = state.get("visibility")
    if status == "missing":
        if answer in {"private", "public"}:
            visibility, approved = answer, True
        else:
            approved = answer in YES_WORDS and visibility in {"private", "public"}
    else:
        approved = answer in YES_WORDS

    log = list(state.get("op_log", []))
    log.append({"action": "approval", "approved": approved})
    return {
        "approved_actions": ["push"] if approved else [],
        "visibility": visibility,
        "op_log": log,
    }