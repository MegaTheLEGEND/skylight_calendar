"""Calendar entities: one aggregate 'All' + one per source_calendar."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
import logging
from typing import Any

from dateutil.parser import parse as parse_datetime

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import SkylightAPIError
from .const import DOMAIN
from .coordinator import SkylightCalendarCoordinator
from .people import (
    add_people_line,
    event_people,
    family_member_labels,
    strip_people_line,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coord: SkylightCalendarCoordinator = data["calendar_coordinator"]
    frame_id: str = data["frame_id"]
    frame_name: str = data["frame_name"]

    # Aggregate entity (backward-compatible with pre-refactor unique_id).
    entities: list[CalendarEntity] = [
        SkylightAggregateCalendar(coord, frame_id, frame_name),
    ]

    seen: set[str] = set()

    def _current_source_ids() -> list[dict]:
        d = coord.data or {}
        return d.get("source_calendars", []) or []

    # Initial per-source entities.
    for sc in _current_source_ids():
        sid = sc["id"]
        entities.append(
            SkylightSourceCalendar(coord, frame_id, frame_name, sid, sc.get("name") or sid)
        )
        seen.add(sid)

    async_add_entities(entities)

    @callback
    def _handle_update() -> None:
        new_entities: list[CalendarEntity] = []
        for sc in _current_source_ids():
            sid = sc["id"]
            if sid in seen:
                continue
            new_entities.append(
                SkylightSourceCalendar(
                    coord, frame_id, frame_name, sid, sc.get("name") or sid
                )
            )
            seen.add(sid)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coord.async_add_listener(_handle_update))


def _parse_events(
    raw_events: dict, source_id: str | None = None, source_key: str | None = None
) -> list[CalendarEvent]:
    """Parse Skylight event feed → CalendarEvent list, optionally filtered by source calendar."""
    events: list[CalendarEvent] = []
    labels = family_member_labels(raw_events.get("included"))
    for ev in raw_events.get("data", []):
        attrs = ev.get("attributes", {})
        if source_id is not None:
            rels = ev.get("relationships", {}) or {}
            sc_rel = rels.get("source_calendar", {}) or {}
            sc_data = sc_rel.get("data") or {}
            ev_source = str(sc_data.get("id") or attrs.get("source_calendar_id") or "")
            if ev_source:
                if ev_source != str(source_id):
                    continue
            elif source_key:
                # The frames API returns no source_calendar relationship on an event.
                # The only link back is calendar_event.calendar_id matching the
                # source calendar's source_id.
                if str(attrs.get("calendar_id") or "") != str(source_key):
                    continue
            else:
                continue
        starts = attrs.get("starts_at")
        ends = attrs.get("ends_at")
        if not starts or not ends:
            continue
        try:
            start_dt = parse_datetime(starts)
            end_dt = parse_datetime(ends)
        except (ValueError, TypeError):
            continue
        events.append(
            CalendarEvent(
                start=start_dt,
                end=end_dt,
                summary=attrs.get("summary") or "Skylight Event",
                description=add_people_line(
                    attrs.get("description") or "", event_people(ev, labels)
                ),
                location=attrs.get("location") or "",
                # uid is what HA hands back on update/delete, so it has to be the
                # id Skylight addresses the event by.
                uid=str(ev.get("id")) if ev.get("id") is not None else None,
            )
        )
    return events


def _wire_datetime(value: date | datetime) -> str:
    """Serialise a HA start/end for Skylight (bare date ⇒ all-day event)."""
    return value.isoformat()


def _to_skylight_fields(data: Mapping[str, Any]) -> dict:
    """Map HA calendar service fields onto Skylight's flat event body.

    HA passes ``dtstart``/``dtend``/``summary``/``description``/``location``/
    ``rrule``; only the keys actually present are translated, so the same mapper
    serves both create (all fields) and update (partial patch).
    """
    out: dict[str, Any] = {}
    if "summary" in data:
        out["summary"] = data["summary"]
    if "description" in data:
        # Drop the generated "People:" line so it never lands in the Skylight event.
        out["description"] = strip_people_line(data["description"])
    if "location" in data:
        out["location"] = data["location"] or ""
    if (start := data.get("dtstart")) is not None:
        out["starts_at"] = _wire_datetime(start)
        out["all_day"] = not isinstance(start, datetime)
    if (end := data.get("dtend")) is not None:
        out["ends_at"] = _wire_datetime(end)
    if rrule := data.get("rrule"):
        # Skylight stores recurrence as a list of iCal lines while HA hands over a
        # bare rule body ("FREQ=WEEKLY;BYDAY=SA"), hence the prefix. The exact
        # shape Skylight wants here is inferred from its type definitions, not
        # observed against a live frame.
        out["rrule"] = [rrule if rrule.startswith("RRULE:") else f"RRULE:{rrule}"]
    return out


class _SkylightCalendarBase(
    CoordinatorEntity[SkylightCalendarCoordinator], CalendarEntity
):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SkylightCalendarCoordinator,
        frame_id: str,
        frame_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._frame_id = frame_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, frame_id)},
            name=frame_name,
            manufacturer="Skylight",
            model="Calendar Frame",
        )

    def _source_filter(self) -> str | None:
        return None

    def _source_key(self) -> str | None:
        return None

    def _all_events(self) -> list[CalendarEvent]:
        raw = (self.coordinator.data or {}).get("events", {}) or {}
        return _parse_events(raw, self._source_filter(), self._source_key())

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        for ev in self._all_events():
            start = ev.start if isinstance(ev.start, datetime) else None
            end = ev.end if isinstance(ev.end, datetime) else None
            if start and end and start <= now <= end:
                return ev
        upcoming = [
            e for e in self._all_events()
            if isinstance(e.start, datetime) and e.start >= now
        ]
        upcoming.sort(key=lambda e: e.start)
        return upcoming[0] if upcoming else None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        result = []
        for ev in self._all_events():
            ev_start = ev.start if isinstance(ev.start, datetime) else None
            ev_end = ev.end if isinstance(ev.end, datetime) else None
            if not ev_start or not ev_end:
                continue
            if ev_end < start_date or ev_start > end_date:
                continue
            result.append(ev)
        return result

    # ── Writes ──────────────────────────────────────────────────────────

    def _timezone(self) -> str:
        return getattr(self.hass.config, "time_zone", None) or "UTC"

    async def async_create_event(self, **kwargs: Any) -> None:
        fields = _to_skylight_fields(kwargs)
        starts_at = fields.get("starts_at")
        ends_at = fields.get("ends_at")
        if not starts_at or not ends_at:
            raise HomeAssistantError("A Skylight event needs both a start and an end.")
        try:
            await self.coordinator.api.create_calendar_event(
                self._frame_id,
                summary=fields.get("summary") or "Event",
                starts_at=starts_at,
                ends_at=ends_at,
                all_day=fields.get("all_day", False),
                description=fields.get("description"),
                location=fields.get("location"),
                rrule=fields.get("rrule"),
                timezone=self._timezone(),
            )
        except SkylightAPIError as err:
            raise HomeAssistantError(f"Skylight rejected the new event: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_update_event(
        self,
        uid: str,
        event: dict[str, Any],
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        _reject_recurrence_range(recurrence_range)
        attributes = _to_skylight_fields(event)
        if not attributes:
            return
        attributes["timezone"] = self._timezone()
        try:
            await self.coordinator.api.update_calendar_event(
                self._frame_id, uid, attributes
            )
        except SkylightAPIError as err:
            raise HomeAssistantError(
                f"Skylight rejected the event update: {err}"
            ) from err
        await self.coordinator.async_request_refresh()

    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        _reject_recurrence_range(recurrence_range)
        try:
            await self.coordinator.api.delete_calendar_event(self._frame_id, uid)
        except SkylightAPIError as err:
            raise HomeAssistantError(f"Skylight rejected the delete: {err}") from err
        await self.coordinator.async_request_refresh()


def _reject_recurrence_range(recurrence_range: str | None) -> None:
    """Skylight addresses one event instance at a time — no series semantics."""
    if recurrence_range:
        raise HomeAssistantError(
            "Skylight can only edit or delete a single event instance, not a "
            "whole recurrence range."
        )


class SkylightAggregateCalendar(_SkylightCalendarBase):
    """Merged view of every source calendar (backward-compatible entity).

    This is the only entity that accepts new events: Skylight puts an event with
    no ``calendar_id`` on the frame's own calendar, whereas pinning one to a
    specific connected source needs account ids we don't read anywhere yet.
    """

    _attr_name = "Calendar"
    _attr_supported_features = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
    )

    def __init__(self, coordinator, frame_id, frame_name):
        super().__init__(coordinator, frame_id, frame_name)
        self._attr_unique_id = f"skylight_{frame_id}_calendar"


class SkylightSourceCalendar(_SkylightCalendarBase):
    """Per-source-calendar entity (one per connected Google/Skylight calendar)."""

    def __init__(self, coordinator, frame_id, frame_name, source_id: str, source_name: str):
        super().__init__(coordinator, frame_id, frame_name)
        self._source_id = str(source_id)
        self._attr_name = source_name
        self._attr_unique_id = f"skylight_{frame_id}_calendar_source_{source_id}"

    @property
    def supported_features(self) -> CalendarEntityFeature:
        """Edit and delete existing events, but only on writable sources.

        Read-only subscriptions (shared Google calendars, holiday feeds) reject
        writes at the source, so don't offer the affordance at all.
        """
        if not self._editable():
            return CalendarEntityFeature(0)
        return (
            CalendarEntityFeature.UPDATE_EVENT | CalendarEntityFeature.DELETE_EVENT
        )

    def _source_calendar(self) -> dict:
        for sc in (self.coordinator.data or {}).get("source_calendars", []) or []:
            if str(sc.get("id")) == self._source_id:
                return sc
        return {}

    def _editable(self) -> bool:
        return bool(self._source_calendar().get("editable"))

    def _source_filter(self) -> str | None:
        return self._source_id

    def _source_key(self) -> str | None:
        return self._source_calendar().get("source_id")
