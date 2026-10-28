"""Offline regression tests; no credentials or live vendor calls.

Uses real source AST nodes and voluptuous validation. Home Assistant dispatch,
config_validation primitives and permissions are represented by test doubles;
this is not a full Home Assistant integration test. Install voluptuous and
PyYAML, then run: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import ast
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import unittest

import voluptuous as vol
import yaml

from test_service_authorization import (
    HomeAssistantError, SkylightAPIError, Rig, load_exact_nodes,
)

ROOT = Path(__file__).resolve().parents[1]


def string(value):
    if not isinstance(value, str):
        raise vol.Invalid("expected a string")
    return value


def local_date(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as err:
        raise vol.Invalid("expected a date") from err


def load_schema(name, helpers):
    scope = load_exact_nodes()
    scope.update(vol=vol, cv=NS(string=string, date=local_date,
        boolean=bool, ensure_list=lambda v: v if isinstance(v, list) else [v]))
    tree = ast.parse((ROOT / "custom_components/skylight/__init__.py").read_text(encoding="utf-8"))
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in helpers:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in {"_FRAME_ID_FIELD", "_SUMMARY", "_POINTS", name}
            for t in node.targets
        ):
            nodes.append(node)
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(module, "<real service schema>", "exec"), scope)
    return scope, scope[name]


def api_client():
    tree = ast.parse((ROOT / "custom_components/skylight/api.py").read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if (
        isinstance(n, ast.ClassDef) and n.name == "SkylightAPI"
    ) or (isinstance(n, ast.FunctionDef) and n.name == "_compact")]
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[]))
    scope = {"CHORE_STATUS_COMPLETE": "complete"}
    exec(compile(module, "<real API client>", "exec"), scope)
    api = object.__new__(scope["SkylightAPI"])
    api._request = AsyncMock(return_value={"data": {}})
    return api


def service_fields(name):
    return yaml.safe_load((ROOT / "custom_components/skylight/services.yaml").read_text(encoding="utf-8"))[name]["fields"]


class CompletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope, self.schema = load_schema("COMPLETE_CHORE_SCHEMA", {"_has_completion_target"})
        self.people = {"data": [
            {"id": "67890", "attributes": {"label": "Alex", "linked_to_profile": True}},
            {"id": "67891", "attributes": {"label": "Alex Calendar", "linked_to_profile": False}},
        ]}

    def api(self, uid="12345", **attrs):
        defaults = {"status": "pending", "up_for_grabs": True}
        defaults.update(attrs)
        return NS(
            get_categories=AsyncMock(return_value=self.people),
            get_chores=AsyncMock(return_value={"data": [{"id": uid, "attributes": defaults}]}),
            complete_chore=AsyncMock(),
        )

    async def test_one_off_omits_instance_date_even_with_start(self):
        api = self.api(start="2026-11-04")
        await self.scope["_complete_chore"](api, "111", self.schema({"chore_id": "12345", "assignee": " alex "}))
        api.complete_chore.assert_awaited_once_with("111", "12345", "2025-01-01", None, category_id="67890")

    async def test_late_occurrence_and_completion_dates_are_separate(self):
        api = self.api("12345-2026-11-04")
        await self.scope["_complete_chore"](api, "111", self.schema({"chore_id": "12345-2026-11-04", "category_id": "67890", "completed_on": "2026-11-08"}))
        api.complete_chore.assert_awaited_once_with("111", "12345", "2026-11-08", "2026-11-04", category_id="67890")
        api.get_chores.assert_awaited_once_with("111", "2026-11-04", "2026-11-05")

    async def test_real_client_wire_payload_and_legacy_compatibility(self):
        api = api_client()
        await api.complete_chore("111", "12345", "2026-11-08")
        self.assertEqual(api._request.await_args.kwargs["json_body"], {"status": "complete", "completed_on": "2026-11-08"})
        await api.complete_chore("111", "12345", "2026-11-08", "2026-11-04", category_id="67890")
        self.assertEqual(api._request.await_args.args, ("PUT", "/api/frames/111/chores/12345/completions"))
        self.assertEqual(api._request.await_args.kwargs["json_body"], {"status": "complete", "completed_on": "2026-11-08", "instance_date": "2026-11-04", "category_id": "67890"})

    async def test_unknown_partial_and_calendar_members_do_not_write(self):
        for identity in ({"assignee": "Al"}, {"assignee": "missing"}, {"category_id": "67891"}, {"category_id": "99999"}):
            with self.subTest(identity=identity):
                api = self.api()
                with self.assertRaises(HomeAssistantError):
                    await self.scope["_complete_chore"](api, "111", self.schema(dict(chore_id="12345", **identity)))
                api.complete_chore.assert_not_awaited()
                api.get_chores.assert_not_awaited()

    async def test_ambiguous_name_rejected_but_exact_id_works(self):
        self.people["data"].append({"id": "67892", "attributes": {"label": "Alex", "linked_to_profile": True}})
        api = self.api()
        with self.assertRaises(HomeAssistantError):
            await self.scope["_complete_chore"](api, "111", self.schema({"chore_id": "12345", "assignee": "Alex"}))
        api.complete_chore.assert_not_awaited()
        await self.scope["_complete_chore"](api, "111", self.schema({"chore_id": "12345", "category_id": "67890"}))
        api.complete_chore.assert_awaited_once()

    async def test_already_completed_noops_without_recredit(self):
        for attrs in ({"status": "complete"}, {"completed_on": "2026-11-04"}, {"completed_at": "2026-11-04T12:00:00Z"}):
            with self.subTest(attrs=attrs):
                api = self.api(**attrs)
                await self.scope["_complete_chore"](api, "111", self.schema({"chore_id": "12345", "category_id": "67890"}))
                api.complete_chore.assert_not_awaited()

    async def test_missing_or_different_occurrence_never_guessed(self):
        for uid in ("99999", "12345-2026-11-04"):
            api = self.api(uid)
            with self.subTest(uid=uid), self.assertRaises(HomeAssistantError):
                await self.scope["_complete_chore"](api, "111", self.schema({"chore_id": "12345", "category_id": "67890"}))
            api.complete_chore.assert_not_awaited()

    async def test_unknown_state_and_someone_elses_chore_rejected(self):
        for attrs in ({"status": "unknown"}, {"up_for_grabs": False, "category_id": "67892"}, {"up_for_grabs": False}):
            api = self.api(**attrs)
            with self.subTest(attrs=attrs), self.assertRaises(HomeAssistantError):
                await self.scope["_complete_chore"](api, "111", self.schema({"chore_id": "12345", "category_id": "67890"}))
            api.complete_chore.assert_not_awaited()

    async def test_own_assigned_chore_accepted(self):
        api = self.api(up_for_grabs=False, category_id="67890")
        await self.scope["_complete_chore"](api, "111", self.schema({"chore_id": "12345", "category_id": "67890"}))
        api.complete_chore.assert_awaited_once()

    async def test_timeout_and_rejection_are_not_retried(self):
        for error in (TimeoutError(), SkylightAPIError("422")):
            api = self.api()
            api.complete_chore.side_effect = error
            with self.subTest(error=type(error)), self.assertRaises(type(error)):
                await self.scope["_complete_chore"](api, "111", self.schema({"chore_id": "12345", "category_id": "67890"}))
            api.complete_chore.assert_awaited_once()

    def test_invalid_ids_identity_and_dates_rejected(self):
        for extra in (
            {}, {"assignee": "Alex", "category_id": "67890"},
            {"assignee": ""}, {"assignee": "   "}, {"category_id": ""},
            {"category_id": "67890", "chore_id": "../other"},
            {"category_id": "67890", "chore_id": "12345-2026-02-30"},
            {"category_id": "67890", "chore_id": "12345-20261104"},
            {"category_id": "67890", "chore_id": "12345-"},
            {"category_id": "67890", "chore_id": "１２３"},
            {"category_id": "67890", "completed_on": "not-a-date"},
            {"category_id": "67890", "instance_date": "2026-11-04"},
        ):
            with self.subTest(extra=extra), self.assertRaises(vol.Invalid):
                self.schema({"chore_id": "12345", **extra})

    def test_action_editor_describes_both_id_forms(self):
        fields = service_fields("complete_chore")
        self.assertTrue(fields["chore_id"]["required"])
        self.assertIn("assignee", fields)
        self.assertIn("category_id", fields)
        self.assertNotIn("instance_date", fields)
