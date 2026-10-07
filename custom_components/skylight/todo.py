"""Todo platform: HA Todo entities per Skylight list AND per family member's chore queue."""

from __future__ import annotations

import logging
from datetime import date, datetime

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import SkylightAPI
from .const import CHORE_COMPLETE_STATUSES, DOMAIN
from .coordinator import SkylightListsCoordinator, SkylightSensorCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    lists_coord: SkylightListsCoordinator = data["lists_coordinator"]
    sensor_coord: SkylightSensorCoordinator = data["sensor_coordinator"]
    api: SkylightAPI = data["api"]
    frame_id: str = data["frame_id"]
    frame_name: str = data["frame_name"]

    entities: list[TodoListEntity] = []

    # 1) One entity per Skylight list.
    for list_id, list_data in (lists_coord.data or {}).items():
        entities.append(
            SkylightTodoList(
                lists_coord, api, frame_id, frame_name, list_id, list_data["name"]
            )
        )

    # 2) One entity per family member: their chore queue (completeable from HA).
    for cat_id, label in _profile_categories(sensor_coord.data or {}):
        entities.append(
            SkylightMemberChoreTodo(
                sensor_coord, api, frame_id, frame_name, cat_id, label
            )
        )

    async_add_entities(entities)

    known_lists: set[str] = {
        e.list_id for e in entities if isinstance(e, SkylightTodoList)
    }

    @callback
    def _handle_lists_update() -> None:
        current = set((lists_coord.data or {}).keys())
        new_ids = current - known_lists
        if not new_ids:
            return
        new_entities = []
        for lid in new_ids:
            ldata = lists_coord.data[lid]
            new_entities.append(
                SkylightTodoList(
                    lists_coord, api, frame_id, frame_name, lid, ldata["name"]
                )
            )
            known_lists.add(lid)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(lists_coord.async_add_listener(_handle_lists_update))


def _profile_categories(data: dict) -> list[tuple[str, str]]:
    cats = (data.get("categories") or {}).get("data", [])
    out: list[tuple[str, str]] = []
    for c in cats:
        attrs = c.get("attributes", {})
        if not attrs.get("linked_to_profile"):
            continue
        label = attrs.get("label", "")
        if "@" in label:
            continue
        out.append((str(c.get("id")), label))
    return out


def _list_item_status(status: TodoItemStatus | None) -> str:
    """HA todo status → the ``pending``/``completed`` a *list item* uses.

    Deliberately not shared with chores: a chore's finished state is the literal
    ``complete``, and it's set through a separate endpoint rather than as a
    field. Merging the two is what made ticking a chore silently do nothing.
    """
    return "completed" if status == TodoItemStatus.COMPLETED else "pending"


def _chore_is_complete(attrs: dict) -> bool:
    """Whether a chore counts as done.

    Checks the completion timestamps as well as ``status`` on purpose. A filled
    ``completed_on`` / ``completed_at`` is unambiguous, and it covers the case
    where the chore *list* feed represents completion differently from the
    completions response — recurring chores expand into per-occurrence records
    (note the ``group``/``series`` fields), so an occurrence may well carry the
    date without the parent's status changing.
    """
    if str(attrs.get("status") or "") in CHORE_COMPLETE_STATUSES:
        return True
    return bool(attrs.get("completed_on") or attrs.get("completed_at"))


def _split_chore_uid(uid: str) -> tuple[str, str | None]:
    """Chore uid → its ``(series_id, occurrence_date)``.

    A recurring chore is listed once per occurrence under a composite id —
    ``101940933-2026-08-28`` — while one-off chores keep the bare series number.
    Every chore route is keyed on the series id alone, so the composite form has
    to be taken apart before it can be put in a URL; the date half is what
    ``instance_date`` wants.
    """
    series, sep, suffix = uid.partition("-")
    if sep and series.isdigit():
        try:
            return series, date.fromisoformat(suffix).isoformat()
        except ValueError:
            pass
    return uid, None


def _chore_start(due: date | datetime | None) -> str:
    """Map a HA due date onto a chore's ``start``.

    Skylight chores are day-scoped, so a due *datetime* loses its time component.
    An item with no due date lands on today, matching what the frame does when a
    chore is added from its touchscreen.
    """
    if isinstance(due, datetime):
        return due.date().isoformat()
    if isinstance(due, date):
        return due.isoformat()
    return dt_util.now().date().isoformat()


class SkylightTodoList(CoordinatorEntity[SkylightListsCoordinator], TodoListEntity):
    _attr_has_entity_name = True
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
    )

    def __init__(
        self,
        coordinator: SkylightListsCoordinator,
        api: SkylightAPI,
        frame_id: str,
        frame_name: str,
        list_id: str,
        list_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._frame_id = frame_id
        self.list_id = list_id
        self._attr_name = list_name
        self._attr_unique_id = f"skylight_{frame_id}_list_{list_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, frame_id)},
            name=frame_name,
            manufacturer="Skylight",
            model="Calendar Frame",
        )

    @property
    def todo_items(self) -> list[TodoItem] | None:
        list_data = (self.coordinator.data or {}).get(self.list_id)
        if list_data is None:
            return None
        items: list[TodoItem] = []
        for it in list_data["items"]:
            status = (
                TodoItemStatus.COMPLETED
                if it["status"] == "completed"
                else TodoItemStatus.NEEDS_ACTION
            )
            items.append(TodoItem(summary=it["name"], uid=it["id"], status=status))
        return items

    async def async_create_todo_item(self, item: TodoItem) -> None:
        await self._api.add_list_item(self._frame_id, self.list_id, item.summary or "")
        await self.coordinator.async_request_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        attrs: dict = {}
        if item.summary is not None:
            attrs["name"] = item.summary
        if item.status is not None:
            attrs["status"] = _list_item_status(item.status)
        if attrs and item.uid:
            await self._api.update_list_item(
                self._frame_id, self.list_id, item.uid, attrs
            )
            await self.coordinator.async_request_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        for uid in uids:
            await self._api.delete_list_item(self._frame_id, self.list_id, uid)
        await self.coordinator.async_request_refresh()


class SkylightMemberChoreTodo(
    CoordinatorEntity[SkylightSensorCoordinator], TodoListEntity
):
    """A single family member's chore queue as a HA Todo entity.

    Full CRUD: chores created here are assigned to this member's category, and
    completing one round-trips to the frame plus the rewards ledger. A chore's
    due date maps onto its ``start`` day.
    """

    _attr_has_entity_name = True
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(
        self,
        coordinator: SkylightSensorCoordinator,
        api: SkylightAPI,
        frame_id: str,
        frame_name: str,
        category_id: str,
        label: str,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._frame_id = frame_id
        self._category_id = str(category_id)
        self._attr_name = f"{label} chores"
        self._attr_unique_id = f"skylight_{frame_id}_chore_todo_{category_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, frame_id)},
            name=frame_name,
            manufacturer="Skylight",
            model="Calendar Frame",
        )

    def _member_chores(self) -> list[dict]:
        raw = (self.coordinator.data or {}).get("chores")
        if raw is None:
            return []
        entries = raw if isinstance(raw, list) else raw.get("data", []) or []
        out = []
        for c in entries:
            rel = (c.get("relationships", {}) or {}).get("category", {}) or {}
            data = rel.get("data") or {}
            assignee = str(data.get("id") or c.get("attributes", {}).get("category_id") or "")
            if assignee == self._category_id:
                out.append(c)
        return out

    @property
    def todo_items(self) -> list[TodoItem] | None:
        result: list[TodoItem] = []
        for c in self._member_chores():
            attrs = c.get("attributes", {}) if isinstance(c, dict) else {}
            summary = attrs.get("summary") or attrs.get("name") or "Chore"
            status = (
                TodoItemStatus.COMPLETED
                if _chore_is_complete(attrs)
                else TodoItemStatus.NEEDS_ACTION
            )
            uid = str(c.get("id"))
            due_raw = (
                attrs.get("start")
                or attrs.get("date")
                or attrs.get("due_date")
                or ""
            )
            due: date | datetime | None = None
            if isinstance(due_raw, str) and len(due_raw) >= 10:
                try:
                    due = date.fromisoformat(due_raw[:10])
                except ValueError:
                    due = None
            result.append(
                TodoItem(
                    summary=summary,
                    uid=uid,
                    status=status,
                    due=due,
                    description=attrs.get("description") or None,
                )
            )
        return result

    async def async_create_todo_item(self, item: TodoItem) -> None:
        # Skylight's create route takes no status, so a new chore always starts
        # pending — completing it is a second call the user makes from the list.
        await self._api.create_chores(
            self._frame_id,
            summary=item.summary or "Chore",
            start=_chore_start(item.due),
            category_ids=[self._category_id],
            description=item.description,
        )
        await self.coordinator.async_request_refresh()

    def _chore_attributes(self, uid: str) -> dict:
        for c in self._member_chores():
            if str(c.get("id")) == str(uid):
                return c.get("attributes", {}) or {}
        return {}

    async def async_update_todo_item(self, item: TodoItem) -> None:
        if not item.uid:
            return

        chore_id, occurrence = _split_chore_uid(item.uid)
        attrs = self._chore_attributes(item.uid)

        attributes: dict = {}
        if item.summary is not None:
            attributes["summary"] = item.summary
        if item.due is not None:
            attributes["start"] = _chore_start(item.due)
        if item.description is not None:
            attributes["description"] = item.description
        if attributes:
            await self._api.update_chore(self._frame_id, chore_id, attributes)

        # Completion lives on its own sub-resource, so only touch it when the
        # tick actually changed: HA re-sends the whole item on any edit, and
        # clearing an already-incomplete chore isn't necessarily a no-op.
        if item.status is not None:
            want_complete = item.status == TodoItemStatus.COMPLETED
            if want_complete != _chore_is_complete(attrs):
                # Which occurrence is being ticked: the composite UID date, or
                # None for bare-UID chores. The API requires instance_date to be
                # absent for bare-UID chores even when start is set — the
                # composite UID format is the sole signal that instance_date is
                # needed.
                instance_date = occurrence
                if want_complete:
                    # The frame files completions by calendar day, so use HA's
                    # local date rather than UTC.
                    await self._api.complete_chore(
                        self._frame_id,
                        chore_id,
                        dt_util.now().date().isoformat(),
                        instance_date,
                    )
                else:
                    await self._api.uncomplete_chore(
                        self._frame_id, chore_id, instance_date
                    )

        await self.coordinator.async_request_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        for uid in uids:
            await self._api.delete_chore(self._frame_id, _split_chore_uid(uid)[0])
        await self.coordinator.async_request_refresh()
