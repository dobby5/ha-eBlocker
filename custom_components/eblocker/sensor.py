"""Sensors for the eBlocker integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import EBlockerConfigEntry, EBlockerData
from .entity import EBlockerDeviceEntity, EBlockerEntity, EBlockerProfileEntity
from .parental_control import bonus_minutes_today, usage_minutes, user_profile_owner

PARALLEL_UPDATES = 0

UNIT_QUERIES = "queries"
UNIT_DEVICES = "devices"


def _blocked_by(reason: str) -> Callable[[EBlockerData], int | None]:
    def _value(data: EBlockerData) -> int | None:
        reasons = data.stats_summary.get("blockedQueriesByReason")
        if reasons is None:
            return None
        return int(reasons.get(reason, 0))

    return _value


def _block_rate(data: EBlockerData) -> float | None:
    summary = data.stats_summary
    queries = summary.get("queries")
    blocked = summary.get("blockedQueries")
    if not queries or blocked is None:
        return None
    return round(blocked / queries * 100, 2)


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return dt_util.utc_from_timestamp(value)
    return dt_util.parse_datetime(str(value))


def _stats_attributes(data: EBlockerData) -> dict[str, Any]:
    since = _timestamp(data.stats_summary.get("begin"))
    return {"counting_since": since.isoformat()} if since else {}


@dataclass(frozen=True, kw_only=True)
class EBlockerSensorDescription(SensorEntityDescription):
    """Describes an eBlocker sensor."""

    value_fn: Callable[[EBlockerData], Any]
    attributes_fn: Callable[[EBlockerData], dict[str, Any]] | None = None


SENSORS: tuple[EBlockerSensorDescription, ...] = (
    # --- Network devices ---
    EBlockerSensorDescription(
        key="devices_total",
        translation_key="devices_total",
        native_unit_of_measurement=UNIT_DEVICES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.devices),
    ),
    EBlockerSensorDescription(
        key="devices_online",
        translation_key="devices_online",
        native_unit_of_measurement=UNIT_DEVICES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for x in d.devices.values() if x.get("isOnline")),
    ),
    EBlockerSensorDescription(
        key="devices_protected",
        translation_key="devices_protected",
        native_unit_of_measurement=UNIT_DEVICES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for x in d.devices.values() if x.get("enabled")),
    ),
    EBlockerSensorDescription(
        key="devices_paused",
        translation_key="devices_paused",
        native_unit_of_measurement=UNIT_DEVICES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for x in d.devices.values() if x.get("paused")),
    ),
    # --- Filter statistics ---
    EBlockerSensorDescription(
        key="dns_queries",
        translation_key="dns_queries",
        native_unit_of_measurement=UNIT_QUERIES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.stats_summary.get("queries"),
        attributes_fn=_stats_attributes,
    ),
    EBlockerSensorDescription(
        key="blocked_queries",
        translation_key="blocked_queries",
        native_unit_of_measurement=UNIT_QUERIES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.stats_summary.get("blockedQueries"),
        attributes_fn=_stats_attributes,
    ),
    EBlockerSensorDescription(
        key="blocked_ads",
        translation_key="blocked_ads",
        native_unit_of_measurement=UNIT_QUERIES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_blocked_by("ADS"),
        attributes_fn=_stats_attributes,
    ),
    EBlockerSensorDescription(
        key="blocked_trackers",
        translation_key="blocked_trackers",
        native_unit_of_measurement=UNIT_QUERIES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_blocked_by("TRACKERS"),
        attributes_fn=_stats_attributes,
    ),
    EBlockerSensorDescription(
        key="blocked_malware",
        translation_key="blocked_malware",
        native_unit_of_measurement=UNIT_QUERIES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_blocked_by("MALWARE"),
        attributes_fn=_stats_attributes,
    ),
    EBlockerSensorDescription(
        key="blocked_parental_control",
        translation_key="blocked_parental_control",
        native_unit_of_measurement=UNIT_QUERIES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_blocked_by("PARENTAL_CONTROL"),
        attributes_fn=_stats_attributes,
    ),
    EBlockerSensorDescription(
        key="blocked_content",
        translation_key="blocked_content",
        native_unit_of_measurement=UNIT_QUERIES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
        value_fn=_blocked_by("CONTENT"),
        attributes_fn=_stats_attributes,
    ),
    EBlockerSensorDescription(
        key="block_rate",
        translation_key="block_rate",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_block_rate,
    ),
    # --- Filter lists ---
    EBlockerSensorDescription(
        key="filter_lists_enabled",
        translation_key="filter_lists_enabled",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda d: (
            None
            if d.blockers is None
            else sum(1 for b in d.blockers.values() if b.get("enabled"))
        ),
        attributes_fn=lambda d: {
            "total": len(d.blockers or {}),
            "custom": sum(
                1
                for b in (d.blockers or {}).values()
                if not b.get("providedByEblocker", True)
            ),
            "failed": [
                (b.get("name") or {}).get("en") or b.get("id")
                for b in (d.blockers or {}).values()
                if str(b.get("updateStatus", "")).endswith("FAILED")
            ],
        },
    ),
    # --- System information ---
    EBlockerSensorDescription(
        key="system_state",
        translation_key="system_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.system_status.get("executionState"),
        attributes_fn=lambda d: {
            "warnings": d.system_status.get("warnings") or [],
            "subsystems_not_ok": [
                s.get("name")
                for s in d.system_status.get("subSystemDetails") or []
                if s.get("status") != "OK"
            ],
        },
    ),
    EBlockerSensorDescription(
        key="version",
        translation_key="version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.system_status.get("projectVersion"),
    ),
    EBlockerSensorDescription(
        key="lists_version",
        translation_key="lists_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.update_status or {}).get("listsPacketVersion"),
    ),
    EBlockerSensorDescription(
        key="next_automatic_update",
        translation_key="next_automatic_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _timestamp(
            (d.update_status or {}).get("nextAutomaticUpdate")
        ),
    ),
    EBlockerSensorDescription(
        key="last_automatic_update",
        translation_key="last_automatic_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _timestamp(
            (d.update_status or {}).get("lastAutomaticUpdate")
        ),
    ),
    EBlockerSensorDescription(
        key="license_valid_until",
        translation_key="license_valid_until",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _timestamp(
            (d.registration or {}).get("licenseNotValidAfter")
        ),
        attributes_fn=lambda d: {
            "license_type": (d.registration or {}).get("licenseType"),
            "registration_state": (d.registration or {}).get("registrationState"),
            "lifetime_license": (d.registration or {}).get("licenseLifetime"),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EBlockerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up eBlocker sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        EBlockerSensor(coordinator, description) for description in SENSORS
    ]
    entities.extend(
        EBlockerPauseRemainingSensor(coordinator, device_id)
        for device_id in coordinator.tracked_device_ids
        if device_id in coordinator.data.devices
    )
    users = coordinator.data.users or {}
    for profile_id in coordinator.profile_ids:
        personal = user_profile_owner(coordinator.data.profiles[profile_id], users)
        entities.extend(
            EBlockerProfileSensor(coordinator, profile_id, description)
            for description in PROFILE_SENSORS
            # usage only exists for a user's own profile
            if personal or not description.needs_usage
        )
    async_add_entities(entities)


class EBlockerSensor(EBlockerEntity, SensorEntity):
    """A sensor of the eBlocker itself."""

    entity_description: EBlockerSensorDescription

    def __init__(self, coordinator, description: EBlockerSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)


class EBlockerPauseRemainingSensor(EBlockerDeviceEntity, SensorEntity):
    """Remaining pause time of a network device."""

    _attr_translation_key = "pause_remaining"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "pause_remaining")

    @property
    def native_value(self) -> int | None:
        if not self.pause:
            return 0 if not self.device.get("paused") else None
        return int(self.pause.get("pausing") or 0)


@dataclass(frozen=True, kw_only=True)
class EBlockerProfileSensorDescription(SensorEntityDescription):
    """Describes a sensor of a parental control profile."""

    value_fn: Callable[[EBlockerProfileSensor], Any]
    # Only for personal profiles with a usage limit (usage data exists)
    needs_usage: bool = False


def _usage(entity: EBlockerProfileSensor) -> dict[str, float | None]:
    owner = entity.owner
    usage = entity.coordinator.data.usage.get(owner["id"]) if owner else None
    return usage_minutes(usage)


def _assigned_devices(entity: EBlockerProfileSensor) -> list[dict[str, Any]]:
    data = entity.coordinator.data
    users = data.users or {}
    return [
        device
        for device in data.devices.values()
        if (users.get(device.get("assignedUser")) or {}).get("associatedProfileId")
        == entity.profile_id
    ]


PROFILE_SENSORS: tuple[EBlockerProfileSensorDescription, ...] = (
    EBlockerProfileSensorDescription(
        key="remaining_time",
        translation_key="remaining_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        needs_usage=True,
        value_fn=lambda e: _usage(e)["remaining"],
    ),
    EBlockerProfileSensorDescription(
        key="used_time",
        translation_key="used_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        needs_usage=True,
        value_fn=lambda e: _usage(e)["used"],
    ),
    EBlockerProfileSensorDescription(
        key="bonus_time",
        translation_key="bonus_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda e: bonus_minutes_today(e.profile, dt_util.now().date()),
    ),
    EBlockerProfileSensorDescription(
        key="assigned_devices",
        translation_key="assigned_devices",
        native_unit_of_measurement=UNIT_DEVICES,
        value_fn=lambda e: len(_assigned_devices(e)),
    ),
)


class EBlockerProfileSensor(EBlockerProfileEntity, SensorEntity):
    """A sensor of a parental control profile."""

    entity_description: EBlockerProfileSensorDescription

    def __init__(
        self,
        coordinator,
        profile_id: int,
        description: EBlockerProfileSensorDescription,
    ) -> None:
        super().__init__(coordinator, profile_id, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        if self.entity_description.needs_usage:
            # Usage is only tracked while the daily limit is active
            return bool(self.profile.get("controlmodeMaxUsage")) and bool(
                user_profile_owner(self.profile, self.coordinator.data.users or {})
            )
        return True

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        key = self.entity_description.key
        if key == "assigned_devices":
            return {"devices": [d.get("name") for d in _assigned_devices(self)]}
        if key == "remaining_time":
            owner = self.owner
            usage = self.coordinator.data.usage.get(owner["id"]) if owner else None
            minutes = _usage(self)
            return {
                "limit_today_minutes": minutes["max"],
                "online_allowed": (usage or {}).get("allowed"),
                "counting": (usage or {}).get("active"),
            }
        return None
