"""Update entity for eBlockerOS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import EBlockerConfigEntry, EBlockerCoordinator
from .entity import EBlockerEntity, run_action

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EBlockerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the eBlockerOS update entity."""
    async_add_entities([EBlockerUpdateEntity(entry.runtime_data)])


class EBlockerUpdateEntity(EBlockerEntity, UpdateEntity):
    """Installs eBlockerOS and filter list updates.

    The eBlocker only reports *whether* updates are available and which
    packages are affected, not the version it would update to. The latest
    version therefore shows the updatable packages.
    """

    _attr_translation_key = "eblockeros"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )

    def __init__(self, coordinator: EBlockerCoordinator) -> None:
        super().__init__(coordinator, "update")

    @property
    def _status(self) -> dict[str, Any]:
        return self.coordinator.data.update_status or {}

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.update_status is not None

    @property
    def installed_version(self) -> str | None:
        return self._status.get(
            "projectVersion"
        ) or self.coordinator.data.system_status.get("projectVersion")

    @property
    def latest_version(self) -> str | None:
        installed = self.installed_version
        if not self._status.get("updatesAvailable"):
            return installed
        packages = self._status.get("updateablePackages") or []
        return f"{installed} + {', '.join(packages)}" if packages else f"{installed} +"

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """The eBlocker decides itself whether an update is pending."""
        return bool(self._status.get("updatesAvailable"))

    @property
    def release_summary(self) -> str | None:
        packages = self._status.get("updateablePackages") or []
        if not self._status.get("updatesAvailable") or not packages:
            return None
        return ", ".join(packages)

    @property
    def in_progress(self) -> bool:
        status = self._status
        return bool(
            status.get("updating")
            or status.get("downloading")
            or status.get("recovering")
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self._status
        return {
            "lists_version": status.get("listsPacketVersion"),
            "checking": status.get("checking"),
            "last_update_attempt_failed": status.get("lastUpdateAttemptFailed"),
            "update_progress": (status.get("updateProgress") or [])[-5:],
        }

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Start the update on the eBlocker."""
        if self._status.get("disabled"):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="updates_disabled"
            )
        result = await run_action(
            self.coordinator, self.coordinator.client.start_update()
        )
        if (
            isinstance(result, dict)
            and not result.get("updating")
            and not self.in_progress
        ):
            # The eBlocker only starts an update when it is idle.
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="update_not_started"
            )
