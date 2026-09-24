from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import anthropic

from .agent import run_agent
from .calendar.base import Calendar
from .calendar.mock import MockCalendar
from .config import Settings
from .email_client import EmailProvider, ImapSmtpEmailProvider
from .models import EmailThread
from .tools import SchedulingTools

log = logging.getLogger(__name__)


@dataclass
class RunSummary:
    processed: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"processed": self.processed, "skipped": self.skipped, "failed": self.failed}


def triage(thread: EmailThread, settings: Settings) -> str | None:
    """Return a reason to skip the thread, or None to hand it to the agent."""
    latest = thread.latest
    if latest.sender == settings.agent_email:
        return "latest message is from the agent"
    if latest.is_auto_generated:
        return "auto-generated message"
    if settings.owner_email not in thread.participants:
        return "owner is not on the thread"
    if settings.allowed_sender_domains:
        domain = latest.sender.rsplit("@", 1)[-1]
        if latest.sender != settings.owner_email and domain not in settings.allowed_sender_domains:
            return f"sender domain {domain} not in allowlist"
    return None


def process_inbox(
    settings: Settings,
    email_provider: EmailProvider,
    calendar: Calendar,
    client: anthropic.Anthropic,
    now: datetime | None = None,
) -> RunSummary:
    summary = RunSummary()
    threads = email_provider.fetch_unprocessed_threads(settings.max_threads_per_run)
    log.info("Fetched %d unprocessed thread(s)", len(threads))
    for thread in threads:
        ref = {"thread_id": thread.thread_id, "subject": thread.latest.subject}
        reason = triage(thread, settings)
        if reason:
            log.info("Skipping %s: %s", ref, reason)
            summary.skipped.append({**ref, "reason": reason})
            email_provider.mark_processed(thread)
            continue
        try:
            tools = SchedulingTools(
                settings=settings, calendar=calendar, email=email_provider, thread=thread, now=now
            )
            result = run_agent(client, settings, tools)
            summary.processed.append(
                {
                    **ref,
                    "summary": result.summary,
                    "replies": len(result.actions.replies),
                    "events": len(result.actions.events),
                    "escalations": len(result.actions.escalations),
                    "dry_run": settings.dry_run,
                }
            )
            email_provider.mark_processed(thread)
        except Exception as exc:  # one bad thread must not block the rest
            log.exception("Failed processing thread %s", ref)
            summary.failed.append({**ref, "error": repr(exc)})
            email_provider.mark_processed(thread, failed=True)
    return summary


def build_graph_client(settings: Settings):
    from .graph_client import (
        GraphClient,
        TokenCacheStore,
        app_only_token_provider,
        delegated_token_provider,
    )

    if settings.graph_auth_mode == "app":
        provider = app_only_token_provider(
            settings.graph_client_id, settings.graph_tenant_id, settings.graph_client_secret
        )
    else:
        provider = delegated_token_provider(
            settings.graph_client_id,
            settings.graph_tenant_id or "organizations",
            TokenCacheStore(settings.graph_token_cache_path, settings.graph_token_cache_b64),
        )
    return GraphClient(provider)


def build_calendar(settings: Settings) -> Calendar:
    if settings.calendar_backend == "graph":
        from .calendar.graph import GraphCalendar

        user_path = f"/users/{settings.owner_email}" if settings.graph_auth_mode == "app" else "/me"
        return GraphCalendar(build_graph_client(settings), user_path=user_path, timezone=settings.timezone)
    return MockCalendar(settings.mock_calendar_path)


def build_email(settings: Settings) -> EmailProvider:
    if settings.email_backend == "graph":
        if settings.graph_auth_mode != "app":
            raise ValueError("EMAIL_BACKEND=graph requires GRAPH_AUTH_MODE=app (client credentials)")
        from .graph_mail import GraphMailProvider

        return GraphMailProvider(build_graph_client(settings), mailbox=settings.agent_email)
    return ImapSmtpEmailProvider(
        address=settings.agent_email,
        password=settings.email_password,
        display_name=settings.agent_name,
        imap_host=settings.imap_host,
        imap_port=settings.imap_port,
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
    )


def run_once(settings: Settings | None = None) -> RunSummary:
    settings = settings or Settings.from_env()
    email_provider = build_email(settings)
    try:
        return process_inbox(settings, email_provider, build_calendar(settings), anthropic.Anthropic())
    finally:
        email_provider.close()
