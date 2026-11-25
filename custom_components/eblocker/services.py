"""Integration-wide actions of the eBlocker integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import EBlockerClient
from .const import (
    ATTR_CATEGORY,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DESCRIPTION,
    ATTR_DOMAINS,
    ATTR_FILTER_TYPE,
    ATTR_FORMAT,
    ATTR_NAME,
    ATTR_UPDATE_INTERVAL,
    ATTR_URL,
    DOMAIN,
    SERVICE_ADD_FILTER_LIST,
)
from .coordinator import EBlockerConfigEntry
from .entity import profile_identifier, run_action
from .filter_lists import (
    CATEGORIES,
    FILTER_TYPES,
    FORMATS,
    UPDATE_INTERVALS,
    build_blocker,
)
from .parental_control import (
    CONTINGENT_DAYS,
    PROFILE_SETTINGS,
    USER_ROLES,
    WEEKDAYS,
    age_on,
    build_contingent,
    find_parental_lists,
    parental_list_name,
    preset_name_key,
    selectable_parental_lists,
    user_profile_owner,
)

ATTR_MINUTES = "minutes"
ATTR_DAYS = "days"
ATTR_TIMES = "times"
ATTR_ACTIVATE = "activate"
ATTR_FILTER_LISTS = "filter_lists"
ATTR_ROLE = "role"
ATTR_BIRTHDAY = "birthday"

SERVICE_ADD_BONUS_TIME = "add_bonus_time"
SERVICE_RESET_BONUS_TIME = "reset_bonus_time"
SERVICE_SET_USAGE_LIMIT = "set_usage_limit"
SERVICE_SET_ONLINE_TIMES = "set_online_times"
SERVICE_SET_BLOCKED_LISTS = "set_blocked_lists"
SERVICE_CREATE_USER = "create_user"
SERVICE_DELETE_USER = "delete_user"

PROFILE_TARGET = {
    vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
}

ADD_FILTER_LIST_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_NAME): vol.All(cv.string, vol.Length(min=1)),
        vol.Optional(ATTR_DESCRIPTION): cv.string,
        vol.Required(ATTR_CATEGORY): vol.In(list(CATEGORIES)),
        vol.Optional(ATTR_FORMAT, default="domains"): vol.In(list(FORMATS)),
        vol.Optional(ATTR_URL): vol.Url(),
        vol.Optional(ATTR_DOMAINS): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_FILTER_TYPE, default="blacklist"): vol.In(FILTER_TYPES),
        vol.Optional(ATTR_UPDATE_INTERVAL): vol.In(list(UPDATE_INTERVALS)),
    }
)

ADD_BONUS_TIME_SCHEMA = vol.Schema(
    {
        **PROFILE_TARGET,
        vol.Required(ATTR_MINUTES): vol.All(
            vol.Coerce(int), vol.Range(min=-720, max=720), vol.NotIn([0])
        ),
    }
)

RESET_BONUS_TIME_SCHEMA = vol.Schema(PROFILE_TARGET)

SET_USAGE_LIMIT_SCHEMA = vol.Schema(
    {
        **PROFILE_TARGET,
        vol.Required(ATTR_MINUTES): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=1440)
        ),
        vol.Optional(ATTR_DAYS): vol.All(
            cv.ensure_list, [vol.In([d.lower() for d in WEEKDAYS])]
        ),
        vol.Optional(ATTR_ACTIVATE, default=True): cv.boolean,
    }
)

SET_ONLINE_TIMES_SCHEMA = vol.Schema(
    {
        **PROFILE_TARGET,
        vol.Required(ATTR_TIMES): vol.All(
            cv.ensure_list,
            [
                vol.Schema(
                    {
                        vol.Required("day"): vol.In(list(CONTINGENT_DAYS)),
                        vol.Required("start"): cv.time,
                        vol.Required("end"): cv.time,
                    }
                )
            ],
        ),
        vol.Optional(ATTR_ACTIVATE, default=True): cv.boolean,
    }
)

SET_BLOCKED_LISTS_SCHEMA = vol.Schema(
    {
        **PROFILE_TARGET,
        vol.Required(ATTR_FILTER_LISTS): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_ACTIVATE, default=True): cv.boolean,
    }
)

CREATE_USER_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_NAME): vol.All(cv.string, vol.Length(min=1, max=50)),
        vol.Optional(ATTR_ROLE, default="child"): vol.In(list(USER_ROLES)),
        vol.Optional(ATTR_BIRTHDAY): cv.date,
    }
)

DELETE_USER_SCHEMA = vol.Schema(PROFILE_TARGET)


def _error(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


def _loaded_entries(hass: HomeAssistant) -> list[EBlockerConfigEntry]:
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


def _get_entry(hass: HomeAssistant, entry_id: str | None) -> EBlockerConfigEntry:
    entries = [
        entry
        for entry in _loaded_entries(hass)
        if entry_id is None or entry.entry_id == entry_id
    ]
    if len(entries) != 1:
        raise _error("config_entry_required" if entries else "no_config_entry")
    return entries[0]


def _target_profiles(call: ServiceCall) -> list[tuple[EBlockerConfigEntry, int]]:
    """Resolve the targeted HA devices to (config entry, profile id)."""
    by_identifier: dict[tuple[str, str], tuple[EBlockerConfigEntry, int]] = {}
    for entry in _loaded_entries(call.hass):
        coordinator = entry.runtime_data
        for profile_id in coordinator.profile_ids:
            by_identifier[profile_identifier(coordinator, profile_id)] = (
                entry,
                profile_id,
            )
    registry = dr.async_get(call.hass)
    targets: list[tuple[EBlockerConfigEntry, int]] = []
    for device_id in call.data[ATTR_DEVICE_ID]:
        device = registry.async_get(device_id)
        match = next(
            (
                by_identifier[i]
                for i in (device.identifiers if device else ())
                if i in by_identifier
            ),
            None,
        )
        if match is None:
            raise _error("not_a_profile_device")
        targets.append(match)
    return targets


async def _for_each_profile(
    call: ServiceCall,
    action: Callable[[EBlockerConfigEntry, int], Awaitable[Any]],
) -> None:
    for entry, profile_id in _target_profiles(call):
        await run_action(entry.runtime_data, action(entry, profile_id))


# ----------------------------------------------------------------------
# Filter lists
# ----------------------------------------------------------------------


async def _async_add_filter_list(call: ServiceCall) -> ServiceResponse:
    data = call.data
    url = data.get(ATTR_URL)
    domains = [d for d in data.get(ATTR_DOMAINS, []) if d.strip()]
    if bool(url) == bool(domains):
        raise _error("url_or_domains")
    entry = _get_entry(call.hass, data.get(ATTR_CONFIG_ENTRY_ID))
    coordinator = entry.runtime_data
    payload = build_blocker(
        name=data[ATTR_NAME],
        category=data[ATTR_CATEGORY],
        list_format=data[ATTR_FORMAT],
        url=url,
        domains=domains,
        filter_type=data[ATTR_FILTER_TYPE],
        update_interval=data.get(ATTR_UPDATE_INTERVAL),
        description=data.get(ATTR_DESCRIPTION),
        language=call.hass.config.language,
    )
    created = await run_action(coordinator, coordinator.client.create_blocker(payload))
    created = created if isinstance(created, dict) else {}
    return {
        "filter_list_id": created.get("id"),
        "update_status": str(created.get("updateStatus", "")).lower() or None,
    }


# ----------------------------------------------------------------------
# Parental control
# ----------------------------------------------------------------------


async def _async_add_bonus_time(call: ServiceCall) -> None:
    minutes = call.data[ATTR_MINUTES]
    await _for_each_profile(
        call, lambda entry, pid: entry.runtime_data.client.add_bonus_time(pid, minutes)
    )


async def _async_reset_bonus_time(call: ServiceCall) -> None:
    await _for_each_profile(
        call, lambda entry, pid: entry.runtime_data.client.reset_bonus_time(pid)
    )


async def _async_set_usage_limit(call: ServiceCall) -> None:
    minutes = call.data[ATTR_MINUTES]
    days = [d.upper() for d in call.data.get(ATTR_DAYS) or []] or list(WEEKDAYS)
    activate = call.data[ATTR_ACTIVATE]

    async def _apply(entry: EBlockerConfigEntry, profile_id: int) -> None:
        client = entry.runtime_data.client
        profile = await client.get_profile(profile_id)
        per_day = {day: 60 for day in WEEKDAYS}
        per_day.update(profile.get("maxUsageTimeByDay") or {})
        per_day.update(dict.fromkeys(days, minutes))
        profile["maxUsageTimeByDay"] = per_day
        if activate:
            profile["controlmodeMaxUsage"] = True
        await client.update_profile(profile)

    await _for_each_profile(call, _apply)


async def _async_set_online_times(call: ServiceCall) -> None:
    contingents = []
    for item in call.data[ATTR_TIMES]:
        if item["end"] <= item["start"] and item["end"].hour + item["end"].minute:
            raise _error("time_window_invalid")
        contingents.append(build_contingent(item["day"], item["start"], item["end"]))
    activate = call.data[ATTR_ACTIVATE]

    async def _apply(entry: EBlockerConfigEntry, profile_id: int) -> None:
        attributes: dict[str, Any] = {"internetAccessContingents": contingents}
        if activate:
            attributes["controlmodeTime"] = bool(contingents)
        await entry.runtime_data.client.set_profile_attributes(profile_id, **attributes)

    await _for_each_profile(call, _apply)


async def _async_set_blocked_lists(call: ServiceCall) -> None:
    wanted = call.data[ATTR_FILTER_LISTS]
    activate = call.data[ATTR_ACTIVATE]
    targets = _target_profiles(call)
    for entry, profile_id in targets:
        coordinator = entry.runtime_data
        lists = {
            list_id: filter_list
            for list_id, filter_list in selectable_parental_lists(
                coordinator.data.parental_lists
            ).items()
            if filter_list.get("filterType") == "blacklist"
        }
        ids, missing = find_parental_lists(lists, wanted)
        if missing:
            language = call.hass.config.language
            raise _error(
                "filter_lists_unknown",
                lists=", ".join(missing),
                available=", ".join(
                    sorted(parental_list_name(f, language) for f in lists.values())
                ),
            )

        async def _apply(client: EBlockerClient, pid: int, ids: list[int]) -> None:
            profile = await client.get_profile(pid)
            profile["inaccessibleSitesPackages"] = ids
            if activate and ids:
                profile["controlmodeUrls"] = True
            elif not ids and not profile.get("accessibleSitesPackages"):
                # Same as the eBlocker UI: no list selected -> web filter off
                profile["controlmodeUrls"] = False
            await client.update_profile(profile)

        await run_action(coordinator, _apply(coordinator.client, profile_id, ids))


async def _async_create_user(call: ServiceCall) -> ServiceResponse:
    """Create a user with own profile, the same way the eBlocker UI does it."""
    data = call.data
    entry = _get_entry(call.hass, data.get(ATTR_CONFIG_ENTRY_ID))
    coordinator = entry.runtime_data
    client = coordinator.client
    role = USER_ROLES[data[ATTR_ROLE]]
    birthday: date | None = data.get(ATTR_BIRTHDAY)

    names = {
        (u.get("name") or "").casefold()
        for u in (coordinator.data.users or {}).values()
    }
    if data[ATTR_NAME].casefold() in names:
        raise _error("user_exists", user=data[ATTR_NAME])

    # Settings template: children get the preset for their age, others none
    profile: dict[str, Any] = {
        "controlmodeUrls": False,
        "controlmodeTime": False,
        "controlmodeMaxUsage": False,
        "internetAccessRestrictionMode": 1,
        "accessibleSitesPackages": [],
        "inaccessibleSitesPackages": [],
        "internetAccessContingents": [],
        "maxUsageTimeByDay": {day: 60 for day in WEEKDAYS},
    }
    if role == "CHILD":
        age = age_on(birthday, dt_util.now().date()) if birthday else 0
        preset = next(
            (
                p
                for p in (coordinator.data.profiles or {}).values()
                if p.get("nameKey") == preset_name_key(age)
            ),
            None,
        )
        if preset:
            profile.update({k: preset[k] for k in PROFILE_SETTINGS if k in preset})

    async def _create() -> dict[str, Any]:
        user = await client.create_user(
            {
                "name": data[ATTR_NAME],
                "nameKey": "PARENTAL_CONTROL_USER_NAME",
                "userRole": role,
                "associatedProfileId": 1,
                "birthday": (
                    [birthday.year, birthday.month, birthday.day] if birthday else None
                ),
                "containsPin": False,
            }
        )
        new_profile = await client.create_profile(
            {
                **profile,
                "name": f"PROFILE_FOR_USER_{user['id']}",
                "description": "",
                "builtin": False,
                "standard": False,
                "hidden": False,
                "forSingleUser": False,
                "parentalControlSettingValidated": False,
            }
        )
        user["associatedProfileId"] = new_profile["id"]
        await client.update_user(user)
        return {"user_id": user["id"], "profile_id": new_profile["id"]}

    result = await run_action(coordinator, _create())
    # New profile -> new entities
    call.hass.config_entries.async_schedule_reload(entry.entry_id)
    return result


async def _delete_user_and_profile(
    client: EBlockerClient, user_id: int, profile_id: int
) -> None:
    # Give the user's devices back to their own system users first
    for device in (await client.get_devices()) or []:
        if user_id in (device.get("assignedUser"), device.get("operatingUser")):
            system_user = device.get("defaultSystemUser")
            await client.set_device_attributes(
                device["id"], assignedUser=system_user, operatingUser=system_user
            )
    await client.delete_user(user_id)
    await client.delete_profile(profile_id)


async def _async_delete_user(call: ServiceCall) -> None:
    for entry, profile_id in _target_profiles(call):
        coordinator = entry.runtime_data
        data = coordinator.data
        profile = (data.profiles or {}).get(profile_id, {})
        owner = user_profile_owner(profile, data.users or {})
        if owner is None:
            raise _error("not_a_user_profile")
        await run_action(
            coordinator,
            _delete_user_and_profile(coordinator.client, owner["id"], profile_id),
        )
        call.hass.config_entries.async_schedule_reload(entry.entry_id)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration-wide actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_FILTER_LIST,
        _async_add_filter_list,
        schema=ADD_FILTER_LIST_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    for name, handler, schema in (
        (SERVICE_ADD_BONUS_TIME, _async_add_bonus_time, ADD_BONUS_TIME_SCHEMA),
        (SERVICE_RESET_BONUS_TIME, _async_reset_bonus_time, RESET_BONUS_TIME_SCHEMA),
        (SERVICE_SET_USAGE_LIMIT, _async_set_usage_limit, SET_USAGE_LIMIT_SCHEMA),
        (SERVICE_SET_ONLINE_TIMES, _async_set_online_times, SET_ONLINE_TIMES_SCHEMA),
        (SERVICE_SET_BLOCKED_LISTS, _async_set_blocked_lists, SET_BLOCKED_LISTS_SCHEMA),
        (SERVICE_DELETE_USER, _async_delete_user, DELETE_USER_SCHEMA),
    ):
        hass.services.async_register(DOMAIN, name, handler, schema=schema)
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_USER,
        _async_create_user,
        schema=CREATE_USER_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
