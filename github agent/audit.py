"""Audit node: works out what the project is and what a GitHub push is missing.

Read-only. Two layers:
  1. Rules from rules.yaml (missing README, LICENSE, .gitignore, per-type files).
  2. Code-level checks on file contents (secrets, personal paths, requirements
     that don't match the imports).

Findings never contain a full secret value; matches are masked.

Run directly for a quick report:
    python audit.py "path/to/folder"
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import yaml

from state import AgentState, FileEntry, SkippedEntry

RULES_PATH = Path(__file__).with_name("rules.yaml")

SEVERITY_ORDER = {"block": 0, "must": 1, "should": 2, "nice": 3}
PYTHON_TYPES = {"python_project", "streamlit", "gradio", "notebook", "library"}

TEXT_EXTS = {
    ".py", ".ipynb", ".md", ".txt", ".toml", ".yaml", ".yml", ".json",
    ".cfg", ".ini", ".js", ".ts", ".html", ".sh", ".bat", ".ps1",
}
MAX_TEXT_BYTES = 300_000
DATA_EXTS = {".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".db", ".sqlite"}

SECRET_PATTERNS = [
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]
GENERIC_SECRET = re.compile(
    r"""(?i)\b(api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*["']([^"'\s]{8,})["']"""
)
PLACEHOLDER_HINTS = ("your", "example", "changeme", "xxxx", "placeholder", "<", "...", "todo")

PERSONAL_PATH_PATTERNS = [
    re.compile(r"""[A-Za-z]:\\{1,2}Users\\{1,2}[^\\/\s"']+""", re.I),
    re.compile(r"""/Users/[^/\s"']+/"""),
    re.compile(r"""/home/[^/\s"']+/"""),
]

# import name -> PyPI package name, where they differ
IMPORT_TO_PACKAGE = {
    "cv2": "opencv-python", "PIL": "pillow", "sklearn": "scikit-learn",
    "yaml": "pyyaml", "bs4": "beautifulsoup4", "dotenv": "python-dotenv",
    "skimage": "scikit-image", "dateutil": "python-dateutil", "attr": "attrs",
}



def _read_text(path: Path) -> str | None:
    """Read a text file, tolerating the UTF-16 files PowerShell likes to write."""
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _finding(check: str, severity: str, message: str, suggestion: str, **extra) -> dict:
    return {"check": check, "severity": severity, "message": message, "suggestion": suggestion, **extra}


def _mask(value: str) -> str:
    return value[:4] + "..." if len(value) > 4 else "..."


def gitignore_lines(skipped: list[SkippedEntry]) -> list[str]:
    """Suggested .gitignore lines, derived from what the scanner had to skip."""
    lines = {"__pycache__/", "*.pyc", ".env"}
    for s in skipped:
        if s["path"].endswith("/"):
            lines.add(s["path"])
        elif s["reason"] == "possible secret":
            lines.add(Path(s["path"]).name)
    return sorted(lines)



def _scan_contents(root: Path, manifest: list[FileEntry]) -> tuple[list[dict], set[str], list[str]]:
    """Return (findings, third-party imports, python files that failed to parse)."""
    findings: list[dict] = []
    imports: set[str] = set()
    syntax_errors: list[str] = []
    local_names = set()
    for f in manifest:
        p = Path(f["path"])
        local_names.add(p.stem)
        if len(p.parts) > 1:
            local_names.add(p.parts[0])

    stdlib = set(sys.stdlib_module_names)

    for f in manifest:
        if f["ext"] not in TEXT_EXTS:
            continue
        text = _read_text(root / f["path"])
        if text is None:
            continue

        for lineno, line in enumerate(text.splitlines(), start=1):
            for label, pattern in SECRET_PATTERNS:
                m = pattern.search(line)
                if m:
                    findings.append(_finding(
                        "secret_in_code", "block",
                        f"Possible {label} ({_mask(m.group(0))}) in {f['path']}:{lineno}",
                        "Remove it from the file, load it from an environment variable, and revoke the exposed key.",
                        path=f["path"], line=lineno,
                    ))
            g = GENERIC_SECRET.search(line)
            if g and not any(h in g.group(2).lower() for h in PLACEHOLDER_HINTS):
                findings.append(_finding(
                    "secret_in_code", "block",
                    f"Hardcoded {g.group(1)} value ({_mask(g.group(2))}) in {f['path']}:{lineno}",
                    "Load it from an environment variable (os.getenv) instead of writing it in the file.",
                    path=f["path"], line=lineno,
                ))
            for pattern in PERSONAL_PATH_PATTERNS:
                m = pattern.search(line)
                if m:
                    findings.append(_finding(
                        "personal_path", "should",
                        f"Hardcoded personal path in {f['path']}:{lineno}",
                        "Use a relative path (e.g. pathlib.Path(__file__).parent / 'file.csv') so it works on other machines and doesn't expose your username.",
                        path=f["path"], line=lineno,
                    ))
                    break

        if f["ext"] == ".py":
            try:
                tree = ast.parse(text)
            except SyntaxError:
                syntax_errors.append(f["path"])
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imports.add(node.module.split(".")[0])

    third_party = {m for m in imports if m not in stdlib and m not in local_names}
    return findings, third_party, syntax_errors


def _requirements_names(root: Path, manifest: list[FileEntry]) -> set[str] | None:
    """Package names listed in requirements.txt, or None if there isn't one."""
    rel = next((f["path"] for f in manifest if f["path"].lower() == "requirements.txt"), None)
    if rel is None:
        return None
    text = _read_text(root / rel) or ""
    names = set()
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        names.add(_norm(re.split(r"[<>=!~;\[\s@]", line, maxsplit=1)[0]))
    return names




def detect_project_type(manifest: list[FileEntry], imports: set[str]) -> str:
    """Deterministic first guess. A later LLM node can refine this."""
    paths = {f["path"].lower() for f in manifest}
    n_py = sum(1 for f in manifest if f["ext"] == ".py")
    n_nb = sum(1 for f in manifest if f["ext"] == ".ipynb")

    if "streamlit" in imports:
        return "streamlit"
    if "gradio" in imports:
        return "gradio"
    if n_nb and n_nb >= n_py:
        return "notebook"
    if "index.html" in paths and n_py == 0:
        return "pages"
    if "pyproject.toml" in paths or "setup.py" in paths:
        return "library"
    if n_py:
        return "python_project"
    return "unknown"




def _apply_rules(rules: list[dict], root_files: dict[str, str], readme_text: str | None) -> list[dict]:
    out = []
    for r in rules or []:
        if "any_of" in r:
            if not any(n.lower() in root_files for n in r["any_of"]):
                out.append(_finding(r["id"], r["severity"], r["message"], r["suggestion"]))
        elif "readme_contains" in r and readme_text is not None:
            if not any(s.lower() in readme_text.lower() for s in r["readme_contains"]):
                out.append(_finding(r["id"], r["severity"], r["message"], r["suggestion"]))
    return out


def audit_project(
    root: str | Path, manifest: list[FileEntry], skipped: list[SkippedEntry]
) -> tuple[str, list[dict]]:
    root = Path(root)
    rules = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))

    findings, imports, syntax_errors = _scan_contents(root, manifest)
    ptype = detect_project_type(manifest, imports)

    root_files = {f["path"].lower(): f["path"] for f in manifest if "/" not in f["path"]}
    readme_rel = next((v for k, v in root_files.items() if k.startswith("readme")), None)
    readme_text = _read_text(root / readme_rel) if readme_rel else None

    findings += _apply_rules(rules.get("common", []), root_files, readme_text)
    if ptype in PYTHON_TYPES:
        findings += _apply_rules(rules.get("python", []), root_files, readme_text)
    findings += _apply_rules(rules.get("by_type", {}).get(ptype, []), root_files, readme_text)

    if readme_rel and readme_text is not None and len(readme_text.strip()) < 200:
        findings.append(_finding(
            "readme_short", "should", f"{readme_rel} is very short",
            "Describe what the project does, how to install it, how to run it, and show an example or screenshot.",
        ))

    # .gitignore: suggest contents if missing, check coverage if present.
    if ".gitignore" not in root_files:
        lines = gitignore_lines(skipped)
        for f in findings:
            if f["check"] == "gitignore":
                f["suggestion"] += " Suggested lines: " + ", ".join(lines)
    else:
        gi = _read_text(root / root_files[".gitignore"]) or ""
        for s in skipped:
            if s["reason"] == "possible secret" and Path(s["path"]).name not in gi:
                findings.append(_finding(
                    "gitignore_secret", "must",
                    f"{s['path']} looks like a secret file but isn't in .gitignore",
                    f"Add {Path(s['path']).name} to .gitignore.", path=s["path"],
                ))
            elif s["reason"] == "virtual environment" and s["path"].rstrip("/") not in gi:
                findings.append(_finding(
                    "gitignore_venv", "should",
                    f"{s['path']} is a virtual environment but isn't in .gitignore",
                    f"Add {s['path']} to .gitignore.", path=s["path"],
                ))

    # requirements vs imports
    if ptype in PYTHON_TYPES:
        listed = _requirements_names(root, manifest)
        if listed is not None:
            missing = sorted(
                m for m in imports
                if _norm(IMPORT_TO_PACKAGE.get(m, m)) not in listed
            )
            if missing:
                findings.append(_finding(
                    "requirements_mismatch", "should",
                    "Imported but not listed in requirements.txt: " + ", ".join(missing),
                    "Add these to requirements.txt (some may already be installed as dependencies of other packages, but listing what you import directly is safer).",
                ))

    for p in syntax_errors:
        findings.append(_finding(
            "python_syntax", "should", f"{p} could not be parsed as Python",
            "Check this file for syntax errors before pushing.", path=p,
        ))

    data_files = [f["path"] for f in manifest if f["ext"] in DATA_EXTS]
    if data_files:
        findings.append(_finding(
            "data_files", "nice",
            "Data files included: " + ", ".join(data_files),
            "Confirm they contain no personal or private data, and mention their source in the README.",
        ))

    findings.sort(key=lambda f: SEVERITY_ORDER.get(f["severity"], 9))
    return ptype, findings




def audit_node(state: AgentState) -> dict:
    """LangGraph node: needs file_manifest and scan_summary from the scan node."""
    summary = state["scan_summary"]
    ptype, findings = audit_project(summary["root"], state["file_manifest"], summary["skipped"])
    return {"project_type": ptype, "findings": findings}



def format_audit(project_type: str, findings: list[dict]) -> str:
    labels = {"block": "BLOCKER", "must": "MUST", "should": "SHOULD", "nice": "NICE"}
    lines = [f"Project type: {project_type}", f"Findings: {len(findings)}", ""]
    for f in findings:
        lines.append(f"[{labels.get(f['severity'], f['severity'])}] {f['message']}")
        lines.append(f"    -> {f['suggestion']}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit('Usage: python audit.py "path/to/folder"')
    from scanner import scan_folder

    manifest, summary = scan_folder(sys.argv[1])
    ptype, findings = audit_project(summary["root"], manifest, summary["skipped"])
    print(format_audit(ptype, findings))