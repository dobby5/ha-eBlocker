"""Buttons for the eBlocker integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import EBlockerApiError
from .const import DOMAIN
from .coordinator import EBlockerConfigEntry, EBlockerCoordinator
from .entity import EBlockerEntity, run_action

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EBlockerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up eBlocker buttons."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            EBlockerCheckUpdatesButton(coordinator),
            EBlockerNewTorIdentityButton(coordinator),
        ]
    )


class EBlockerNewTorIdentityButton(EBlockerEntity, ButtonEntity):
    """Gets new Tor circuits, i.e. a new exit IP for all Tor devices."""

    _attr_translation_key = "new_tor_identity"

    def __init__(self, coordinator: EBlockerCoordinator) -> None:
        super().__init__(coordinator, "new_tor_identity")

    async def async_press(self) -> None:
        await run_action(self.coordinator, self.coordinator.client.new_tor_identity())


class EBlockerCheckUpdatesButton(EBlockerEntity, ButtonEntity):
    """Makes the eBlocker look for new updates (allowed once per minute)."""

    _attr_translation_key = "check_updates"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EBlockerCoordinator) -> None:
        super().__init__(coordinator, "check_updates")

    async def async_press(self) -> None:
        client = self.coordinator.client

        async def _check() -> None:
            try:
                await client.check_for_updates()
            except EBlockerApiError as err:
                if err.status == 400:
                    # eBlocker: "Requests too frequent" (limit: once per minute)
                    raise HomeAssistantError(
                        translation_domain=DOMAIN,
                        translation_key="check_too_frequent",
                    ) from err
                raise

        await run_action(self.coordinator, _check())
