"""Offline tests for family-member tags on calendar events.

Run from the repository root:
    python3 -m unittest discover -s tests -p 'test_people.py' -v

``people.py`` has no Home Assistant imports, so it is loaded straight from its file.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "custom_components/skylight/people.py"
spec = importlib.util.spec_from_file_location("skylight_people", SOURCE)
people = importlib.util.module_from_spec(spec)
spec.loader.exec_module(people)


def category(cid, label, profile=True):
    return {"id": cid, "type": "category",
            "attributes": {"label": label, "linked_to_profile": profile}}


def event(*category_ids):
    return {"id": "e1", "relationships": {"categories": {"data": [
        {"id": cid, "type": "category"} for cid in category_ids]}}}


INCLUDED = [
    category("1", "Ivy"), category("2", "Stella"), category("3", "US Holidays", profile=False),
    {"id": "9", "type": "calendar_account", "attributes": {"email": "a@example.com"}},
]


class FamilyMemberLabels(unittest.TestCase):
    def test_only_family_members_are_kept(self):
        self.assertEqual(people.family_member_labels(INCLUDED), {"1": "Ivy", "2": "Stella"})

    def test_missing_or_empty_included(self):
        self.assertEqual(people.family_member_labels(None), {})
        self.assertEqual(people.family_member_labels([]), {})

    def test_blank_label_is_ignored(self):
        self.assertEqual(people.family_member_labels([category("1", "  ")]), {})


class EventPeople(unittest.TestCase):
    labels = {"1": "Ivy", "2": "Stella"}

    def test_names_follow_skylight_order(self):
        self.assertEqual(people.event_people(event("2", "1"), self.labels), ["Stella", "Ivy"])

    def test_calendar_buckets_and_unknown_ids_are_ignored(self):
        self.assertEqual(people.event_people(event("3", "99", "1"), self.labels), ["Ivy"])

    def test_duplicates_collapse(self):
        self.assertEqual(people.event_people(event("1", "1"), self.labels), ["Ivy"])

    def test_event_without_relationships(self):
        self.assertEqual(people.event_people({"id": "e"}, self.labels), [])
        self.assertEqual(people.event_people({"relationships": {"categories": None}}, self.labels), [])

    def test_integer_ids_match_string_labels(self):
        ev = {"relationships": {"categories": {"data": [{"id": 1}]}}}
        self.assertEqual(people.event_people(ev, self.labels), ["Ivy"])


class DescriptionLine(unittest.TestCase):
    def test_adds_line_after_existing_description(self):
        self.assertEqual(people.add_people_line("Bring skates", ["Stella"]),
                         "Bring skates\n\nPeople: Stella")

    def test_empty_description_gets_only_the_line(self):
        self.assertEqual(people.add_people_line("", ["Ivy", "Stella"]), "People: Ivy, Stella")
        self.assertEqual(people.add_people_line(None, ["Ivy"]), "People: Ivy")

    def test_no_people_leaves_description_alone(self):
        self.assertEqual(people.add_people_line("Bring skates", []), "Bring skates")
        self.assertEqual(people.add_people_line("", []), "")

    def test_never_stacks_two_lines(self):
        once = people.add_people_line("Notes", ["Ivy"])
        self.assertEqual(people.add_people_line(once, ["Ivy"]), once)
        self.assertEqual(people.add_people_line(once, ["Stella"]), "Notes\n\nPeople: Stella")

    def test_strip_round_trips(self):
        for original in ("", "Bring skates", "Line one\nLine two"):
            with_line = people.add_people_line(original, ["Ivy", "Stella"])
            self.assertEqual(people.strip_people_line(with_line), original)

    def test_strip_leaves_user_text_that_only_mentions_people(self):
        text = "People: bring a friend\nthen skate"
        self.assertEqual(people.strip_people_line(text), text)

    def test_strip_handles_none(self):
        self.assertEqual(people.strip_people_line(None), "")


if __name__ == "__main__":
    unittest.main()
