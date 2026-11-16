"""Parse the ids Skylight lists chores under.

Kept free of Home Assistant imports so the offline tests can load it directly.
"""

from __future__ import annotations

from datetime import date, time
import re
from typing import NamedTuple

# Occurrence suffix after the series id: a date, plus a time of day for chores
# scheduled at one (``101940933-2026-11-11-1400``). ASCII digits only.
_OCCURRENCE = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})(?:-([0-9]{2})([0-9]{2}))?")


class ChoreUid(NamedTuple):
    """A chore id taken apart.

    ``instance_date`` is ``YYYY-MM-DD`` and ``instance_time`` is ``HH:MM`` (the
    same form as a chore's ``start_time``). Both are ``None`` for a bare id.
    """

    series: str
    instance_date: str | None
    instance_time: str | None


def split_chore_uid(uid: str) -> ChoreUid:
    """Chore uid -> its series id and, for an occurrence, which one.

    Skylight lists a recurring chore once per occurrence under a composite id:
    ``<series>-YYYY-MM-DD`` for an all-day chore and ``<series>-YYYY-MM-DD-HHMM``
    for one scheduled at a time of day. One-off chores keep the bare series
    number. Every chore route is keyed on the series id alone, so the composite
    form has to be taken apart before it can go in a URL; the rest becomes
    ``instance_date`` / ``instance_time`` in the completion body.

    Anything that doesn't parse is returned whole as the series, with no
    occurrence, so callers can reject it rather than guess.
    """
    series, sep, suffix = uid.partition("-")
    if sep and series.isascii() and series.isdigit():
        match = _OCCURRENCE.fullmatch(suffix)
        if match:
            day, hour, minute = match.groups()
            try:
                parsed_day = date.fromisoformat(day)
                parsed_time = time(int(hour), int(minute)) if hour else None
            except ValueError:
                pass
            else:
                return ChoreUid(
                    series,
                    parsed_day.isoformat(),
                    parsed_time.strftime("%H:%M") if parsed_time else None,
                )
    return ChoreUid(uid, None, None)
