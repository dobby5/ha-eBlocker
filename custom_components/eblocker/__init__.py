"""The eBlocker integration."""

from __future__ import annotations

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import EBlockerClient
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, MIN_SCAN_INTERVAL
from .coordinator import EBlockerConfigEntry, EBlockerCoordinator
from .entity import hub_device_info, hub_identifier, profile_identifier
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register integration-wide actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EBlockerConfigEntry) -> bool:
    """Set up eBlocker from a config entry."""
    client = EBlockerClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data.get(CONF_PASSWORD),
    )
    scan_interval = max(
        MIN_SCAN_INTERVAL,
        int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
    )
    coordinator = EBlockerCoordinator(hass, entry, client, scan_interval)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Register the eBlocker itself first, so network devices can link to it.
    hub = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **hub_device_info(coordinator)
    )
    coordinator.hub_device_id = hub.id

    _remove_untracked_devices(hass, entry, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


# Entities that are disabled by default. ``entity_registry_enabled_default``
# only applies when an entity is registered for the first time, so entities
# created by older versions are disabled once by the migration below.
_DISABLED_BY_DEFAULT_SUFFIXES = (
    "_filter_lists_enabled",
    "_webrtc_blocked",
    "_referrer_removed",
    "_dnt_header",
)
# Global switches of built-in parental control lists (chosen per profile)
_DISABLED_BY_DEFAULT_INFIX = "_filter_list_builtin_domain_parental_control_"


async def async_migrate_entry(hass: HomeAssistant, entry: EBlockerConfigEntry) -> bool:
    """Migrate an old config entry."""
    if entry.version > 1:
        return False  # downgrade from a future version
    if entry.minor_version < 2:
        hub = entry.unique_id or entry.entry_id
        registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            unique_id = entity.unique_id
            if entity.disabled_by is not None or not unique_id.startswith(hub):
                continue  # already disabled (e.g. by the user) or not a hub entity
            if unique_id.endswith(_DISABLED_BY_DEFAULT_SUFFIXES) or (
                _DISABLED_BY_DEFAULT_INFIX in unique_id
            ):
                registry.async_update_entity(
                    entity.entity_id,
                    disabled_by=er.RegistryEntryDisabler.INTEGRATION,
                )
        hass.config_entries.async_update_entry(entry, minor_version=2)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EBlockerConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(
    hass: HomeAssistant, entry: EBlockerConfigEntry
) -> None:
    """Reload when the options (tracked devices, interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _remove_untracked_devices(
    hass: HomeAssistant, entry: EBlockerConfigEntry, coordinator: EBlockerCoordinator
) -> None:
    """Remove registry devices of network devices that are no longer selected."""
    registry = dr.async_get(hass)
    keep = (
        {hub_identifier(coordinator)}
        | {(DOMAIN, device_id) for device_id in coordinator.tracked_device_ids}
        | {profile_identifier(coordinator, pid) for pid in coordinator.profile_ids}
    )
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if device.identifiers & keep:
            continue
        if coordinator.data.profiles is None and any(
            "_profile_" in identifier for _, identifier in device.identifiers
        ):
            continue  # profiles could not be read: keep their devices
        owners = getattr(device, "config_entries", None)
        if owners is not None and len(owners) > 1:
            # Older Home Assistant: the device is shared with another
            # integration, so only detach it from this entry.
            registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )
        else:
            registry.async_remove_device(device.id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: EBlockerConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing network devices from the UI (not the eBlocker itself)."""
    return hub_identifier(entry.runtime_data) not in device_entry.identifiers
