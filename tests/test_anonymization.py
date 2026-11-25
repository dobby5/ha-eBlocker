"""Tests for per-device anonymization (Tor/VPN, browser cloaking) and the
global privacy settings."""

from __future__ import annotations

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.eblocker.api import USER_AGENT

from .conftest import CHILD_ID, KID_ID, LAPTOP_ID


async def _setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _select(hass: HomeAssistant, entity_id: str, option: str) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id, "option": option},
        blocking=True,
    )


async def test_ip_anonymization_states(hass: HomeAssistant, mock_client, config_entry):
    """Current anonymization comes from the device flags; deleted VPNs hidden."""
    await _setup(hass, config_entry)

    laptop = hass.states.get("select.laptop_ip_anonymization")
    assert laptop.state == "off"
    assert laptop.attributes["options"] == ["off", "tor", "Home VPN"]
    assert hass.states.get("select.kids_tablet_ip_anonymization").state == "tor"


async def test_enable_tor(hass: HomeAssistant, mock_client, config_entry):
    """Selecting Tor routes the device through Tor."""
    await _setup(hass, config_entry)
    await _select(hass, "select.laptop_ip_anonymization", "tor")
    mock_client.set_tor.assert_awaited_once_with(LAPTOP_ID, True)
    mock_client.set_vpn.assert_not_awaited()


async def test_switch_tor_to_vpn(hass: HomeAssistant, mock_client, config_entry):
    """Tor and VPN exclude each other: Tor is turned off before VPN starts."""
    await _setup(hass, config_entry)
    await _select(hass, "select.kids_tablet_ip_anonymization", "Home VPN")
    mock_client.set_tor.assert_awaited_once_with(KID_ID, False)
    mock_client.set_vpn.assert_awaited_once_with(2, KID_ID, True)


async def test_disable_anonymization(hass: HomeAssistant, mock_client, config_entry):
    """'Off' stops Tor."""
    await _setup(hass, config_entry)
    await _select(hass, "select.kids_tablet_ip_anonymization", "off")
    mock_client.set_tor.assert_awaited_once_with(KID_ID, False)
    mock_client.set_vpn.assert_not_awaited()


async def test_vpn_to_off(hass: HomeAssistant, mock_client, config_entry):
    """An active VPN is shown by name and can be switched off."""
    devices = await mock_client.get_devices()
    for device in devices:
        if device["id"] == LAPTOP_ID:
            device.update(useAnonymizationService=True, useVPNProfileID=2)
    mock_client.get_devices.side_effect = None
    mock_client.get_devices.return_value = devices
    await _setup(hass, config_entry)

    assert hass.states.get("select.laptop_ip_anonymization").state == "Home VPN"
    await _select(hass, "select.laptop_ip_anonymization", "off")
    mock_client.set_vpn.assert_awaited_once_with(2, LAPTOP_ID, False)
    mock_client.set_tor.assert_not_awaited()


async def test_anonymization_needs_protection(
    hass: HomeAssistant, mock_client, config_entry
):
    """Devices not protected by the eBlocker cannot be anonymized."""
    devices = await mock_client.get_devices()
    for device in devices:
        if device["id"] == LAPTOP_ID:
            device["enabled"] = False
    mock_client.get_devices.side_effect = None
    mock_client.get_devices.return_value = devices
    await _setup(hass, config_entry)
    with pytest.raises(ServiceValidationError):
        await _select(hass, "select.laptop_ip_anonymization", "tor")
    mock_client.set_tor.assert_not_awaited()


async def test_browser_cloaking(hass: HomeAssistant, mock_client, config_entry):
    """Cloaking is set per device; it needs HTTPS support for the device."""
    await _setup(hass, config_entry)

    laptop = hass.states.get("select.laptop_browser_cloaking")
    assert laptop.state == "Mac"
    assert laptop.attributes["options"] == ["off", "iPhone", "PC (Linux)", "Mac"]
    # HTTPS support is off for the tablet -> cloaking has no effect
    assert (
        hass.states.get("select.kids_tablet_browser_cloaking").state
        == STATE_UNAVAILABLE
    )

    await _select(hass, "select.laptop_browser_cloaking", "iPhone")
    mock_client.set_cloaked_user_agent.assert_awaited_with(LAPTOP_ID, 113, "iPhone")
    await _select(hass, "select.laptop_browser_cloaking", "off")
    mock_client.set_cloaked_user_agent.assert_awaited_with(LAPTOP_ID, 113, "Off")
    # The assigned user is passed along (here: the child for the tablet)
    mock_client.get_cloaked_user_agent.assert_any_await(KID_ID, CHILD_ID)


async def test_privacy_switches(hass: HomeAssistant, mock_client, config_entry):
    """Global WebRTC / referrer / Do-Not-Track switches (disabled by default)."""
    await _setup(hass, config_entry)
    registry = er.async_get(hass)
    privacy = (
        "switch.my_eblocker_block_webrtc",
        "switch.my_eblocker_remove_referrer",
        "switch.my_eblocker_send_do_not_track",
    )
    for entity_id in privacy:
        assert registry.async_get(entity_id).disabled_by is (
            er.RegistryEntryDisabler.INTEGRATION
        )
        assert hass.states.get(entity_id) is None

    # After the user enables them they work
    for entity_id in privacy:
        registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("switch.my_eblocker_block_webrtc").state == STATE_OFF
    assert hass.states.get("switch.my_eblocker_remove_referrer").state == STATE_OFF
    assert hass.states.get("switch.my_eblocker_send_do_not_track").state == STATE_ON

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.my_eblocker_block_webrtc"},
        blocking=True,
    )
    mock_client.set_privacy_setting.assert_awaited_once_with("webrtc_blocked", True)


async def test_new_tor_identity(hass: HomeAssistant, mock_client, config_entry):
    """The button requests a new Tor identity."""
    await _setup(hass, config_entry)
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.my_eblocker_new_tor_identity"},
        blocking=True,
    )
    mock_client.new_tor_identity.assert_awaited_once()


async def test_client_sends_own_user_agent():
    """A dedicated user agent keeps cloaking changes off HA's own traffic."""
    assert USER_AGENT == "HomeAssistant-eBlocker-Integration"
