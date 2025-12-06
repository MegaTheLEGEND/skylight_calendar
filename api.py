from collections.abc import Mapping
import logging

import aiohttp

from homeassistant.util import dt as dt_util

from .const import BASE_URL

_LOGGER = logging.getLogger(__name__)


class SkylightAPI:
    """Async wrapper for Skylight API calls."""

    def __init__(self, entry_data: Mapping, hass=None):
        """Initialize with config entry data (MappingProxyType or dict)."""
        self.auth_code = entry_data.get("auth_code")
        self.frame_id = entry_data.get("frame_id")
        self._hass = hass  # optional, needed to get HA timezone

    def get_headers(self) -> dict:
        """Return headers with the Authorization code."""
        return {"Authorization": f"Basic {self.auth_code}"}

    async def get_events(self, range_start: str, range_end: str) -> dict | bool:
        """Fetch calendar events asynchronously."""
        url = f"{BASE_URL}/frames/{self.frame_id}/calendar_events"
        headers = self.get_headers()

        # Use Home Assistant timezone if available, otherwise fallback
        timezone = "UTC"
        if self._hass is not None:
            timezone = getattr(self._hass.config, "time_zone", "UTC")
        elif hasattr(dt_util, "DEFAULT_TIME_ZONE"):
            timezone = str(dt_util.DEFAULT_TIME_ZONE)

        params = {
            "date_min": range_start,
            "date_max": range_end,
            "timezone": timezone,
        }

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 401:
                        _LOGGER.warning("Unauthorized: invalid auth code")
                        return False
                    if resp.status == 404:
                        _LOGGER.warning("Frame not found: %s", self.frame_id)
                        return False

                    resp.raise_for_status()
                    return await resp.json()

        except Exception as e:
            _LOGGER.error("SkylightAPI error: %s", e)
            return False
