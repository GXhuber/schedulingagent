from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from datetime import datetime, timedelta

import anthropic

from .config import Settings
from .email_client import InMemoryEmailProvider
from .models import EmailMessage, EmailThread
from .runner import build_calendar, process_inbox, run_once


def cmd_run_once(_: argparse.Namespace) -> int:
    print(json.dumps(run_once().as_dict(), indent=2))
    return 0


def cmd_auth_graph(_: argparse.Namespace) -> int:
    from .graph_client import TokenCacheStore, device_code_login

    s = Settings.from_env()
    if not s.graph_client_id:
        print("GRAPH_CLIENT_ID is not set", file=sys.stderr)
        return 2
    store = TokenCacheStore(s.graph_token_cache_path)
    user = device_code_login(s.graph_client_id, s.graph_tenant_id or "organizations", store)
    print(f"Signed in as {user}. Token cache written to {store.path}.")
    print("For serverless: base64-encode that file into GRAPH_TOKEN_CACHE_B64 (keep it secret).")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Run the agent against a sample thread with the mock calendar, in dry-run mode."""
    s = replace(Settings.from_env(), dry_run=True, calendar_backend="mock")
    owner = s.owner_email or "owner@example.com"
    agent = s.agent_email or "scheduler@example.com"
    s = replace(s, owner_email=owner, agent_email=agent)
    now = datetime.now(s.tzinfo)
    thread = EmailThread(
        thread_id="demo",
        messages=[
            EmailMessage(
                uid="1",
                message_id="<demo-1@example.com>",
                thread_id="demo",
                subject="Intro call",
                sender="counterparty@example.com",
                to=(owner,),
                cc=(agent,),
                date=now - timedelta(minutes=5),
                body=args.body,
            )
        ],
    )
    provider = InMemoryEmailProvider([thread])
    summary = process_inbox(s, provider, build_calendar(s), anthropic.Anthropic(), now=now)
    print(json.dumps(summary.as_dict(), indent=2))
    for sent in provider.sent:
        print(f"\n--- would send to {sent.to} cc {sent.cc} ---\n{sent.body}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="scheduling-agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run-once", help="poll the inbox once and act").set_defaults(fn=cmd_run_once)
    sub.add_parser("auth-graph", help="one-time Microsoft 365 sign-in").set_defaults(fn=cmd_auth_graph)
    demo = sub.add_parser("demo", help="dry-run the agent on a sample email")
    demo.add_argument(
        "--body",
        default="Hi, would love to find 30 minutes next week to catch up. Looping in your scheduler.",
    )
    demo.set_defaults(fn=cmd_demo)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
