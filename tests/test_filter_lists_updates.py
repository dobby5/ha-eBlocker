"""Tests for filter list management and eBlockerOS updates."""

from __future__ import annotations

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.eblocker.api import EBlockerApiError
from custom_components.eblocker.const import DOMAIN

ADS = "switch.my_eblocker_filter_list_ads"
TRACKERS = "switch.my_eblocker_filter_list_trackers"
CUSTOM_URL = "switch.my_eblocker_filter_list_porn"
CUSTOM_DOMAINS = "switch.my_eblocker_filter_list_my_domains"
UPDATE = "update.my_eblocker_eblockeros"


async def _setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_filter_list_entities(hass: HomeAssistant, mock_client, config_entry):
    """Every filter list gets a switch; duplicate names are disambiguated."""
    await _setup(hass, config_entry)

    assert hass.states.get(ADS).state == STATE_ON
    assert hass.states.get(TRACKERS).state == STATE_OFF
    assert hass.states.get("switch.my_eblocker_filter_list_malware_domain")
    assert hass.states.get("switch.my_eblocker_filter_list_malware_pattern")
    porn = hass.states.get(CUSTOM_URL)
    assert porn.attributes["custom"] is True
    assert porn.attributes["url"] == "https://nsfw.example.org"
    assert porn.attributes["category"] == "parental_control"
    # "Active filter lists" is disabled by default
    sensor = er.async_get(hass).async_get("sensor.my_eblocker_active_filter_lists")
    assert sensor.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_active_filter_lists_sensor(
    hass: HomeAssistant, mock_client, config_entry
):
    """Once enabled, the sensor counts the enabled filter lists."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, minor_version=2)
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{config_entry.unique_id}_filter_lists_enabled",
        config_entry=config_entry,
        suggested_object_id="my_eblocker_active_filter_lists",
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.my_eblocker_active_filter_lists").state == "6"


async def test_builtin_parental_lists_disabled_by_default(
    hass: HomeAssistant, mock_client, config_entry
):
    """Built-in parental control lists are chosen per profile; their global
    switch on the eBlocker starts disabled. Custom lists stay enabled."""
    await _setup(hass, config_entry)
    registry = er.async_get(hass)

    gambling = registry.async_get("switch.my_eblocker_filter_list_gambling")
    assert gambling is not None
    assert gambling.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get("switch.my_eblocker_filter_list_gambling") is None

    porn = registry.async_get(CUSTOM_URL)
    assert porn.disabled_by is None
    assert registry.async_get(ADS).disabled_by is None


async def test_toggle_builtin_filter_list(
    hass: HomeAssistant, mock_client, config_entry
):
    """Turning a list off writes the fresh list object with enabled=False."""
    await _setup(hass, config_entry)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": ADS}, blocking=True
    )
    written = mock_client.update_blocker.await_args.args[0]
    assert written["id"] == 11
    assert written["enabled"] is False
    assert written["name"] == {"de": "Ads", "en": "Ads"}


async def test_toggle_uses_current_id(hass: HomeAssistant, mock_client, config_entry):
    """Built-in IDs can change after an eBlocker restart; the key stays stable."""
    await _setup(hass, config_entry)
    for blocker in mock_client.blockers:
        if blocker["id"] == 11:
            blocker["id"] = 7
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": ADS}, blocking=True
    )
    assert mock_client.update_blocker.await_args.args[0]["id"] == 7


async def test_custom_list_without_url_is_read_only(
    hass: HomeAssistant, mock_client, config_entry
):
    """eBlockerOS cannot write back domain-only custom lists."""
    await _setup(hass, config_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": CUSTOM_DOMAINS}, blocking=True
        )
    mock_client.update_blocker.assert_not_awaited()


async def test_add_filter_list(hass: HomeAssistant, mock_client, config_entry):
    """add_filter_list creates the list and a new switch appears."""
    await _setup(hass, config_entry)

    def _created(blocker):
        created = {**blocker, "id": 1004, "updateStatus": "NEW"}
        mock_client.blockers.append(created)
        return created

    mock_client.create_blocker.side_effect = _created
    response = await hass.services.async_call(
        DOMAIN,
        "add_filter_list",
        {
            "name": "Social",
            "category": "parental_control",
            "url": "https://lists.example.org/social.txt",
        },
        blocking=True,
        return_response=True,
    )
    assert response == {"filter_list_id": 1004, "update_status": "new"}
    payload = mock_client.create_blocker.await_args.args[0]
    assert payload["type"] == "DOMAIN"
    assert payload["format"] == "DOMAINS"
    assert payload["category"] == "PARENTAL_CONTROL"
    assert payload["url"] == "https://lists.example.org/social.txt"
    assert payload["updateInterval"] == "DAILY"
    assert payload["filterType"] == "blacklist"

    await hass.async_block_till_done()
    assert hass.states.get("switch.my_eblocker_filter_list_social")


async def test_add_filter_list_with_domains(
    hass: HomeAssistant, mock_client, config_entry
):
    """Domain lists are sent as content, one domain per line."""
    await _setup(hass, config_entry)
    await hass.services.async_call(
        DOMAIN,
        "add_filter_list",
        {
            "name": "Mine",
            "category": "ads",
            "domains": ["ads.example.com", " track.example.com "],
        },
        blocking=True,
    )
    payload = mock_client.create_blocker.await_args.args[0]
    assert payload["content"] == "ads.example.com\ntrack.example.com"
    assert payload["updateInterval"] == "NEVER"
    assert "url" not in payload


@pytest.mark.parametrize(
    "data",
    [
        {"name": "x", "category": "ads"},
        {
            "name": "x",
            "category": "ads",
            "url": "https://a.example.org",
            "domains": ["b.example.org"],
        },
    ],
)
async def test_add_filter_list_needs_url_xor_domains(
    hass: HomeAssistant, mock_client, config_entry, data
):
    """Exactly one of URL and domains must be given."""
    await _setup(hass, config_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "add_filter_list", data, blocking=True)
    mock_client.create_blocker.assert_not_awaited()


async def test_update_custom_filter_list(
    hass: HomeAssistant, mock_client, config_entry
):
    """update_filter_list changes URL and interval of a custom list."""
    await _setup(hass, config_entry)
    await hass.services.async_call(
        DOMAIN,
        "update_filter_list",
        {
            "entity_id": CUSTOM_URL,
            "url": "https://other.example.org/list.txt",
            "update_interval": "never",
        },
        blocking=True,
    )
    written = mock_client.update_blocker.await_args.args[0]
    assert written["id"] == 1002
    assert written["url"] == "https://other.example.org/list.txt"
    assert written["updateInterval"] == "NEVER"


async def test_remove_filter_list(hass: HomeAssistant, mock_client, config_entry):
    """Removing a custom list deletes it and its switch."""
    await _setup(hass, config_entry)

    async def _delete(blocker_id):
        mock_client.blockers = [
            b for b in mock_client.blockers if b["id"] != blocker_id
        ]

    mock_client.delete_blocker.side_effect = _delete
    await hass.services.async_call(
        DOMAIN, "remove_filter_list", {"entity_id": CUSTOM_URL}, blocking=True
    )
    mock_client.delete_blocker.assert_awaited_once_with(1002)
    await hass.async_block_till_done()
    assert hass.states.get(CUSTOM_URL) is None


async def test_builtin_lists_cannot_be_removed(
    hass: HomeAssistant, mock_client, config_entry
):
    """Built-in lists can only be toggled."""
    await _setup(hass, config_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "remove_filter_list", {"entity_id": ADS}, blocking=True
        )
    mock_client.delete_blocker.assert_not_awaited()


async def test_filter_list_action_on_other_switch(
    hass: HomeAssistant, mock_client, config_entry
):
    """Filter list actions on other switches fail with a clear error."""
    await _setup(hass, config_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "remove_filter_list",
            {"entity_id": "switch.laptop_protection"},
            blocking=True,
        )


async def test_update_entity(hass: HomeAssistant, mock_client, config_entry):
    """The update entity reflects the eBlocker status and installs updates."""
    await _setup(hass, config_entry)
    state = hass.states.get(UPDATE)
    assert state.state == STATE_ON
    assert state.attributes["installed_version"] == "4.0.3"
    assert state.attributes["latest_version"] == "4.0.3 + eblocker-lists"
    assert state.attributes["release_summary"] == "eblocker-lists"

    await hass.services.async_call(
        "update", "install", {"entity_id": UPDATE}, blocking=True
    )
    mock_client.start_update.assert_awaited_once()


async def test_update_entity_no_updates(hass: HomeAssistant, mock_client, config_entry):
    """Without pending updates the entity is off."""
    status = await mock_client.get_update_status()
    status["updatesAvailable"] = False
    mock_client.get_update_status.return_value = status
    await _setup(hass, config_entry)
    state = hass.states.get(UPDATE)
    assert state.state == STATE_OFF
    assert state.attributes["latest_version"] == "4.0.3"


async def test_update_not_started(hass: HomeAssistant, mock_client, config_entry):
    """If the eBlocker is busy and does not start, the user is told."""
    await _setup(hass, config_entry)
    mock_client.start_update.return_value = {"updating": False}
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "update", "install", {"entity_id": UPDATE}, blocking=True
        )


async def test_check_updates_button(hass: HomeAssistant, mock_client, config_entry):
    """The button asks the eBlocker to check; too frequent requests fail nicely."""
    await _setup(hass, config_entry)
    entity_id = "button.my_eblocker_check_for_updates"
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )
    mock_client.check_for_updates.assert_awaited_once()

    mock_client.check_for_updates.side_effect = EBlockerApiError(
        400, "Requests too frequent"
    )
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "button", "press", {"entity_id": entity_id}, blocking=True
        )
    assert err.value.translation_key == "check_too_frequent"


async def test_automatic_updates_switch(hass: HomeAssistant, mock_client, config_entry):
    """The automatic updates switch maps to automaticUpdatesActivated."""
    await _setup(hass, config_entry)
    entity_id = "switch.my_eblocker_automatic_updates"
    assert hass.states.get(entity_id).state == STATE_ON
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity_id}, blocking=True
    )
    mock_client.set_automatic_updates.assert_awaited_once_with(False)
