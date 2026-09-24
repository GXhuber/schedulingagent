from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ..graph_client import GraphClient
from ..models import CalendarEvent


class GraphCalendar:
    """Microsoft 365 calendar via Graph.

    `user_path` is "/me" in delegated mode or "/users/{owner-upn}" in app-only mode.
    """

    def __init__(self, client: GraphClient, *, user_path: str, timezone: str) -> None:
        self.client = client
        self.user_path = user_path.rstrip("/")
        self.tz = ZoneInfo(timezone)
        self.timezone = timezone

    def _prefer(self) -> dict[str, str]:
        return {"Prefer": f'outlook.timezone="{self.timezone}"'}

    def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        params = {
            "startDateTime": start.astimezone(self.tz).isoformat(),
            "endDateTime": end.astimezone(self.tz).isoformat(),
            "$select": "id,subject,start,end,showAs,attendees,webLink,onlineMeeting,isCancelled",
            "$orderby": "start/dateTime",
            "$top": "200",
        }
        items = self.client.get_all(f"{self.user_path}/calendarView", params=params, headers=self._prefer())
        return [self._to_event(i) for i in items if not i.get("isCancelled")]

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
        payload = {
            "subject": subject,
            "body": {"contentType": "text", "content": body},
            "start": {"dateTime": _naive_iso(start, self.tz), "timeZone": self.timezone},
            "end": {"dateTime": _naive_iso(end, self.tz), "timeZone": self.timezone},
            "attendees": [{"emailAddress": {"address": a}, "type": "required"} for a in attendees],
            "isOnlineMeeting": online_meeting,
        }
        if online_meeting:
            payload["onlineMeetingProvider"] = "teamsForBusiness"
        created = self.client.post(f"{self.user_path}/events", json=payload, headers=self._prefer())
        return self._to_event(created or {})

    @staticmethod
    def _to_event(item: dict) -> CalendarEvent:
        online = item.get("onlineMeeting") or {}
        return CalendarEvent(
            id=item["id"],
            subject=item.get("subject") or "(no subject)",
            start=_parse_graph_dt(item["start"]),
            end=_parse_graph_dt(item["end"]),
            attendees=tuple(
                (a.get("emailAddress") or {}).get("address", "").lower()
                for a in item.get("attendees", [])
                if (a.get("emailAddress") or {}).get("address")
            ),
            is_busy=item.get("showAs", "busy") not in {"free", "workingElsewhere"},
            web_link=item.get("webLink"),
            online_meeting_url=online.get("joinUrl"),
        )


def _naive_iso(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).replace(tzinfo=None).isoformat(timespec="seconds")


def _parse_graph_dt(value: dict) -> datetime:
    raw = value["dateTime"].split(".")[0]
    return datetime.fromisoformat(raw).replace(tzinfo=ZoneInfo(value["timeZone"]))
