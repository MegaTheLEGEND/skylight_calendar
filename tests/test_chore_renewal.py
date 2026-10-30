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


class RenewalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope, self.schema = load_schema("CREATE_CHORE_SCHEMA", {"_has_assignee", "_has_renewal_schedule"})
        self.base = {"summary": "Replace filter", "up_for_grabs": True}

    async def test_legacy_create_preserves_native_defaults(self):
        api = api_client()
        await self.scope["_create_chore"](api, "111", self.schema(self.base))
        request = api._request.await_args
        self.assertEqual(request.args, ("POST", "/api/frames/111/chores/create_multiple"))
        body = request.kwargs["json_body"]
        self.assertIsNone(body["renewal_interval"])
        self.assertIsNone(body["renewal_unit"])
        self.assertEqual(body["start"], "2025-01-01")
        self.assertEqual(body["category_ids"], [])
        self.assertEqual(body["summary"], "Replace filter")

    async def test_all_supported_units_reach_native_api(self):
        for unit in ("day", "week", "month"):
            with self.subTest(unit=unit):
                api = api_client()
                data = self.schema(dict(self.base, renewal_interval=30, renewal_unit=unit, start="2026-11-04"))
                await self.scope["_create_chore"](api, "111", data)
                body = api._request.await_args.kwargs["json_body"]
                self.assertEqual(body["renewal_interval"], 30)
                self.assertEqual(body["renewal_unit"], unit)
                self.assertEqual(body["start"], "2026-11-04")
                self.assertIsNone(body["recurrence_set"])
                api._request.assert_awaited_once()

    def test_invalid_or_partial_schedules_rejected(self):
        cases = [
            {"renewal_interval": 30}, {"renewal_unit": "day"},
            {"renewal_interval": 0, "renewal_unit": "day"},
            {"renewal_interval": -1, "renewal_unit": "day"},
            {"renewal_interval": "bad", "renewal_unit": "day"},
            {"renewal_interval": 1, "renewal_unit": "year"},
            {"renewal_interval": 30, "renewal_unit": "day", "recurrence_set": "RRULE:FREQ=MONTHLY"},
            {"renewal_interval": 30, "renewal_unit": "day", "recurring_until": "2026-12-01"},
        ]
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(vol.Invalid):
                self.schema(dict(self.base, **extra))

    def test_existing_calendar_recurrence_unchanged(self):
        data = self.schema(dict(self.base, recurrence_set="RRULE:FREQ=WEEKLY", recurring_until="2026-12-01"))
        self.assertEqual(data["recurrence_set"], "RRULE:FREQ=WEEKLY")
        self.assertNotIn("renewal_interval", data)

    def test_existing_assignee_requirement_still_applies(self):
        with self.assertRaises(vol.Invalid):
            self.schema({"summary": "Replace filter", "renewal_interval": 30, "renewal_unit": "day"})

    def test_action_editor_documents_fields(self):
        fields = service_fields("create_chore")
        self.assertEqual(fields["renewal_interval"]["selector"]["number"]["min"], 1)
        self.assertEqual(fields["renewal_unit"]["selector"]["select"]["options"], ["day", "week", "month"])

    async def test_rejected_create_propagates_without_another_write(self):
        api = NS(create_chores=AsyncMock(side_effect=SkylightAPIError("422")))
        with self.assertRaises(SkylightAPIError):
            await self.scope["_create_chore"](api, "111", self.schema(dict(self.base, renewal_interval=30, renewal_unit="day")))
        api.create_chores.assert_awaited_once()
