"""The agent graph.

    START -> router --(push)-----> scan -> audit -> plan -> approval -> create_repo? -> push -> END
                 \\                  |        |       |         |
                  \\--(action)->      error    secret  error     no
                    prepare_action -> confirm_action -> execute_action -> END

A checkpointer is attached so the graph can pause (approval / confirmation) and
resume later. A resume must use the SAME graph object and the SAME config.

Run from a terminal:
    python graph.py "push 'C:\\path\\to\\project' to repo my-project as private"
    python graph.py "C:\\path\\to\\project" "my-project"        (push, shortcut form)
"""

from __future__ import annotations

import os
import sys
import uuid

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from actions import (
    confirm_action, execute_action, prepare_action, route_after_confirm, route_after_prepare,
)
from approval import approval_node, route_after_audit
from audit import audit_node, format_audit
from intent import route_intent, router_node
from push import (
    create_repo_node, plan_node, push_node, route_after_approval, route_after_create, route_after_plan,
)
from scanner import format_report, format_size, scan_node
from state import AgentState


def route_after_scan(state: AgentState) -> str:
    return "end" if state.get("error") else "audit"


def build_graph():
    g = StateGraph(AgentState)

    g.add_node("router", router_node)
    g.add_node("scan", scan_node)
    g.add_node("audit", audit_node)
    g.add_node("plan", plan_node)
    g.add_node("approval", approval_node)
    g.add_node("create_repo", create_repo_node)
    g.add_node("push", push_node)
    g.add_node("prepare_action", prepare_action)
    g.add_node("confirm_action", confirm_action)
    g.add_node("execute_action", execute_action)

    g.add_edge(START, "router")
    g.add_conditional_edges("router", route_intent, {"push": "scan", "action": "prepare_action", "end": END})

    # push pipeline
    g.add_conditional_edges("scan", route_after_scan, {"audit": "audit", "end": END})
    g.add_conditional_edges("audit", route_after_audit, {"plan": "plan", "end": END})
    g.add_conditional_edges("plan", route_after_plan, {"approval": "approval", "end": END})
    g.add_conditional_edges(
        "approval", route_after_approval, {"create_repo": "create_repo", "push": "push", "end": END}
    )
    g.add_conditional_edges("create_repo", route_after_create, {"push": "push", "end": END})
    g.add_edge("push", END)

    # single actions (issue, pr, create repo, archive, delete)
    g.add_conditional_edges("prepare_action", route_after_prepare, {"confirm": "confirm_action", "end": END})
    g.add_conditional_edges("confirm_action", route_after_confirm, {"execute": "execute_action", "end": END})
    g.add_edge("execute_action", END)

    return g.compile(checkpointer=MemorySaver())


# ------------------------------------------------------------ interactive ----

def _show_push_approval(result: dict, ask: dict) -> str:
    print(format_report(result["file_manifest"], result["scan_summary"]))
    print("\n" + "=" * 60 + "\n")
    print(format_audit(result["project_type"], result["findings"]))
    print("\n" + "=" * 60)
    print(f"Target: {ask['repo']}  [{ask['mode']}]")
    if ask.get("branch"):
        print(f"Branch: {ask['branch']}")
    print(f"{ask['files_to_push']} files, {format_size(ask['total_size'])}. Planned commits:")
    for i, c in enumerate(ask["commit_plan"], start=1):
        print(f"  {i}. {c['message']}  ({c['files']} file{'s' if c['files'] != 1 else ''})")
    if ask["must_fix"]:
        print("Unresolved MUST items:")
        for m in ask["must_fix"]:
            print(f"  - {m}")
    if ask["needs_visibility"]:
        hint = f" (or 'yes' to use {ask['visibility']})" if ask.get("visibility") else ""
        return f"Create this repo and push? Type private or public{hint}, anything else cancels: "
    return "Approve? (yes/no): "


def _show_action_confirm(ask: dict) -> str:
    print("\n" + "=" * 60)
    print(ask["summary"])
    if ask["level"] == "typed":
        return f"Type {ask['phrase']} to confirm, anything else cancels: "
    if ask["level"] == "visibility":
        return "Type private or public to confirm, anything else cancels: "
    return "Confirm? (yes/no): "


def run_agent(
    prompt: str | None = None,
    folder: str | None = None,
    repo: str | None = None,
    visibility: str | None = None,
    thread_id: str | None = None,
) -> AgentState:
    """Run one request. Pass a plain-English prompt, or folder + repo for a push."""
    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id or f"run-{uuid.uuid4().hex[:6]}"}}

    state: dict = {}
    if prompt:
        state["user_prompt"] = prompt
    if folder:
        state.update(intent="push", folder_path=folder)
    if repo:
        state["target_repo"] = repo
    if visibility:
        state["visibility"] = visibility

    result = graph.invoke(state, config=config)

    while "__interrupt__" in result and not result.get("error"):
        ask = result["__interrupt__"][0].value
        if ask["kind"] == "push_approval":
            question = _show_push_approval(result, ask)
        else:
            question = _show_action_confirm(ask)
        result = graph.invoke(Command(resume=input(question)), config=config)

    print()
    if result.get("error"):
        print(result["error"])
    elif result.get("push_result"):
        r = result["push_result"]
        if r.get("note") and not r["commits"]:
            print(r["note"])
        else:
            print(f"Pushed {r['files']} files in {r['commits']} commit{'s' if r['commits'] != 1 else ''} to {r['repo_url']}")
            if r["mode"] == "update":
                print(f"Branch: {r['branch']}")
                print(f"Pull request: {r['pr_url']}" if r.get("pr_url") else (r.get("note") or ""))
    elif result.get("action_result"):
        a = result["action_result"]
        print(a["summary"] + (f"\n{a['url']}" if a.get("url") else ""))
    elif any(f["severity"] == "block" for f in result.get("findings", [])):
        print(format_audit(result["project_type"], result["findings"]))
        print("\nBLOCKED: a possible secret was found. Fix it before anything can be pushed.")
    else:
        print("Cancelled. Nothing was changed.")
    return result


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 1:
        run_agent(prompt=args[0])
    elif len(args) == 2 and os.path.isdir(args[0]):
        run_agent(folder=args[0], repo=args[1])
    else:
        sys.exit(
            'Usage:\n'
            '  python graph.py "push \'C:\\path\\to\\project\' to repo my-project as private"\n'
            '  python graph.py "C:\\path\\to\\project" "my-project"'
        )