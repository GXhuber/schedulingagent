from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from scheduling_agent.availability import find_free_slots, merge_busy, spread_slots
from scheduling_agent.models import TimeSlot

TZ = ZoneInfo("America/New_York")


def _dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ)


def test_merge_busy_pads_and_merges_adjacent_blocks():
    busy = [TimeSlot(_dt(21, 9), _dt(21, 10)), TimeSlot(_dt(21, 10, 10), _dt(21, 11))]
    merged = merge_busy(busy, timedelta(minutes=15))
    assert merged == [TimeSlot(_dt(21, 8, 45), _dt(21, 11, 15))]


def test_free_slots_respect_working_hours_busy_blocks_and_weekends():
    # Monday 2026-09-21; busy 9-12, so first 30-min slot with 15-min buffer is 12:15 -> ceil to 12:30
    busy = [TimeSlot(_dt(21, 9), _dt(21, 12))]
    slots = find_free_slots(
        busy=busy,
        window_start=_dt(21, 0),
        window_end=_dt(27, 23),  # through Sunday
        duration=timedelta(minutes=30),
        tz=TZ,
        work_start_hour=9,
        work_end_hour=17,
        buffer=timedelta(minutes=15),
    )
    assert slots[0].start == _dt(21, 12, 30)
    assert all(9 <= s.start.hour < 17 for s in slots)
    assert all(s.end <= s.start.replace(hour=17, minute=0) for s in slots)
    assert all(s.start.weekday() < 5 for s in slots)
    assert not any(s.overlaps(busy[0]) for s in slots)


def test_free_slots_honour_window_start_mid_day():
    slots = find_free_slots(
        busy=[],
        window_start=_dt(21, 14, 20),
        window_end=_dt(21, 17),
        duration=timedelta(minutes=60),
        tz=TZ,
        work_start_hour=9,
        work_end_hour=17,
    )
    assert [s.start for s in slots] == [_dt(21, 14, 30), _dt(21, 15), _dt(21, 15, 30), _dt(21, 16)]


def test_spread_slots_prefers_distinct_days():
    slots = [
        TimeSlot(_dt(21, 9), _dt(21, 9, 30)),
        TimeSlot(_dt(21, 10), _dt(21, 10, 30)),
        TimeSlot(_dt(22, 9), _dt(22, 9, 30)),
        TimeSlot(_dt(23, 9), _dt(23, 9, 30)),
    ]
    picked = spread_slots(slots, 3)
    assert [s.start.day for s in picked] == [21, 22, 23]
