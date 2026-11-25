"""Base entities for the eBlocker integration."""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import EBlockerError
from .const import DOMAIN, MANUFACTURER
from .coordinator import EBlockerCoordinator
from .parental_control import profile_name, user_profile_owner

# Home Assistant 2026.8+ links child devices by registry id (``via_device_id``);
# older versions only know the identifier tuple (``via_device``).
_HAS_VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__annotations__


def hub_identifier(coordinator: EBlockerCoordinator) -> tuple[str, str]:
    """Return the device registry identifier of the eBlocker itself."""
    entry = coordinator.config_entry
    return (DOMAIN, entry.unique_id or entry.entry_id)


def hub_device_info(coordinator: EBlockerCoordinator) -> DeviceInfo:
    """Return the device info of the eBlocker itself."""
    data = coordinator.data
    eblocker = data.eblocker if data else {}
    mac = eblocker.get("hardwareAddress")
    product = ((data.registration or {}).get("productInfo") or {}) if data else {}
    return DeviceInfo(
        identifiers={hub_identifier(coordinator)},
        connections={(CONNECTION_NETWORK_MAC, mac)} if mac else set(),
        manufacturer=MANUFACTURER,
        model=product.get("productName") or "eBlocker",
        name=eblocker.get("name") or "eBlocker",
        sw_version=(data.system_status.get("projectVersion") if data else None),
        configuration_url=coordinator.client.base_url,
    )


class EBlockerEntity(CoordinatorEntity[EBlockerCoordinator]):
    """Entity that belongs to the eBlocker itself."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EBlockerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{hub_identifier(coordinator)[1]}_{key}"
        self._attr_device_info = hub_device_info(coordinator)


class EBlockerDeviceEntity(CoordinatorEntity[EBlockerCoordinator]):
    """Entity that belongs to a network device managed by the eBlocker."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: EBlockerCoordinator, device_id: str, key: str
    ) -> None:
        super().__init__(coordinator)
        self.device_id = device_id
        self._attr_unique_id = f"{device_id}_{key}"
        device = coordinator.data.devices.get(device_id, {})
        mac = device.get("hardwareAddress")
        info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            connections={(CONNECTION_NETWORK_MAC, mac)} if mac else set(),
            manufacturer=device.get("vendor") or None,
            name=device.get("name") or device_id,
        )
        if _HAS_VIA_DEVICE_ID and coordinator.hub_device_id:
            info["via_device_id"] = coordinator.hub_device_id
        else:
            info["via_device"] = hub_identifier(coordinator)
        self._attr_device_info = info

    @property
    def device(self) -> dict[str, Any]:
        """Return the current device data."""
        return self.coordinator.data.devices.get(self.device_id, {})

    @property
    def pause(self) -> dict[str, Any]:
        """Return the current pause data of the device."""
        return self.coordinator.data.pauses.get(self.device_id, {})

    @property
    def available(self) -> bool:
        """Device entities are unavailable when the eBlocker forgot the device."""
        return super().available and self.device_id in self.coordinator.data.devices


def profile_identifier(
    coordinator: EBlockerCoordinator, profile_id: int
) -> tuple[str, str]:
    """Return the device registry identifier of a parental control profile."""
    return (DOMAIN, f"{hub_identifier(coordinator)[1]}_profile_{profile_id}")


class EBlockerProfileEntity(CoordinatorEntity[EBlockerCoordinator]):
    """Entity that belongs to a parental control profile (one per user)."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: EBlockerCoordinator, profile_id: int, key: str
    ) -> None:
        super().__init__(coordinator)
        self.profile_id = profile_id
        self._attr_unique_id = f"{profile_identifier(coordinator, profile_id)[1]}_{key}"
        data = coordinator.data
        profile = (data.profiles or {}).get(profile_id, {})
        info = DeviceInfo(
            identifiers={profile_identifier(coordinator, profile_id)},
            manufacturer=MANUFACTURER,
            model="Parental control profile",
            name=profile_name(
                profile, data.users or {}, coordinator.hass.config.language
            ),
        )
        if _HAS_VIA_DEVICE_ID and coordinator.hub_device_id:
            info["via_device_id"] = coordinator.hub_device_id
        else:
            info["via_device"] = hub_identifier(coordinator)
        self._attr_device_info = info

    @property
    def profile(self) -> dict[str, Any]:
        """Return the current profile data."""
        return (self.coordinator.data.profiles or {}).get(self.profile_id, {})

    @property
    def owner(self) -> dict[str, Any] | None:
        """Return the user owning this (personal) profile, if any."""
        return user_profile_owner(self.profile, self.coordinator.data.users or {})

    @property
    def available(self) -> bool:
        return super().available and bool(self.profile)


async def run_action(coordinator: EBlockerCoordinator, coro: Any) -> Any:
    """Run a write request, translate errors and refresh the data afterwards."""
    try:
        return await coro
    except EBlockerError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="action_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    finally:
        await coordinator.async_request_refresh()


def device_label(device: dict[str, Any]) -> str:
    """Return a human-friendly label for device selection lists."""
    name = device.get("name") or device.get("id", "?")
    ipv4 = [ip for ip in device.get("ipAddresses", []) if ":" not in ip]
    parts = [name]
    if ipv4:
        parts.append(ipv4[0])
    elif device.get("hardwareAddress"):
        parts.append(device["hardwareAddress"])
    label = " – ".join(parts)
    if device.get("isOnline"):
        label += " (online)"
    return label
