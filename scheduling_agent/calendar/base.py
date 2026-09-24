from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..models import CalendarEvent


class Calendar(Protocol):
    def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]: ...

    def create_event(
        self,
        *,
        subject: str,
        start: datetime,
        end: datetime,
        attendees: tuple[str, ...],
        body: str = "",
        online_meeting: bool = True,
    ) -> CalendarEvent: ...
