"""Skylight integration initialization."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .api import SkylightAPI
<<<<<<< Updated upstream
from .const import CONF_FRAME_ID, CONF_PASSWORD, CONF_USERNAME, DOMAIN
=======
from .const import DOMAIN
>>>>>>> Stashed changes

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["calendar"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
<<<<<<< Updated upstream
    username = entry.data.get("username") or entry.data.get(CONF_USERNAME)
    frame_id = entry.data.get("frame_id") or entry.data.get(CONF_FRAME_ID)
    password = entry.data.get("password") or entry.data.get(CONF_PASSWORD)
    auth_code = entry.data.get("auth_code")
=======
    auth_code = entry.data.get("auth_code")
    stored_frames = entry.data.get("frame_data", [])
>>>>>>> Stashed changes

    if not auth_code:
        _LOGGER.error("Skylight auth_code missing — cannot load integration")
        return False

<<<<<<< Updated upstream
    api = SkylightAPI(
        {
            "frame_id": frame_id,
            "username": username,
            "password": password,
            "auth_code": auth_code,
        },
        hass=hass,
    )
=======
    api = SkylightAPI({"auth_code": auth_code}, hass=hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"api": api}
>>>>>>> Stashed changes

    # Fetch live frames from Skylight API

    try:
        live_frames = await api.get_frames()
    except Exception as e:
        _LOGGER.error("Failed to fetch frames from Skylight API: %s", e)
        live_frames = stored_frames  # fallback

    stored_ids = {f["id"] for f in stored_frames}
    live_ids = {f["id"] for f in live_frames}

    new_ids = live_ids - stored_ids

    if new_ids:
        new_frames = [f for f in live_frames if f["id"] in new_ids]
        _LOGGER.warning("New Skylight frames detected: %s", new_frames)

        updated_frames = stored_frames + new_frames

        # Save back into the config entry
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                "frame_data": updated_frames,
            },
        )
        stored_frames = updated_frames  # use updated list

        _LOGGER.warning("Config entry updated with new frames")

    # Deduplicate frames by ID

    seen_ids = set()
    unique_frames = []
    for frame in stored_frames:
        if frame["id"] not in seen_ids:
            seen_ids.add(frame["id"])
            unique_frames.append(frame)

    # Create Home Assistant devices for each frame
    device_registry = dr.async_get(hass)
    for frame in unique_frames:
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, frame["id"])},
            manufacturer="Skylight",
            name=frame.get("name", f"Skylight Frame {frame['id']}"),
            model="Calendar Frame",
        )

    # Forward to platforms

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    unload_ok = all(
        [
            await hass.config_entries.async_forward_entry_unload(entry, platform)
            for platform in PLATFORMS
        ]
    )
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
