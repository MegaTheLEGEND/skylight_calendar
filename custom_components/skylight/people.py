"""Family-member tags on calendar events.

Skylight tags an event with one or more *categories*. Categories that are
``linked_to_profile`` are family members; the rest are calendar buckets such as
"US Holidays". The event feed returns the tags as ``relationships.categories``
(ids only) plus an ``included`` list holding the category records, but only when
the request asks for ``include=categories``.

Home Assistant's ``CalendarEvent`` has no attendee field, so the names are
appended to the description as a ``People:`` line. Automations can match it with
``trigger.calendar_event.description`` or ``calendar.get_events``. The line is
stripped again before a description is written back to Skylight, so editing an
event in Home Assistant never copies it into the Skylight event itself.

Kept free of Home Assistant imports so it can be unit tested offline.
"""

from __future__ import annotations

import re
from typing import Any

PEOPLE_PREFIX = "People: "
# The generated trailer: the last line of the description, after a blank line
# (or the whole description when it was empty).
_TRAILER = re.compile(r"(?:\n\n|\A)" + re.escape(PEOPLE_PREFIX) + r"[^\n]*\Z")


def family_member_labels(included: list[dict] | None) -> dict[str, str]:
    """Map category id → name, for family members only."""
    labels: dict[str, str] = {}
    for record in included or []:
        if record.get("type") != "category":
            continue
        attrs = record.get("attributes") or {}
        label = (attrs.get("label") or "").strip()
        if label and attrs.get("linked_to_profile"):
            labels[str(record.get("id"))] = label
    return labels


def event_people(event: dict, labels: dict[str, str]) -> list[str]:
    """Names of the family members tagged on one event, in Skylight's order."""
    refs = ((event.get("relationships") or {}).get("categories") or {}).get("data") or []
    names: list[str] = []
    for ref in refs:
        name = labels.get(str(ref.get("id")))
        if name and name not in names:
            names.append(name)
    return names


def add_people_line(description: str, people: list[str]) -> str:
    """Return ``description`` with a ``People: A, B`` trailer (unchanged if no people)."""
    description = strip_people_line(description)
    if not people:
        return description
    line = PEOPLE_PREFIX + ", ".join(people)
    return f"{description}\n\n{line}" if description else line


def strip_people_line(description: Any) -> str:
    """Remove the generated trailer, if present."""
    return _TRAILER.sub("", description or "")
