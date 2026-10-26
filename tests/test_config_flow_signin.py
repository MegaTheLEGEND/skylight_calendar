"""Offline tests for the Skylight sign-in menu, browser step and refresh-token step.

Run from the repository root:
    python3 -m unittest discover -s tests -p 'test_config_flow_signin.py' -v

The real ``config_flow.py`` is imported, with small stand-ins for Home Assistant, voluptuous
and the vendor API (``api.py``) so nothing needs to be installed and nothing touches the network.
"""
from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components/skylight"


class SkylightAPIError(Exception):
    pass


class SkylightAuthError(SkylightAPIError):
    pass


STUB = types.SimpleNamespace(frames=[], exchange=None, refresh=None, calls=[])


class FakeSkylightAPI:
    def __init__(self, **kwargs):
        STUB.calls.append(("api", kwargs))

    async def get_frames(self):
        if isinstance(STUB.frames, Exception):
            raise STUB.frames
        return STUB.frames


async def fake_exchange_authorization_code(session, code, code_verifier, device_fingerprint=""):
    STUB.calls.append(("code", code, device_fingerprint))
    if isinstance(STUB.exchange, Exception):
        raise STUB.exchange
    return STUB.exchange


async def fake_exchange_refresh_token(session, refresh_token, device_fingerprint=""):
    STUB.calls.append(("refresh", refresh_token, device_fingerprint))
    if isinstance(STUB.refresh, Exception):
        raise STUB.refresh
    return STUB.refresh


def install_stubs():
    class ConfigFlow:
        context: dict = {}

        def __init_subclass__(cls, domain=None, **kwargs):
            cls.DOMAIN = domain

        def async_show_menu(self, **kw):
            return {"type": "menu", **kw}

        def async_show_form(self, **kw):
            return {"type": "form", **kw}

        def async_create_entry(self, **kw):
            return {"type": "create_entry", **kw}

        def async_abort(self, **kw):
            return {"type": "abort", **kw}

        async def async_set_unique_id(self, value):
            self.unique_id = value

        def _abort_if_unique_id_configured(self):
            pass

        def _async_current_entries(self):
            return []

    class OptionsFlow:
        pass

    voluptuous = types.ModuleType("voluptuous")
    voluptuous.Schema = lambda schema, **kw: ("schema", schema)
    voluptuous.Required = lambda key, **kw: key
    voluptuous.Optional = lambda key, **kw: key
    voluptuous.All = lambda *a, **kw: a
    voluptuous.Coerce = lambda t: t
    voluptuous.Range = lambda **kw: kw
    voluptuous.In = lambda options: options

    ha = types.ModuleType("homeassistant")
    ha_ce = types.ModuleType("homeassistant.config_entries")
    ha_ce.ConfigEntry = object
    ha_ce.ConfigFlow = ConfigFlow
    ha_ce.OptionsFlow = OptionsFlow
    ha.config_entries = ha_ce
    ha_core = types.ModuleType("homeassistant.core")
    ha_core.callback = lambda func: func
    ha_def = types.ModuleType("homeassistant.data_entry_flow")
    ha_def.FlowResult = dict
    ha_helpers = types.ModuleType("homeassistant.helpers")
    ha_http = types.ModuleType("homeassistant.helpers.aiohttp_client")
    ha_http.async_get_clientsession = lambda hass: "session"

    package = types.ModuleType("skylight")
    package.__path__ = [str(PACKAGE)]
    api = types.ModuleType("skylight.api")
    api.SkylightAPI = FakeSkylightAPI
    api.SkylightAPIError = SkylightAPIError
    api.SkylightAuthError = SkylightAuthError
    api.exchange_authorization_code = fake_exchange_authorization_code
    api.exchange_refresh_token = fake_exchange_refresh_token

    sys.modules.update({
        "voluptuous": voluptuous, "homeassistant": ha, "homeassistant.config_entries": ha_ce,
        "homeassistant.core": ha_core, "homeassistant.data_entry_flow": ha_def,
        "homeassistant.helpers": ha_helpers, "homeassistant.helpers.aiohttp_client": ha_http,
        "skylight": package, "skylight.api": api,
    })
    return importlib.import_module("skylight.config_flow")


config_flow = install_stubs()
TOKEN = "dGhpcy1pcy1hLWZha2UtcmVmcmVzaC10b2tlbi0xMjM0NTY"  # not a real token
FRAME_A = {"id": "10", "name": "Kitchen"}
FRAME_B = {"id": "11", "name": "Hall"}


def run(coro):
    return asyncio.run(coro)


def new_flow():
    STUB.frames, STUB.exchange, STUB.refresh, STUB.calls = [FRAME_A], None, None, []
    flow = config_flow.SkylightConfigFlow()
    flow.hass = mock.Mock()
    flow.context = {}
    return flow


class SignInMenu(unittest.TestCase):
    def test_user_step_offers_both_methods(self):
        result = run(new_flow().async_step_user())
        self.assertEqual(result["type"], "menu")
        self.assertEqual(result["step_id"], "user")
        self.assertEqual(result["menu_options"], ["browser", "refresh_token"])

    def test_browser_step_still_shows_the_authorize_link(self):
        result = run(new_flow().async_step_browser())
        self.assertEqual(result["step_id"], "browser")
        self.assertIn("code_challenge", result["description_placeholders"]["auth_url"])


class RefreshTokenSignIn(unittest.TestCase):
    def setUp(self):
        self.flow = new_flow()
        STUB.refresh = {"access_token": "access-1", "refresh_token": "rotated-refresh-token-0001"}

    def submit(self, text):
        return run(self.flow.async_step_refresh_token({"refresh_token": text}))

    def test_first_view_is_a_form(self):
        result = run(self.flow.async_step_refresh_token())
        self.assertEqual((result["type"], result["step_id"]), ("form", "refresh_token"))

    def test_garbage_is_rejected_without_calling_skylight(self):
        result = self.submit("not a token")
        self.assertEqual(result["errors"], {"refresh_token": "invalid_token"})
        self.assertEqual(STUB.calls, [])

    def test_single_frame_creates_the_entry_with_the_rotated_token(self):
        result = self.submit(TOKEN)
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["title"], "Kitchen")
        data = result["data"]
        self.assertEqual(data["access_token"], "access-1")
        self.assertEqual(data["refresh_token"], "rotated-refresh-token-0001")
        self.assertEqual(data["frame_id"], "10")
        self.assertEqual(len(data["device_fingerprint"]), 32)  # generated

    def test_supplied_fingerprint_is_sent_and_stored_unchanged(self):
        result = self.submit(f'{{"refresh_token": "{TOKEN}", "device_fingerprint": "my-device-id"}}')
        self.assertEqual(STUB.calls[0], ("refresh", TOKEN, "my-device-id"))
        self.assertEqual(result["data"]["device_fingerprint"], "my-device-id")

    def test_rejected_token_shows_an_auth_error(self):
        STUB.refresh = SkylightAuthError("revoked")
        result = self.submit(TOKEN)
        self.assertEqual(result["errors"], {"base": "invalid_auth_token"})

    def test_unreachable_api_shows_cannot_connect(self):
        STUB.refresh = SkylightAPIError("down")
        self.assertEqual(self.submit(TOKEN)["errors"], {"base": "cannot_connect"})

    def test_frame_lookup_failure_shows_cannot_connect(self):
        STUB.frames = SkylightAPIError("down")
        self.assertEqual(self.submit(TOKEN)["errors"], {"base": "cannot_connect"})

    def test_account_without_frames(self):
        STUB.frames = []
        self.assertEqual(self.submit(TOKEN)["errors"], {"base": "no_frames"})

    def test_several_frames_lead_to_the_picker(self):
        STUB.frames = [FRAME_A, FRAME_B]
        result = self.submit(TOKEN)
        self.assertEqual((result["type"], result["step_id"]), ("form", "pick_frame"))

    def test_reauth_updates_the_entry_instead_of_creating_one(self):
        entry = types.SimpleNamespace(data={"frame_id": "10", "access_token": "old"}, entry_id="e1")
        self.flow._reauth_entry = entry
        updates = []
        manager = self.flow.hass.config_entries
        manager.async_update_entry = lambda e, data: updates.append(data)

        async def reload(entry_id):
            return True
        manager.async_reload = reload
        result = self.submit(TOKEN)
        self.assertEqual(result, {"type": "abort", "reason": "reauth_successful"})
        self.assertEqual(updates[0]["refresh_token"], "rotated-refresh-token-0001")
        self.assertEqual(updates[0]["frame_id"], "10")  # untouched fields survive


class BrowserCodeSignIn(unittest.TestCase):
    """The original method keeps working after the refactor."""

    def test_valid_code_creates_the_entry(self):
        flow = new_flow()
        STUB.exchange = {"access_token": "a", "refresh_token": "r" * 24}
        run(flow.async_step_browser())  # creates the PKCE pair
        result = run(flow.async_step_browser({"code": "https://ourskylight.com/welcome?code=ABC123"}))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(STUB.calls[0][:2], ("code", "ABC123"))

    def test_bad_code_text_is_rejected(self):
        flow = new_flow()
        result = run(flow.async_step_browser({"code": "   "}))
        self.assertEqual(result["errors"], {"code": "invalid_code"})

    def test_expired_code_shows_invalid_auth(self):
        flow = new_flow()
        STUB.exchange = SkylightAuthError("expired")
        result = run(flow.async_step_browser({"code": "ABC123"}))
        self.assertEqual(result["errors"], {"base": "invalid_auth"})

    def test_no_frames_error_comes_back_on_the_browser_form(self):
        flow = new_flow()
        STUB.exchange = {"access_token": "a", "refresh_token": "r" * 24}
        STUB.frames = []
        result = run(flow.async_step_browser({"code": "ABC123"}))
        self.assertEqual((result["step_id"], result["errors"]), ("browser", {"base": "no_frames"}))


if __name__ == "__main__":
    unittest.main()
