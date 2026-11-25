"""Select entities for the eBlocker integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import CLOAKING_OFF
from .const import DOMAIN
from .coordinator import EBlockerConfigEntry, EBlockerCoordinator
from .entity import EBlockerDeviceEntity, EBlockerProfileEntity, run_action
from .parental_control import RESTRICTION_MODES

PARALLEL_UPDATES = 1

UNASSIGNED = "unassigned"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EBlockerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up eBlocker selects."""
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = [
        EBlockerWebFilterModeSelect(coordinator, profile_id)
        for profile_id in coordinator.profile_ids
    ]
    device_ids = [
        d for d in coordinator.tracked_device_ids if d in coordinator.data.devices
    ]
    if coordinator.data.users is not None:
        entities.extend(
            EBlockerDeviceUserSelect(coordinator, device_id) for device_id in device_ids
        )
    entities.extend(
        EBlockerIpAnonymizationSelect(coordinator, device_id)
        for device_id in device_ids
    )
    if coordinator.data.user_agents is not None:
        entities.extend(
            EBlockerCloakingSelect(coordinator, device_id) for device_id in device_ids
        )
    async_add_entities(entities)


OFF = "off"
TOR = "tor"


class EBlockerIpAnonymizationSelect(EBlockerDeviceEntity, SelectEntity):
    """Routes all traffic of a device through Tor or a VPN profile.

    Mirrors the eBlocker settings UI: Tor and VPN exclude each other, so the
    other one is switched off first.
    """

    _attr_translation_key = "ip_anonymization"

    def __init__(self, coordinator: EBlockerCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "ip_anonymization")

    @property
    def _vpn_profiles(self) -> dict[int, dict[str, Any]]:
        return {
            pid: p
            for pid, p in (self.coordinator.data.vpn_profiles or {}).items()
            if p.get("enabled", True)
            and not p.get("deleted")
            and not p.get("temporary")
        }

    @property
    def options(self) -> list[str]:
        return [
            OFF,
            TOR,
            *(p.get("name") or f"VPN #{pid}" for pid, p in self._vpn_profiles.items()),
        ]

    def _active(self) -> tuple[bool, int | None]:
        """Return (Tor active, active VPN profile id) from the device flags."""
        device = self.device
        if not device.get("useAnonymizationService"):
            return False, None
        if device.get("routeThroughTor"):
            return True, None
        return False, device.get("useVPNProfileID")

    @property
    def current_option(self) -> str | None:
        tor, vpn = self._active()
        if tor:
            return TOR
        if vpn is not None:
            profile = (self.coordinator.data.vpn_profiles or {}).get(vpn)
            return (profile or {}).get("name") or f"VPN #{vpn}"
        return OFF

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        tor, vpn = self._active()
        return {"vpn_profile_id": vpn, "tor": tor}

    async def async_select_option(self, option: str) -> None:
        target_vpn: int | None = None
        if option not in (OFF, TOR):
            target_vpn = next(
                (
                    pid
                    for pid, p in self._vpn_profiles.items()
                    if (p.get("name") or f"VPN #{pid}") == option
                ),
                None,
            )
            if target_vpn is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="vpn_profile_not_found",
                    translation_placeholders={"profile": option},
                )
        if (
            option != OFF
            and not self.device.get("enabled")
            and not self.device.get("paused")
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="anonymization_device_disabled",
                translation_placeholders={"device": self.device.get("name", "")},
            )
        tor, vpn = self._active()
        client = self.coordinator.client

        async def _apply() -> None:
            if vpn is not None and vpn != target_vpn:
                await client.set_vpn(vpn, self.device_id, False)
            if tor and option != TOR:
                await client.set_tor(self.device_id, False)
            if option == TOR and not tor:
                await client.set_tor(self.device_id, True)
            if target_vpn is not None and target_vpn != vpn:
                await client.set_vpn(target_vpn, self.device_id, True)

        await run_action(self.coordinator, _apply())


class EBlockerCloakingSelect(EBlockerDeviceEntity, SelectEntity):
    """Cloaks the browser of a device as another device type (user agent).

    Only works when HTTPS support is enabled for the eBlocker and the device.
    """

    _attr_translation_key = "browser_cloaking"

    def __init__(self, coordinator: EBlockerCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "browser_cloaking")

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return (
            super().available
            and bool(data.ssl_enabled)
            and bool(self.device.get("sslEnabled"))
            and self.device_id in data.cloaking
        )

    @property
    def options(self) -> list[str]:
        agents = [
            a for a in self.coordinator.data.user_agents or [] if a != CLOAKING_OFF
        ]
        return [OFF, *agents]

    @property
    def current_option(self) -> str | None:
        agent = self.coordinator.data.cloaking.get(self.device_id, CLOAKING_OFF)
        return OFF if agent == CLOAKING_OFF else agent

    async def async_select_option(self, option: str) -> None:
        await run_action(
            self.coordinator,
            self.coordinator.client.set_cloaked_user_agent(
                self.device_id,
                self.device.get("assignedUser"),
                CLOAKING_OFF if option == OFF else option,
            ),
        )


class EBlockerWebFilterModeSelect(EBlockerProfileEntity, SelectEntity):
    """Web filter mode of a profile: block the selected categories, or only
    allow the selected allow lists (e.g. fragFINN)."""

    _attr_translation_key = "web_filter_mode"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EBlockerCoordinator, profile_id: int) -> None:
        super().__init__(coordinator, profile_id, "web_filter_mode")
        self._attr_options = list(RESTRICTION_MODES)

    @property
    def current_option(self) -> str | None:
        mode = self.profile.get("internetAccessRestrictionMode") or 1
        return next((k for k, v in RESTRICTION_MODES.items() if v == mode), None)

    async def async_select_option(self, option: str) -> None:
        await run_action(
            self.coordinator,
            self.coordinator.client.set_profile_attributes(
                self.profile_id, internetAccessRestrictionMode=RESTRICTION_MODES[option]
            ),
        )


class EBlockerDeviceUserSelect(EBlockerDeviceEntity, SelectEntity):
    """Assigns a network device to a parental control user.

    The device then follows the user's profile (filters, time limits).
    "unassigned" gives the device back to its own system user, which uses
    the standard profile.
    """

    _attr_translation_key = "device_user"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EBlockerCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "user")

    @property
    def _users(self) -> dict[int, dict[str, Any]]:
        """Return the users a device can be assigned to (no system users)."""
        users = self.coordinator.data.users or {}
        return {uid: u for uid, u in users.items() if not u.get("system")}

    @property
    def options(self) -> list[str]:
        names = sorted(
            (u.get("name") or f"#{uid}" for uid, u in self._users.items()),
            key=str.casefold,
        )
        return [UNASSIGNED, *names]

    @property
    def current_option(self) -> str | None:
        assigned = self.device.get("assignedUser")
        if assigned in self._users:
            return self._users[assigned].get("name") or f"#{assigned}"
        return UNASSIGNED

    async def async_select_option(self, option: str) -> None:
        if option == UNASSIGNED:
            user_id = self.device.get("defaultSystemUser")
        else:
            user_id = next(
                (
                    uid
                    for uid, u in self._users.items()
                    if (u.get("name") or f"#{uid}") == option
                ),
                None,
            )
        if user_id is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="user_not_found",
                translation_placeholders={"user": option},
            )
        await run_action(
            self.coordinator,
            self.coordinator.client.set_device_attributes(
                self.device_id, assignedUser=user_id, operatingUser=user_id
            ),
        )
