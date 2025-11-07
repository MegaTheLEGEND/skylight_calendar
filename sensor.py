"""Skylight sensor platform."""

# this is basically just a test before i got calendar.py working

from datetime import datetime, timedelta
import logging

from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util

from .api import SkylightAPI
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class SkylightTodayEventsSensor(Entity):
    """Sensor that stores the raw API response for today."""

    def __init__(self, api: SkylightAPI, frame_id: str):
        self._api = api
        self._frame_id = frame_id
        self._state = None
        self._raw_response = None

    @property
    def name(self):
        return f"Skylight Frame {self._frame_id} Raw Response Today"

    @property
    def unique_id(self):
        return f"skylight_{self._frame_id}_raw_response_today"

    @property
    def state(self):
        return self._state

    @property
    def extra_state_attributes(self):
        return {"raw_response": self._raw_response}

    async def async_update(self):
        """Fetch today-to-tomorrow events from Skylight API."""
        try:
            today = dt_util.now().date()
            tomorrow = today + timedelta(days=1)
            start = today.isoformat()
            end = tomorrow.isoformat()
            self._raw_response = await self._api.get_events(start, end)
            self._state = self._raw_response
        except Exception as err:
            _LOGGER.error("Error fetching raw Skylight response: %s", err)
            self._raw_response = None
            self._state = None


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Skylight sensor from a config entry."""
    api = hass.data[DOMAIN][entry.entry_id]["api"]
    frame_id = hass.data[DOMAIN][entry.entry_id]["frame_id"]

    sensor = SkylightTodayEventsSensor(api, frame_id)
    async_add_entities([sensor], update_before_add=True)
