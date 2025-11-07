"""Skylight integration initialization."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import SkylightAPI
from .const import CONF_FRAME_ID, CONF_PASSWORD, DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["calendar"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    frame_id = entry.data.get("frame_id") or entry.data.get(CONF_FRAME_ID)
    auth_code = entry.data.get("password") or entry.data.get(CONF_PASSWORD)

    if frame_id is None or auth_code is None:
        _LOGGER.error("Skylight config entry missing required data: %s", entry.data)
        return False

    api = SkylightAPI({"frame_id": frame_id, "password": auth_code}, hass=hass)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "frame_id": frame_id,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""

    # Unload platforms
    unload_results = [
        await hass.config_entries.async_forward_entry_unload(entry, platform)
        for platform in PLATFORMS
    ]
    unload_ok = all(unload_results)

    # Remove API reference
    hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
