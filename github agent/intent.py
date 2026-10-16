"""Turns a plain-English request into an intent plus parameters.

This is a deterministic, rule-based parser on purpose: it is predictable and
needs no model. It understands a fixed set of phrasings (see EXAMPLES). The
parse_request() function is the single place to swap in an LLM later; nothing
else in the graph needs to change.

Tips for phrasing: put folder paths and titles in quotes, and name repos with
the word "repo" ("... to repo my-project").
"""

from __future__ import annotations

import re

from state import AgentState

EXAMPLES = [
    'push "C:\\path\\to\\project" to repo my-project as private',
    "create a private repo called my-project",
    'create an issue in repo my-project titled "Add a README" with body "Needs docs"',
    'open a pull request in repo my-project from feature-x into main titled "Add feature"',
    "archive repo old-project",
    "delete repo old-project",
]

QUOTED = re.compile(r"""(?<!\w)(["'\u201c])(.+?)(["'\u201d])(?!\w)""")
WIN_PATH = re.compile(r"""[A-Za-z]:\\[^\s"']+""")
NAME = r"([\w.\-]+(?:/[\w.\-]+)?)"
STOPWORDS = {
    "a", "an", "the", "my", "new", "private", "public", "github", "main", "master",
    "it", "this", "that", "me", "repo", "repository", "as", "titled", "called", "named",
}

PUSH_VERBS = r"\b(push|upload|publish|commit|send|put)\b"


def _looks_like_path(text: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", text)) or "\\" in text or "/" in text or text.startswith("~")


def _first_name(patterns: list[str], text: str) -> str | None:
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I):
            candidate = m.group(1)
            if candidate.lower() not in STOPWORDS:
                return candidate
    return None


def parse_request(text: str) -> dict:
    t = text.strip()
    quoted = [m.group(2).strip() for m in QUOTED.finditer(t)]
    plain = QUOTED.sub(" ", t)

    folder = next((q for q in quoted if _looks_like_path(q)), None)
    others = [q for q in quoted if q != folder]
    if folder is None:
        m = WIN_PATH.search(plain)
        if m:
            folder = m.group(0)
            plain = plain.replace(folder, " ")

    visibility = None
    vm = re.search(r"\b(private|public)\b", plain, re.I)
    if vm:
        visibility = vm.group(1).lower()

    low = plain.lower()
    has_repo_word = bool(re.search(r"\brepo(sitory)?\b", low))

    if re.search(r"\b(delete|remove)\b", low) and has_repo_word:
        intent = "repo_delete"
    elif re.search(r"\barchive\b", low):
        intent = "repo_archive"
    elif re.search(r"\bissue\b", low):
        intent = "issue"
    elif re.search(r"\b(pull request|pr)\b", low):
        intent = "pr"
    elif folder and re.search(PUSH_VERBS, low):
        intent = "push"
    elif re.search(r"\b(create|make|new)\b", low) and has_repo_word:
        intent = "repo_create"
    elif re.search(PUSH_VERBS, low):
        intent = "push"
    else:
        return {"intent": "unknown", "missing": []}

    repo = _first_name([r"\brepo(?:sitory)?\s+(?:called\s+|named\s+)?" + NAME], plain)
    if repo is None and intent in {"repo_create"}:
        repo = _first_name([r"\b(?:called|named)\s+" + NAME], plain)
    if repo is None and intent in {"push", "issue", "pr"}:
        repo = _first_name([r"\b(?:in|on|to|into)\s+(?:the\s+)?" + NAME], plain)
    if repo is None and intent in {"push", "repo_create"} and others:
        repo = others[0]
        others = others[1:]
    if repo is None and intent in {"repo_archive", "repo_delete"}:
        repo = _first_name([r"\b(?:archive|delete|remove)\s+(?:the\s+)?" + NAME], plain)

    params: dict = {}
    if intent == "issue":
        params["title"] = others[0] if others else None
        params["body"] = others[1] if len(others) > 1 else ""
    if intent == "pr":
        params["title"] = others[0] if others else None
        params["body"] = others[1] if len(others) > 1 else ""
        params["head"] = _first_name([r"\bfrom\s+(?:branch\s+)?([\w./\-]+)"], plain)
        params["base"] = _first_name([r"\binto\s+(?:branch\s+)?([\w./\-]+)"], plain)

    missing = []
    if intent == "push" and not folder:
        missing.append("folder (put the path in quotes)")
    if intent in {"push", "issue", "pr", "repo_create", "repo_archive", "repo_delete"} and not repo:
        missing.append('repo name (say "repo NAME")')
    if intent == "issue" and not params.get("title"):
        missing.append("issue title (put it in quotes)")
    if intent == "pr":
        if not params.get("title"):
            missing.append("PR title (put it in quotes)")
        if not params.get("head"):
            missing.append('source branch ("from BRANCH")')

    return {
        "intent": intent,
        "folder": folder,
        "repo": repo,
        "visibility": visibility,
        "params": params,
        "missing": missing,
    }


# ------------------------------------------------------------ graph pieces ----

def router_node(state: AgentState) -> dict:
    if state.get("intent"):  # already set by the caller (e.g. folder + repo given directly)
        return {}
    parsed = parse_request(state.get("user_prompt", ""))
    if parsed["intent"] == "unknown":
        examples = "\n  ".join(EXAMPLES)
        return {"error": f"I couldn't tell what you want. Try something like:\n  {examples}"}
    if parsed["missing"]:
        return {"error": "I understood the request but I'm missing: " + "; ".join(parsed["missing"])}

    out: dict = {
        "intent": parsed["intent"],
        "target_repo": parsed["repo"],
        "visibility": parsed["visibility"],
        "params": parsed["params"],
    }
    if parsed.get("folder"):
        out["folder_path"] = parsed["folder"]
    return out


def route_intent(state: AgentState) -> str:
    if state.get("error"):
        return "end"
    return "push" if state.get("intent") == "push" else "action"
