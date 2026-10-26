"""Offline tests: calendar events carry family-member tags, and writes never echo them back.

Run from the repository root:
    python3 -m unittest discover -s tests -p 'test_calendar_people.py' -v

The real ``_parse_events`` and ``_to_skylight_fields`` are loaded from calendar.py's AST, so
Home Assistant and the vendor API are not needed.
"""
from __future__ import annotations

import ast
from collections.abc import Mapping
from datetime import date, datetime
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any
import unittest

ROOT = Path(__file__).resolve().parents[1] / "custom_components/skylight"

spec = importlib.util.spec_from_file_location("skylight_people", ROOT / "people.py")
people = importlib.util.module_from_spec(spec)
spec.loader.exec_module(people)


def load_calendar_functions():
    tree = ast.parse((ROOT / "calendar.py").read_text())
    wanted = {"_parse_events", "_to_skylight_fields", "_wire_datetime"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    module = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes],
        type_ignores=[]))
    scope = {
        "CalendarEvent": lambda **kw: NS(**kw),
        "parse_datetime": datetime.fromisoformat,
        "date": date, "datetime": datetime, "Mapping": Mapping, "Any": Any,
        "add_people_line": people.add_people_line, "event_people": people.event_people,
        "family_member_labels": people.family_member_labels, "strip_people_line": people.strip_people_line,
    }
    exec(compile(module, str(ROOT / "calendar.py"), "exec"), scope)
    return scope


FUNCS = load_calendar_functions()


def feed(events, included=None):
    body = {"data": events}
    if included is not None:
        body["included"] = included
    return body


def raw_event(eid, summary, description="", categories=()):
    return {"id": eid, "attributes": {
                "summary": summary, "description": description, "location": "",
                "starts_at": "2026-10-27T16:55:00-04:00", "ends_at": "2026-10-27T17:55:00-04:00"},
            "relationships": {"categories": {"data": [{"id": c, "type": "category"} for c in categories]}}}


INCLUDED = [
    {"id": "1", "type": "category", "attributes": {"label": "Ivy", "linked_to_profile": True}},
    {"id": "2", "type": "category", "attributes": {"label": "Stella", "linked_to_profile": True}},
    {"id": "3", "type": "category", "attributes": {"label": "US Holidays", "linked_to_profile": False}},
]


class ParseEvents(unittest.TestCase):
    def parse(self, body):
        return FUNCS["_parse_events"](body)

    def test_tagged_event_gets_a_people_line(self):
        [event] = self.parse(feed([raw_event("a", "Skating", categories=["2", "1"])], INCLUDED))
        self.assertEqual(event.description, "People: Stella, Ivy")
        self.assertEqual(event.summary, "Skating")

    def test_existing_description_is_kept_above_the_line(self):
        [event] = self.parse(feed([raw_event("a", "Skating", "Bring skates", ["2"])], INCLUDED))
        self.assertEqual(event.description, "Bring skates\n\nPeople: Stella")

    def test_untagged_and_bucket_only_events_are_unchanged(self):
        events = self.parse(feed([raw_event("a", "Ensemble", "x"), raw_event("b", "Holiday", "", ["3"])], INCLUDED))
        self.assertEqual([e.description for e in events], ["x", ""])

    def test_feed_without_included_still_parses(self):
        [event] = self.parse(feed([raw_event("a", "Skating", "n", ["2"])]))
        self.assertEqual(event.description, "n")

    def test_the_names_are_matchable_by_a_template(self):
        import re
        [event] = self.parse(feed([raw_event("a", "Power of the pen", categories=["1"])], INCLUDED))
        self.assertTrue(re.search(r"\b(ivy|stella)\b", event.description, re.I))


class WriteFields(unittest.TestCase):
    def test_people_line_is_not_written_back(self):
        fields = FUNCS["_to_skylight_fields"]({"description": "Bring skates\n\nPeople: Stella"})
        self.assertEqual(fields["description"], "Bring skates")

    def test_description_only_people_line_becomes_empty(self):
        self.assertEqual(FUNCS["_to_skylight_fields"]({"description": "People: Ivy"})["description"], "")

    def test_none_description_is_empty(self):
        self.assertEqual(FUNCS["_to_skylight_fields"]({"description": None})["description"], "")

    def test_other_fields_are_untouched(self):
        fields = FUNCS["_to_skylight_fields"]({"summary": "S", "location": "Rink"})
        self.assertEqual(fields, {"summary": "S", "location": "Rink"})


if __name__ == "__main__":
    unittest.main()
