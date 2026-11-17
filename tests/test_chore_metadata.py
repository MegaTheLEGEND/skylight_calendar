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


class MetadataTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope, self.schema = load_schema("UPDATE_CHORE_SCHEMA", {"_has_chore_metadata"})

    async def test_emoji_only_preserves_reward_and_other_fields(self):
        api = api_client()
        await self.scope["_update_chore"](api, "111", self.schema({"chore_id": "12345", "emoji": "🧹"}))
        api._request.assert_awaited_once_with("PUT", "/api/frames/111/chores/12345", json_body={"emoji_icon": "🧹"})

    async def test_reward_only_and_zero_to_null(self):
        for points, expected in [(1, 1), (5, 5), (0, None)]:
            with self.subTest(points=points):
                api = api_client()
                await self.scope["_update_chore"](api, "111", self.schema({"chore_id": "12345", "reward_points": points}))
                api._request.assert_awaited_once_with("PUT", "/api/frames/111/chores/12345", json_body={"reward_points": expected})

    async def test_both_fields(self):
        api = api_client()
        await self.scope["_update_chore"](api, "111", self.schema({"chore_id": "12345", "reward_points": 1, "emoji": "🧹"}))
        self.assertEqual(api._request.await_args.kwargs["json_body"], {"reward_points": 1, "emoji_icon": "🧹"})

    def test_invalid_updates_rejected(self):
        for data in [
            {"chore_id": "12345"}, {"chore_id": "12345", "reward_points": -1},
            {"chore_id": "12345", "emoji": ""},
            {"chore_id": "12345-2026-11-04", "reward_points": 1},
            {"chore_id": "../other", "reward_points": 1},
            {"chore_id": "１２３", "reward_points": 1},
            {"chore_id": "12345", "status": "complete"},
            {"chore_id": "12345", "emoji": "🧹", "summary": "Unrelated edit"},
        ]:
            with self.subTest(data=data), self.assertRaises(vol.Invalid):
                self.schema(data)

    def test_action_editor_uses_nonnegative_rewards(self):
        fields = service_fields("update_chore")
        self.assertTrue(fields["chore_id"]["required"])
        self.assertEqual(fields["reward_points"]["selector"]["number"]["min"], 0)

    async def test_write_error_is_mapped_and_not_retried_or_refreshed(self):
        rig = Rig(self.scope, uid=None)
        api = rig.hass.data["skylight"]["a"]["api"]
        api.update_chore = AsyncMock(side_effect=SkylightAPIError("422"))
        with self.assertRaises(HomeAssistantError):
            await rig.call("update_chore")
        api.update_chore.assert_awaited_once()
        self.assertFalse(any(x[0] == "refresh" for x in rig.log))
