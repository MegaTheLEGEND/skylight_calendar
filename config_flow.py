"""Config flow for Skylight integration."""

from __future__ import annotations

import base64
import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from .const import BASE_URL, CONF_FRAME_ID, CONF_PASSWORD, CONF_USERNAME, DOMAIN

_LOGGER = logging.getLogger(__name__)


class SkylightConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Skylight."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step of configuration."""
        errors = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            frame_id = user_input[CONF_FRAME_ID]

            try:
                resp_json = await self.getUserAuthSession(username, password)

                if (
                    isinstance(resp_json, dict)
                    and "data" in resp_json
                    and "attributes" in resp_json["data"]
                    and "token" in resp_json["data"]["attributes"]
                    and resp_json["data"]["attributes"]["token"]
                ):
                    userToken = resp_json["data"]["attributes"]["token"]
                    userId = resp_json["data"]["id"]

                    auth_code = base64.b64encode(
                        f"{userId}:{userToken}".encode()
                    ).decode()
                    _LOGGER.debug("User auth response: %s", resp_json)
                    # if its a success: now we verify frame ID using using BasicAuth code
                    try:
                        status = await self._authenticate(auth_code, frame_id)

                        if status == 200:
                            # save Auth code to config entry data
                            user_input["auth_code"] = auth_code

                            # Success: create the config entry
                            return self.async_create_entry(
                                title=f"Skylight Frame {frame_id}",
                                data=user_input,
                            )
                        elif status == 401:
                            errors["base"] = "authentication_failed"
                        elif status == 404:
                            errors["base"] = "frame_not_found"
                        else:
                            errors["base"] = "connection_error"

                    except Exception as err:  # pylint: disable=broad-except
                        _LOGGER.exception(
                            "Error authenticating with Skylight API: %s", err
                        )
                        errors["base"] = "connection_error"

            except Exception as err:
                _LOGGER.exception("Error authenticating with Skylight API: %s", err)
                errors["base"] = "connection_error"

        # Show form if first load or validation failed
        data_schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_FRAME_ID): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def getUserAuthSession(self, email: str, password: str) -> int:
        """Login to Skylight using the sessions POST endpoint."""
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
                    resp_json = await resp.json()
                    _LOGGER.debug("User auth response: %s", resp_json)
                    return resp_json
        except Exception as e:
            _LOGGER.error("Skylight POST /sessions failed: %s", e)
            return 0

    # this is where we send a request to verify auth code and frame ID is actually valid
    async def _authenticate(self, auth_code: str, frame: str) -> int:
        """Try logging in using Authorization header and return HTTP status."""
        url = f"{BASE_URL}/frames/{frame}"
        headers = {"Authorization": f"Basic {auth_code}"}

        try:
            async with aiohttp.ClientSession() as session:  # noqa: SIM117
                async with session.get(url, headers=headers) as resp:
                    _LOGGER.debug("Auth response: %s", resp.status)
                    return resp.status
        except Exception as e:
            _LOGGER.error("Skylight authentication request failed: %s", e)
            return 0

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return the options flow handler for settings button."""
        return SkylightOptionsFlowHandler(config_entry)


class SkylightOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Skylight options (Settings button)."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options for the integration."""
        if user_input is not None:
            # update config data so changes stick
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=user_input
            )
            return self.async_create_entry(title="", data={})

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME, default=self.config_entry.data.get(CONF_USERNAME)
                ): str,
                vol.Required(
                    CONF_PASSWORD, default=self.config_entry.data.get(CONF_PASSWORD)
                ): str,
                vol.Required(
                    CONF_FRAME_ID, default=self.config_entry.data.get(CONF_FRAME_ID)
                ): str,
            }
        )

        return self.async_show_form(step_id="init", data_schema=data_schema)
