"""Interactive nightly Gmail session.

    python agent.py            # SAFE MODE: drafts are saved to Gmail, but 'send' sends nothing
    python agent.py --live     # 'send' really sends (after you type it)

Flow: digest -> pick an email -> LLM drafts a reply (or you write it) -> tweak it ->
type 'send'. Nothing is ever sent without you typing exactly: send
"""
import argparse
import os

import digest
import drafting
import gmail_tools as gt
import state
import triage

HELP = """
Commands:
  read N      show the full email
  reply N     LLM drafts a reply to email N
  write N     you write the reply yourself
  done N      mark email N as read and hide it from future digests
  skip N      hide email N from future digests (changes nothing in Gmail)
  list        show the digest again
  help        this message
  quit
"""

DEFAULT_STYLE = "Friendly, concise and professional. Short paragraphs."


def read_multiline():
    print("Type your reply. Finish with a line containing only a dot (.)")
    lines = []
    while True:
        line = input()
        if line.strip() == ".":
            return "\n".join(lines).strip()
        lines.append(line)


def review(service, draft, body, ctx):
    """Show the draft, let the user revise it, and send only on an explicit 'send'."""
    thread, args, use_llm, name, addr, style = (
        ctx["thread"], ctx["args"], ctx["use_llm"], ctx["name"], ctx["addr"], ctx["style"])
    while True:
        print("\n----- DRAFT (saved in your Gmail Drafts) -----")
        print(f"To: {draft['to']}\nSubject: {draft['subject']}\n\n{body}")
        print("----------------------------------------------")
        gaps = drafting.find_placeholders(body)
        if gaps:
            print(f"Heads up: still needs filling in: {', '.join(gaps)}")
        action = input("send | discard | manual | or tell me what to change > ").strip()
        low = action.lower()

        if low == "send":
            if gaps:
                print("Not sent: the draft still has [placeholders]. Tell me what to put there.")
                continue
            res = gt.send_draft(service, draft["draft_id"], live=args.live)
            if res["status"] == "sent":
                print(f"Sent to {res['to']}.")
                return True
            elif res["status"] == "dry_run":
                print("SAFE MODE: not sent. The draft is in your Gmail Drafts. "
                      "Run with --live to really send.")
            else:
                print(f"Not sent: {res['reason']}. Draft is still in your Gmail Drafts.")
            return False
        if low in ("discard", "d"):
            gt.discard_draft(service, draft["draft_id"])
            print("Draft discarded.")
            return False
        if low == "manual":
            body = read_multiline()
        elif action:
            if not use_llm:
                print("The LLM is not running. Use 'manual' to rewrite it yourself.")
                continue
            print("Revising...")
            body = drafting.draft_reply(thread, name, addr, ctx["model"], style,
                                        to_name=ctx["to_name"], signoff=ctx["signoff"],
                                        instruction=action, previous=body)
        else:
            continue
        gt.update_draft(service, draft["draft_id"], body)


def start_reply(service, e, mode, ctx):
    if e["category"] == "linkedin":
        print("That is a LinkedIn notification: it can't be answered by email. "
              "(That's one for the LinkedIn agent later.)")
        return
    if triage.is_noreply(e["sender_addr"]):
        print("That is an automated no-reply sender, so there is nobody to reply to.")
        return

    ctx["thread"] = gt.get_thread_text(ctx["service"], e["thread_id"])
    ctx["to_name"] = drafting.first_name(e["sender_name"])
    if mode == "reply":
        if not ctx["use_llm"]:
            print("The LLM is not running. Use 'write N' to write the reply yourself.")
            return
        print("Drafting...")
        body = drafting.draft_reply(ctx["thread"], ctx["name"], ctx["addr"], ctx["model"],
                                    ctx["style"], to_name=ctx["to_name"], signoff=ctx["signoff"])
    else:
        body = read_multiline()
    if not body:
        print("Empty reply, cancelled.")
        return
    draft = gt.create_reply_draft(service, e["id"], body)
    if review(service, draft, body, ctx):  # really sent
        state.mark_handled(e["thread_id"])
        e["handled"] = True
        try:
            gt.mark_read(service, [e["id"]])
        except Exception:
            pass  # not critical


def main(argv=None):
    ap = argparse.ArgumentParser(description="Interactive nightly Gmail session")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--max", type=int, default=100)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--live", action="store_true", help="really send when you type 'send'")
    ap.add_argument("--model", default=os.getenv("OLLAMA_MODEL", "llama3.2:3b"))
    args = ap.parse_args(argv)
    digest.safe_console()

    service = gt.get_service()
    rules = triage.load_rules()
    can_write = gt.can_write()
    if not can_write:
        print("Your token is read-only, so replying is disabled. To enable it: set SCOPES in "
              "gmail_auth.py to ['https://www.googleapis.com/auth/gmail.modify'], delete "
              "token.json and run gmail_auth.py again.")
    print("MODE:", "LIVE (send really sends)" if args.live else "SAFE (send is a dry run)")

    use_llm = digest.resolve_llm(rules, args.no_llm)
    print(f"Fetching emails from the last {args.hours}h...")
    emails, hidden = digest.run_triage(service, rules, args.hours, args.max, use_llm, args.model)
    digest.print_digest(emails, args.hours, hidden)
    digest.save_digest(emails)
    by_number = {e["number"]: e for e in emails if "number" in e}
    print(HELP)

    ctx = {"service": service, "args": args, "use_llm": use_llm, "model": args.model,
           "name": rules.get("my_name", "Shubh"), "addr": gt.my_address(service),
           "style": rules.get("reply_style", DEFAULT_STYLE),
           "signoff": rules.get("reply_signoff", "Best regards,"), "to_name": ""}

    while True:
        try:
            cmd = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not cmd:
            continue
        parts = cmd.split()
        verb = parts[0].lower()
        if verb in ("quit", "exit", "q"):
            break
        if verb == "help":
            print(HELP)
            continue
        if verb == "list":
            digest.print_digest(emails, args.hours, hidden)
            continue
        if verb not in ("read", "reply", "write", "done", "skip"):
            print("Unknown command. Type 'help'.")
            continue
        if len(parts) < 2 or not parts[1].isdigit() or int(parts[1]) not in by_number:
            print(f"Usage: {verb} N   (N is a number from the digest)")
            continue
        e = by_number[int(parts[1])]

        try:
            if verb == "read":
                print(f"\nFrom: {e['sender_name']} <{e['sender_addr']}>\nSubject: {e['subject']}\n")
                print(gt.get_body_excerpt(service, e["id"], limit=3000) or "(no text body)")
            elif verb == "skip":
                state.mark_handled(e["thread_id"])
                e["handled"] = True
                print("Hidden from future digests.")
            elif not can_write:
                print("Read-only token: re-authorise with the gmail.modify scope first.")
            elif verb == "done":
                gt.mark_read(service, [e["id"]])
                state.mark_handled(e["thread_id"])
                e["handled"] = True
                print("Marked as read and hidden from future digests.")
            else:
                start_reply(service, e, verb, ctx)
        except Exception as exc:  # keep the session alive on any API/LLM hiccup
            print(f"Error: {exc}")


if __name__ == "__main__":
    main()