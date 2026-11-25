"""DataUpdateCoordinator for the eBlocker integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    EBlockerApiError,
    EBlockerAuthError,
    EBlockerClient,
    EBlockerConnectionError,
    EBlockerError,
    EBlockerLoginThrottledError,
)
from .const import CONF_TRACKED_DEVICES, DEFAULT_SCAN_INTERVAL, DOMAIN
from .filter_lists import blocker_key
from .parental_control import managed_profile_ids, user_profile_owner

_LOGGER = logging.getLogger(__name__)

type EBlockerConfigEntry = ConfigEntry[EBlockerCoordinator]


@dataclass
class EBlockerData:
    """Snapshot of everything read from the eBlocker in one update."""

    system_status: dict[str, Any]
    eblocker: dict[str, Any]
    devices: dict[str, dict[str, Any]]
    stats: dict[str, Any]
    update_status: dict[str, Any] | None = None
    registration: dict[str, Any] | None = None
    pauses: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Filter lists by stable key (see filter_lists.blocker_key); None if the
    # eBlocker did not answer the request.
    blockers: dict[str, dict[str, Any]] | None = None
    # Parental control (None if the request failed)
    users: dict[int, dict[str, Any]] | None = None
    profiles: dict[int, dict[str, Any]] | None = None
    parental_lists: dict[int, dict[str, Any]] | None = None
    usage: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Anonymization (None if the request failed)
    vpn_profiles: dict[int, dict[str, Any]] | None = None
    user_agents: list[str] | None = None
    privacy: dict[str, bool] | None = None
    ssl_enabled: bool | None = None
    # Cloaked user agent name per tracked device ("Off" = not cloaked)
    cloaking: dict[str, str] = field(default_factory=dict)

    @property
    def stats_summary(self) -> dict[str, Any]:
        """Return the summary block of the total statistics."""
        return self.stats.get("summary") or {}


class EBlockerCoordinator(DataUpdateCoordinator[EBlockerData]):
    """Polls the eBlocker REST API."""

    config_entry: EBlockerConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: EBlockerConfigEntry,
        client: EBlockerClient,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        # Device registry id of the eBlocker itself (set during setup).
        self.hub_device_id: str | None = None

    @property
    def tracked_device_ids(self) -> list[str]:
        """Return the device IDs the user selected for per-device entities."""
        return list(self.config_entry.options.get(CONF_TRACKED_DEVICES, []))

    @property
    def profile_ids(self) -> list[int]:
        """Return the parental control profiles that get entities."""
        data = self.data
        if not data or data.profiles is None or data.users is None:
            return []
        return managed_profile_ids(data.profiles, data.users)

    async def _async_update_data(self) -> EBlockerData:
        try:
            return await self._fetch()
        except EBlockerAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EBlockerLoginThrottledError as err:
            raise UpdateFailed(f"Login temporarily blocked: {err}") from err
        except EBlockerConnectionError as err:
            raise UpdateFailed(f"eBlocker not reachable: {err}") from err
        except EBlockerError as err:
            raise UpdateFailed(f"Error from eBlocker: {err}") from err

    async def _fetch(self) -> EBlockerData:
        client = self.client
        (
            system_status,
            device_list,
            stats,
            update_status,
            registration,
            blocker_list,
            user_list,
            profile_list,
            parental_lists,
            vpn_profiles,
            user_agents,
            privacy,
            ssl_enabled,
        ) = await asyncio.gather(
            client.get_system_status(),
            client.get_devices(),
            client.get_total_stats(),
            _optional(client.get_update_status()),
            _optional(client.get_registration()),
            _optional(client.get_blockers()),
            _optional(client.get_users()),
            _optional(client.get_profiles()),
            _optional(client.get_parental_filter_lists()),
            _optional(client.get_vpn_profiles()),
            _optional(client.get_user_agents()),
            _optional(client.get_privacy_settings()),
            _optional(client.get_ssl_enabled()),
        )

        eblocker: dict[str, Any] = {}
        devices: dict[str, dict[str, Any]] = {}
        for device in device_list:
            if device.get("isEblocker"):
                eblocker = device
            elif device.get("id"):
                devices[device["id"]] = device

        tracked = [d for d in self.tracked_device_ids if d in devices]
        pause_results = await asyncio.gather(
            *(_optional(client.get_pause(device_id)) for device_id in tracked)
        )
        cloak_results = await asyncio.gather(
            *(
                _optional(
                    client.get_cloaked_user_agent(
                        device_id, devices[device_id].get("assignedUser")
                    )
                )
                for device_id in tracked
            )
        )
        cloaking = {
            device_id: str(result.get("agentName") or "Off")
            for device_id, result in zip(tracked, cloak_results, strict=True)
            if isinstance(result, dict)
        }

        pauses = {
            device_id: result
            for device_id, result in zip(tracked, pause_results, strict=True)
            if isinstance(result, dict)
        }

        users = (
            None if user_list is None else {u["id"]: u for u in user_list if "id" in u}
        )
        profiles = (
            None
            if profile_list is None
            else {p["id"]: p for p in profile_list if "id" in p}
        )

        # Today's usage only exists for users whose own profile limits the time.
        usage: dict[int, dict[str, Any]] = {}
        if users and profiles:
            limited = [
                owner["id"]
                for pid in managed_profile_ids(profiles, users)
                if profiles[pid].get("controlmodeMaxUsage")
                and (owner := user_profile_owner(profiles[pid], users))
            ]
            results = await asyncio.gather(
                *(_optional(client.get_usage(uid)) for uid in limited)
            )
            usage = {
                uid: result
                for uid, result in zip(limited, results, strict=True)
                if isinstance(result, dict)
            }

        return EBlockerData(
            system_status=system_status or {},
            eblocker=eblocker,
            devices=devices,
            stats=stats or {},
            update_status=update_status,
            registration=registration,
            pauses=pauses,
            blockers=(
                None
                if blocker_list is None
                else {blocker_key(b): b for b in blocker_list if "id" in b}
            ),
            users=users,
            profiles=profiles,
            parental_lists=(
                None
                if parental_lists is None
                else {f["id"]: f for f in parental_lists if "id" in f}
            ),
            usage=usage,
            vpn_profiles=(
                None
                if vpn_profiles is None
                else {p["id"]: p for p in vpn_profiles if "id" in p}
            ),
            user_agents=user_agents,
            privacy=privacy,
            ssl_enabled=ssl_enabled,
            cloaking=cloaking,
        )


async def _optional(coro: Any) -> Any:
    """Await an API call whose failure must not fail the whole update."""
    try:
        return await coro
    except EBlockerApiError as err:
        _LOGGER.debug("Optional eBlocker request failed: %s", err)
        return None
