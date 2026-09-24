from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from ..models import CalendarEvent


class MockCalendar:
    """JSON-file-backed calendar for local development and tests."""

    def __init__(self, path: str | Path | None = None, events: list[CalendarEvent] | None = None):
        self.path = Path(path) if path else None
        self.events: list[CalendarEvent] = list(events or [])
        if self.path and self.path.exists():
            self.events.extend(self._load())

    def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        return sorted(
            (e for e in self.events if e.start < end and start < e.end), key=lambda e: e.start
        )

    def create_event(
        self,
        *,
        subject: str,
        start: datetime,
        end: datetime,
        attendees: tuple[str, ...],
        body: str = "",
        online_meeting: bool = True,
    ) -> CalendarEvent:
        event = CalendarEvent(
            id=f"mock-{uuid.uuid4().hex[:8]}",
            subject=subject,
            start=start,
            end=end,
            attendees=attendees,
            online_meeting_url="https://example.invalid/mock-meeting" if online_meeting else None,
        )
        self.events.append(event)
        self._save()
        return event

    def _load(self) -> list[CalendarEvent]:
        data = json.loads(self.path.read_text())
        return [
            CalendarEvent(
                id=e["id"],
                subject=e["subject"],
                start=datetime.fromisoformat(e["start"]),
                end=datetime.fromisoformat(e["end"]),
                attendees=tuple(e.get("attendees", ())),
                is_busy=e.get("is_busy", True),
            )
            for e in data
        ]

    def _save(self) -> None:
        if not self.path:
            return
        self.path.write_text(
            json.dumps(
                [
                    {
                        "id": e.id,
                        "subject": e.subject,
                        "start": e.start.isoformat(),
                        "end": e.end.isoformat(),
                        "attendees": list(e.attendees),
                        "is_busy": e.is_busy,
                    }
                    for e in self.events
                ],
                indent=2,
            )
        )
