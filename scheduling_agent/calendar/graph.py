from __future__ import annotations

import base64
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import msal
import requests

from ..models import CalendarEvent

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Calendars.ReadWrite", "User.Read"]


class TokenCacheStore:
    """Loads/saves the MSAL token cache.

    Bootstrap: run `scheduling-agent auth-graph` locally, which writes `path`. For serverless
    deployments, either ship that file or set GRAPH_TOKEN_CACHE_B64 with its base64 contents.
    MSAL refreshes tokens in memory; when it does, `save` persists the new cache to `path` if
    writable so a long-lived deployment does not lose the refresh token rotation.
    """

    def __init__(self, path: str | Path, b64: str = "") -> None:
        self.path = Path(path)
        self.b64 = b64

    def load(self) -> msal.SerializableTokenCache:
        cache = msal.SerializableTokenCache()
        if self.b64:
            cache.deserialize(base64.b64decode(self.b64).decode())
        elif self.path.exists():
            cache.deserialize(self.path.read_text())
        return cache

    def save(self, cache: msal.SerializableTokenCache) -> None:
        if not cache.has_state_changed:
            return
        try:
            self.path.write_text(cache.serialize())
        except OSError as exc:
            log.warning("Could not persist Graph token cache to %s: %s", self.path, exc)


class GraphCalendar:
    """Microsoft 365 calendar via Microsoft Graph, delegated (user) permissions."""

    def __init__(
        self,
        *,
        client_id: str,
        tenant_id: str,
        cache_store: TokenCacheStore,
        timezone: str,
    ) -> None:
        self.cache_store = cache_store
        self.cache = cache_store.load()
        self.app = msal.PublicClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            token_cache=self.cache,
        )
        self.tz = ZoneInfo(timezone)
        self.timezone = timezone

    def _token(self) -> str:
        accounts = self.app.get_accounts()
        result = self.app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
        if not result or "access_token" not in result:
            raise RuntimeError(
                "No cached Graph credentials. Run `scheduling-agent auth-graph` locally and "
                "deploy the resulting token cache."
            )
        self.cache_store.save(self.cache)
        return result["access_token"]

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Prefer": f'outlook.timezone="{self.timezone}"',
            "Content-Type": "application/json",
        }

    def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        params = {
            "startDateTime": start.astimezone(self.tz).isoformat(),
            "endDateTime": end.astimezone(self.tz).isoformat(),
            "$select": "id,subject,start,end,showAs,attendees,webLink,onlineMeeting,isCancelled",
            "$orderby": "start/dateTime",
            "$top": "200",
        }
        url: str | None = f"{GRAPH}/me/calendarView"
        events: list[CalendarEvent] = []
        while url:
            resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("value", []):
                if item.get("isCancelled"):
                    continue
                events.append(self._to_event(item))
            url = data.get("@odata.nextLink")
            params = {}
        return events

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
            "attendees": [
                {"emailAddress": {"address": a}, "type": "required"} for a in attendees
            ],
            "isOnlineMeeting": online_meeting,
        }
        if online_meeting:
            payload["onlineMeetingProvider"] = "teamsForBusiness"
        resp = requests.post(f"{GRAPH}/me/events", headers=self._headers(), json=payload, timeout=30)
        resp.raise_for_status()
        return self._to_event(resp.json())

    def _to_event(self, item: dict) -> CalendarEvent:
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


def device_code_login(client_id: str, tenant_id: str, cache_store: TokenCacheStore) -> str:
    """Interactive one-time bootstrap. Returns the signed-in account's username."""
    cache = cache_store.load()
    app = msal.PublicClientApplication(
        client_id, authority=f"https://login.microsoftonline.com/{tenant_id}", token_cache=cache
    )
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Device flow failed: {flow.get('error_description', flow)}")
    print(flow["message"])
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(result.get("error_description", str(result)))
    cache_store.save(cache)
    return result.get("id_token_claims", {}).get("preferred_username", "unknown")


def _naive_iso(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).replace(tzinfo=None).isoformat(timespec="seconds")


def _parse_graph_dt(value: dict) -> datetime:
    raw = value["dateTime"].split(".")[0]
    return datetime.fromisoformat(raw).replace(tzinfo=ZoneInfo(value["timeZone"]))
