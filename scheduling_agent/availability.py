from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import TimeSlot


def merge_busy(busy: list[TimeSlot], buffer: timedelta) -> list[TimeSlot]:
    padded = sorted(
        (TimeSlot(b.start - buffer, b.end + buffer) for b in busy), key=lambda s: s.start
    )
    merged: list[TimeSlot] = []
    for slot in padded:
        if merged and slot.start <= merged[-1].end:
            last = merged[-1]
            merged[-1] = TimeSlot(last.start, max(last.end, slot.end))
        else:
            merged.append(slot)
    return merged


def find_free_slots(
    *,
    busy: list[TimeSlot],
    window_start: datetime,
    window_end: datetime,
    duration: timedelta,
    tz: ZoneInfo,
    work_start_hour: int,
    work_end_hour: int,
    buffer: timedelta = timedelta(0),
    step: timedelta = timedelta(minutes=30),
    limit: int | None = None,
    skip_weekends: bool = True,
) -> list[TimeSlot]:
    """Return candidate slots inside working hours that do not overlap any busy block.

    All datetimes must be timezone-aware. Candidates start on `step` boundaries.
    """
    blocked = merge_busy(busy, buffer)
    out: list[TimeSlot] = []

    day = window_start.astimezone(tz).date()
    last_day = window_end.astimezone(tz).date()
    while day <= last_day:
        if skip_weekends and day.weekday() >= 5:
            day += timedelta(days=1)
            continue
        day_start = datetime.combine(day, time(work_start_hour), tzinfo=tz)
        day_end = datetime.combine(day, time(work_end_hour), tzinfo=tz)
        cursor = _ceil_to_step(max(day_start, window_start.astimezone(tz)), step)
        while cursor + duration <= min(day_end, window_end.astimezone(tz)):
            candidate = TimeSlot(cursor, cursor + duration)
            if not any(candidate.overlaps(b) for b in blocked):
                out.append(candidate)
                if limit is not None and len(out) >= limit:
                    return out
            cursor += step
        day += timedelta(days=1)
    return out


def spread_slots(slots: list[TimeSlot], limit: int) -> list[TimeSlot]:
    """Pick up to `limit` slots preferring distinct days, then distinct times of day."""
    if len(slots) <= limit:
        return slots
    by_day: dict = {}
    for s in slots:
        by_day.setdefault(s.start.date(), []).append(s)
    picked: list[TimeSlot] = []
    round_idx = 0
    while len(picked) < limit:
        progressed = False
        for day_slots in by_day.values():
            if round_idx < len(day_slots):
                picked.append(day_slots[round_idx])
                progressed = True
                if len(picked) >= limit:
                    break
        if not progressed:
            break
        round_idx += 1
    return sorted(picked, key=lambda s: s.start)


def _ceil_to_step(dt: datetime, step: timedelta) -> datetime:
    seconds = int(step.total_seconds())
    epoch = int(dt.timestamp())
    remainder = epoch % seconds
    if remainder == 0:
        return dt
    return dt + timedelta(seconds=seconds - remainder)
