"""Tests for parental control (profiles, users, usage, actions)."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
import pytest

from custom_components.eblocker.const import DOMAIN

from .conftest import CHILD_ID, CHILD_PROFILE_ID, EBLOCKER_ID, KID_ID, LAPTOP_ID

LISA = f"{EBLOCKER_ID}_profile_{CHILD_PROFILE_ID}"
STANDARD = f"{EBLOCKER_ID}_profile_1"


async def _setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _device_id(hass: HomeAssistant, entry, identifier: str) -> str:
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, identifier), entry.entry_id
    )
    assert device is not None
    return device.id


async def test_profile_entities(hass: HomeAssistant, mock_client, config_entry):
    """The standard profile and each user's profile get entities."""
    await _setup(hass, config_entry)

    # Standard profile (used by all system users)
    assert hass.states.get("switch.standard_profile_web_filter").state == STATE_ON
    assert (
        hass.states.get("switch.standard_profile_internet_blocked").state == STATE_OFF
    )
    web_filter = hass.states.get("switch.standard_profile_web_filter")
    assert web_filter.attributes["blocked_lists"] == [
        "Inappropriate Content",
        "Gambling",
    ]
    assert hass.states.get("sensor.standard_profile_assigned_devices").state == "2"
    # No usage sensors for the standard profile (it belongs to no user)
    assert hass.states.get("sensor.standard_profile_remaining_time_today") is None

    # Personal profile of user "Lisa"
    assert hass.states.get("switch.lisa_daily_time_limit").state == STATE_ON
    limit = hass.states.get("switch.lisa_daily_time_limit")
    assert limit.attributes["monday_minutes"] == 60
    assert limit.attributes["saturday_minutes"] == 120
    remaining = hass.states.get("sensor.lisa_remaining_time_today")
    assert float(remaining.state) == 30.0
    assert remaining.attributes["limit_today_minutes"] == 60.0
    assert float(hass.states.get("sensor.lisa_used_time_today").state) == 30.0
    assert hass.states.get("sensor.lisa_bonus_time_today").state == "15"
    assigned = hass.states.get("sensor.lisa_assigned_devices")
    assert assigned.state == "1"
    assert assigned.attributes["devices"] == ["Kids tablet"]

    # Usage is only requested for the user with a time limit
    mock_client.get_usage.assert_awaited_with(CHILD_ID)
    # Hidden and unused built-in profiles get no entities
    assert hass.states.get("switch.children_profile_with_usage_time_web_filter") is None


async def test_internet_block_switch(hass: HomeAssistant, mock_client, config_entry):
    """Blocking the internet writes internetBlocked on the profile."""
    await _setup(hass, config_entry)
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.lisa_internet_blocked"},
        blocking=True,
    )
    mock_client.set_profile_attributes.assert_awaited_once_with(
        CHILD_PROFILE_ID, internetBlocked=True
    )


async def test_device_user_select(hass: HomeAssistant, mock_client, config_entry):
    """Devices can be assigned to users and back."""
    await _setup(hass, config_entry)
    laptop = hass.states.get("select.laptop_user")
    assert laptop.state == "unassigned"
    assert laptop.attributes["options"] == ["unassigned", "Lisa"]
    assert hass.states.get("select.kids_tablet_user").state == "Lisa"

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.laptop_user", "option": "Lisa"},
        blocking=True,
    )
    mock_client.set_device_attributes.assert_awaited_with(
        LAPTOP_ID, assignedUser=CHILD_ID, operatingUser=CHILD_ID
    )

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.kids_tablet_user", "option": "unassigned"},
        blocking=True,
    )
    # back to the device's own system user
    mock_client.set_device_attributes.assert_awaited_with(
        KID_ID, assignedUser=114, operatingUser=114
    )


async def test_bonus_time(hass: HomeAssistant, mock_client, config_entry):
    """Bonus time targets the profile device."""
    await _setup(hass, config_entry)
    device_id = _device_id(hass, config_entry, LISA)
    await hass.services.async_call(
        DOMAIN,
        "add_bonus_time",
        {"device_id": device_id, "minutes": 30},
        blocking=True,
    )
    mock_client.add_bonus_time.assert_awaited_once_with(CHILD_PROFILE_ID, 30)

    await hass.services.async_call(
        DOMAIN, "reset_bonus_time", {"device_id": device_id}, blocking=True
    )
    mock_client.reset_bonus_time.assert_awaited_once_with(CHILD_PROFILE_ID)


async def test_action_needs_profile_device(
    hass: HomeAssistant, mock_client, config_entry
):
    """Network devices are rejected as targets of profile actions."""
    await _setup(hass, config_entry)
    laptop = _device_id(hass, config_entry, LAPTOP_ID)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "add_bonus_time",
            {"device_id": laptop, "minutes": 10},
            blocking=True,
        )


async def test_set_usage_limit(hass: HomeAssistant, mock_client, config_entry):
    """The limit is set for the given days, others keep their value."""
    await _setup(hass, config_entry)
    await hass.services.async_call(
        DOMAIN,
        "set_usage_limit",
        {
            "device_id": _device_id(hass, config_entry, LISA),
            "minutes": 90,
            "days": ["monday", "tuesday"],
        },
        blocking=True,
    )
    written = mock_client.update_profile.await_args.args[0]
    assert written["id"] == CHILD_PROFILE_ID
    assert written["maxUsageTimeByDay"]["MONDAY"] == 90
    assert written["maxUsageTimeByDay"]["TUESDAY"] == 90
    assert written["maxUsageTimeByDay"]["SATURDAY"] == 120
    assert written["maxUsageTimeByDay"]["SUNDAY"] == 60
    assert written["controlmodeMaxUsage"] is True


async def test_set_online_times(hass: HomeAssistant, mock_client, config_entry):
    """Online time windows are converted to eBlocker contingents."""
    await _setup(hass, config_entry)
    await hass.services.async_call(
        DOMAIN,
        "set_online_times",
        {
            "device_id": _device_id(hass, config_entry, LISA),
            "times": [
                {"day": "weekdays", "start": "07:00", "end": "20:00"},
                {"day": "saturday", "start": "09:30", "end": "00:00"},
            ],
        },
        blocking=True,
    )
    mock_client.set_profile_attributes.assert_awaited_once_with(
        CHILD_PROFILE_ID,
        internetAccessContingents=[
            {"onDay": 8, "fromMinutes": 420, "tillMinutes": 1200, "totalMinutes": 780},
            {"onDay": 6, "fromMinutes": 570, "tillMinutes": 1440, "totalMinutes": 870},
        ],
        controlmodeTime=True,
    )


async def test_set_online_times_invalid(hass: HomeAssistant, mock_client, config_entry):
    """End before start is rejected."""
    await _setup(hass, config_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_online_times",
            {
                "device_id": _device_id(hass, config_entry, LISA),
                "times": [{"day": "monday", "start": "20:00", "end": "07:00"}],
            },
            blocking=True,
        )


async def test_set_blocked_lists(hass: HomeAssistant, mock_client, config_entry):
    """Lists can be given by German/English name or custom name."""
    await _setup(hass, config_entry)
    await hass.services.async_call(
        DOMAIN,
        "set_blocked_lists",
        {
            "device_id": _device_id(hass, config_entry, STANDARD),
            "filter_lists": ["Glücksspiel", "social networks", "Porn"],
        },
        blocking=True,
    )
    written = mock_client.update_profile.await_args.args[0]
    assert written["id"] == 1
    assert written["inaccessibleSitesPackages"] == [2, 3, 1002]
    assert written["controlmodeUrls"] is True


async def test_set_blocked_lists_empty_disables_filter(
    hass: HomeAssistant, mock_client, config_entry
):
    """Like the eBlocker UI: no category left -> web filter off."""
    await _setup(hass, config_entry)
    await hass.services.async_call(
        DOMAIN,
        "set_blocked_lists",
        {"device_id": _device_id(hass, config_entry, STANDARD), "filter_lists": []},
        blocking=True,
    )
    written = mock_client.update_profile.await_args.args[0]
    assert written["inaccessibleSitesPackages"] == []
    assert written["controlmodeUrls"] is False


async def test_set_blocked_lists_unknown(
    hass: HomeAssistant, mock_client, config_entry
):
    """Unknown names fail and list the available ones; ad lists are not offered."""
    await _setup(hass, config_entry)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "set_blocked_lists",
            {
                "device_id": _device_id(hass, config_entry, STANDARD),
                "filter_lists": ["Ads"],
            },
            blocking=True,
        )
    assert err.value.translation_placeholders["lists"] == "Ads"
    assert "Gambling" in err.value.translation_placeholders["available"]
    mock_client.update_profile.assert_not_awaited()


async def test_category_switches(hass: HomeAssistant, mock_client, config_entry):
    """Every parental control list is a switch on each profile (like the UI)."""
    await _setup(hass, config_entry)

    assert hass.states.get("switch.standard_profile_block_gambling").state == STATE_ON
    assert (
        hass.states.get("switch.standard_profile_block_inappropriate_content").state
        == STATE_ON
    )
    assert (
        hass.states.get("switch.standard_profile_block_social_networks").state
        == STATE_OFF
    )
    assert hass.states.get("switch.standard_profile_block_porn").state == STATE_OFF
    # Allow lists (whitelists) are offered as "allow"
    assert hass.states.get("switch.standard_profile_allow_fragfinn").state == STATE_OFF
    # Ad lists are not parental control categories
    assert hass.states.get("switch.standard_profile_block_ads") is None
    # Same categories for the user's own profile
    assert hass.states.get("switch.lisa_block_gambling").state == STATE_OFF

    gambling = hass.states.get("switch.standard_profile_block_gambling")
    assert gambling.attributes["role"] == "rule"
    fragfinn = hass.states.get("switch.standard_profile_allow_fragfinn")
    assert fragfinn.attributes["role"] == "exception"


async def test_category_switch_unavailable_when_globally_off(
    hass: HomeAssistant, mock_client, config_entry
):
    """A list switched off on the eBlocker has no effect for any profile."""
    await _setup(hass, config_entry)
    assert hass.states.get("switch.standard_profile_block_music").state == (
        STATE_UNAVAILABLE
    )
    assert hass.states.get("switch.lisa_block_music").state == STATE_UNAVAILABLE


async def test_category_switch_on_enables_web_filter(
    hass: HomeAssistant, mock_client, config_entry
):
    """Blocking a category adds it and turns the web filter on."""
    await _setup(hass, config_entry)
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.lisa_block_social_networks"},
        blocking=True,
    )
    written = mock_client.update_profile.await_args.args[0]
    assert written["id"] == CHILD_PROFILE_ID
    assert written["inaccessibleSitesPackages"] == [3]
    assert written["controlmodeUrls"] is True


async def test_category_switch_off_last_disables_web_filter(
    hass: HomeAssistant, mock_client, config_entry
):
    """Removing the last category turns the web filter off (UI behaviour)."""
    for profile in mock_client.profiles:
        if profile["id"] == CHILD_PROFILE_ID:
            profile["inaccessibleSitesPackages"] = [2]
            profile["controlmodeUrls"] = True
    await _setup(hass, config_entry)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.lisa_block_gambling"}, blocking=True
    )
    written = mock_client.update_profile.await_args.args[0]
    assert written["inaccessibleSitesPackages"] == []
    assert written["controlmodeUrls"] is False


async def test_category_switch_keeps_other_lists(
    hass: HomeAssistant, mock_client, config_entry
):
    """Turning one category off keeps the others and the web filter."""
    await _setup(hass, config_entry)
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.standard_profile_block_gambling"},
        blocking=True,
    )
    written = mock_client.update_profile.await_args.args[0]
    assert written["inaccessibleSitesPackages"] == [0]
    assert written["controlmodeUrls"] is True


async def test_new_parental_list_gets_switches(
    hass: HomeAssistant, mock_client, config_entry
):
    """A new category (e.g. a custom list) appears without reload."""
    await _setup(hass, config_entry)
    lists = await mock_client.get_parental_filter_lists()
    lists.append(
        {
            "id": 1005,
            "name": None,
            "customerCreatedName": "Streaming",
            "filterType": "blacklist",
            "builtin": False,
            "category": "PARENTAL_CONTROL",
        }
    )
    mock_client.get_parental_filter_lists.return_value = lists
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("switch.lisa_block_streaming").state == STATE_OFF
    assert hass.states.get("switch.standard_profile_block_streaming")


async def test_web_filter_mode_select(hass: HomeAssistant, mock_client, config_entry):
    """The mode switches between blocking categories and allow lists only."""
    await _setup(hass, config_entry)
    entity_id = "select.lisa_web_filter_mode"
    assert hass.states.get(entity_id).state == "block_listed"
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id, "option": "allow_listed_only"},
        blocking=True,
    )
    mock_client.set_profile_attributes.assert_awaited_once_with(
        CHILD_PROFILE_ID, internetAccessRestrictionMode=2
    )


async def test_create_child_user(hass: HomeAssistant, mock_client, config_entry):
    """A child gets an own profile with the preset for its age (UI behaviour)."""
    await _setup(hass, config_entry)
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        response = await hass.services.async_call(
            DOMAIN,
            "create_user",
            {"name": "Max", "role": "child", "birthday": date(2017, 5, 1)},
            blocking=True,
            return_response=True,
        )
    assert response == {"user_id": 300, "profile_id": 301}

    user = mock_client.create_user.await_args.args[0]
    assert user["name"] == "Max"
    assert user["userRole"] == "CHILD"
    assert user["birthday"] == [2017, 5, 1]

    profile = mock_client.create_profile.await_args.args[0]
    assert profile["name"] == "PROFILE_FOR_USER_300"
    assert profile["builtin"] is False
    # 9 years old -> "children profile with usage time" preset (id 5)
    assert profile["controlmodeMaxUsage"] is True
    assert profile["inaccessibleSitesPackages"] == [0, 2, 3]

    updated = mock_client.update_user.await_args.args[0]
    assert updated["id"] == 300
    assert updated["associatedProfileId"] == 301
    reload.assert_called_once_with(config_entry.entry_id)


async def test_create_user_duplicate_name(
    hass: HomeAssistant, mock_client, config_entry
):
    """User names must be unique."""
    await _setup(hass, config_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "create_user", {"name": "lisa"}, blocking=True, return_response=True
        )
    mock_client.create_user.assert_not_awaited()


async def test_delete_user(hass: HomeAssistant, mock_client, config_entry):
    """Deleting a user frees its devices and removes user and profile."""
    await _setup(hass, config_entry)
    with patch.object(hass.config_entries, "async_schedule_reload"):
        await hass.services.async_call(
            DOMAIN,
            "delete_user",
            {"device_id": _device_id(hass, config_entry, LISA)},
            blocking=True,
        )
    mock_client.set_device_attributes.assert_awaited_once_with(
        KID_ID, assignedUser=114, operatingUser=114
    )
    mock_client.delete_user.assert_awaited_once_with(CHILD_ID)
    mock_client.delete_profile.assert_awaited_once_with(CHILD_PROFILE_ID)


async def test_standard_profile_cannot_be_deleted(
    hass: HomeAssistant, mock_client, config_entry
):
    """The standard profile is not a user."""
    await _setup(hass, config_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "delete_user",
            {"device_id": _device_id(hass, config_entry, STANDARD)},
            blocking=True,
        )
    mock_client.delete_user.assert_not_awaited()
