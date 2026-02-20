"""Config flow for Skylight integration."""

from __future__ import annotations

import base64
import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
import homeassistant.helpers.config_validation as cv

from .const import BASE_URL, CONF_PASSWORD, CONF_USERNAME, DOMAIN

_LOGGER = logging.getLogger(__name__)


class SkylightConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Skylight."""

    VERSION = 1

    def __init__(self):
        self._auth_code = None
        self._frames = []  # always store frames here

    async def async_step_user(self, user_input=None):
        """Initial login step."""

        errors = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            try:
                resp_json = await self.getUserAuthSession(username, password)

                if (
                    isinstance(resp_json, dict)
                    and "data" in resp_json
                    and "attributes" in resp_json["data"]
                    and "token" in resp_json["data"]["attributes"]
                ):
                    userToken = resp_json["data"]["attributes"]["token"]
                    userId = resp_json["data"]["id"]

                    # Build Basic Auth token
                    auth_code = base64.b64encode(
                        f"{userId}:{userToken}".encode()
                    ).decode()

                    # Verify login actually works
                    status = await self._authenticate(auth_code)
                    if status == 200:
                        self._auth_code = auth_code

                        # Fetch frames for this account
                        frames = await self._fetch_frames(auth_code)
                        if not frames:
                            errors["base"] = "No frames found on this account."
                        else:
                            self._frames = frames
                            return await self.async_step_select_frames()
                    else:
                        errors["base"] = "Authentication Failed"

                else:
                    errors["base"] = "Authentication Failed"

            except Exception:
                _LOGGER.exception("Error authenticating with Skylight API")
                errors["base"] = "Connection Error"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_select_frames(self, user_input=None):
        """Select frames to include in Home Assistant."""
        errors = {}

        frame_options = {frame["id"]: frame["name"] for frame in self._frames}

        if user_input:
            selected_ids = user_input["What frames would you like to add?"]
            selected_frames = [f for f in self._frames if f["id"] in selected_ids]

            return self.async_create_entry(
                title="Skylight Frames",
                data={"auth_code": self._auth_code, "frame_data": selected_frames},
            )

        return self.async_show_form(
            step_id="select_frames",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "What frames would you like to add?", default=[]
                    ): cv.multi_select(frame_options)
                }
            ),
            errors=errors,
        )

    async def _fetch_frames(self, auth_code: str):
        """Fetch available frames (id + name)."""
        url = f"{BASE_URL}/frames"
        headers = {"Authorization": f"Basic {auth_code}"}

        try:
            async with aiohttp.ClientSession() as session:  # noqa: SIM117
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        return None

                    data = await resp.json()

                    frames = []
                    for item in data.get("data", []):
                        frame_id = item.get("id")
                        name = item.get("attributes", {}).get("name")
                        if frame_id and name:
                            frames.append({"id": frame_id, "name": name})

                    return frames

        except Exception as e:
            _LOGGER.error("Failed to fetch frames: %s", e)
            return None

    async def getUserAuthSession(self, email: str, password: str):
        """Login → POST /sessions."""
        url = f"{BASE_URL}/sessions"

        data = {
            "email": email,
            "password": password,
            "resettingPassword": "false",
            "textMeTheApp": "false",
            "agreedToMarketing": "false",
        }

        headers = {
            "accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        try:
            async with aiohttp.ClientSession() as session:  # noqa: SIM117
                async with session.post(url, data=data, headers=headers) as resp:
                    return await resp.json()

        except Exception as e:
            _LOGGER.error("Skylight POST /sessions failed: %s", e)
            return {}

    async def _authenticate(self, auth_code: str):
        """Verify Basic Auth token works."""
        url = f"{BASE_URL}/frames"
        headers = {"Authorization": f"Basic {auth_code}"}

        try:
            async with aiohttp.ClientSession() as session:  # noqa: SIM117
                async with session.get(url, headers=headers) as resp:
                    return resp.status

        except Exception as e:
            _LOGGER.error("Skylight auth check failed: %s", e)
            return 0
