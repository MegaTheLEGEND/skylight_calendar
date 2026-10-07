"""Sensor entities: chores/meals + per-member breakdowns, per-slot meals, star totals."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MEAL_CATEGORY_NAMES
from .coordinator import SkylightSensorCoordinator
from .entity import SkylightDeviceEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coord: SkylightSensorCoordinator = data["sensor_coordinator"]
    frame_id: str = data["frame_id"]
    frame_name: str = data["frame_name"]

    entities: list[SensorEntity] = [
        SkylightChoresTodaySensor(coord, frame_id, frame_name),
        SkylightAllChoresSensor(coord, frame_id, frame_name),
        SkylightMealsTodaySensor(coord, frame_id, frame_name),
    ]

    # Per-member profile categories → chores + rewards sensors.
    for cat_id, label in _profile_categories(coord.data or {}):
        entities.append(
            SkylightRewardPointsSensor(coord, frame_id, frame_name, cat_id, label)
        )
        entities.append(
            SkylightMemberChoresSensor(coord, frame_id, frame_name, cat_id, label)
        )

    # Per-slot meal sensors (Breakfast/Lunch/Dinner/Snack) — always present so cards work
    # even when no meal is planned for a slot.
    for cat_id, label in MEAL_CATEGORY_NAMES.items():
        entities.append(
            SkylightMealSlotSensor(coord, frame_id, frame_name, cat_id, label)
        )

    # Task Box — reusable chore-template pool
    entities.append(SkylightTaskBoxSensor(coord, frame_id, frame_name))

    # Device settings we can read but not safely write (see DEVICE_DIAGNOSTICS).
    entities.extend(
        SkylightDeviceDiagnostic(
            data["frame_coordinator"],
            data["api"],
            frame_id,
            frame_name,
            description,
        )
        for description in DEVICE_DIAGNOSTICS
    )

    async_add_entities(entities)


# Read-only on purpose. These are all writable on the device, but each is an
# enum whose full option set we've only ever seen one value of ("screen_off",
# "off", 0, null). Publishing a select built from a guessed option list would
# hand users controls that 4xx; surfacing the current value costs nothing and is
# honest. Promote any of these to a select once a capture shows the real options.
DEVICE_DIAGNOSTICS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="sleep_mode",
        name="Sleep mode behaviour",
        icon="mdi:power-sleep",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="nightlight_color",
        name="Night light colour",
        icon="mdi:palette",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="sleep_sound",
        name="Sleep sound",
        icon="mdi:music-note",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="slideshow_style",
        name="Slideshow style",
        icon="mdi:image-multiple",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="timezone",
        name="Frame timezone",
        icon="mdi:earth",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


class SkylightDeviceDiagnostic(SkylightDeviceEntity, SensorEntity):
    """A device setting surfaced read-only."""

    def __init__(
        self, coordinator, api, frame_id, frame_name, description
    ) -> None:
        super().__init__(
            coordinator,
            api,
            frame_id,
            frame_name,
            description.key,
            # Namespaced: "sleep_mode" as a device key would otherwise collide
            # with the sleep_mode_on switch's unique_id. HA scopes uniqueness by
            # domain so it would work, but two entities sharing an id string is
            # a trap for anyone reading the registry later.
            unique_suffix=f"setting_{description.key}",
        )
        self.entity_description = description

    @property
    def native_value(self) -> str | None:
        raw = self._raw_value
        return None if raw is None else str(raw)


def _category_label_map(data: dict) -> dict[str, str]:
    """category_id -> label lookup covering all categories (profiles + calendar buckets)."""
    cats = (data.get("categories") or {}).get("data", [])
    return {
        str(c.get("id")): c.get("attributes", {}).get("label", "")
        for c in cats
    }


def _enrich_chore(c: dict, cat_map: dict[str, str]) -> dict[str, Any]:
    """Full detail record for a chore — for Lovelace/automation templates."""
    attrs = c.get("attributes", {}) if isinstance(c, dict) else {}
    rrules = attrs.get("recurrence_set") or []
    rrule = rrules[0] if rrules else None
    cat_id = _chore_assignee_id(c)
    return {
        "id": c.get("id"),
        "summary": attrs.get("summary") or attrs.get("name"),
        "description": attrs.get("description"),
        "emoji": attrs.get("emoji_icon"),
        "status": attrs.get("status"),
        "reward_points": attrs.get("reward_points"),
        "start_date": _chore_when(attrs),
        "start_time": attrs.get("start_time"),
        "completed_on": attrs.get("completed_on"),
        "completed_at": attrs.get("completed_at"),
        "recurring": attrs.get("recurring"),
        "recurrence_rrule": rrule,
        "routine": attrs.get("routine"),
        "up_for_grabs": attrs.get("up_for_grabs"),
        "timer_seconds": attrs.get("timer_seconds"),
        "assignee_id": cat_id,
        "assignee": cat_map.get(str(cat_id)) if cat_id else None,
    }


def _meal_included_maps(data: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """Return (meal_category_by_id, meal_recipe_by_id) from included payload."""
    inc = (data or {}).get("meals_included") or []
    cats = {str(i["id"]): i for i in inc if i.get("type") == "meal_category"}
    recipes = {str(i["id"]): i for i in inc if i.get("type") == "meal_recipe"}
    return cats, recipes


def _enrich_meal(
    m: dict, cats: dict[str, dict], recipes: dict[str, dict]
) -> dict[str, Any]:
    attrs = m.get("attributes", {}) if isinstance(m, dict) else {}
    rels = m.get("relationships", {}) or {}
    cat_id = ((rels.get("meal_category") or {}).get("data") or {}).get("id")
    recipe_id = ((rels.get("meal_recipe") or {}).get("data") or {}).get("id")
    cat_label = None
    recipe_title = None
    recipe_desc = None
    if cat_id and str(cat_id) in cats:
        cat_label = cats[str(cat_id)].get("attributes", {}).get("label")
    if recipe_id and str(recipe_id) in recipes:
        r_attrs = recipes[str(recipe_id)].get("attributes", {})
        recipe_title = r_attrs.get("summary")
        recipe_desc = r_attrs.get("description")
    return {
        "id": m.get("id"),
        "summary": attrs.get("summary"),
        "description": attrs.get("description"),
        "note": attrs.get("note"),
        "date": _meal_when(attrs),
        "category_id": cat_id,
        "category": cat_label or MEAL_CATEGORY_NAMES.get(str(cat_id or ""), "Other"),
        "recipe_id": recipe_id,
        "recipe_title": recipe_title,
        "recipe_description": recipe_desc,
        "recurring": attrs.get("recurring"),
        "recurrence_rrule": attrs.get("rrule"),
        "instances": attrs.get("instances"),
    }


def _profile_categories(data: dict) -> list[tuple[str, str]]:
    """Extract (category_id, label) for family-member profiles only."""
    cats = (data.get("categories") or {}).get("data", [])
    out: list[tuple[str, str]] = []
    for c in cats:
        attrs = c.get("attributes", {})
        if not attrs.get("linked_to_profile"):
            continue
        label = attrs.get("label", "")
        if "@" in label:  # skip Google-calendar sub-category entries
            continue
        out.append((str(c.get("id")), label))
    return out


def _chore_entries(data: dict) -> list[dict]:
    raw = (data or {}).get("chores")
    if raw is None:
        return []
    return raw if isinstance(raw, list) else raw.get("data", []) or []


def _meal_entries(data: dict) -> list[dict]:
    raw = (data or {}).get("meals")
    if raw is None:
        return []
    return raw if isinstance(raw, list) else raw.get("data", []) or []


def _chore_assignee_id(c: dict) -> str | None:
    attrs = c.get("attributes", {}) if isinstance(c, dict) else {}
    rel = (c.get("relationships", {}) or {}).get("category", {}) or {}
    data = rel.get("data") or {}
    return str(data.get("id") or attrs.get("category_id") or "") or None


def _chore_when(attrs: dict) -> str:
    return (
        attrs.get("start")
        or attrs.get("date")
        or attrs.get("due_date")
        or ""
    )


def _meal_when(attrs: dict) -> str:
    """Legacy single-date field. Prefer `_meal_scheduled_for_today` for filtering."""
    return attrs.get("date") or attrs.get("starts_at") or ""


def _meal_scheduled_for_today(attrs: dict, today: str) -> bool:
    """Skylight schedules recurring meals via `instances: [ISO-date, ...]`.

    Falls back to the legacy `date`/`starts_at` field for non-recurring meals.
    """
    instances = attrs.get("instances") or []
    if today in instances:
        return True
    when = _meal_when(attrs)
    return isinstance(when, str) and when.startswith(today)


def _meal_category_id(m: dict) -> str | None:
    rel = (m.get("relationships", {}) or {}).get("meal_category", {}) or {}
    data = rel.get("data") or {}
    return str(data.get("id") or "") or None


def _status_breakdown(items: list[dict]) -> dict[str, int]:
    """Aggregate {status: count} across a chore/list."""
    out: dict[str, int] = {}
    for it in items:
        attrs = it.get("attributes", {}) if isinstance(it, dict) else {}
        s = attrs.get("status") or "unknown"
        out[s] = out.get(s, 0) + 1
    return out


class _SkylightBaseSensor(CoordinatorEntity[SkylightSensorCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SkylightSensorCoordinator,
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


class SkylightChoresTodaySensor(_SkylightBaseSensor):
    _attr_name = "Chores today"
    _attr_icon = "mdi:broom"

    def __init__(self, coordinator, frame_id, frame_name):
        super().__init__(coordinator, frame_id, frame_name)
        self._attr_unique_id = f"skylight_{frame_id}_chores_today"

    def _today_chores(self) -> list[dict]:
        today = dt_util.now().date().isoformat()
        out = []
        for c in _chore_entries(self.coordinator.data or {}):
            attrs = c.get("attributes", {}) if isinstance(c, dict) else {}
            due = _chore_when(attrs)
            if isinstance(due, str) and due.startswith(today):
                out.append(c)
        return out

    @property
    def native_value(self) -> int:
        return len(self._today_chores())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        chores = self._today_chores()
        cat_map = _category_label_map(self.coordinator.data or {})
        return {
            "chores": [_enrich_chore(c, cat_map) for c in chores],
            "by_status": _status_breakdown(chores),
        }


class SkylightAllChoresSensor(_SkylightBaseSensor):
    """Every chore in the coordinator's cached window (today through +7 days),
    unfiltered by date.

    Unlike ``Chores today``, this also surfaces chores whose ``start`` date has
    already passed — the underlying ``get_chores`` call always asks Skylight
    for ``include_late=true``, so overdue-but-incomplete chores are already in
    ``coordinator.data``, just filtered out by the "today" sensors' own
    date check. That matters most for up-for-grabs chores: they carry no
    single assignee, so there is no per-member sensor or todo entity that
    would otherwise surface them once their date is in the past.
    """

    _attr_name = "All chores"
    _attr_icon = "mdi:clipboard-list"

    def __init__(self, coordinator, frame_id, frame_name):
        super().__init__(coordinator, frame_id, frame_name)
        self._attr_unique_id = f"skylight_{frame_id}_all_chores"

    @property
    def native_value(self) -> int:
        return len(_chore_entries(self.coordinator.data or {}))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        chores = _chore_entries(self.coordinator.data or {})
        cat_map = _category_label_map(self.coordinator.data or {})
        return {
            "chores": [_enrich_chore(c, cat_map) for c in chores],
            "by_status": _status_breakdown(chores),
        }


class SkylightMemberChoresSensor(_SkylightBaseSensor):
    """Chores today for a single family member."""

    _attr_icon = "mdi:broom"

    def __init__(self, coordinator, frame_id, frame_name, category_id: str, label: str):
        super().__init__(coordinator, frame_id, frame_name)
        self._category_id = str(category_id)
        self._attr_name = f"{label} chores today"
        self._attr_unique_id = f"skylight_{frame_id}_chores_today_{category_id}"

    def _member_chores(self) -> list[dict]:
        today = dt_util.now().date().isoformat()
        out = []
        for c in _chore_entries(self.coordinator.data or {}):
            attrs = c.get("attributes", {}) if isinstance(c, dict) else {}
            if not _chore_when(attrs).startswith(today):
                continue
            if _chore_assignee_id(c) != self._category_id:
                continue
            out.append(c)
        return out

    @property
    def native_value(self) -> int:
        return len(self._member_chores())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        chores = self._member_chores()
        cat_map = _category_label_map(self.coordinator.data or {})
        return {
            "chores": [_enrich_chore(c, cat_map) for c in chores],
            "by_status": _status_breakdown(chores),
        }


class SkylightMealsTodaySensor(_SkylightBaseSensor):
    _attr_name = "Meals today"
    _attr_icon = "mdi:silverware-fork-knife"

    def __init__(self, coordinator, frame_id, frame_name):
        super().__init__(coordinator, frame_id, frame_name)
        self._attr_unique_id = f"skylight_{frame_id}_meals_today"

    def _today_meals(self) -> list[dict]:
        today = dt_util.now().date().isoformat()
        out = []
        for m in _meal_entries(self.coordinator.data or {}):
            attrs = m.get("attributes", {}) if isinstance(m, dict) else {}
            if _meal_scheduled_for_today(attrs, today):
                out.append(m)
        return out

    @property
    def native_value(self) -> int:
        return len(self._today_meals())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        meals = self._today_meals()
        cats, recipes = _meal_included_maps(self.coordinator.data or {})
        by_slot: dict[str, int] = {name: 0 for name in MEAL_CATEGORY_NAMES.values()}
        for m in meals:
            cat_id = _meal_category_id(m)
            slot = MEAL_CATEGORY_NAMES.get(cat_id or "", "Other")
            by_slot[slot] = by_slot.get(slot, 0) + 1
        return {
            "meals": [_enrich_meal(m, cats, recipes) for m in meals],
            "by_slot": by_slot,
        }


class SkylightMealSlotSensor(_SkylightBaseSensor):
    """One entity per meal slot (Breakfast/Lunch/Dinner/Snack)."""

    _attr_icon = "mdi:silverware-fork-knife"

    def __init__(self, coordinator, frame_id, frame_name, category_id: str, label: str):
        super().__init__(coordinator, frame_id, frame_name)
        self._category_id = str(category_id)
        self._slot_label = label
        self._attr_name = f"{label} today"
        self._attr_unique_id = f"skylight_{frame_id}_meal_slot_{category_id}"

    def _slot_meals(self) -> list[dict]:
        today = dt_util.now().date().isoformat()
        out = []
        for m in _meal_entries(self.coordinator.data or {}):
            attrs = m.get("attributes", {}) if isinstance(m, dict) else {}
            if not _meal_scheduled_for_today(attrs, today):
                continue
            if _meal_category_id(m) != self._category_id:
                continue
            out.append(m)
        return out

    @property
    def native_value(self) -> str:
        meals = self._slot_meals()
        if not meals:
            return "none"
        cats, recipes = _meal_included_maps(self.coordinator.data or {})
        summaries = []
        for m in meals:
            enriched = _enrich_meal(m, cats, recipes)
            # Prefer recipe title when it's meaningfully different from the summary.
            title = enriched.get("recipe_title") or enriched.get("summary") or "?"
            summaries.append(title)
        return ", ".join(summaries)[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        meals = self._slot_meals()
        cats, recipes = _meal_included_maps(self.coordinator.data or {})
        return {
            "count": len(meals),
            "meals": [_enrich_meal(m, cats, recipes) for m in meals],
        }


class SkylightTaskBoxSensor(_SkylightBaseSensor):
    """Reusable chore-template items — the frame's Task Box.

    State is the item count. `extra_state_attributes.items` lists every
    template with summary, emoji, reward_points, and routine flag — the pool
    the frame pulls from when adding an ad-hoc chore from its touchscreen.
    """

    _attr_name = "Task box"
    _attr_icon = "mdi:clipboard-list-outline"

    def __init__(self, coordinator, frame_id, frame_name):
        super().__init__(coordinator, frame_id, frame_name)
        self._attr_unique_id = f"skylight_{frame_id}_task_box"

    def _items(self) -> list[dict]:
        raw = (self.coordinator.data or {}).get("task_box")
        if raw is None:
            return []
        entries = raw if isinstance(raw, list) else raw.get("data", [])
        return [e for e in entries if isinstance(e, dict)]

    @property
    def native_value(self) -> int:
        return len(self._items())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": e.get("id"),
                    "summary": e.get("attributes", {}).get("summary"),
                    "emoji": e.get("attributes", {}).get("emoji_icon"),
                    "reward_points": e.get("attributes", {}).get("reward_points"),
                    "routine": e.get("attributes", {}).get("routine"),
                }
                for e in self._items()
            ]
        }


class SkylightRewardPointsSensor(_SkylightBaseSensor):
    _attr_icon = "mdi:star-circle"
    _attr_native_unit_of_measurement = "★"

    def __init__(
        self,
        coordinator: SkylightSensorCoordinator,
        frame_id: str,
        frame_name: str,
        category_id: str,
        label: str,
    ) -> None:
        super().__init__(coordinator, frame_id, frame_name)
        self._category_id = category_id
        self._attr_name = f"{label} stars"
        self._attr_unique_id = f"skylight_{frame_id}_stars_{category_id}"

    @property
    def native_value(self) -> int | None:
        rp = (self.coordinator.data or {}).get("reward_points")
        if rp is None:
            return None
        entries = rp if isinstance(rp, list) else rp.get("data", [])
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("category_id")) == str(self._category_id):
                return entry.get("current_point_balance") or entry.get("balance")
            attrs = entry.get("attributes", {})
            rel = (entry.get("relationships", {}) or {}).get("category", {}) or {}
            data = rel.get("data") or {}
            if str(data.get("id")) == str(self._category_id):
                return (
                    attrs.get("current_point_balance")
                    or attrs.get("balance")
                    or attrs.get("points")
                )
        return None
