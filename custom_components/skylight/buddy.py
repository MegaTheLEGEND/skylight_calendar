"""Skylight Buddy devices and their alarms.

A Buddy is a second device on the frame (``role == "buddy"``). The frame
coordinator only ever reads the primary device, so Buddies get their own
coordinator and their own HA device.
Alarms live at ``/api/frames/{fid}/devices/{did}/alarms``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import time as dt_time, timedelta
import logging
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import SkylightAPI, SkylightAPIError, SkylightAuthError
from .const import DOMAIN, FRAME_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class SkylightBuddyCoordinator(DataUpdateCoordinator):
    """Data shape: ``{device_id: {"attributes": {...}, "alarms": {alarm_id: {...}}}}``."""

    def __init__(self, hass, api: SkylightAPI, frame_id: str,
                 update_interval: int = FRAME_SCAN_INTERVAL) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} buddies {frame_id}",
            update_interval=timedelta(seconds=update_interval),
        )
        self.api = api
        self.frame_id = frame_id

    async def _async_update_data(self) -> dict:
        try:
            devices = await self.api.get_devices(self.frame_id)
            out: dict[str, dict] = {}
            for dev in devices:
                if dev["attributes"].get("role") != "buddy":
                    continue
                alarms = await self.api.get_alarms(self.frame_id, dev["id"])
                out[dev["id"]] = {
                    "attributes": dev["attributes"],
                    "alarms": {a["id"]: a["attributes"] for a in alarms},
                }
            return out
        except SkylightAuthError as err:
            raise UpdateFailed(f"Auth failed: {err}") from err
        except SkylightAPIError as err:
            raise UpdateFailed(str(err)) from err


def add_new_entities(coordinator: SkylightBuddyCoordinator,
                     async_add_entities, factory: Callable[[str, str | None], list]) -> None:
    """Add entities for Buddies/alarms as they appear.

    ``factory(device_id, alarm_id)`` is called once per device with
    ``alarm_id=None`` and once per alarm; it returns the entities to add.
    """
    seen: set[tuple[str, str | None]] = set()

    def _check() -> None:
        new = []
        for did, dev in (coordinator.data or {}).items():
            for key in [None, *dev["alarms"]]:
                if (did, key) not in seen:
                    seen.add((did, key))
                    new.extend(factory(did, key))
        if new:
            async_add_entities(new)

    _check()
    coordinator.async_add_listener(_check)


class SkylightBuddyEntity(CoordinatorEntity[SkylightBuddyCoordinator]):
    """Base for a Buddy setting (``alarm_id`` None) or one of its alarms."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SkylightBuddyCoordinator, frame_id: str,
                 device_id: str, key: str, alarm_id: str | None = None) -> None:
        super().__init__(coordinator)
        self._frame_id = frame_id
        self._device_id = device_id
        self._key = key
        self._alarm_id = alarm_id
        scope = f"alarm_{alarm_id}_{key}" if alarm_id else key
        self._attr_unique_id = f"skylight_{frame_id}_{device_id}_{scope}"
        dev = (coordinator.data or {}).get(device_id, {})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{frame_id}_{device_id}")},
            name=dev.get("attributes", {}).get("name") or "Skylight Buddy",
            manufacturer="Skylight",
            model="Buddy",
        )

    @property
    def _source(self) -> dict | None:
        dev = (self.coordinator.data or {}).get(self._device_id)
        if dev is None:
            return None
        return dev["alarms"].get(self._alarm_id) if self._alarm_id else dev["attributes"]

    @property
    def _raw_value(self) -> Any:
        return (self._source or {}).get(self._key)

    @property
    def available(self) -> bool:
        src = self._source
        return super().available and src is not None and self._key in src

    @property
    def _alarm_label(self) -> str:
        return (self._source or {}).get("label") or f"Alarm {self._alarm_id}"

    async def _async_patch(self, value: Any) -> None:
        api = self.coordinator.api
        try:
            if self._alarm_id:
                resp = await api.patch_alarm(self._frame_id, self._device_id,
                                             self._alarm_id, {self._key: value})
            else:
                resp = await api.patch_device(self._frame_id, self._device_id,
                                              {self._key: value})
        except SkylightAPIError as err:
            raise HomeAssistantError(
                f"Skylight rejected {self._key}={value!r}: {err}"
            ) from err
        # Apply the returned resource now; async_request_refresh is debounced
        # and would leave the old value showing for several seconds.
        fresh = ((resp or {}).get("data") or {}).get("attributes")
        dev = (self.coordinator.data or {}).get(self._device_id)
        if fresh and dev is not None:
            if self._alarm_id:
                dev["alarms"][self._alarm_id] = fresh
            else:
                dev["attributes"] = fresh
            self.coordinator.async_set_updated_data(self.coordinator.data)
        else:
            await self.coordinator.async_request_refresh()


def parse_hhmm(raw: Any) -> dt_time | None:
    if not isinstance(raw, str):
        return None
    try:
        return dt_time.fromisoformat(raw)
    except ValueError:
        return None
