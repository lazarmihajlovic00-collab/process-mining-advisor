"""Working-time calendar in integer minutes since T0 (Monday 2026-07-06 00:00, start of Q3 2026).

Everything in the simulator runs on this clock; timestamps become datetimes only when the log is written.
"""

from __future__ import annotations

from datetime import datetime, timedelta

T0 = datetime(2026, 7, 6)  # a Monday
DAY = 1440
WORKDAYS = (0, 1, 2, 3, 4)


def to_dt(t: float) -> datetime:
    return T0 + timedelta(minutes=float(t))


def from_dt(d: datetime) -> float:
    return (d - T0).total_seconds() / 60.0


def weekday(t: float) -> int:
    return int(t // DAY) % 7


def next_open(t: float, windows: tuple[tuple[int, int], ...] = ((480, 960),), days: tuple[int, ...] = WORKDAYS) -> float:
    """Earliest time >= t that falls inside one of the daily windows (minutes from midnight) on one of the days."""
    for _ in range(400):
        d = int(t // DAY)
        m = t - d * DAY
        if d % 7 in days:
            for start, end in windows:
                if m < start:
                    return d * DAY + start
                if m < end:
                    return t
        t = (d + 1) * DAY
    raise ValueError("no open window within 400 days")


def add_open(t: float, minutes: float, windows: tuple[tuple[int, int], ...] = ((480, 960),),
             days: tuple[int, ...] = WORKDAYS) -> float:
    """Start at the first open moment >= t and work `minutes`; work that does not fit continues in the next window."""
    t = next_open(t, windows, days)
    while True:
        d = int(t // DAY)
        m = t - d * DAY
        end = next(e for s, e in windows if s <= m < e)
        avail = end - m
        if minutes <= avail:
            return t + minutes
        minutes -= avail
        t = next_open(d * DAY + end, windows, days)
