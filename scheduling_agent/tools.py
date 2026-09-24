"""Plain-Python scheduling operations. `agent.py` wraps these as Claude tools.

Every method returns a string (JSON where structured) so results can be handed straight back
to the model. Guardrails live here, not in the prompt, so they hold regardless of what the
model decides to do.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .availability import find_free_slots, spread_slots
from .calendar.base import Calendar
from .config import Settings
from .email_client import EmailProvider, build_reply
from .models import CalendarEvent, EmailThread, OutgoingEmail, TimeSlot

log = logging.getLogger(__name__)


@dataclass
class ActionLog:
    replies: list[OutgoingEmail] = field(default_factory=list)
    events: list[CalendarEvent] = field(default_factory=list)
    escalations: list[str] = field(default_factory=list)
    dry_run: bool = False


class SchedulingTools:
    def __init__(
        self,
        *,
        settings: Settings,
        calendar: Calendar,
        email: EmailProvider,
        thread: EmailThread,
        now: datetime | None = None,
    ) -> None:
        self.s = settings
        self.calendar = calendar
        self.email = email
        self.thread = thread
        self.now = (now or datetime.now(settings.tzinfo)).astimezone(settings.tzinfo)
        self.log = ActionLog(dry_run=settings.dry_run)

    # ---- read-only -------------------------------------------------------------------------

    def get_availability(
        self,
        earliest: str | None = None,
        latest: str | None = None,
        duration_minutes: int | None = None,
        limit: int | None = None,
    ) -> str:
        duration = timedelta(minutes=duration_minutes or self.s.default_duration_minutes)
        start = self._parse_dt(earliest) if earliest else None
        min_start = self.now + timedelta(hours=self.s.min_notice_hours)
        window_start = max(start, min_start) if start else min_start
        window_end = (
            self._parse_dt(latest) if latest else window_start + timedelta(days=self.s.lookahead_days)
        )
        if window_end <= window_start:
            return json.dumps({"error": "latest must be after earliest (and after the minimum notice window)"})

        busy = self._busy(window_start, window_end)
        slots = find_free_slots(
            busy=busy,
            window_start=window_start,
            window_end=window_end,
            duration=duration,
            tz=self.s.tzinfo,
            work_start_hour=self.s.work_start_hour,
            work_end_hour=self.s.work_end_hour,
            buffer=timedelta(minutes=self.s.buffer_minutes),
        )
        picked = spread_slots(slots, limit or self.s.max_proposed_slots)
        return json.dumps(
            {
                "timezone": self.s.timezone,
                "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
                "duration_minutes": int(duration.total_seconds() // 60),
                "total_free_slots": len(slots),
                "suggested": [self._slot_json(sl) for sl in picked],
            }
        )

    def check_slot(self, start: str, duration_minutes: int | None = None) -> str:
        begin = self._parse_dt(start)
        end = begin + timedelta(minutes=duration_minutes or self.s.default_duration_minutes)
        conflicts = [
            e for e in self.calendar.list_events(begin, end) if e.is_busy and e.slot.overlaps(TimeSlot(begin, end))
        ]
        in_hours = self.s.work_start_hour <= begin.astimezone(self.s.tzinfo).hour < self.s.work_end_hour
        too_soon = begin < self.now + timedelta(hours=self.s.min_notice_hours)
        return json.dumps(
            {
                "slot": self._slot_json(TimeSlot(begin, end)),
                "free": not conflicts,
                "conflicts": [self._event_public(e) for e in conflicts],
                "within_working_hours": in_hours,
                "weekend": begin.astimezone(self.s.tzinfo).weekday() >= 5,
                "less_than_min_notice": too_soon,
            }
        )

    # ---- side effects ----------------------------------------------------------------------

    def create_meeting(
        self,
        start: str,
        title: str,
        attendees: list[str],
        duration_minutes: int | None = None,
        notes: str = "",
        force: bool = False,
    ) -> str:
        begin = self._parse_dt(start)
        end = begin + timedelta(minutes=duration_minutes or self.s.default_duration_minutes)
        invitees = self._validate_attendees(attendees)
        if isinstance(invitees, str):
            return json.dumps({"error": invitees})

        existing = self.calendar.list_events(begin, end)
        for e in existing:
            if e.start == begin and set(e.attendees) & set(invitees):
                return json.dumps({"status": "already_exists", "event": self._event_public(e)})
        conflicts = [e for e in existing if e.is_busy and e.slot.overlaps(TimeSlot(begin, end))]
        if conflicts and not force:
            return json.dumps(
                {
                    "error": "slot conflicts with existing events; pick another time or pass force=true "
                    "only if the owner explicitly approved double-booking",
                    "conflicts": [self._event_public(e) for e in conflicts],
                }
            )

        body = notes or f"Scheduled by {self.s.agent_name} on behalf of {self.s.owner_name}."
        if self.s.dry_run:
            event = CalendarEvent(
                id="dry-run", subject=title, start=begin, end=end, attendees=tuple(invitees)
            )
            log.info("[DRY RUN] would create event %s", self._event_public(event))
        else:
            event = self.calendar.create_event(
                subject=title, start=begin, end=end, attendees=tuple(invitees), body=body
            )
        self.log.events.append(event)
        return json.dumps({"status": "created", "dry_run": self.s.dry_run, "event": self._event_public(event)})

    def send_reply(self, body: str) -> str:
        if self.log.replies:
            return json.dumps({"error": "a reply was already sent in this run; do not send another"})
        extra_cc = (self.s.owner_email,) if self.s.always_cc_owner else ()
        message = build_reply(
            self.thread, agent_email=self.s.agent_email, body=body.strip(), extra_cc=extra_cc
        )
        if not message.to:
            return json.dumps({"error": "no recipients after excluding the agent address"})
        if self.s.dry_run:
            log.info("[DRY RUN] would send to=%s cc=%s\n%s", message.to, message.cc, message.body)
        else:
            self.email.send(message, self.thread)
        self.log.replies.append(message)
        return json.dumps({"status": "sent", "dry_run": self.s.dry_run, "to": message.to, "cc": message.cc})

    def escalate_to_owner(self, reason: str) -> str:
        self.log.escalations.append(reason)
        latest = self.thread.latest
        body = (
            f"Hi {self.s.owner_name},\n\nI need your input on the thread \"{latest.subject}\" "
            f"from {latest.sender}:\n\n{reason}\n\nReply on the original thread and copy me and I'll "
            f"take it from there.\n\n{self.s.agent_name}"
        )
        message = OutgoingEmail(
            to=(self.s.owner_email,),
            cc=(),
            subject=f"[Needs your input] {latest.subject}",
            body=body,
            in_reply_to=latest.message_id,
            references=(*latest.references, latest.message_id),
        )
        if self.s.dry_run:
            log.info("[DRY RUN] would escalate to owner: %s", reason)
        else:
            self.email.send(message, self.thread)
        return json.dumps({"status": "escalated", "dry_run": self.s.dry_run})

    # ---- helpers ---------------------------------------------------------------------------

    def _busy(self, start: datetime, end: datetime) -> list[TimeSlot]:
        return [e.slot for e in self.calendar.list_events(start, end) if e.is_busy]

    def _validate_attendees(self, attendees: list[str]) -> list[str] | str:
        allowed = {a.lower() for a in self.thread.participants} | {self.s.owner_email}
        allowed.discard(self.s.agent_email)
        cleaned = []
        for a in attendees:
            a = a.strip().lower()
            if a == self.s.agent_email:
                continue
            if a not in allowed:
                return f"{a} is not on this email thread; only thread participants can be invited"
            if a not in cleaned:
                cleaned.append(a)
        if self.s.owner_email not in cleaned:
            cleaned.insert(0, self.s.owner_email)
        if len(cleaned) < 2:
            return "at least one attendee besides the owner is required"
        return cleaned

    def _parse_dt(self, value: str) -> datetime:
        dt = datetime.fromisoformat(value.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.s.tzinfo)
        return dt.astimezone(self.s.tzinfo)

    def _slot_json(self, slot: TimeSlot) -> dict:
        s = slot.start.astimezone(self.s.tzinfo)
        e = slot.end.astimezone(self.s.tzinfo)
        return {
            "start": s.isoformat(),
            "end": e.isoformat(),
            "human": f"{s:%A, %B %-d} {s:%-I:%M %p}–{e:%-I:%M %p} {s:%Z}",
        }

    def _event_public(self, e: CalendarEvent) -> dict:
        # Never expose the owner's other meeting subjects to counterparties.
        out = {"start": e.start.astimezone(self.s.tzinfo).isoformat(), "end": e.end.astimezone(self.s.tzinfo).isoformat()}
        if e.attendees:
            out["attendees"] = list(e.attendees)
        if e.online_meeting_url:
            out["join_url"] = e.online_meeting_url
        if e.web_link:
            out["web_link"] = e.web_link
        return out
