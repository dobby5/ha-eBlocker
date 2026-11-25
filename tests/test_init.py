"""Tests for setup, entities and actions of the eBlocker integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
import pytest

from custom_components.eblocker.api import EBlockerAuthError, EBlockerConnectionError
from custom_components.eblocker.const import DOMAIN

from .conftest import KID_ID, LAPTOP_ID


async def _setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_creates_entities(hass: HomeAssistant, mock_client, config_entry):
    """Hub and tracked device entities are created with correct states."""
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    assert hass.states.get("binary_sensor.my_eblocker_reachable").state == STATE_ON
    assert hass.states.get("sensor.my_eblocker_network_devices").state == "3"
    assert hass.states.get("sensor.my_eblocker_devices_online").state == "2"
    assert hass.states.get("sensor.my_eblocker_protected_devices").state == "2"
    assert hass.states.get("sensor.my_eblocker_dns_queries").state == "1000"
    assert hass.states.get("sensor.my_eblocker_blocked_ads").state == "10"
    assert hass.states.get("sensor.my_eblocker_blocked_trackers").state == "200"
    assert hass.states.get("sensor.my_eblocker_block_rate").state == "25.0"
    assert hass.states.get("sensor.my_eblocker_eblockeros_version").state == "4.0.3"
    assert (
        hass.states.get("binary_sensor.my_eblocker_updates_available").state == STATE_ON
    )
    assert (
        hass.states.get("binary_sensor.my_eblocker_system_problem").state == STATE_OFF
    )

    assert hass.states.get("binary_sensor.laptop_protected").state == STATE_ON
    assert hass.states.get("binary_sensor.laptop_online").state == STATE_ON
    assert hass.states.get("binary_sensor.kids_tablet_online").state == STATE_OFF
    assert hass.states.get("switch.laptop_protection").state == STATE_ON
    assert hass.states.get("switch.laptop_pause").state == STATE_OFF
    assert hass.states.get("switch.laptop_ad_filter").state == STATE_ON
    # Untracked device gets no entities
    assert hass.states.get("switch.printer_protection") is None

    registry = dr.async_get(hass)
    laptop = registry.async_get_device_by_identifier(
        (DOMAIN, LAPTOP_ID), config_entry.entry_id
    )
    assert laptop is not None and laptop.via_device_id is not None


async def test_unreachable_keeps_reachable_sensor(
    hass: HomeAssistant, mock_client, config_entry
):
    """When the eBlocker goes down, 'reachable' turns off, others go unavailable."""
    await _setup(hass, config_entry)
    mock_client.get_system_status.side_effect = EBlockerConnectionError("down")

    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.my_eblocker_reachable").state == STATE_OFF
    assert hass.states.get("sensor.my_eblocker_dns_queries").state == STATE_UNAVAILABLE


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, mock_client, config_entry
):
    """A rejected password puts the entry into reauth."""
    mock_client.get_devices.side_effect = EBlockerAuthError("bad")
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_pause_switch(hass: HomeAssistant, mock_client, config_entry):
    """Turning the pause switch on/off pauses with the default duration."""
    await _setup(hass, config_entry)

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.laptop_pause"}, blocking=True
    )
    mock_client.set_pause.assert_awaited_with(LAPTOP_ID, 15 * 60)

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.laptop_pause"}, blocking=True
    )
    mock_client.set_pause.assert_awaited_with(LAPTOP_ID, 0)


async def test_pause_action_with_duration(
    hass: HomeAssistant, mock_client, config_entry
):
    """The eblocker.pause action accepts any duration."""
    await _setup(hass, config_entry)
    await hass.services.async_call(
        DOMAIN,
        "pause",
        {"entity_id": "switch.laptop_pause", "duration": {"minutes": 45}},
        blocking=True,
    )
    mock_client.set_pause.assert_awaited_with(LAPTOP_ID, 45 * 60)


async def test_pause_not_allowed(hass: HomeAssistant, mock_client, config_entry):
    """Devices with parental control restrictions cannot be paused."""
    await _setup(hass, config_entry)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": "switch.kids_tablet_pause"},
            blocking=True,
        )
    mock_client.set_pause.assert_not_awaited()


async def test_pause_refused_by_eblocker(
    hass: HomeAssistant, mock_client, config_entry
):
    """If the eBlocker answers pausing=0, the action fails visibly."""
    await _setup(hass, config_entry)
    mock_client.set_pause.side_effect = lambda d, s: {
        "pausing": 0,
        "pausingAllowed": False,
    }
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": "switch.laptop_pause"}, blocking=True
        )


async def test_filter_switches(hass: HomeAssistant, mock_client, config_entry):
    """Filter and protection switches write the device attribute."""
    await _setup(hass, config_entry)

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.laptop_ad_filter"}, blocking=True
    )
    mock_client.set_device_attributes.assert_awaited_with(
        LAPTOP_ID, filterAdsEnabled=False
    )

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.laptop_protection"}, blocking=True
    )
    mock_client.set_device_attributes.assert_awaited_with(LAPTOP_ID, enabled=False)
    mock_client.set_pause.assert_not_awaited()


async def test_untracking_removes_device(
    hass: HomeAssistant, mock_client, config_entry
):
    """Removing a device from the options removes it from the registry."""
    await _setup(hass, config_entry)
    registry = dr.async_get(hass)
    assert registry.async_get_device_by_identifier(
        (DOMAIN, KID_ID), config_entry.entry_id
    )

    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, "tracked_devices": [LAPTOP_ID]}
    )
    await hass.async_block_till_done()

    assert (
        registry.async_get_device_by_identifier((DOMAIN, KID_ID), config_entry.entry_id)
        is None
    )
    assert hass.states.get("switch.kids_tablet_pause") is None


async def test_unload(hass: HomeAssistant, mock_client, config_entry):
    """The entry unloads cleanly."""
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_diagnostics_redacts_secrets(
    hass: HomeAssistant, mock_client, config_entry
):
    """Diagnostics never contain the password or network addresses."""
    from custom_components.eblocker.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    await _setup(hass, config_entry)
    diag = await async_get_config_entry_diagnostics(hass, config_entry)
    text = str(diag)
    assert "secret" not in text
    assert "192.0.2.20" not in text
    assert diag["data"]["stats"]["summary"]["queries"] == 1000
