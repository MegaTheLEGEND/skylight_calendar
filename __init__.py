"""setup skylight_calendar integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .api import SkylightAPI
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["calendar"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    auth_code = entry.data.get("auth_code")
    stored_frames = entry.data.get("frame_data", [])

    if not auth_code:
        _LOGGER.error("Skylight auth_code missing — cannot load integration")
        return False

    api = SkylightAPI({"auth_code": auth_code}, hass=hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"api": api}

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
