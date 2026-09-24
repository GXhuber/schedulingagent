from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class EmailMessage:
    uid: str
    message_id: str
    thread_id: str
    subject: str
    sender: str
    to: tuple[str, ...]
    cc: tuple[str, ...]
    date: datetime
    body: str
    in_reply_to: str | None = None
    references: tuple[str, ...] = ()
    is_auto_generated: bool = False

    @property
    def participants(self) -> set[str]:
        return {self.sender, *self.to, *self.cc}


@dataclass
class EmailThread:
    thread_id: str
    messages: list[EmailMessage] = field(default_factory=list)

    @property
    def latest(self) -> EmailMessage:
        return self.messages[-1]

    @property
    def participants(self) -> set[str]:
        out: set[str] = set()
        for m in self.messages:
            out |= m.participants
        return out


@dataclass(frozen=True)
class OutgoingEmail:
    to: tuple[str, ...]
    cc: tuple[str, ...]
    subject: str
    body: str
    in_reply_to: str | None
    references: tuple[str, ...]


@dataclass(frozen=True)
class TimeSlot:
    start: datetime
    end: datetime

    def overlaps(self, other: TimeSlot) -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    subject: str
    start: datetime
    end: datetime
    attendees: tuple[str, ...] = ()
    is_busy: bool = True
    web_link: str | None = None
    online_meeting_url: str | None = None

    @property
    def slot(self) -> TimeSlot:
        return TimeSlot(self.start, self.end)
