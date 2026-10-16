"""Shared state for the GitHub agent graph.

Every node reads from and returns a partial update to AgentState.
Nested shapes (FileEntry, SkippedEntry, ScanSummary) are plain TypedDicts so
they serialize cleanly when LangGraph checkpoints the state.
"""

from __future__ import annotations

from typing import TypedDict


class FileEntry(TypedDict):
    path: str  # relative to the scanned root, always forward slashes
    size: int  # bytes
    ext: str  # lowercase extension including the dot, or "" if none


class SkippedEntry(TypedDict):
    path: str  # relative path of the skipped file or folder
    reason: str  # human-readable reason
    files: int  # how many files this entry covers (1 for a single file)


class ScanSummary(TypedDict):
    root: str
    files_to_push: int
    total_size: int  # bytes across files_to_push
    skipped_files: int  # total files skipped, including those inside skipped folders
    skipped: list[SkippedEntry]
    warnings: list[str]


class AgentState(TypedDict, total=False):
    # Input
    user_prompt: str
    intent: str  # push | issue | pr | repo_create | repo_admin
    folder_path: str

    # Target
    target_repo: str | None  # "name" or "owner/name"
    visibility: str | None  # "public" | "private"; only used when a new repo is created
    push_mode: str  # "pr" (default for existing repos) | "direct"
    dest_path: str  # folder inside the repo to put the files in ("" = repo root)
    repo_mode: str  # create | empty | update
    default_branch: str
    changes: dict  # {"added": [...], "modified": [...], "unchanged": [...]}
    notice: str  # a plain message, e.g. "nothing to push"

    # Scan + audit
    file_manifest: list[FileEntry]
    scan_summary: ScanSummary
    project_type: str  # streamlit | notebook | hf_space | pages | library | unknown
    findings: list[dict]  # {check, severity, suggestion}

    # Approval + execution
    approved_actions: list[str]
    commit_plan: list[dict]  # grouped files + commit message
    op_log: list[dict]  # every action the agent took
    push_result: dict | None  # repo_url, branch, commits, files, mode, pr_url
    repo_status: str  # missing | empty | has_commits
    params: dict  # request details: title, body, head, base, owner, name, branch, default_branch
    action_plan: dict  # what a non-push action will do, and how it must be confirmed
    action_result: dict | None  # outcome of a non-push action

    # Errors
    error: str | None