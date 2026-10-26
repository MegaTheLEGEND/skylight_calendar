"""Offline tests: token refresh is serialised, and a lost sign-in asks the user to re-authenticate.

Run from the repository root:
    python3 -m unittest discover -s tests -p 'test_auth_recovery.py' -v

The real ``api.py``, ``coordinator.py`` and ``buddy.py`` are imported under the package name
``skylight_real`` with small stand-ins for aiohttp, yarl and Home Assistant, so nothing needs to
be installed and nothing touches the network. The fake Skylight server rotates refresh tokens
exactly like the real one does: a refresh token works once.
"""
from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components/skylight"


# ── fake Skylight server ─────────────────────────────────────────────────

class Resp:
    def __init__(self, status, text="", headers=None):
        self.status, self._text, self.headers = status, text, headers or {}

    async def text(self):
        return self._text


class Ctx:
    def __init__(self, make):
        self._make = make

    async def __aenter__(self):
        return await self._make()

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Serves API calls for whichever access token is currently valid; rotates refresh tokens."""

    def __init__(self):
        self.valid_access = "access-0"
        self.valid_refresh = "refresh-0"
        self.refresh_posts = 0
        self.refresh_status = None       # force a failure status on the token endpoint
        self.refresh_issues_bad_token = False
        self.generation = 0

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        async def make():
            await asyncio.sleep(0)
            token = headers["Authorization"].split()[1]
            return Resp(200, '{"data": []}') if token == self.valid_access else Resp(401)
        return Ctx(make)

    def post(self, url, data=None, headers=None, timeout=None):
        async def make():
            self.refresh_posts += 1
            await asyncio.sleep(0.01)    # lets concurrent callers pile up behind the first
            if self.refresh_status is not None:
                return Resp(self.refresh_status, '{"error": "x"}')
            if data.get("refresh_token") != self.valid_refresh:
                return Resp(400, '{"error": "invalid_grant"}')   # a rotated-out token
            self.generation += 1
            self.valid_refresh = f"refresh-{self.generation}"
            new_access = f"access-{self.generation}"
            self.valid_access = "never-accepted" if self.refresh_issues_bad_token else new_access
            return Resp(200, json.dumps({"access_token": new_access, "refresh_token": self.valid_refresh}))
        return Ctx(make)


# ── importing the real modules with stand-ins ────────────────────────────

class UpdateFailed(Exception):
    pass


class ConfigEntryAuthFailed(Exception):
    pass


class DataUpdateCoordinator:
    def __init__(self, hass, logger, name=None, update_interval=None):
        self.hass, self.name = hass, name

    def __class_getitem__(cls, item):
        return cls


def load():
    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        return mod

    package = module("skylight_real")
    package.__path__ = [str(PACKAGE)]
    stubs = {
        "aiohttp": module("aiohttp", ClientSession=object, ClientTimeout=lambda **kw: kw),
        "yarl": module("yarl", URL=type("URL", (str,), {"with_query": lambda self, q: self})),
        "homeassistant": module("homeassistant"),
        "homeassistant.core": module("homeassistant.core", HomeAssistant=object, callback=lambda f: f),
        "homeassistant.exceptions": module("homeassistant.exceptions", ConfigEntryAuthFailed=ConfigEntryAuthFailed,
                                           HomeAssistantError=type("HomeAssistantError", (Exception,), {})),
        "homeassistant.helpers": module("homeassistant.helpers"),
        "homeassistant.helpers.device_registry": module("homeassistant.helpers.device_registry", DeviceInfo=dict),
        "homeassistant.helpers.update_coordinator": module(
            "homeassistant.helpers.update_coordinator", DataUpdateCoordinator=DataUpdateCoordinator,
            CoordinatorEntity=DataUpdateCoordinator, UpdateFailed=UpdateFailed),
        "homeassistant.util": module("homeassistant.util"),
        "homeassistant.util.dt": module("homeassistant.util.dt", now=lambda: __import__("datetime").datetime(2026, 1, 1)),
        "skylight_real": package,
    }
    stubs["homeassistant.util"].dt = stubs["homeassistant.util.dt"]
    with mock.patch.dict(sys.modules, stubs):
        return (importlib.import_module("skylight_real.api"),
                importlib.import_module("skylight_real.coordinator"),
                importlib.import_module("skylight_real.buddy"))


api_mod, coordinator_mod, buddy_mod = load()


def make_api(session, cb=None):
    return api_mod.SkylightAPI(session=session, access_token="access-0", refresh_token="refresh-0",
                               device_fingerprint="fp", token_update_cb=cb)


# ── refresh serialisation ────────────────────────────────────────────────

class RefreshIsSerialised(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_401s_refresh_exactly_once(self):
        session = FakeSession()
        session.valid_access = "access-expired-on-server"   # the client's access-0 is now stale
        saved = []

        async def persist(access, refresh, fingerprint):
            saved.append((access, refresh))

        api = make_api(session, persist)
        calls = [api.get_calendar_events("f", "2026-01-01", "2026-01-02") for _ in range(6)]
        # Make the first refresh the one that issues the token the retries will use.
        results = await asyncio.gather(*calls, return_exceptions=True)
        self.assertEqual(session.refresh_posts, 1, "every caller refreshed instead of sharing one refresh")
        self.assertTrue(all(not isinstance(r, Exception) for r in results), results)
        self.assertEqual(saved, [("access-1", "refresh-1")], "the rotated pair is saved once")
        self.assertEqual((api.access_token, api.refresh_token), ("access-1", "refresh-1"))

    async def test_a_second_refresh_with_a_spent_token_would_fail(self):
        """Sanity check of the fake: this is the failure the lock prevents."""
        session = FakeSession()
        session.valid_access = "x"
        first = await session.post("u", data={"refresh_token": "refresh-0"}).__aenter__()
        second = await session.post("u", data={"refresh_token": "refresh-0"}).__aenter__()
        self.assertEqual((first.status, second.status), (200, 400))

    async def test_a_caller_that_lost_the_race_does_not_refresh_again(self):
        session = FakeSession()
        api = make_api(session)
        api._access_token = "access-already-replaced"        # someone else refreshed meanwhile
        await api._refresh_access_token(stale_token="access-0")
        self.assertEqual(session.refresh_posts, 0)

    async def test_an_unconditional_refresh_still_works(self):
        session = FakeSession()
        api = make_api(session)
        await api._refresh_access_token()
        self.assertEqual((session.refresh_posts, api.access_token), (1, "access-1"))


# ── what counts as "signed out" ──────────────────────────────────────────

class RefreshFailureKinds(unittest.IsolatedAsyncioTestCase):
    async def refresh_with(self, status):
        session = FakeSession()
        session.valid_access = "other"
        session.refresh_status = status
        api = make_api(session)
        with self.assertRaises(Exception) as caught:
            await api.get_calendar_events("f", "a", "b")
        return caught.exception

    async def test_rejected_credentials_are_auth_errors(self):
        for status in (400, 401, 403):
            with self.subTest(status=status):
                self.assertIsInstance(await self.refresh_with(status), api_mod.SkylightAuthError)

    async def test_server_trouble_is_not_an_auth_error(self):
        for status in (429, 500, 502, 503):
            with self.subTest(status=status):
                error = await self.refresh_with(status)
                self.assertIsInstance(error, api_mod.SkylightAPIError)
                self.assertNotIsInstance(error, api_mod.SkylightAuthError)

    async def test_still_unauthorised_after_a_good_refresh_is_an_auth_error(self):
        session = FakeSession()
        session.valid_access = "other"
        session.refresh_issues_bad_token = True
        with self.assertRaises(api_mod.SkylightAuthError):
            await make_api(session).get_calendar_events("f", "a", "b")

    async def test_config_flow_exchanges_use_the_same_split(self):
        for status, expected in ((400, api_mod.SkylightAuthError), (503, api_mod.SkylightAPIError)):
            with self.subTest(status=status):
                session = FakeSession()
                session.refresh_status = status
                with self.assertRaises(expected) as caught:
                    await api_mod.exchange_refresh_token(session, "refresh-0", "fp")
                if expected is api_mod.SkylightAPIError:
                    self.assertNotIsInstance(caught.exception, api_mod.SkylightAuthError)


# ── coordinators ask for re-authentication ───────────────────────────────

class FailingAPI:
    def __init__(self, error):
        self.error = error

    def __getattr__(self, name):
        async def fail(*args, **kwargs):
            raise self.error
        return fail


class CoordinatorsReauthenticate(unittest.IsolatedAsyncioTestCase):
    def coordinators(self, error):
        hass = mock.Mock()
        hass.config.time_zone = "UTC"
        api = FailingAPI(error)
        return {
            "calendar": coordinator_mod.SkylightCalendarCoordinator(hass, api, "f"),
            "lists": coordinator_mod.SkylightListsCoordinator(hass, api, "f"),
            "frame": coordinator_mod.SkylightFrameCoordinator(hass, api, "f"),
            "photos": coordinator_mod.SkylightPhotosCoordinator(hass, api, "f"),
            "buddy": buddy_mod.SkylightBuddyCoordinator(hass, api, "f"),
        }

    async def test_auth_failure_asks_for_reauthentication(self):
        for name, coordinator in self.coordinators(api_mod.SkylightAuthError("revoked")).items():
            with self.subTest(coordinator=name), self.assertRaises(ConfigEntryAuthFailed):
                await coordinator._async_update_data()

    async def test_ordinary_failures_do_not(self):
        for name, coordinator in self.coordinators(api_mod.SkylightAPIError("503")).items():
            if name == "photos":
                continue   # photos are non-critical and return [] by design
            with self.subTest(coordinator=name):
                with self.assertRaises(UpdateFailed):
                    await coordinator._async_update_data()
        self.assertEqual(await self.coordinators(api_mod.SkylightAPIError("503"))["photos"]._async_update_data(), [])


if __name__ == "__main__":
    unittest.main()
