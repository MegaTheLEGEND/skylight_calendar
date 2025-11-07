"""Skylight calendar platform."""

from datetime import datetime
import logging

from dateutil.parser import parse as parse_datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.util import dt as dt_util

from .api import SkylightAPI
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class SkylightCalendar(CalendarEntity):
    """Calendar that fetches events from Skylight API."""

    def __init__(self, api: SkylightAPI, frame_id: str):
        """Initialize the calendar entity."""
        self._api = api
        self._frame_id = frame_id
        self._name = f"Skylight Frame {frame_id} Calendar"
        self._event = None

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return f"skylight_{self._frame_id}_calendar"

    @property
    def event(self):
        """Return the current active event, if any."""
        return self._event

    async def async_get_events(self, hass, start_date: datetime, end_date: datetime):
        """Return events within the specified datetime range."""
        try:
            start_iso = start_date.date().isoformat()
            end_iso = end_date.date().isoformat()

            raw_events = await self._api.get_events(start_iso, end_iso)
            if not isinstance(raw_events, dict) or "data" not in raw_events:
                return []

            result = []
            now = dt_util.now()
            self._event = None

            for ev in raw_events["data"]:
                attrs = ev.get("attributes", {})

                # Parse start/end datetimes safely
                try:
                    ev_start = (
                        parse_datetime(attrs.get("starts_at"))
                        if attrs.get("starts_at")
                        else None
                    )
                    ev_end = (
                        parse_datetime(attrs.get("ends_at"))
                        if attrs.get("ends_at")
                        else None
                    )
                except Exception as err:
                    _LOGGER.warning("Failed to parse event datetime: %s", err)
                    continue

                if not ev_start or not ev_end:
                    continue

                # Wrap in CalendarEvent dataclass
                event_obj = CalendarEvent(
                    start=ev_start,
                    end=ev_end,
                    summary=attrs.get("summary", "Skylight Event"),
                    description=attrs.get("description") or "",
                    location=attrs.get("location") or "",
                )
                result.append(event_obj)

                # Update current event if happening now
                if ev_start <= now <= ev_end:
                    self._event = event_obj

            return result

        except Exception as err:
            _LOGGER.error("Error fetching Skylight events: %s", err)
            return []


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the Skylight calendar from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    frame_id = data["frame_id"]

    calendar = SkylightCalendar(api, frame_id)
    async_add_entities([calendar])
