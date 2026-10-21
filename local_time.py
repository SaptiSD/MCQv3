"""The class's clock, as opposed to the server's.

Every opening and closing time used to be turned into a timestamp with a bare
`.astimezone()`, which means "the timezone of whatever machine runs this". The
live host runs on UTC, so a teacher in Texas who typed "8:00 AM" saved 8:00 UTC
-- 3:00 AM Central -- and their own screen, formatted on the same machine, read
it back to them as 8:00 AM. Quizzes opened five hours before anyone meant them
to, and nothing on screen could show it.

Pure apart from reading the setting: `[app] timezone` in secrets.toml, or the
MCQ_TIMEZONE environment variable, defaulting to the class's own zone.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_ZONE = "America/Chicago"


@lru_cache(maxsize=1)
def zone() -> ZoneInfo:
    name = os.getenv("MCQ_TIMEZONE", "")
    if not name:
        try:
            import streamlit as st
            name = str(st.secrets["app"]["timezone"])
        except Exception:
            name = ""
    try:
        return ZoneInfo(name or DEFAULT_ZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_ZONE)


def zone_label(moment: datetime | None = None) -> str:
    """The abbreviation teachers read next to a time: CDT, CST."""
    return (moment or datetime.now(zone())).astimezone(zone()).tzname() or str(zone())


def now() -> datetime:
    return datetime.now(zone())


def today() -> date:
    return now().date()


def combine(day: date, clock: time) -> datetime:
    """A date and time a teacher picked, read as the class's local time."""
    return datetime.combine(day, clock).replace(tzinfo=zone())


def parse(stored) -> datetime:
    """A stored timestamp as an aware datetime in the class's zone."""
    moment = stored if isinstance(stored, datetime) else datetime.fromisoformat(str(stored))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(zone())


def fmt(stored, with_zone: bool = True) -> str:
    moment = parse(stored)
    text = moment.strftime("%b %d, %I:%M %p").replace(" 0", " ")
    return f"{text} {moment.tzname()}" if with_zone else text
