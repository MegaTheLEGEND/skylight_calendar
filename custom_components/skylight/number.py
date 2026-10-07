"""Number platform: numeric Skylight device settings."""

from __future__ import annotations

import logging

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .buddy import SkylightBuddyEntity, add_new_entities
from .entity import SkylightDeviceEntity

_LOGGER = logging.getLogger(__name__)


NUMBERS: tuple[NumberEntityDescription, ...] = (
    NumberEntityDescription(
        key="brightness",
        name="Brightness",
        icon="mdi:brightness-6",
        native_min_value=0,
        native_max_value=255,
        native_step=1,
    ),
    NumberEntityDescription(
        key="slideshow_speed",
        name="Slideshow speed",
        icon="mdi:timer-outline",
        native_min_value=0,
        native_max_value=240,
        native_step=1,
        native_unit_of_measurement="s",
    ),
    # The 0-100 ranges below are inferred, not observed: a real frame reports
    # nightlight_brightness=65 and sleep_sound_volume=70, which rules out the
    # 0-255 scale `brightness` uses but doesn't prove the ceiling. If the frame
    # rejects a high value, this is the first place to look.
    NumberEntityDescription(
        key="nightlight_brightness",
        name="Night light brightness",
        icon="mdi:brightness-4",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement="%",
        entity_category=EntityCategory.CONFIG,
    ),
    NumberEntityDescription(
        key="sleep_sound_volume",
        name="Sleep sound volume",
        icon="mdi:volume-medium",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement="%",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SkylightDeviceNumber(
            data["frame_coordinator"],
            data["api"],
            data["frame_id"],
            data["frame_name"],
            description,
        )
        for description in NUMBERS
    )

    buddy = data["buddy_coordinator"]
    buddy_numbers = [d for d in NUMBERS
                     if d.key in ("nightlight_brightness", "sleep_sound_volume")]

    def _buddy_entities(device_id: str, alarm_id: str | None) -> list:
        if alarm_id:
            return [SkylightAlarmVolume(buddy, data["frame_id"], device_id, alarm_id)]
        return [SkylightBuddyNumber(buddy, data["frame_id"], device_id, d)
                for d in buddy_numbers]

    add_new_entities(buddy, async_add_entities, _buddy_entities)


class SkylightDeviceNumber(SkylightDeviceEntity, NumberEntity):
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, coordinator, api, frame_id, frame_name, description
    ) -> None:
        super().__init__(coordinator, api, frame_id, frame_name, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        raw = self._raw_value
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self._async_patch(int(value))


class SkylightBuddyNumber(SkylightBuddyEntity, NumberEntity):
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator, frame_id, device_id, description) -> None:
        super().__init__(coordinator, frame_id, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        raw = self._raw_value
        return None if raw is None else float(raw)

    async def async_set_native_value(self, value: float) -> None:
        await self._async_patch(int(value))


class SkylightAlarmVolume(SkylightBuddyEntity, NumberEntity):
    """Volume of one Buddy alarm (observed 0-100)."""

    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:volume-medium"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, frame_id, device_id, alarm_id) -> None:
        super().__init__(coordinator, frame_id, device_id, "volume", alarm_id)

    @property
    def name(self) -> str:
        return f"{self._alarm_label} alarm volume"

    @property
    def native_value(self) -> float | None:
        raw = self._raw_value
        return None if raw is None else float(raw)

    async def async_set_native_value(self, value: float) -> None:
        await self._async_patch(int(value))
