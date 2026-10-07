"""The Skylight Calendar integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import date
import logging
import mimetypes
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, Unauthorized, UnknownUser
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api import SkylightAPI, SkylightAPIError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CALENDAR_INTERVAL,
    CONF_DEVICE_FINGERPRINT,
    CONF_FRAME_ID,
    CONF_FRAME_INTERVAL,
    CONF_FRAME_NAME,
    CONF_LISTS_INTERVAL,
    CONF_PHOTOS_INTERVAL,
    CONF_REFRESH_TOKEN,
    CONF_SENSOR_INTERVAL,
    DOMAIN,
    PLATFORM_BINARY_SENSOR,
    PLATFORM_CALENDAR,
    PLATFORM_IMAGE,
    PLATFORM_NUMBER,
    PLATFORM_SENSOR,
    PLATFORM_SWITCH,
    PLATFORM_TIME,
    PLATFORM_TODO,
    SCAN_INTERVAL_OPTIONS,
)
from .buddy import SkylightBuddyCoordinator
from .coordinator import (
    SkylightCalendarCoordinator,
    SkylightFrameCoordinator,
    SkylightListsCoordinator,
    SkylightPhotosCoordinator,
    SkylightSensorCoordinator,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    PLATFORM_CALENDAR,
    PLATFORM_TODO,
    PLATFORM_SENSOR,
    PLATFORM_IMAGE,
    PLATFORM_SWITCH,
    PLATFORM_NUMBER,
    PLATFORM_TIME,
    PLATFORM_BINARY_SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Skylight frame from a config entry."""
    session = async_get_clientsession(hass)

    access_token = entry.data.get(CONF_ACCESS_TOKEN)
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
    device_fp = entry.data.get(CONF_DEVICE_FINGERPRINT, "")
    frame_id = entry.data.get(CONF_FRAME_ID)
    frame_name = entry.data.get(CONF_FRAME_NAME) or f"Skylight Frame {frame_id}"

    if not access_token or not refresh_token or not frame_id:
        _LOGGER.error(
            "Skylight config entry incomplete (missing tokens or frame_id) — "
            "please remove and re-add the integration"
        )
        return False

    async def _persist_tokens(new_access: str, new_refresh: str, new_fp: str | None) -> None:
        new_data = {
            **entry.data,
            CONF_ACCESS_TOKEN: new_access,
            CONF_REFRESH_TOKEN: new_refresh,
        }
        if new_fp:
            new_data[CONF_DEVICE_FINGERPRINT] = new_fp
        hass.config_entries.async_update_entry(entry, data=new_data)

    api = SkylightAPI(
        session=session,
        access_token=access_token,
        refresh_token=refresh_token,
        device_fingerprint=device_fp,
        token_update_cb=_persist_tokens,
    )

    def _interval(key: str) -> int:
        return int(entry.options.get(key, SCAN_INTERVAL_OPTIONS[key]))

    calendar_coord = SkylightCalendarCoordinator(
        hass, api, frame_id, _interval(CONF_CALENDAR_INTERVAL)
    )
    lists_coord = SkylightListsCoordinator(
        hass, api, frame_id, _interval(CONF_LISTS_INTERVAL)
    )
    sensor_coord = SkylightSensorCoordinator(
        hass, api, frame_id, _interval(CONF_SENSOR_INTERVAL)
    )
    frame_coord = SkylightFrameCoordinator(
        hass, api, frame_id, _interval(CONF_FRAME_INTERVAL)
    )
    photos_coord = SkylightPhotosCoordinator(
        hass, api, frame_id, _interval(CONF_PHOTOS_INTERVAL)
    )
    buddy_coord = SkylightBuddyCoordinator(
        hass, api, frame_id, _interval(CONF_FRAME_INTERVAL)
    )

    await calendar_coord.async_config_entry_first_refresh()
    await lists_coord.async_config_entry_first_refresh()
    await sensor_coord.async_config_entry_first_refresh()
    await frame_coord.async_config_entry_first_refresh()
    # Photos are optional — don't fail entry setup if the feed 500s.
    try:
        await photos_coord.async_config_entry_first_refresh()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Initial photos refresh failed — continuing without photos")
    # Buddies are optional too; their entities appear on a later refresh.
    try:
        await buddy_coord.async_config_entry_first_refresh()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Initial Buddy refresh failed — continuing without Buddies")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "frame_id": frame_id,
        "frame_name": frame_name,
        "calendar_coordinator": calendar_coord,
        "lists_coordinator": lists_coord,
        "sensor_coordinator": sensor_coord,
        "frame_coordinator": frame_coord,
        "photos_coordinator": photos_coord,
        "buddy_coordinator": buddy_coord,
    }

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, frame_id)},
        manufacturer="Skylight",
        name=frame_name,
        model="Calendar Frame",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    # Poll intervals are baked into the coordinators at construction, so a change
    # only takes effect on reload.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload after the options flow saves."""
    await hass.config_entries.async_reload(entry.entry_id)


SERVICE_UPLOAD_MEDIA = "upload_media"
SERVICE_CREATE_CHORE = "create_chore"
SERVICE_CREATE_TASK = "create_task"
SERVICE_CREATE_LIST = "create_list"
SERVICE_DELETE_LIST = "delete_list"
SERVICE_DELETE_CHORE = "delete_chore"
SERVICE_CREATE_REWARD = "create_reward"
SERVICE_REDEEM_REWARD = "redeem_reward"
SERVICE_CREATE_RECIPE = "create_recipe"
SERVICE_PLAN_MEAL = "plan_meal"
SERVICE_ADD_RECIPE_TO_GROCERY_LIST = "add_recipe_to_grocery_list"

_ALLOWED_EXTS = {
    # images
    "jpg", "jpeg", "png", "gif", "heic", "heif", "webp",
    # videos (Skylight app accepts short clips)
    "mp4", "mov", "m4v",
}

# Every service accepts an optional frame_id; it's only required when more than
# one frame is configured.
_FRAME_ID_FIELD = {vol.Optional("frame_id"): vol.All(str, vol.Length(min=1))}
_SUMMARY = vol.All(str, vol.Length(min=1))
_POINTS = vol.All(vol.Coerce(int), vol.Range(min=0))


UPLOAD_MEDIA_SCHEMA = vol.Schema(
    {
        vol.Required("file_path"): vol.All(str, vol.Length(min=1)),
        vol.Optional("caption", default=""): str,
        **_FRAME_ID_FIELD,
    }
)

def _has_assignee(data: dict) -> dict:
    """A normal chore needs an assignee; an up-for-grabs chore is
    intentionally unassigned, so the requirement is skipped in that case."""
    if data.get("up_for_grabs"):
        return data
    if not data.get("assignees") and not data.get("category_ids"):
        raise vol.Invalid(
            "A chore needs an assignee: pass assignees (family member names) "
            "or category_ids (raw IDs), unless up_for_grabs is true."
        )
    return data


CREATE_CHORE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required("summary"): _SUMMARY,
            # Either form works, and both accept one value or several — the
            # route creates one chore per assignee.
            vol.Optional("assignees"): vol.All(cv.ensure_list, [cv.string]),
            vol.Optional("category_ids"): vol.All(cv.ensure_list, [cv.string]),
            vol.Optional("start"): cv.date,
            vol.Optional("start_time"): cv.string,
            vol.Optional("description"): cv.string,
            vol.Optional("routine", default=False): cv.boolean,
            vol.Optional("up_for_grabs", default=False): cv.boolean,
            vol.Optional("recurrence_set"): cv.string,
            vol.Optional("recurring_until"): cv.date,
            **_FRAME_ID_FIELD,
        }
    ),
    _has_assignee,
)

CREATE_TASK_SCHEMA = vol.Schema(
    {
        vol.Required("summary"): _SUMMARY,
        vol.Optional("emoji"): cv.string,
        vol.Optional("reward_points"): _POINTS,
        vol.Optional("routine", default=False): cv.boolean,
        **_FRAME_ID_FIELD,
    }
)

CREATE_LIST_SCHEMA = vol.Schema(
    {
        vol.Required("label"): _SUMMARY,
        vol.Optional("kind", default="to_do"): vol.In(["to_do", "shopping"]),
        vol.Optional("color"): cv.string,
        **_FRAME_ID_FIELD,
    }
)

DELETE_LIST_SCHEMA = vol.Schema(
    {vol.Required("list_id"): cv.string, **_FRAME_ID_FIELD}
)

DELETE_CHORE_SCHEMA = vol.Schema(
    {vol.Required("chore_id"): cv.string, **_FRAME_ID_FIELD}
)

CREATE_REWARD_SCHEMA = vol.Schema(
    {
        vol.Required("name"): _SUMMARY,
        vol.Required("point_value"): _POINTS,
        vol.Optional("description"): cv.string,
        vol.Optional("emoji"): cv.string,
        vol.Optional("category_ids"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("respawn_on_redemption", default=False): cv.boolean,
        **_FRAME_ID_FIELD,
    }
)

REDEEM_REWARD_SCHEMA = vol.Schema(
    {
        vol.Required("reward_id"): cv.string,
        vol.Optional("category_id"): cv.string,
        **_FRAME_ID_FIELD,
    }
)

CREATE_RECIPE_SCHEMA = vol.Schema(
    {
        vol.Required("summary"): _SUMMARY,
        vol.Optional("description"): cv.string,
        vol.Optional("meal_category_id"): cv.string,
        **_FRAME_ID_FIELD,
    }
)

PLAN_MEAL_SCHEMA = vol.Schema(
    {
        vol.Required("date"): cv.date,
        vol.Required("meal_category_id"): cv.string,
        vol.Optional("recipe_id"): cv.string,
        **_FRAME_ID_FIELD,
    }
)

ADD_RECIPE_TO_GROCERY_LIST_SCHEMA = vol.Schema(
    {vol.Required("recipe_id"): cv.string, **_FRAME_ID_FIELD}
)


def _resolve_entry(hass: HomeAssistant, target_frame: str | None) -> dict:
    """Pick the loaded config entry a service call targets."""
    entries = hass.data.get(DOMAIN, {})
    if not entries:
        raise HomeAssistantError("No Skylight config entries loaded.")

    if target_frame:
        entry_data = next(
            (v for v in entries.values() if str(v.get("frame_id")) == str(target_frame)),
            None,
        )
        if entry_data is None:
            raise HomeAssistantError(
                f"No Skylight frame with id={target_frame} configured."
            )
        return entry_data

    if len(entries) > 1:
        raise HomeAssistantError(
            "Multiple Skylight frames configured — pass frame_id to disambiguate."
        )
    return next(iter(entries.values()))


# ── Service actions ─────────────────────────────────────────────────────
# Each takes the resolved client + frame and the validated call data. The
# dispatcher in _make_write_handler owns entry resolution, error translation and
# the follow-up coordinator refresh, so these stay one call each.

ServiceAction = Callable[[SkylightAPI, str, Mapping[str, Any]], Awaitable[None]]


async def _resolve_assignees(
    api: SkylightAPI, frame_id: str, names: list[str]
) -> list[str]:
    """Map family member names onto category IDs.

    Only categories with ``linked_to_profile`` are candidates. A frame's
    category list also holds calendar buckets ("US Holidays", "Garbage Pickup")
    and near-miss names — "Robbie" and "Robbie's Calendar" both exist on a real
    frame — and assigning a chore to one of those is silently wrong rather than
    an error, so they're excluded outright. Exact matches beat substring ones
    for the same reason.
    """
    people: list[tuple[str, str]] = [
        (str(c.get("id")), (c.get("attributes") or {}).get("label") or "")
        for c in (await api.get_categories(frame_id)).get("data", []) or []
        if (c.get("attributes") or {}).get("linked_to_profile")
    ]

    resolved: list[str] = []
    unknown: list[str] = []
    for name in names:
        wanted = name.strip().lower()
        exact = next((cid for cid, label in people if label.lower() == wanted), None)
        partial = next((cid for cid, label in people if wanted in label.lower()), None)
        if match := (exact or partial):
            resolved.append(match)
        else:
            unknown.append(name)

    if unknown:
        known = ", ".join(sorted(label for _, label in people if label)) or "none"
        raise HomeAssistantError(
            f"Unknown Skylight family member(s): {', '.join(unknown)}. "
            f"Known members on this frame: {known}."
        )
    return resolved


async def _create_chore(api: SkylightAPI, frame_id: str, data: Mapping[str, Any]) -> None:
    start: date | None = data.get("start")
    until: date | None = data.get("recurring_until")
    category_ids = list(data.get("category_ids") or [])
    if names := data.get("assignees"):
        category_ids += await _resolve_assignees(api, frame_id, names)
    await api.create_chores(
        frame_id,
        summary=data["summary"],
        start=(start or dt_util.now().date()).isoformat(),
        category_ids=category_ids,
        description=data.get("description"),
        start_time=data.get("start_time"),
        routine=data["routine"],
        up_for_grabs=data["up_for_grabs"],
        recurrence_set=data.get("recurrence_set"),
        recurring_until=until.isoformat() if until else None,
    )


async def _create_task(api: SkylightAPI, frame_id: str, data: Mapping[str, Any]) -> None:
    await api.create_task_box_item(
        frame_id,
        summary=data["summary"],
        emoji_icon=data.get("emoji"),
        routine=data["routine"],
        reward_points=data.get("reward_points"),
    )


async def _create_list(api: SkylightAPI, frame_id: str, data: Mapping[str, Any]) -> None:
    await api.create_list(
        frame_id, label=data["label"], kind=data["kind"], color=data.get("color")
    )


async def _delete_list(api: SkylightAPI, frame_id: str, data: Mapping[str, Any]) -> None:
    await api.delete_list(frame_id, data["list_id"])


async def _delete_chore(api: SkylightAPI, frame_id: str, data: Mapping[str, Any]) -> None:
    """Delete a chore outright — the only supported way to remove an
    up-for-grabs chore, since it has no assignee and so never appears in a
    per-member todo list (todo.remove_item has nothing to target)."""
    await api.delete_chore(frame_id, data["chore_id"])


async def _create_reward(api: SkylightAPI, frame_id: str, data: Mapping[str, Any]) -> None:
    await api.create_reward(
        frame_id,
        name=data["name"],
        point_value=data["point_value"],
        description=data.get("description"),
        emoji_icon=data.get("emoji"),
        category_ids=data.get("category_ids"),
        respawn_on_redemption=data["respawn_on_redemption"],
    )


async def _redeem_reward(api: SkylightAPI, frame_id: str, data: Mapping[str, Any]) -> None:
    await api.redeem_reward(frame_id, data["reward_id"], data.get("category_id"))


async def _create_recipe(api: SkylightAPI, frame_id: str, data: Mapping[str, Any]) -> None:
    await api.create_recipe(
        frame_id,
        summary=data["summary"],
        description=data.get("description"),
        meal_category_id=data.get("meal_category_id"),
    )


async def _plan_meal(api: SkylightAPI, frame_id: str, data: Mapping[str, Any]) -> None:
    await api.create_meal_sitting(
        frame_id,
        date=data["date"].isoformat(),
        meal_category_id=data["meal_category_id"],
        recipe_id=data.get("recipe_id"),
    )


async def _add_recipe_to_grocery(
    api: SkylightAPI, frame_id: str, data: Mapping[str, Any]
) -> None:
    await api.add_recipe_to_grocery_list(frame_id, data["recipe_id"])


# (service name, schema, coordinator to refresh afterwards, action)
_WRITE_SERVICES: tuple[tuple[str, vol.Schema, str, ServiceAction], ...] = (
    (SERVICE_CREATE_CHORE, CREATE_CHORE_SCHEMA, "sensor_coordinator", _create_chore),
    (SERVICE_CREATE_TASK, CREATE_TASK_SCHEMA, "sensor_coordinator", _create_task),
    (SERVICE_CREATE_LIST, CREATE_LIST_SCHEMA, "lists_coordinator", _create_list),
    (SERVICE_DELETE_LIST, DELETE_LIST_SCHEMA, "lists_coordinator", _delete_list),
    (SERVICE_DELETE_CHORE, DELETE_CHORE_SCHEMA, "sensor_coordinator", _delete_chore),
    (SERVICE_CREATE_REWARD, CREATE_REWARD_SCHEMA, "sensor_coordinator", _create_reward),
    (SERVICE_REDEEM_REWARD, REDEEM_REWARD_SCHEMA, "sensor_coordinator", _redeem_reward),
    (SERVICE_CREATE_RECIPE, CREATE_RECIPE_SCHEMA, "sensor_coordinator", _create_recipe),
    (SERVICE_PLAN_MEAL, PLAN_MEAL_SCHEMA, "sensor_coordinator", _plan_meal),
    (
        SERVICE_ADD_RECIPE_TO_GROCERY_LIST,
        ADD_RECIPE_TO_GROCERY_LIST_SCHEMA,
        "lists_coordinator",
        _add_recipe_to_grocery,
    ),
)

_ALL_SERVICES = (SERVICE_UPLOAD_MEDIA, *(name for name, *_ in _WRITE_SERVICES))


async def _async_authorize_frame_write(
    hass: HomeAssistant, call: ServiceCall, frame_id: str
) -> None:
    """Require control of the selected frame's aggregate calendar.

    HA's generic service dispatch preserves user context but does not enforce
    entity permissions for domain services. Context-free trusted automations
    retain their existing behavior. Media upload additionally needs an admin.
    """
    if not call.context.user_id:
        return
    user = await hass.auth.async_get_user(call.context.user_id)
    if user is None:
        raise UnknownUser(context=call.context)
    if not user.is_active:
        raise Unauthorized(context=call.context)
    if call.service == SERVICE_UPLOAD_MEDIA:
        if not user.is_admin:
            raise Unauthorized(context=call.context)
        return
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "calendar", DOMAIN, f"skylight_{frame_id}_calendar"
    )
    if entity_id is None or not user.permissions.check_entity(entity_id, POLICY_CONTROL):
        raise Unauthorized(context=call.context)


def _make_write_handler(
    hass: HomeAssistant, coordinator_key: str, action: ServiceAction
):
    """Wrap a service action with entry resolution, error mapping and a refresh."""

    async def _handler(call: ServiceCall) -> None:
        entry_data = _resolve_entry(hass, call.data.get("frame_id"))
        await _async_authorize_frame_write(hass, call, entry_data["frame_id"])
        try:
            await action(entry_data["api"], entry_data["frame_id"], call.data)
        except SkylightAPIError as err:
            raise HomeAssistantError(
                f"Skylight rejected {DOMAIN}.{call.service}: {err}"
            ) from err
        coordinator = entry_data.get(coordinator_key)
        if coordinator is not None:
            await coordinator.async_request_refresh()

    return _handler


def _async_register_services(hass: HomeAssistant) -> None:
    """Register domain-level services once. Idempotent."""
    if hass.services.has_service(DOMAIN, SERVICE_UPLOAD_MEDIA):
        return

    async def _handle_upload_media(call: ServiceCall) -> None:
        file_path = call.data["file_path"]
        caption = call.data.get("caption", "") or ""

        entry_data = _resolve_entry(hass, call.data.get("frame_id"))
        await _async_authorize_frame_write(hass, call, entry_data["frame_id"])
        api: SkylightAPI = entry_data["api"]
        frame_id: str = entry_data["frame_id"]

        # Validate + read the file. Path check is defensive — HA doesn't restrict
        # local paths but we surface an actionable error early.
        path = Path(file_path).expanduser()
        if not path.is_file():
            raise HomeAssistantError(f"File not found: {path}")

        # Enforce allowlist_external_dirs so users can't sneak arbitrary reads.
        if not hass.config.is_allowed_path(str(path)):
            raise HomeAssistantError(
                f"{path} is not under an allowlist_external_dirs entry. "
                "Add its parent to `homeassistant.allowlist_external_dirs` in "
                "configuration.yaml, or drop the file into /config."
            )

        ext = path.suffix.lstrip(".").lower() or "jpg"
        if ext not in _ALLOWED_EXTS:
            raise HomeAssistantError(
                f"Extension .{ext} not supported. Allowed: "
                + ", ".join(sorted(_ALLOWED_EXTS))
            )
        content_type = mimetypes.guess_type(str(path))[0] or f"image/{ext}"

        # Read off the event loop.
        file_data = await hass.async_add_executor_job(path.read_bytes)

        _LOGGER.info(
            "Skylight upload_media: uploading %s (%d bytes, %s) to frame %s",
            path.name, len(file_data), content_type, frame_id,
        )
        try:
            result = await api.upload_media(
                frame_ids=[frame_id],
                file_data=file_data,
                ext=ext,
                content_type=content_type,
                caption=caption,
            )
        except SkylightAPIError as err:
            raise HomeAssistantError(f"Skylight upload failed: {err}") from err

        msg_ids = (result or {}).get("data", {}).get("message_ids", [])
        _LOGGER.info(
            "Skylight upload_media: success, message_ids=%s (frame %s)",
            msg_ids, frame_id,
        )

        # Kick the photos coordinator to refresh quickly so image.<frame>_latest_photo
        # updates within seconds instead of waiting for the next scheduled poll.
        photos_coord: SkylightPhotosCoordinator | None = entry_data.get(
            "photos_coordinator"
        )
        if photos_coord is not None:
            await photos_coord.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_UPLOAD_MEDIA, _handle_upload_media, schema=UPLOAD_MEDIA_SCHEMA
    )

    for name, schema, coordinator_key, action in _WRITE_SERVICES:
        hass.services.async_register(
            DOMAIN,
            name,
            _make_write_handler(hass, coordinator_key, action),
            schema=schema,
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Remove the shared services when the last entry unloads.
        if not hass.data.get(DOMAIN):
            for name in _ALL_SERVICES:
                if hass.services.has_service(DOMAIN, name):
                    hass.services.async_remove(DOMAIN, name)
    return unload_ok
