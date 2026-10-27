"""When a routine runs: every so often, at a time of day on chosen days, monthly, or once.

Times are the Mac's own clock: the app runs on this machine and nowhere
else, so "09:00" means nine in the morning wherever the machine is, across daylight-saving
changes, and nothing is stored about a time zone.
"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, field_validator

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
CLOCK = r"^([01]\d|2[0-3]):[0-5]\d$"


class Interval(BaseModel):
    """Every so often, counted from the last run. The first run starts right away."""

    kind: Literal["interval"] = "interval"
    # A minute is the floor: the scheduler looks for work every ten seconds.
    every_seconds: int = Field(ge=60, le=366 * 86_400)


class Weekly(BaseModel):
    """At a time of day on chosen days of the week. All seven days is "every day"."""

    kind: Literal["weekly"] = "weekly"
    time: str = Field(pattern=CLOCK)
    days: list[int] = Field(min_length=1, max_length=7)  # Monday is 0

    @field_validator("days")
    @classmethod
    def real_days(cls, value: list[int]) -> list[int]:
        if any(day not in range(7) for day in value):
            raise ValueError("Days are numbered 0 (Monday) to 6 (Sunday).")
        return sorted(set(value))


class Monthly(BaseModel):
    """At a time of day on one day of the month. The 31st means the last day of a shorter month."""

    kind: Literal["monthly"] = "monthly"
    time: str = Field(pattern=CLOCK)
    day: int = Field(ge=1, le=31)


class Once(BaseModel):
    """One time, at a date and time on this Mac's clock."""

    kind: Literal["once"] = "once"
    at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):[0-5]\d$")

    @field_validator("at")
    @classmethod
    def a_real_date(cls, value: str) -> str:
        datetime.fromisoformat(value)
        return value


Timing = Annotated[Interval | Weekly | Monthly | Once, Field(discriminator="kind")]
_TIMING = TypeAdapter(Timing)


def parse(value: Any) -> Interval | Weekly | Monthly | Once:
    return _TIMING.validate_python(value)


def _local(moment: datetime, tz: tzinfo | None) -> datetime:
    """An aware moment as this Mac's wall clock, or a given zone's in tests."""
    return moment.astimezone(tz).replace(tzinfo=None)


def _aware(naive: datetime, tz: tzinfo | None) -> datetime:
    return (naive.replace(tzinfo=tz) if tz is not None else naive.astimezone()).astimezone(UTC)


def next_due(
    timing: Interval | Weekly | Monthly | Once,
    *,
    created_at: str,
    last_run_at: str | None,
    tz: tzinfo | None = None,
) -> datetime | None:
    """The next moment this should fire, or None when it never will again.

    Counted from the last run, or from when it was made if it has never run. So a routine whose
    time passed while the app was closed is due once when it opens, not once per time missed,
    and one made at ten for "every day at nine" first runs tomorrow.
    """
    last = datetime.fromisoformat(last_run_at) if last_run_at else None
    if isinstance(timing, Interval):
        return last + timedelta(seconds=timing.every_seconds) if last else datetime.fromisoformat(created_at)
    if isinstance(timing, Once):
        return None if last else _aware(datetime.fromisoformat(timing.at), tz)
    after = _local(last or datetime.fromisoformat(created_at), tz)
    clock = time(int(timing.time[:2]), int(timing.time[3:]))
    if isinstance(timing, Weekly):
        for offset in range(8):
            day = after.date() + timedelta(days=offset)
            candidate = datetime.combine(day, clock)
            if day.weekday() in timing.days and candidate > after:
                return _aware(candidate, tz)
        return None
    year, month = after.year, after.month
    for _ in range(14):
        candidate = datetime.combine(
            datetime(year, month, min(timing.day, calendar.monthrange(year, month)[1])), clock
        )
        if candidate > after:
            return _aware(candidate, tz)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return None


def _every(seconds: int) -> str:
    for size, name in ((604_800, "week"), (86_400, "day"), (3_600, "hour"), (60, "minute")):
        if seconds % size == 0:
            count = seconds // size
            return f"every {name}" if count == 1 else f"every {count} {name}s"
    return f"every {seconds} seconds"


def _ordinal(day: int) -> str:
    return f"{day}{'th' if 10 <= day % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')}"


def describe(timing: Interval | Weekly | Monthly | Once) -> str:
    """The timing in the words a person would use for it."""
    if isinstance(timing, Interval):
        return _every(timing.every_seconds)
    if isinstance(timing, Once):
        at = datetime.fromisoformat(timing.at)
        return f"once on {at.strftime('%a')} {at.day} {at.strftime('%b %Y')} at {at.strftime('%H:%M')}"
    if isinstance(timing, Monthly):
        day = "the last day" if timing.day == 31 else f"the {_ordinal(timing.day)}"
        return f"on {day} of each month at {timing.time}"
    if timing.days == list(range(7)):
        return f"every day at {timing.time}"
    if timing.days == list(range(5)):
        return f"on weekdays at {timing.time}"
    if timing.days == [5, 6]:
        return f"at weekends at {timing.time}"
    return f"on {', '.join(DAY_NAMES[day][:3] for day in timing.days)} at {timing.time}"


def from_words(
    repeat: str,
    *,
    time: str = "",
    date: str = "",
    days: list[str] | None = None,
    day_of_month: int | None = None,
    every_minutes: int | None = None,
) -> Interval | Weekly | Monthly | Once:
    """A timing from the plain fields an agent fills in. Raises ValueError saying what is missing."""
    if repeat == "every":
        if not every_minutes:
            raise ValueError("Say how often with every_minutes, for example 60 for every hour.")
        return parse({"kind": "interval", "every_seconds": int(every_minutes) * 60})
    if not time:
        raise ValueError('Give the time of day as "HH:MM" on a 24-hour clock, for example "09:00".')
    if repeat == "once":
        if not date:
            raise ValueError('Give the date as "YYYY-MM-DD".')
        return parse({"kind": "once", "at": f"{date}T{time}"})
    if repeat == "monthly":
        if not day_of_month:
            raise ValueError("Give day_of_month, 1 to 31. 31 means the last day of the month.")
        return parse({"kind": "monthly", "time": time, "day": int(day_of_month)})
    if repeat == "weekly":
        known = {name.lower(): index for index, name in enumerate(DAY_NAMES)}
        chosen = [known.get(str(day).strip().lower()) for day in days or []]
        if not chosen or None in chosen:
            raise ValueError('Give days as full English names, for example ["Monday", "Thursday"].')
        return parse({"kind": "weekly", "time": time, "days": chosen})
    if repeat not in {"daily", "weekdays"}:
        raise ValueError('repeat is one of "once", "every", "daily", "weekdays", "weekly" or "monthly".')
    return parse({"kind": "weekly", "time": time, "days": list(range(5 if repeat == "weekdays" else 7))})
