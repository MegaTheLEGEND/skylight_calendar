"""Calendar entity for Skylight frame integration."""

import logging

from dateutil.parser import parse as parse_datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class SkylightCalendar(CalendarEntity):
    """Calendar entity for a Skylight frame."""

    def __init__(self, api, frame_id, frame_name=None):
        """Initialize the calendar entity."""
        self._api = api
        self._frame_id = frame_id
        self._name = frame_name or f"Skylight Frame {frame_id} Calendar"
        self._event = None  # current active event

    @property
    def name(self):
        """Return the name of the calendar."""
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID for this calendar."""
        return f"skylight_{self._frame_id}_calendar"

    @property
    def event(self):
        """Return the current active event, if any."""
        return self._event

    async def async_get_events(self, hass, start_date, end_date):
        """Return events within the specified datetime range."""
        try:
            start_iso = start_date.date().isoformat()
            end_iso = end_date.date().isoformat()

            raw_events = await self._api.get_events(self._frame_id, start_iso, end_iso)

            if not raw_events or "data" not in raw_events:
                return []

            now = dt_util.now()
            self._event = None
            events = []

            for ev in raw_events["data"]:
                attrs = ev.get("attributes", {})

                # Parse start/end times safely
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

                if not ev_start or not ev_end:
                    continue

                event_obj = CalendarEvent(
                    start=ev_start,
                    end=ev_end,
                    summary=attrs.get("summary", "Skylight Event"),
                    description=attrs.get("description") or "",
                    location=attrs.get("location") or "",
                )
                events.append(event_obj)

                # Track current active event
                if ev_start <= now <= ev_end:
                    self._event = event_obj

        except Exception as e:
            _LOGGER.error("Failed to fetch events for frame %s: %s", self._frame_id, e)
            return []

        return events


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Skylight calendars from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]

    # Each frame in the entry gets its own calendar entity
    frames = entry.data.get("frame_data", [])
    calendars = [SkylightCalendar(api, f["id"], f["name"]) for f in frames]

    async_add_entities(calendars)
