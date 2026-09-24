from datetime import datetime
from zoneinfo import ZoneInfo

from scheduling_agent.calendar.graph import GraphCalendar

from .conftest import GUEST, OWNER
from .fake_graph import FakeGraphClient

TZ = ZoneInfo("America/New_York")


def test_list_events_parses_and_skips_cancelled_and_free():
    client = FakeGraphClient(
        {
            "/calendarView": {
                "value": [
                    {
                        "id": "1",
                        "subject": "Busy",
                        "start": {"dateTime": "2026-09-22T09:00:00.0000000", "timeZone": "America/New_York"},
                        "end": {"dateTime": "2026-09-22T10:00:00.0000000", "timeZone": "America/New_York"},
                        "showAs": "busy",
                        "attendees": [{"emailAddress": {"address": GUEST.upper()}}],
                        "onlineMeeting": {"joinUrl": "https://teams.example/j"},
                    },
                    {
                        "id": "2",
                        "subject": "Focus",
                        "start": {"dateTime": "2026-09-22T11:00:00", "timeZone": "America/New_York"},
                        "end": {"dateTime": "2026-09-22T12:00:00", "timeZone": "America/New_York"},
                        "showAs": "free",
                    },
                    {"id": "3", "isCancelled": True, "start": {}, "end": {}},
                ]
            }
        }
    )
    cal = GraphCalendar(client, user_path=f"/users/{OWNER}", timezone="America/New_York")
    events = cal.list_events(datetime(2026, 9, 22, tzinfo=TZ), datetime(2026, 9, 23, tzinfo=TZ))
    assert [e.id for e in events] == ["1", "2"]
    assert events[0].start == datetime(2026, 9, 22, 9, tzinfo=TZ)
    assert events[0].attendees == (GUEST,)
    assert events[0].online_meeting_url == "https://teams.example/j"
    assert events[1].is_busy is False
    assert client.calls[0][1] == f"/users/{OWNER}/calendarView"


def test_create_event_payload_uses_owner_timezone_and_teams():
    client = FakeGraphClient()
    client.next_post = {
        "/events": {
            "id": "new",
            "subject": "Intro",
            "start": {"dateTime": "2026-09-22T15:00:00", "timeZone": "America/New_York"},
            "end": {"dateTime": "2026-09-22T15:30:00", "timeZone": "America/New_York"},
            "attendees": [],
        }
    }
    cal = GraphCalendar(client, user_path=f"/users/{OWNER}", timezone="America/New_York")
    start = datetime(2026, 9, 22, 19, 0, tzinfo=ZoneInfo("UTC"))  # 15:00 ET
    created = cal.create_event(subject="Intro", start=start, end=start.replace(minute=30), attendees=(OWNER, GUEST))
    payload = client.calls[0][2]
    assert payload["start"] == {"dateTime": "2026-09-22T15:00:00", "timeZone": "America/New_York"}
    assert payload["onlineMeetingProvider"] == "teamsForBusiness"
    assert [a["emailAddress"]["address"] for a in payload["attendees"]] == [OWNER, GUEST]
    assert created.id == "new"
