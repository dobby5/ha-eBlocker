"""Tests for the config entry migration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.eblocker.const import DOMAIN

from .conftest import EBLOCKER_ID


async def test_migration_disables_default_disabled_entities(
    hass: HomeAssistant, mock_client, config_entry
):
    """Entities that became 'disabled by default' are disabled once in
    existing installations; a user's own choice is respected."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, minor_version=1)
    registry = er.async_get(hass)

    def _create(platform: str, key: str, **kwargs) -> er.RegistryEntry:
        return registry.async_get_or_create(
            platform,
            DOMAIN,
            f"{EBLOCKER_ID}_{key}",
            config_entry=config_entry,
            **kwargs,
        )

    # Created enabled by an older version
    sensor = _create("sensor", "filter_lists_enabled")
    webrtc = _create("switch", "webrtc_blocked")
    gambling = _create("switch", "filter_list_builtin_domain_parental_control_gambling")
    # Must stay as they are
    ads = _create("switch", "filter_list_builtin_domain_ads_ads")
    queries = _create("sensor", "dns_queries")
    user_disabled = _create(
        "switch", "dnt_header", disabled_by=er.RegistryEntryDisabler.USER
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    integration = er.RegistryEntryDisabler.INTEGRATION
    assert registry.async_get(sensor.entity_id).disabled_by is integration
    assert registry.async_get(webrtc.entity_id).disabled_by is integration
    assert registry.async_get(gambling.entity_id).disabled_by is integration
    assert registry.async_get(ads.entity_id).disabled_by is None
    assert registry.async_get(queries.entity_id).disabled_by is None
    assert registry.async_get(user_disabled.entity_id).disabled_by is (
        er.RegistryEntryDisabler.USER
    )
    assert config_entry.minor_version == 2


async def test_migration_runs_only_once(hass: HomeAssistant, mock_client, config_entry):
    """Entities the user enables again after the migration stay enabled."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, minor_version=2)
    registry = er.async_get(hass)
    webrtc = registry.async_get_or_create(
        "switch", DOMAIN, f"{EBLOCKER_ID}_webrtc_blocked", config_entry=config_entry
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(webrtc.entity_id).disabled_by is None
