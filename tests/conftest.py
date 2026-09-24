from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from scheduling_agent.calendar.mock import MockCalendar
from scheduling_agent.config import Settings
from scheduling_agent.email_client import InMemoryEmailProvider
from scheduling_agent.models import CalendarEvent, EmailMessage, EmailThread

TZ = ZoneInfo("America/New_York")
OWNER = "owner@firm.com"
AGENT = "scheduler@gmail.com"
GUEST = "guest@partner.com"

# A Monday at 10:00 ET
NOW = datetime(2026, 9, 21, 10, 0, tzinfo=TZ)


@pytest.fixture
def settings(monkeypatch) -> Settings:
    monkeypatch.setenv("OWNER_EMAIL", OWNER)
    monkeypatch.setenv("AGENT_EMAIL", AGENT)
    monkeypatch.setenv("OWNER_NAME", "Owner")
    monkeypatch.setenv("AGENT_NAME", "Sched")
    return replace(Settings.from_env(), dry_run=False)


def message(
    *,
    uid: str = "1",
    sender: str = GUEST,
    to=(OWNER,),
    cc=(AGENT,),
    body: str = "Can we meet next week?",
    subject: str = "Intro",
    date: datetime = NOW - timedelta(minutes=5),
    auto: bool = False,
    references=(),
) -> EmailMessage:
    return EmailMessage(
        uid=uid,
        message_id=f"<{uid}@test>",
        thread_id="thr",
        subject=subject,
        sender=sender,
        to=tuple(to),
        cc=tuple(cc),
        date=date,
        body=body,
        references=tuple(references),
        is_auto_generated=auto,
    )


@pytest.fixture
def thread() -> EmailThread:
    return EmailThread(thread_id="thr", messages=[message()])


@pytest.fixture
def calendar() -> MockCalendar:
    tomorrow = NOW + timedelta(days=1)
    return MockCalendar(
        events=[
            CalendarEvent(
                id="e1",
                subject="Board prep",
                start=tomorrow.replace(hour=9, minute=0),
                end=tomorrow.replace(hour=12, minute=0),
            ),
            CalendarEvent(
                id="e2",
                subject="Lunch",
                start=tomorrow.replace(hour=12, minute=0),
                end=tomorrow.replace(hour=13, minute=0),
                is_busy=False,
            ),
        ]
    )


@pytest.fixture
def email_provider(thread) -> InMemoryEmailProvider:
    return InMemoryEmailProvider([thread])
