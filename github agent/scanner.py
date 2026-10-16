"""Folder scanner: decides which files are candidates for pushing.

Read-only. Never modifies, moves, or deletes anything in the scanned folder.

Key behaviours:
- Prunes ignored folders (venv, node_modules, .git, ...) without walking into
  them, so a 19,000-file virtualenv costs almost nothing to skip.
- Detects virtualenvs by their pyvenv.cfg file, so it works whatever the
  folder is named.
- Skips likely secrets (.env, private keys, credentials) and reports them.
- Skips files over GitHub's 100 MB limit and warns on files over 50 MB.
- Does not follow symlinks.

Run directly for a quick report:
    python scanner.py "path/to/folder"
"""

from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path

from state import AgentState, FileEntry, ScanSummary, SkippedEntry



IGNORED_DIRS = {
    ".git", ".hg", ".svn",
    "venv", ".venv", "env", "virtualenv",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".ipynb_checkpoints",
    "node_modules",
    ".idea", ".vscode",
    "dist", "build", ".tox", ".cache",
}
IGNORED_DIR_PATTERNS = ["*.egg-info"]

IGNORED_FILE_PATTERNS = ["*.pyc", "*.pyo", ".DS_Store", "Thumbs.db", "desktop.ini"]

# Files that usually contain credentials. Skipped and reported, never pushed.
SECRET_FILE_PATTERNS = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx",
    "id_rsa", "id_rsa.*", "id_ed25519", "id_ed25519.*",
    "credentials.json", "secrets.json", "secrets.toml",
]
# Safe templates that look like secrets but are meant to be committed.
SECRET_ALLOWLIST = [".env.example", ".env.sample", ".env.template"]

WARN_SIZE = 50 * 1024 * 1024  # GitHub warns above this
MAX_SIZE = 100 * 1024 * 1024  # GitHub rejects above this



def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def _dir_skip_reason(name: str, path: str) -> str | None:
    if os.path.isfile(os.path.join(path, "pyvenv.cfg")):
        return "virtual environment"
    if name in IGNORED_DIRS or _matches(name, IGNORED_DIR_PATTERNS):
        return "ignored folder"
    if os.path.islink(path):
        return "symlink (not followed)"
    return None


def _file_skip_reason(name: str) -> str | None:
    if name not in SECRET_ALLOWLIST and _matches(name, SECRET_FILE_PATTERNS):
        return "possible secret"
    if _matches(name, IGNORED_FILE_PATTERNS):
        return "ignored file"
    return None


def _count_files(path: str) -> int:
    """Count files under a skipped folder without following symlinks."""
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if entry.is_symlink():
                        total += 1
                    elif entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    else:
                        total += 1
        except OSError:
            continue
    return total


def format_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"




def scan_folder(root: str | os.PathLike) -> tuple[list[FileEntry], ScanSummary]:
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise FileNotFoundError(f"Not a folder: {root_path}")

    manifest: list[FileEntry] = []
    skipped: list[SkippedEntry] = []
    warnings: list[str] = []
    skipped_files = 0

    for current, dirs, files in os.walk(root_path, topdown=True, followlinks=False):
        rel_dir = Path(current).relative_to(root_path)

        # Prune ignored folders in place so os.walk never descends into them.
        kept_dirs = []
        for d in sorted(dirs):
            full = os.path.join(current, d)
            reason = _dir_skip_reason(d, full)
            if reason:
                count = _count_files(full)
                skipped.append(
                    SkippedEntry(path=(rel_dir / d).as_posix() + "/", reason=reason, files=count)
                )
                skipped_files += count
            else:
                kept_dirs.append(d)
        dirs[:] = kept_dirs

        for name in sorted(files):
            full = os.path.join(current, name)
            rel = (rel_dir / name).as_posix()

            reason = _file_skip_reason(name)
            if reason:
                skipped.append(SkippedEntry(path=rel, reason=reason, files=1))
                skipped_files += 1
                continue

            try:
                size = os.path.getsize(full)
            except OSError:
                continue

            if size > MAX_SIZE:
                skipped.append(
                    SkippedEntry(path=rel, reason=f"over 100 MB limit ({format_size(size)})", files=1)
                )
                skipped_files += 1
                warnings.append(
                    f"{rel} is {format_size(size)}; GitHub rejects files over 100 MB. "
                    "Consider Git LFS or hosting it elsewhere (e.g. Hugging Face Hub)."
                )
                continue

            if size > WARN_SIZE:
                warnings.append(f"{rel} is {format_size(size)}; GitHub warns above 50 MB.")

            manifest.append(FileEntry(path=rel, size=size, ext=Path(name).suffix.lower()))

    summary = ScanSummary(
        root=str(root_path),
        files_to_push=len(manifest),
        total_size=sum(f["size"] for f in manifest),
        skipped_files=skipped_files,
        skipped=skipped,
        warnings=warnings,
    )
    return manifest, summary




def scan_node(state: AgentState) -> dict:
    """LangGraph node: reads state['folder_path'], returns a partial state update."""
    try:
        manifest, summary = scan_folder(state["folder_path"])
    except (KeyError, FileNotFoundError, PermissionError) as exc:
        return {"error": f"Scan failed: {exc}"}
    return {"file_manifest": manifest, "scan_summary": summary, "error": None}


# ------------------------------------------------------------------ CLI ----

def format_report(manifest: list[FileEntry], summary: ScanSummary) -> str:
    lines = [
        f"Scanned: {summary['root']}",
        f"To push : {summary['files_to_push']} files ({format_size(summary['total_size'])})",
        f"Skipped : {summary['skipped_files']:,} files",
        "",
        "Files to push:",
    ]
    lines += [f"  {f['path']}  ({format_size(f['size'])})" for f in manifest]
    if summary["skipped"]:
        lines += ["", "Skipped:"]
        lines += [
            f"  {s['path']}  [{s['reason']}]" + (f"  {s['files']:,} files" if s["files"] > 1 else "")
            for s in summary["skipped"]
        ]
    if summary["warnings"]:
        lines += ["", "Warnings:"] + [f"  {w}" for w in summary["warnings"]]
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit('Usage: python scanner.py "path/to/folder"')
    m, s = scan_folder(sys.argv[1])
    print(format_report(m, s))