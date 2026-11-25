"""Binary sensors for the eBlocker integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EBlockerConfigEntry, EBlockerData
from .entity import EBlockerDeviceEntity, EBlockerEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class EBlockerBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a binary sensor of the eBlocker itself."""

    value_fn: Callable[[EBlockerData], bool | None]


HUB_SENSORS: tuple[EBlockerBinarySensorDescription, ...] = (
    EBlockerBinarySensorDescription(
        key="updates_available",
        translation_key="updates_available",
        device_class=BinarySensorDeviceClass.UPDATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (
            None
            if d.update_status is None
            else bool(d.update_status.get("updatesAvailable"))
        ),
    ),
    EBlockerBinarySensorDescription(
        key="system_problem",
        translation_key="system_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (
            d.system_status.get("executionState") != "RUNNING"
            or any(
                s.get("status") != "OK"
                for s in d.system_status.get("subSystemDetails") or []
            )
        ),
    ),
)


@dataclass(frozen=True, kw_only=True)
class EBlockerDeviceBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a binary sensor of a network device."""

    value_fn: Callable[[dict[str, Any]], bool | None]


DEVICE_SENSORS: tuple[EBlockerDeviceBinarySensorDescription, ...] = (
    EBlockerDeviceBinarySensorDescription(
        key="protected",
        translation_key="protected",
        value_fn=lambda dev: bool(dev.get("enabled")) and not dev.get("paused"),
    ),
    EBlockerDeviceBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda dev: bool(dev.get("isOnline")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EBlockerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up eBlocker binary sensors."""
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [EBlockerReachableSensor(coordinator)]
    entities.extend(
        EBlockerBinarySensor(coordinator, description) for description in HUB_SENSORS
    )
    entities.extend(
        EBlockerDeviceBinarySensor(coordinator, device_id, description)
        for device_id in coordinator.tracked_device_ids
        if device_id in coordinator.data.devices
        for description in DEVICE_SENSORS
    )
    async_add_entities(entities)


class EBlockerReachableSensor(EBlockerEntity, BinarySensorEntity):
    """Whether the eBlocker answered the last poll.

    Unlike all other entities this one stays available when the eBlocker is
    down, so automations can trigger on it turning off.
    """

    _attr_translation_key = "reachable"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "reachable")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success


class EBlockerBinarySensor(EBlockerEntity, BinarySensorEntity):
    """A binary sensor of the eBlocker itself."""

    entity_description: EBlockerBinarySensorDescription

    def __init__(self, coordinator, description) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data)


class EBlockerDeviceBinarySensor(EBlockerDeviceEntity, BinarySensorEntity):
    """A binary sensor of a network device."""

    entity_description: EBlockerDeviceBinarySensorDescription

    def __init__(self, coordinator, device_id: str, description) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.device)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "protected":
            return None
        dev = self.device
        return {
            "enabled": dev.get("enabled"),
            "paused": dev.get("paused"),
            "filter_mode": dev.get("filterMode"),
            "ip_addresses": dev.get("ipAddresses", []),
        }
