"""Fixtures for eBlocker tests."""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SCAN_INTERVAL
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eblocker.const import (
    CONF_PAUSE_MINUTES,
    CONF_TRACKED_DEVICES,
    DOMAIN,
)

HOST = "192.0.2.10"
EBLOCKER_ID = "device:525400000001"
LAPTOP_ID = "device:aabbcc000001"
KID_ID = "device:aabbcc000002"
CHILD_ID = 200
CHILD_PROFILE_ID = 201

SYSTEM_STATUS: dict[str, Any] = {
    "executionState": "RUNNING",
    "warnings": [],
    "subSystemDetails": [
        {"name": "HTTP_SERVER", "status": "OK"},
        {"name": "ICAP_SERVER", "status": "OK"},
    ],
    "projectVersion": "4.0.3",
}


def _device(device_id: str, name: str, ip: str, **extra: Any) -> dict[str, Any]:
    device = {
        "id": device_id,
        "ipAddresses": [ip],
        "enabled": True,
        "paused": False,
        "useAnonymizationService": False,
        "routeThroughTor": False,
        "filterMode": "AUTOMATIC",
        "filterAdsEnabled": True,
        "filterTrackersEnabled": True,
        "malwareFilterEnabled": True,
        "name": name,
        "vendor": "ACME",
        "isOnline": True,
        "isEblocker": False,
        "operatingUser": 113,
        "assignedUser": 113,
        "defaultSystemUser": 113,
        "hardwareAddress": "aa:bb:cc:"
        + device_id[-6:-4]
        + ":"
        + device_id[-4:-2]
        + ":"
        + device_id[-2:],
    }
    device.update(extra)
    return device


DEVICES: list[dict[str, Any]] = [
    _device(LAPTOP_ID, "Laptop", "192.0.2.20", sslEnabled=True),
    _device(
        KID_ID,
        "Kids tablet",
        "192.0.2.21",
        isOnline=False,
        useAnonymizationService=True,
        routeThroughTor=True,
        assignedUser=CHILD_ID,
        operatingUser=CHILD_ID,
        defaultSystemUser=114,
    ),
    _device(
        "device:aabbcc000003",
        "Printer",
        "192.0.2.22",
        enabled=False,
        assignedUser=115,
        operatingUser=115,
        defaultSystemUser=115,
    ),
    {
        "id": EBLOCKER_ID,
        "ipAddresses": [HOST],
        "name": "My eBlocker",
        "vendor": "eBlocker",
        "isOnline": True,
        "isEblocker": True,
        "hardwareAddress": "52:54:00:00:00:01",
    },
]

STATS: dict[str, Any] = {
    "summary": {
        "begin": 1775213238.837,
        "end": 1795005443.048,
        "queries": 1000,
        "blockedQueries": 250,
        "blockedQueriesByReason": {
            "ADS": 10,
            "TRACKERS": 200,
            "PARENTAL_CONTROL": 30,
            "MALWARE": 5,
            "CONTENT": 5,
        },
    }
}

UPDATE_STATUS: dict[str, Any] = {
    "updatesAvailable": True,
    "listsPacketVersion": "20261001063707",
    "lastAutomaticUpdate": "2026-10-01T18:27:11.412083501Z",
    "nextAutomaticUpdate": "2026-11-19T04:10Z",
    "projectVersion": "4.0.3",
    "updating": False,
    "downloading": False,
    "recovering": False,
    "disabled": False,
    "automaticUpdatesActivated": True,
    "automaticUpdatesAllowed": False,
    "updateablePackages": ["eblocker-lists"],
}


def _blocker(blocker_id: int, name: str, btype: str, category: str, **extra):
    blocker = {
        "id": blocker_id,
        "name": {"de": name, "en": name},
        "description": {"de": name, "en": name},
        "type": btype,
        "category": category,
        "lastUpdate": 1790843928708,
        "providedByEblocker": True,
        "enabled": True,
        "filterType": "blacklist",
    }
    blocker.update(extra)
    return blocker


# Same shape as GET /api/blockers/ on eBlockerOS 4.0.3
BLOCKERS: list[dict[str, Any]] = [
    _blocker(4, "Malware", "DOMAIN", "MALWARE"),
    _blocker(11, "Ads", "DOMAIN", "ADS"),
    _blocker(12, "Trackers", "DOMAIN", "TRACKER", enabled=False),
    _blocker(2, "Gambling", "DOMAIN", "PARENTAL_CONTROL"),
    _blocker(17, "Malware", "PATTERN", "MALWARE"),
    _blocker(
        1002,
        "Porn",
        "DOMAIN",
        "PARENTAL_CONTROL",
        providedByEblocker=False,
        url="https://nsfw.example.org",
        format="DOMAINS",
        updateInterval="DAILY",
        updateStatus="READY",
    ),
    _blocker(
        1003,
        "My domains",
        "DOMAIN",
        "ADS",
        providedByEblocker=False,
        format="DOMAINS",
        updateInterval="NEVER",
        updateStatus="READY",
    ),
]

# Same shape as GET /api/adminconsole/users and /userprofiles (eBlockerOS 4.0.3)
USERS: list[dict[str, Any]] = [
    {
        "id": 113,
        "name": "Laptop",
        "system": True,
        "associatedProfileId": 1,
        "userRole": "OTHER",
    },
    {
        "id": 114,
        "name": "Tablet",
        "system": True,
        "associatedProfileId": 1,
        "userRole": "OTHER",
    },
    {
        "id": 115,
        "name": "Printer",
        "system": True,
        "associatedProfileId": 1,
        "userRole": "OTHER",
    },
    {
        "id": 2,
        "name": "LIMBO",
        "system": True,
        "associatedProfileId": 4,
        "userRole": "OTHER",
    },
    {
        "id": CHILD_ID,
        "name": "Lisa",
        "nameKey": "PARENTAL_CONTROL_USER_NAME",
        "system": False,
        "associatedProfileId": CHILD_PROFILE_ID,
        "userRole": "CHILD",
        "containsPin": False,
    },
]


def _profile(profile_id: int, **extra: Any) -> dict[str, Any]:
    profile = {
        "id": profile_id,
        "name": None,
        "nameKey": "",
        "builtin": True,
        "standard": False,
        "hidden": False,
        "controlmodeUrls": False,
        "controlmodeTime": False,
        "controlmodeMaxUsage": False,
        "internetBlocked": False,
        "internetAccessRestrictionMode": 1,
        "accessibleSitesPackages": [],
        "inaccessibleSitesPackages": [],
        "internetAccessContingents": [],
        "maxUsageTimeByDay": {},
        "bonusTimeUsage": None,
    }
    profile.update(extra)
    return profile


def _today_bonus(minutes: int) -> dict[str, Any]:
    from homeassistant.util import dt as dt_util

    now = dt_util.now()
    return {
        "dateTime": [now.year, now.month, now.day, now.hour, now.minute, 0],
        "bonusMinutes": minutes,
    }


PROFILES: list[dict[str, Any]] = [
    _profile(
        1,
        nameKey="PARENTAL_CONTROL_DEFAULT_PROFILE_NAME",
        standard=True,
        controlmodeUrls=True,
        inaccessibleSitesPackages=[0, 2],
    ),
    _profile(
        5,
        nameKey="PARENTAL_CONTROL_FULL_2_PROFILE_NAME",
        controlmodeUrls=True,
        controlmodeTime=True,
        controlmodeMaxUsage=True,
        inaccessibleSitesPackages=[0, 2, 3],
        internetAccessContingents=[
            {"onDay": 8, "fromMinutes": 420, "tillMinutes": 1200, "totalMinutes": 0}
        ],
        maxUsageTimeByDay=dict.fromkeys(
            [
                "MONDAY",
                "TUESDAY",
                "WEDNESDAY",
                "THURSDAY",
                "FRIDAY",
                "SATURDAY",
                "SUNDAY",
            ],
            60,
        ),
    ),
    _profile(4, nameKey="PARENTAL_CONTROL_USERPROFILE_LIMBO_NAME", hidden=True),
    _profile(
        CHILD_PROFILE_ID,
        name="PROFILE_FOR_USER_200",
        builtin=False,
        controlmodeMaxUsage=True,
        maxUsageTimeByDay={"MONDAY": 60, "SATURDAY": 120},
        bonusTimeUsage="TODAY",
    ),
]

PARENTAL_LISTS: list[dict[str, Any]] = [
    {
        "id": 0,
        "name": {"de": "Unangemessene Inhalte", "en": "Inappropriate Content"},
        "filterType": "blacklist",
        "builtin": True,
        "category": "PARENTAL_CONTROL",
    },
    {
        "id": 2,
        "name": {"de": "Glücksspiel", "en": "Gambling"},
        "filterType": "blacklist",
        "builtin": True,
        "category": "PARENTAL_CONTROL",
    },
    {
        "id": 3,
        "name": {"de": "Soziale Netzwerke", "en": "Social Networks"},
        "filterType": "blacklist",
        "builtin": True,
        "category": "PARENTAL_CONTROL",
    },
    {
        "id": 10,
        "name": {"de": "fragFINN", "en": "fragFINN"},
        "filterType": "whitelist",
        "builtin": True,
        "category": "PARENTAL_CONTROL",
    },
    {
        # switched off globally on the eBlocker
        "id": 7,
        "name": {"de": "Musik", "en": "Music"},
        "filterType": "blacklist",
        "builtin": True,
        "disabled": True,
        "category": "PARENTAL_CONTROL",
    },
    {
        "id": 11,
        "name": {"de": "Ads", "en": "Ads"},
        "filterType": "blacklist",
        "builtin": True,
        "category": "ADS",
    },
    {
        "id": 1002,
        "name": None,
        "customerCreatedName": "Porn",
        "filterType": "blacklist",
        "builtin": False,
        "category": "PARENTAL_CONTROL",
    },
]

# Jackson serializes java.time.Duration as seconds
USAGE: dict[int, dict[str, Any]] = {
    CHILD_ID: {
        "active": True,
        "allowed": True,
        "usedTime": 1800.0,
        "accountedTime": 1800.0,
        "maxUsageTime": 3600.0,
    }
}

REGISTRATION: dict[str, Any] = {
    "licenseType": "SUBSCRIPTION",
    "licenseNotValidAfter": "2035-09-12T14:26:09.000Z",
    "licenseLifetime": True,
    "registrationState": "OK",
    "productInfo": {"productName": "eBlocker Family"},
}

PAUSES: dict[str, dict[str, Any]] = {
    LAPTOP_ID: {"pausing": 0, "pausingAllowed": True},
    KID_ID: {"pausing": 0, "pausingAllowed": False},
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in all tests."""
    return


@pytest.fixture
def mock_client():
    """Patch the API client used by the integration and the config flow."""
    with (
        patch("custom_components.eblocker.EBlockerClient", autospec=True) as client_cls,
        patch("custom_components.eblocker.config_flow.EBlockerClient", new=client_cls),
    ):
        client = client_cls.return_value
        client.base_url = f"http://{HOST}:3000"
        client.async_authenticate = AsyncMock(return_value=None)
        client.get_system_status = AsyncMock(return_value=copy.deepcopy(SYSTEM_STATUS))
        client.get_devices = AsyncMock(side_effect=lambda: copy.deepcopy(DEVICES))
        client.get_total_stats = AsyncMock(return_value=copy.deepcopy(STATS))
        client.get_update_status = AsyncMock(return_value=copy.deepcopy(UPDATE_STATUS))
        client.get_registration = AsyncMock(return_value=copy.deepcopy(REGISTRATION))
        client.get_pause = AsyncMock(side_effect=lambda d: copy.deepcopy(PAUSES[d]))
        client.set_pause = AsyncMock(
            side_effect=lambda d, s: {"pausing": s, "pausingAllowed": True}
        )
        client.set_device_attributes = AsyncMock(return_value=None)
        # Filter lists: tests may change client.blockers to simulate changes
        client.blockers = copy.deepcopy(BLOCKERS)
        client.get_blockers = AsyncMock(
            side_effect=lambda: copy.deepcopy(client.blockers)
        )
        client.update_blocker = AsyncMock(side_effect=lambda b: b)
        client.create_blocker = AsyncMock(
            side_effect=lambda b: {**b, "id": 1004, "updateStatus": "NEW"}
        )
        client.delete_blocker = AsyncMock(return_value=None)
        # Updates
        client.start_update = AsyncMock(return_value={"updating": True})
        client.check_for_updates = AsyncMock(return_value={"checking": True})
        client.set_automatic_updates = AsyncMock(return_value=None)
        # Parental control
        client.users = copy.deepcopy(USERS)
        profiles = copy.deepcopy(PROFILES)
        for profile in profiles:
            if profile["bonusTimeUsage"] == "TODAY":
                profile["bonusTimeUsage"] = _today_bonus(15)
        client.profiles = profiles
        client.get_users = AsyncMock(side_effect=lambda: copy.deepcopy(client.users))
        client.get_profiles = AsyncMock(
            side_effect=lambda: copy.deepcopy(client.profiles)
        )
        client.get_profile = AsyncMock(
            side_effect=lambda pid: copy.deepcopy(
                next(p for p in client.profiles if p["id"] == pid)
            )
        )
        client.update_profile = AsyncMock(side_effect=lambda p: p)
        client.set_profile_attributes = AsyncMock(return_value=None)
        client.add_bonus_time = AsyncMock(return_value=None)
        client.reset_bonus_time = AsyncMock(return_value=None)
        client.get_parental_filter_lists = AsyncMock(
            return_value=copy.deepcopy(PARENTAL_LISTS)
        )
        client.get_usage = AsyncMock(
            side_effect=lambda uid: copy.deepcopy(USAGE.get(uid))
        )
        client.create_user = AsyncMock(
            side_effect=lambda u: {**u, "id": 300, "system": False}
        )
        client.create_profile = AsyncMock(side_effect=lambda p: {**p, "id": 301})
        client.update_user = AsyncMock(side_effect=lambda u: u)
        client.delete_user = AsyncMock(return_value=None)
        client.delete_profile = AsyncMock(return_value=None)
        # Anonymization
        client.get_vpn_profiles = AsyncMock(
            return_value=[
                {
                    "id": 2,
                    "name": "Home VPN",
                    "enabled": True,
                    "deleted": False,
                    "temporary": False,
                },
                {
                    "id": 3,
                    "name": "Old VPN",
                    "enabled": True,
                    "deleted": True,
                    "temporary": False,
                },
            ]
        )
        client.set_tor = AsyncMock(return_value=None)
        client.set_vpn = AsyncMock(return_value=None)
        client.new_tor_identity = AsyncMock(return_value=None)
        client.get_user_agents = AsyncMock(
            return_value=["Off", "iPhone", "PC (Linux)", "Mac"]
        )
        client.get_cloaked_user_agent = AsyncMock(
            side_effect=lambda device_id, user_id: {
                "agentName": "Mac" if device_id == LAPTOP_ID else "Off",
                "isCustomUserAgent": False,
            }
        )
        client.set_cloaked_user_agent = AsyncMock(return_value=None)
        client.get_privacy_settings = AsyncMock(
            return_value={
                "webrtc_blocked": False,
                "referrer_removed": False,
                "dnt_header": True,
            }
        )
        client.set_privacy_setting = AsyncMock(return_value=None)
        client.get_ssl_enabled = AsyncMock(return_value=True)
        yield client


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry tracking two devices."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="My eBlocker",
        unique_id=EBLOCKER_ID,
        data={CONF_HOST: HOST, CONF_PORT: 3000, CONF_PASSWORD: "secret"},
        options={
            CONF_TRACKED_DEVICES: [LAPTOP_ID, KID_ID],
            CONF_SCAN_INTERVAL: 60,
            CONF_PAUSE_MINUTES: 15,
        },
    )
