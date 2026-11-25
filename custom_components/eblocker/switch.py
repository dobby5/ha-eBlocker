"""Switches for the eBlocker integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    entity_platform,
    entity_registry as er,
)
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import (
    ATTR_DESCRIPTION,
    ATTR_DURATION,
    ATTR_NAME,
    ATTR_UPDATE_INTERVAL,
    ATTR_URL,
    CONF_PAUSE_MINUTES,
    DEFAULT_PAUSE_MINUTES,
    DOMAIN,
    SERVICE_PAUSE,
    SERVICE_REMOVE_FILTER_LIST,
    SERVICE_UPDATE_FILTER_LIST,
)
from .coordinator import EBlockerConfigEntry, EBlockerCoordinator
from .entity import (
    EBlockerDeviceEntity,
    EBlockerEntity,
    EBlockerProfileEntity,
    profile_identifier,
    run_action,
)
from .filter_lists import UPDATE_INTERVALS, blocker_key, blocker_name, is_custom
from .parental_control import (
    PROFILE_LIST_ATTRIBUTE,
    WEEKDAYS,
    format_contingents,
    parental_list_name,
    selectable_parental_lists,
    toggle_profile_list,
)

PARALLEL_UPDATES = 1

MAX_PAUSE = timedelta(hours=24)


@dataclass(frozen=True, kw_only=True)
class EBlockerAttributeSwitchDescription(SwitchEntityDescription):
    """A switch that maps to one boolean attribute of an eBlocker device."""

    attribute: str


ATTRIBUTE_SWITCHES: tuple[EBlockerAttributeSwitchDescription, ...] = (
    EBlockerAttributeSwitchDescription(
        key="filter_ads",
        translation_key="filter_ads",
        entity_category=EntityCategory.CONFIG,
        attribute="filterAdsEnabled",
    ),
    EBlockerAttributeSwitchDescription(
        key="filter_trackers",
        translation_key="filter_trackers",
        entity_category=EntityCategory.CONFIG,
        attribute="filterTrackersEnabled",
    ),
    EBlockerAttributeSwitchDescription(
        key="filter_malware",
        translation_key="filter_malware",
        entity_category=EntityCategory.CONFIG,
        attribute="malwareFilterEnabled",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EBlockerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up eBlocker switches."""
    coordinator = entry.runtime_data
    device_ids = [
        d for d in coordinator.tracked_device_ids if d in coordinator.data.devices
    ]
    entities: list[SwitchEntity] = []
    for device_id in device_ids:
        entities.append(EBlockerProtectionSwitch(coordinator, device_id))
        entities.append(EBlockerPauseSwitch(coordinator, device_id))
        entities.extend(
            EBlockerAttributeSwitch(coordinator, device_id, description)
            for description in ATTRIBUTE_SWITCHES
        )
    entities.append(EBlockerAutoUpdateSwitch(coordinator))
    if coordinator.data.privacy is not None:
        entities.extend(
            EBlockerPrivacySwitch(coordinator, key)
            for key in ("webrtc_blocked", "referrer_removed", "dnt_header")
        )
    entities.extend(
        EBlockerProfileSwitch(coordinator, profile_id, description)
        for profile_id in coordinator.profile_ids
        for description in PROFILE_SWITCHES
    )
    async_add_entities(entities)

    _setup_filter_list_switches(hass, entry, coordinator, async_add_entities)
    _setup_profile_list_switches(hass, entry, coordinator, async_add_entities)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_PAUSE,
        {
            vol.Required(ATTR_DURATION): vol.All(
                cv.time_period, vol.Range(min=timedelta(0), max=MAX_PAUSE)
            )
        },
        "async_pause",
    )
    platform.async_register_entity_service(
        SERVICE_UPDATE_FILTER_LIST,
        {
            vol.Optional(ATTR_NAME): vol.All(cv.string, vol.Length(min=1)),
            vol.Optional(ATTR_DESCRIPTION): cv.string,
            vol.Optional(ATTR_URL): vol.Url(),
            vol.Optional(ATTR_UPDATE_INTERVAL): vol.In(list(UPDATE_INTERVALS)),
        },
        "async_update_filter_list",
    )
    platform.async_register_entity_service(
        SERVICE_REMOVE_FILTER_LIST, {}, "async_remove_filter_list"
    )


def _setup_filter_list_switches(
    hass: HomeAssistant,
    entry: EBlockerConfigEntry,
    coordinator: EBlockerCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a switch per filter list and keep them in sync with the eBlocker."""
    known: set[str] = set()

    @callback
    def _sync() -> None:
        blockers = coordinator.data.blockers if coordinator.data else None
        if blockers is None:
            return  # request failed: keep entities (they become unavailable)
        new = [key for key in blockers if key not in known]
        if new:
            known.update(new)
            async_add_entities(
                EBlockerFilterListSwitch(coordinator, key) for key in new
            )
        registry = er.async_get(hass)
        for key in [key for key in known if key not in blockers]:
            known.discard(key)
            unique_id = EBlockerFilterListSwitch.unique_id_for(coordinator, key)
            if entity_id := registry.async_get_entity_id("switch", DOMAIN, unique_id):
                registry.async_remove(entity_id)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class _FilterListActionsMixin:
    """Entity-service stubs, so the filter-list actions fail cleanly elsewhere."""

    async def async_update_filter_list(self, **kwargs: Any) -> None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_a_filter_list"
        )

    async def async_remove_filter_list(self) -> None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_a_filter_list"
        )

    async def async_pause(self, duration: timedelta) -> None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_a_pause_switch"
        )


class _EBlockerSwitchBase(_FilterListActionsMixin, EBlockerDeviceEntity, SwitchEntity):
    async def _run(self, coro: Any) -> Any:
        return await run_action(self.coordinator, coro)


class EBlockerProtectionSwitch(_EBlockerSwitchBase):
    """Turns eBlocker protection for a device on or off (device 'enabled')."""

    _attr_translation_key = "protection"

    def __init__(self, coordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "protection")

    @property
    def is_on(self) -> bool:
        return bool(self.device.get("enabled"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        # On a paused device the eBlocker treats enabled=true as "end the pause".
        await self._run(
            self.coordinator.client.set_device_attributes(self.device_id, enabled=True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        client = self.coordinator.client

        async def _disable() -> None:
            if self.device.get("paused"):
                # End a running pause first, otherwise the pause timer would
                # re-enable the device later.
                await client.set_pause(self.device_id, 0)
            await client.set_device_attributes(self.device_id, enabled=False)

        await self._run(_disable())


class EBlockerPauseSwitch(_EBlockerSwitchBase):
    """Pauses eBlocker protection for a device for a limited time."""

    _attr_translation_key = "pause"

    def __init__(self, coordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "pause")

    @property
    def is_on(self) -> bool:
        return bool(self.device.get("paused"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pause = self.pause
        return {
            "remaining_seconds": pause.get("pausing", 0) if pause else None,
            "pausing_allowed": pause.get("pausingAllowed") if pause else None,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        minutes = self.coordinator.config_entry.options.get(
            CONF_PAUSE_MINUTES, DEFAULT_PAUSE_MINUTES
        )
        await self.async_pause(timedelta(minutes=minutes))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_pause(timedelta(0))

    async def async_pause(self, duration: timedelta) -> None:
        """Pause the device for ``duration`` (0 ends a running pause)."""
        seconds = int(duration.total_seconds())
        if seconds > 0:
            if self.pause and self.pause.get("pausingAllowed") is False:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="pause_not_allowed",
                    translation_placeholders={"device": self.device.get("name", "")},
                )
            if not self.device.get("enabled") and not self.device.get("paused"):
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="pause_device_disabled",
                    translation_placeholders={"device": self.device.get("name", "")},
                )
        result = await self._run(
            self.coordinator.client.set_pause(self.device_id, seconds)
        )
        if seconds > 0 and (not result or not result.get("pausing")):
            # The eBlocker answers with pausing=0 when it refused the pause.
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="pause_not_allowed",
                translation_placeholders={"device": self.device.get("name", "")},
            )


class EBlockerAttributeSwitch(_EBlockerSwitchBase):
    """Switches a single filter option of a device."""

    entity_description: EBlockerAttributeSwitchDescription

    def __init__(self, coordinator, device_id: str, description) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return bool(self.device.get(self.entity_description.attribute))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, value: bool) -> None:
        await self._run(
            self.coordinator.client.set_device_attributes(
                self.device_id, **{self.entity_description.attribute: value}
            )
        )


class _EBlockerHubSwitch(_FilterListActionsMixin, EBlockerEntity, SwitchEntity):
    """Switch that belongs to the eBlocker itself."""


class EBlockerAutoUpdateSwitch(_EBlockerHubSwitch):
    """Automatic eBlockerOS updates on/off."""

    _attr_translation_key = "automatic_updates"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EBlockerCoordinator) -> None:
        super().__init__(coordinator, "automatic_updates")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.update_status is not None

    @property
    def is_on(self) -> bool:
        status = self.coordinator.data.update_status or {}
        return bool(status.get("automaticUpdatesActivated"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.coordinator.data.update_status or {}
        return {
            "allowed_by_license": status.get("automaticUpdatesAllowed"),
            "next_automatic_update": status.get("nextAutomaticUpdate"),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await run_action(
            self.coordinator, self.coordinator.client.set_automatic_updates(True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await run_action(
            self.coordinator, self.coordinator.client.set_automatic_updates(False)
        )


class EBlockerPrivacySwitch(_EBlockerHubSwitch):
    """Global anonymization setting (WebRTC, referrer, Do-Not-Track)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EBlockerCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self.key = key
        self._attr_translation_key = key
        self._attr_entity_registry_enabled_default = False

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.privacy is not None

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data.privacy or {}).get(self.key))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await run_action(
            self.coordinator,
            self.coordinator.client.set_privacy_setting(self.key, True),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await run_action(
            self.coordinator,
            self.coordinator.client.set_privacy_setting(self.key, False),
        )


class EBlockerFilterListSwitch(_EBlockerHubSwitch):
    """Enables or disables one filter list (ads, trackers, malware, ...)."""

    _attr_translation_key = "filter_list"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EBlockerCoordinator, key: str) -> None:
        super().__init__(coordinator, f"filter_list_{key}")
        self.key = key
        language = coordinator.hass.config.language
        blockers = coordinator.data.blockers or {}
        blocker = blockers[key]
        name = blocker_name(blocker, language)
        same_name = [b for b in blockers.values() if blocker_name(b, language) == name]
        if len(same_name) > 1:
            # e.g. "Malware" exists as domain list and as URL pattern list
            name = f"{name} ({str(blocker.get('type', '')).capitalize()})"
        self.list_name = name
        self._attr_translation_placeholders = {"name": name}
        if blocker.get("category") == "PARENTAL_CONTROL" and not is_custom(blocker):
            # Built-in parental control lists are chosen per profile
            # ("Block …" switches). This global main switch is rarely needed,
            # so it starts disabled. Custom lists stay enabled: the
            # update/remove actions target this entity.
            self._attr_entity_registry_enabled_default = False

    @staticmethod
    def unique_id_for(coordinator: EBlockerCoordinator, key: str) -> str:
        """Return the unique id of the switch for a filter list key."""
        entry = coordinator.config_entry
        return f"{entry.unique_id or entry.entry_id}_filter_list_{key}"

    @property
    def blocker(self) -> dict[str, Any]:
        """Return the current filter list data."""
        return (self.coordinator.data.blockers or {}).get(self.key, {})

    @property
    def available(self) -> bool:
        return super().available and bool(self.blocker)

    @property
    def is_on(self) -> bool:
        return bool(self.blocker.get("enabled"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        b = self.blocker
        last_update = b.get("lastUpdate")
        return {
            "filter_list_id": b.get("id"),
            "category": _lower(b.get("category")),
            "list_type": _lower(b.get("type")),
            "filter_type": b.get("filterType"),
            "custom": is_custom(b),
            "url": b.get("url"),
            "format": _lower(b.get("format")),
            "update_interval": _lower(b.get("updateInterval")),
            "update_status": _lower(b.get("updateStatus")),
            "last_update": (
                dt_util.utc_from_timestamp(last_update / 1000).isoformat()
                if isinstance(last_update, (int, float))
                else None
            ),
            "error": b.get("error"),
        }

    def _error(self, key: str) -> ServiceValidationError:
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders={"name": self.list_name},
        )

    async def _fresh_blocker(self) -> dict[str, Any]:
        """Re-read the list: built-in IDs may change when the eBlocker restarts."""
        for blocker in await self.coordinator.client.get_blockers():
            if blocker_key(blocker) == self.key:
                return blocker
        raise self._error("filter_list_not_found")

    def _ensure_writable(self, blocker: dict[str, Any]) -> None:
        # eBlockerOS dereferences the URL when a custom list is written back
        # (BlockerService.updateBlocker), so lists without URL would fail with
        # a server error. Refuse early with a clear message instead.
        if is_custom(blocker) and not blocker.get("url"):
            raise self._error("filter_list_without_url")

    async def _set_enabled(self, enabled: bool) -> None:
        async def _write() -> None:
            blocker = await self._fresh_blocker()
            self._ensure_writable(blocker)
            blocker["enabled"] = enabled
            await self.coordinator.client.update_blocker(blocker)

        await run_action(self.coordinator, _write())

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_enabled(False)

    async def async_update_filter_list(self, **kwargs: Any) -> None:
        """Change name, description, URL or update interval of a custom list."""
        if not is_custom(self.blocker):
            raise self._error("filter_list_builtin")

        async def _write() -> None:
            blocker = await self._fresh_blocker()
            self._ensure_writable(blocker)
            lang = (self.hass.config.language or "en").split("-")[0]
            if name := kwargs.get(ATTR_NAME):
                blocker["name"] = {lang: name}
            if (description := kwargs.get(ATTR_DESCRIPTION)) is not None:
                blocker["description"] = {lang: description}
            if url := kwargs.get(ATTR_URL):
                blocker["url"] = url
            if interval := kwargs.get(ATTR_UPDATE_INTERVAL):
                blocker["updateInterval"] = UPDATE_INTERVALS[interval]
            await self.coordinator.client.update_blocker(blocker)

        await run_action(self.coordinator, _write())

    async def async_remove_filter_list(self) -> None:
        """Delete a custom filter list."""
        if not is_custom(self.blocker):
            raise self._error("filter_list_builtin")

        async def _delete() -> None:
            blocker = await self._fresh_blocker()
            await self.coordinator.client.delete_blocker(blocker["id"])

        await run_action(self.coordinator, _delete())


def _lower(value: Any) -> str | None:
    return str(value).lower() if value else None


def _setup_profile_list_switches(
    hass: HomeAssistant,
    entry: EBlockerConfigEntry,
    coordinator: EBlockerCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a switch per profile and parental control list, kept in sync."""
    known: set[tuple[int, int]] = set()
    profile_ids = coordinator.profile_ids  # new profiles reload the entry

    @callback
    def _sync() -> None:
        data = coordinator.data
        if not data or data.parental_lists is None:
            return  # request failed: keep entities (they become unavailable)
        lists = selectable_parental_lists(data.parental_lists)
        wanted = {(pid, list_id) for pid in profile_ids for list_id in lists}
        if new := wanted - known:
            known.update(new)
            async_add_entities(
                EBlockerProfileListSwitch(coordinator, pid, list_id)
                for pid, list_id in sorted(new)
            )
        registry = er.async_get(hass)
        for pid, list_id in known - wanted:
            known.discard((pid, list_id))
            unique_id = EBlockerProfileListSwitch.unique_id_for(
                coordinator, pid, list_id
            )
            if entity_id := registry.async_get_entity_id("switch", DOMAIN, unique_id):
                registry.async_remove(entity_id)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class EBlockerProfileListSwitch(
    _FilterListActionsMixin, EBlockerProfileEntity, SwitchEntity
):
    """Blocks (or, for allow lists, allows) one category for a profile."""

    def __init__(
        self, coordinator: EBlockerCoordinator, profile_id: int, list_id: int
    ) -> None:
        super().__init__(coordinator, profile_id, f"list_{list_id}")
        self.list_id = list_id
        filter_list = (coordinator.data.parental_lists or {})[list_id]
        self._is_allow_list = filter_list.get("filterType") == "whitelist"
        self._attr_translation_key = (
            "allow_list" if self._is_allow_list else "block_list"
        )
        self._attr_translation_placeholders = {
            "name": parental_list_name(filter_list, coordinator.hass.config.language)
        }

    @staticmethod
    def unique_id_for(
        coordinator: EBlockerCoordinator, profile_id: int, list_id: int
    ) -> str:
        """Return the unique id of the switch for a profile and list."""
        return f"{profile_identifier(coordinator, profile_id)[1]}_list_{list_id}"

    @property
    def _list(self) -> dict[str, Any]:
        return (self.coordinator.data.parental_lists or {}).get(self.list_id, {})

    @property
    def available(self) -> bool:
        # A list switched off globally on the eBlocker is ignored for every
        # profile, so the profile switch would have no effect.
        return super().available and bool(self._list) and not self._list.get("disabled")

    @property
    def is_on(self) -> bool:
        attribute = PROFILE_LIST_ATTRIBUTE[
            "whitelist" if self._is_allow_list else "blacklist"
        ]
        return self.list_id in (self.profile.get(attribute) or [])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        profile = self.profile
        mode = profile.get("internetAccessRestrictionMode") or 1
        # In "block listed" mode allow lists are exceptions and vice versa
        is_main = (mode == 1) != self._is_allow_list
        return {
            "filter_list_id": self.list_id,
            "web_filter_active": bool(profile.get("controlmodeUrls")),
            "role": "rule" if is_main else "exception",
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, enabled: bool) -> None:
        client = self.coordinator.client

        async def _write() -> None:
            profile = await client.get_profile(self.profile_id)
            filter_list = {**self._list, "id": self.list_id}
            await client.update_profile(
                toggle_profile_list(profile, filter_list, enabled)
            )

        await run_action(self.coordinator, _write())


@dataclass(frozen=True, kw_only=True)
class EBlockerProfileSwitchDescription(SwitchEntityDescription):
    """A switch that maps to one boolean attribute of a profile."""

    attribute: str


PROFILE_SWITCHES: tuple[EBlockerProfileSwitchDescription, ...] = (
    EBlockerProfileSwitchDescription(
        key="internet_blocked",
        translation_key="internet_blocked",
        attribute="internetBlocked",
    ),
    EBlockerProfileSwitchDescription(
        key="web_filter",
        translation_key="web_filter",
        entity_category=EntityCategory.CONFIG,
        attribute="controlmodeUrls",
    ),
    EBlockerProfileSwitchDescription(
        key="online_times",
        translation_key="online_times",
        entity_category=EntityCategory.CONFIG,
        attribute="controlmodeTime",
    ),
    EBlockerProfileSwitchDescription(
        key="usage_limit",
        translation_key="usage_limit",
        entity_category=EntityCategory.CONFIG,
        attribute="controlmodeMaxUsage",
    ),
)


class EBlockerProfileSwitch(
    _FilterListActionsMixin, EBlockerProfileEntity, SwitchEntity
):
    """Switches one parental control setting of a profile."""

    entity_description: EBlockerProfileSwitchDescription

    def __init__(
        self,
        coordinator: EBlockerCoordinator,
        profile_id: int,
        description: EBlockerProfileSwitchDescription,
    ) -> None:
        super().__init__(coordinator, profile_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return bool(self.profile.get(self.entity_description.attribute))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        profile = self.profile
        key = self.entity_description.key
        if key == "web_filter":
            lists = self.coordinator.data.parental_lists or {}
            language = self.hass.config.language if self.hass else "en"
            return {
                "blocked_lists": [
                    parental_list_name(lists[i], language) if i in lists else i
                    for i in sorted(profile.get("inaccessibleSitesPackages") or [])
                ],
                "allowed_lists": [
                    parental_list_name(lists[i], language) if i in lists else i
                    for i in sorted(profile.get("accessibleSitesPackages") or [])
                ],
                "mode": (
                    "whitelist"
                    if profile.get("internetAccessRestrictionMode") == 2
                    else "blacklist"
                ),
            }
        if key == "online_times":
            return {
                "online_times": format_contingents(
                    profile.get("internetAccessContingents")
                )
            }
        if key == "usage_limit":
            per_day = profile.get("maxUsageTimeByDay") or {}
            return {f"{day.lower()}_minutes": per_day.get(day) for day in WEEKDAYS}
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, value: bool) -> None:
        await run_action(
            self.coordinator,
            self.coordinator.client.set_profile_attributes(
                self.profile_id, **{self.entity_description.attribute: value}
            ),
        )
