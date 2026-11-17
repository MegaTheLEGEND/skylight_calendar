"""Offline regression tests for timed chore occurrences (issue #18).

A chore scheduled at a time of day is listed as ``<series>-YYYY-MM-DD-HHMM``.
Completing, un-completing or deleting one must split that id and send the time
as ``instance_time`` (``HH:MM``) next to ``instance_date``.

Run from the repository root: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

import voluptuous as vol

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

# Import the module, not its TestCase classes, so unittest doesn't collect and
# re-run them a second time from this file.
import test_chore_completion as completion
from test_service_authorization import load_chore_ids

split_chore_uid = load_chore_ids().split_chore_uid


class SplitChoreUidTests(unittest.TestCase):
    def test_supported_forms(self):
        cases = {
            "12345": ("12345", None, None),
            "12345-2026-11-11": ("12345", "2026-11-11", None),
            "12345-2026-11-11-1400": ("12345", "2026-11-11", "14:00"),
            "12345-2026-11-11-0005": ("12345", "2026-11-11", "00:05"),
            "12345-2026-11-11-2359": ("12345", "2026-11-11", "23:59"),
        }
        for uid, expected in cases.items():
            with self.subTest(uid=uid):
                self.assertEqual(tuple(split_chore_uid(uid)), expected)

    def test_unparseable_ids_are_returned_whole(self):
        for uid in (
            "12345-2026-11-11-2400",  # no hour 24
            "12345-2026-11-11-1260",  # no minute 60
            "12345-2026-11-11-140",   # three digits
            "12345-2026-11-11-14:00",  # colon form is not what Skylight lists
            "12345-2026-02-30",
            "12345-20261111",
            "12345-",
            "abc-2026-11-11",
            "１２３-2026-11-11",       # non-ASCII digits
            "12345-２０２６-11-11",
        ):
            with self.subTest(uid=uid):
                self.assertEqual(tuple(split_chore_uid(uid)), (uid, None, None))


class TimedCompletionServiceTests(unittest.IsolatedAsyncioTestCase):
    """skylight.complete_chore with a timed occurrence id."""

    def setUp(self):
        self.scope, self.schema = completion.load_schema(
            "COMPLETE_CHORE_SCHEMA", {"_has_completion_target"}
        )

    def api(self, uid):
        people = {"data": [{"id": "67890", "attributes": {"label": "Alex", "linked_to_profile": True}}]}
        return NS(
            get_categories=AsyncMock(return_value=people),
            get_chores=AsyncMock(return_value={"data": [
                {"id": uid, "attributes": {"status": "pending", "up_for_grabs": True}}
            ]}),
            complete_chore=AsyncMock(),
        )

    async def test_timed_occurrence_sends_instance_time(self):
        uid = "12345-2026-11-11-1400"
        api = self.api(uid)
        await self.scope["_complete_chore"](
            api, "111", self.schema({"chore_id": uid, "category_id": "67890", "completed_on": "2026-11-11"})
        )
        api.get_chores.assert_awaited_once_with("111", "2026-11-11", "2026-11-12")
        api.complete_chore.assert_awaited_once_with(
            "111", "12345", "2026-11-11", "2026-11-11", instance_time="14:00", category_id="67890"
        )

    async def test_delete_chore_service_strips_occurrence(self):
        for uid in ("12345", "12345-2026-11-11", "12345-2026-11-11-1400"):
            api = NS(delete_chore=AsyncMock())
            with self.subTest(uid=uid):
                await self.scope["_delete_chore"](api, "111", {"chore_id": uid})
                api.delete_chore.assert_awaited_once_with("111", "12345")

    def test_delete_chore_documented_for_action_editor(self):
        fields = completion.service_fields("delete_chore")
        self.assertTrue(fields["chore_id"]["required"])
        self.assertIn("frame_id", fields)

    def test_schema_accepts_timed_and_rejects_bad_times(self):
        self.schema({"chore_id": "12345-2026-11-11-1400", "category_id": "67890"})
        for uid in ("12345-2026-11-11-2400", "12345-2026-11-11-1260", "12345-2026-11-11-14:00"):
            with self.subTest(uid=uid), self.assertRaises(vol.Invalid):
                self.schema({"chore_id": uid, "category_id": "67890"})


class TimedApiWirePayloadTests(unittest.IsolatedAsyncioTestCase):
    """Bodies the real SkylightAPI methods send (vendor call mocked)."""

    async def test_complete_timed_occurrence(self):
        api = completion.api_client()
        await api.complete_chore("111", "12345", "2026-11-11", "2026-11-11", instance_time="14:00")
        self.assertEqual(api._request.await_args.args, ("PUT", "/api/frames/111/chores/12345/completions"))
        self.assertEqual(
            api._request.await_args.kwargs["json_body"],
            {"status": "complete", "instance_date": "2026-11-11", "instance_time": "14:00", "completed_on": "2026-11-11"},
        )

    async def test_uncomplete_timed_occurrence(self):
        api = completion.api_client()
        await api.uncomplete_chore("111", "12345", "2026-11-11", instance_time="14:00")
        self.assertEqual(
            api._request.await_args.kwargs["json_body"],
            {"status": "pending", "instance_date": "2026-11-11", "instance_time": "14:00"},
        )

    async def test_all_day_and_bare_ids_unchanged(self):
        api = completion.api_client()
        await api.complete_chore("111", "12345", "2026-11-11", "2026-11-10")
        self.assertEqual(
            api._request.await_args.kwargs["json_body"],
            {"status": "complete", "instance_date": "2026-11-10", "completed_on": "2026-11-11"},
        )
        await api.complete_chore("111", "12345", "2026-11-11")
        self.assertEqual(
            api._request.await_args.kwargs["json_body"],
            {"status": "complete", "completed_on": "2026-11-11"},
        )


if __name__ == "__main__":
    unittest.main()
