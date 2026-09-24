import json
from dataclasses import replace
from datetime import timedelta

from scheduling_agent.tools import SchedulingTools

from .conftest import AGENT, GUEST, NOW, OWNER


def make_tools(settings, calendar, email_provider, thread, **overrides):
    return SchedulingTools(
        settings=replace(settings, **overrides),
        calendar=calendar,
        email=email_provider,
        thread=thread,
        now=NOW,
    )


def test_get_availability_skips_busy_and_min_notice(settings, calendar, email_provider, thread):
    t = make_tools(settings, calendar, email_provider, thread)
    data = json.loads(t.get_availability())
    assert data["timezone"] == "America/New_York"
    starts = [s["start"] for s in data["suggested"]]
    # Tuesday 9-12 busy (+15m buffer) => nothing before 12:30 on Tuesday
    assert all(not s.startswith("2026-09-22T09") for s in starts)
    assert all(not s.startswith("2026-09-22T1[01]") for s in starts)
    # min notice 24h => nothing on Monday
    assert all(not s.startswith("2026-09-21") for s in starts)
    assert len(starts) <= settings.max_proposed_slots
    assert len({s[:10] for s in starts}) == len(starts)  # spread across days


def test_check_slot_reports_conflict_without_leaking_subject(settings, calendar, email_provider, thread):
    t = make_tools(settings, calendar, email_provider, thread)
    out = json.loads(t.check_slot("2026-09-22T10:00"))
    assert out["free"] is False
    assert "Board prep" not in json.dumps(out)
    free = json.loads(t.check_slot("2026-09-22T15:00"))
    assert free["free"] and free["within_working_hours"] and not free["less_than_min_notice"]


def test_check_slot_free_events_do_not_block(settings, calendar, email_provider, thread):
    t = make_tools(settings, calendar, email_provider, thread)
    assert json.loads(t.check_slot("2026-09-22T12:00"))["free"] is True


def test_create_meeting_only_invites_thread_participants(settings, calendar, email_provider, thread):
    t = make_tools(settings, calendar, email_provider, thread)
    bad = json.loads(t.create_meeting("2026-09-22T15:00", "Intro", ["stranger@evil.com"]))
    assert "not on this email thread" in bad["error"]
    assert calendar.list_events(NOW, NOW + timedelta(days=7)) == calendar.events[:2]


def test_create_meeting_adds_owner_and_dedupes(settings, calendar, email_provider, thread):
    t = make_tools(settings, calendar, email_provider, thread)
    first = json.loads(t.create_meeting("2026-09-22T15:00", "Intro", [GUEST, AGENT]))
    assert first["status"] == "created"
    assert first["event"]["attendees"] == [OWNER, GUEST]
    again = json.loads(t.create_meeting("2026-09-22T15:00", "Intro", [GUEST]))
    assert again["status"] == "already_exists"
    assert len(calendar.events) == 3


def test_create_meeting_refuses_conflict_unless_forced(settings, calendar, email_provider, thread):
    t = make_tools(settings, calendar, email_provider, thread)
    out = json.loads(t.create_meeting("2026-09-22T10:00", "Intro", [GUEST]))
    assert "conflicts" in out["error"]
    forced = json.loads(t.create_meeting("2026-09-22T10:00", "Intro", [GUEST], force=True))
    assert forced["status"] == "created"


def test_dry_run_records_but_does_not_send_or_book(settings, calendar, email_provider, thread):
    t = make_tools(settings, calendar, email_provider, thread, dry_run=True)
    assert json.loads(t.create_meeting("2026-09-22T15:00", "Intro", [GUEST]))["dry_run"] is True
    assert json.loads(t.send_reply("hello"))["dry_run"] is True
    assert len(calendar.events) == 2
    assert email_provider.sent == []
    assert len(t.log.events) == 1 and len(t.log.replies) == 1


def test_send_reply_once_per_run_and_cc_owner(settings, calendar, email_provider, thread):
    t = make_tools(settings, calendar, email_provider, thread)
    ok = json.loads(t.send_reply("Options"))
    assert ok["to"] == [GUEST, OWNER] and ok["cc"] == []
    assert email_provider.sent[0].body == "Options"
    assert "already sent" in json.loads(t.send_reply("again"))["error"]


def test_escalate_goes_only_to_owner(settings, calendar, email_provider, thread):
    t = make_tools(settings, calendar, email_provider, thread)
    t.escalate_to_owner("They want to cancel Friday.")
    sent = email_provider.sent[0]
    assert sent.to == (OWNER,) and sent.cc == ()
    assert sent.subject.startswith("[Needs your input]")
