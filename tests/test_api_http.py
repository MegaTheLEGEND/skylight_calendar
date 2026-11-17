"""HTTP-level tests of the real SkylightAPI client against a local fake server.

The client code in api.py runs unmodified and makes real HTTP requests; only
BASE_URL is pointed at an in-process aiohttp server that records each request
and answers like Skylight does for the chore completion routes, including the
422s from #10 and #18. No credentials or live vendor calls.

Needs aiohttp (installed with Home Assistant); skipped without it.
Run from the repository root: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest

try:
    from aiohttp import ClientSession, web
except ImportError:  # pragma: no cover
    web = None

import test_chore_completion as completion

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "custom_components/skylight"


def load_api_module():
    """Import const.py and api.py as a package without running __init__.py."""
    name = "_skylight_offline"
    if f"{name}.api" in sys.modules:
        return sys.modules[f"{name}.api"]
    pkg = types.ModuleType(name)
    pkg.__path__ = [str(PKG_DIR)]
    sys.modules[name] = pkg
    for sub in ("const", "api"):
        spec = importlib.util.spec_from_file_location(f"{name}.{sub}", PKG_DIR / f"{sub}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{name}.{sub}"] = module
        spec.loader.exec_module(module)
    return sys.modules[f"{name}.api"]


# Chores the fake frame knows: series id -> how its occurrences are keyed.
TIMED, ALL_DAY, ON_DEMAND = "11111", "22222", "33333"


def fake_skylight(log):
    """Minimal stand-in for the completion and chore routes."""

    async def completions(request):
        chore = request.match_info["chore"]
        body = await request.json()
        log.append((request.method, request.path, body, request.headers.get("Authorization")))
        if not chore.isdigit():
            return web.json_response({"errors": {"id": ["is not a number"]}}, status=422)
        errors = {}
        if chore in (TIMED, ALL_DAY) and not body.get("instance_date"):
            errors["instance_date"] = ["can't be blank"]
        if chore == TIMED and not body.get("instance_time"):
            errors["instance_time"] = ["can't be blank"]
        if chore == ON_DEMAND and "instance_date" in body:
            errors["instance_date"] = ["must be blank"]
        if errors:
            return web.json_response({"errors": errors}, status=422)
        return web.json_response({"data": {"id": chore, "attributes": {"status": body["status"]}}})

    async def update(request):
        body = await request.json()
        log.append((request.method, request.path, body, request.headers.get("Authorization")))
        return web.json_response({"data": {"id": request.match_info["chore"], "attributes": body}})

    async def delete(request):
        log.append((request.method, request.path, None, request.headers.get("Authorization")))
        return web.Response(status=204)

    app = web.Application()
    app.router.add_put("/api/frames/{frame}/chores/{chore}/completions", completions)
    app.router.add_put("/api/frames/{frame}/chores/{chore}", update)
    app.router.add_delete("/api/frames/{frame}/chores/{chore}", delete)
    return app


@unittest.skipIf(web is None, "aiohttp not installed")
class ApiHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mod = load_api_module()
        self.log = []
        self.runner = web.AppRunner(fake_skylight(self.log))
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self._old_base = self.mod.BASE_URL
        self.mod.BASE_URL = f"http://127.0.0.1:{port}"
        self.session = ClientSession()
        self.api = self.mod.SkylightAPI(self.session, "access", "refresh")
        self.split = completion.load_exact_nodes()["split_chore_uid"]

    async def asyncTearDown(self):
        self.mod.BASE_URL = self._old_base
        await self.session.close()
        await self.runner.cleanup()

    async def tick(self, uid, *, done=True):
        """What the todo entity does when a chore is ticked or un-ticked."""
        series, instance_date, instance_time = self.split(uid)
        if done:
            return await self.api.complete_chore(
                "f1", series, "2026-11-11", instance_date, instance_time=instance_time
            )
        return await self.api.uncomplete_chore(
            "f1", series, instance_date, instance_time=instance_time
        )

    async def test_timed_chore_completes_and_uncompletes(self):
        await self.tick(f"{TIMED}-2026-11-11-1400")
        await self.tick(f"{TIMED}-2026-11-11-1400", done=False)
        self.assertEqual(self.log[0][:3], (
            "PUT", f"/api/frames/f1/chores/{TIMED}/completions",
            {"status": "complete", "instance_date": "2026-11-11", "instance_time": "14:00", "completed_on": "2026-11-11"},
        ))
        self.assertEqual(self.log[1][2], {"status": "pending", "instance_date": "2026-11-11", "instance_time": "14:00"})
        self.assertEqual(self.log[0][3], "Bearer access")

    async def test_old_parser_reproduces_issue_18(self):
        """The pre-fix behaviour: whole composite id in the URL, no dates."""
        with self.assertRaises(self.mod.SkylightAPIError) as ctx:
            await self.api.complete_chore("f1", f"{TIMED}-2026-11-11-1400", "2026-11-11", None)
        self.assertIn("422", str(ctx.exception))
        self.assertIn("is not a number", str(ctx.exception))

    async def test_all_day_and_on_demand_chores_unchanged(self):
        await self.tick(f"{ALL_DAY}-2026-11-10")
        await self.tick(ON_DEMAND)
        self.assertEqual(self.log[0][2], {"status": "complete", "instance_date": "2026-11-10", "completed_on": "2026-11-11"})
        self.assertEqual(self.log[1][2], {"status": "complete", "completed_on": "2026-11-11"})

    async def test_update_chore_service_body(self):
        """skylight.update_chore (PR #16) through the real client."""
        scope = completion.load_exact_nodes()
        await scope["_update_chore"](self.api, "f1", {"chore_id": ALL_DAY, "emoji": "🧹", "reward_points": 0})
        self.assertEqual(self.log[0][:3], ("PUT", f"/api/frames/f1/chores/{ALL_DAY}", {"emoji_icon": "🧹", "reward_points": None}))

    async def test_delete_uses_series_id(self):
        await self.api.delete_chore("f1", self.split(f"{TIMED}-2026-11-11-1400").series)
        self.assertEqual(self.log[0][:2], ("DELETE", f"/api/frames/f1/chores/{TIMED}"))


if __name__ == "__main__":
    unittest.main()
