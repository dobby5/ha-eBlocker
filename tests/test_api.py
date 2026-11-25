"""Tests for the eBlocker API client (HTTP level, mocked)."""

from __future__ import annotations

import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eblocker.api import (
    EBlockerAuthError,
    EBlockerClient,
    EBlockerLoginThrottledError,
)

BASE = "http://192.0.2.10:3000"
TOKEN_URL = f"{BASE}/api/adminconsole/authentication/token/ADMINCONSOLE"
LOGIN_URL = f"{BASE}/api/adminconsole/authentication/login/ADMINCONSOLE"
DEVICES_URL = f"{BASE}/api/adminconsole/devices"
PAUSE_URL = f"{BASE}/api/adminconsole/device/pause"


def _token(name: str, password_required: bool = True) -> dict:
    return {
        "token": name,
        "appContext": "ADMINCONSOLE",
        "expiresOn": int(time.time()) + 3600,
        "passwordRequired": password_required,
    }


def _client(hass: HomeAssistant, password: str | None = "secret") -> EBlockerClient:
    return EBlockerClient(async_get_clientsession(hass), "192.0.2.10", 3000, password)


async def test_login_with_password(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
):
    """With a password set, the client logs in and sends the bearer token."""
    aioclient_mock.get(TOKEN_URL, json=_token("unauthenticated"))
    aioclient_mock.post(LOGIN_URL, json=_token("authenticated"))
    aioclient_mock.get(DEVICES_URL, json=[{"id": "device:1"}])

    devices = await _client(hass).get_devices()

    assert devices == [{"id": "device:1"}]
    login_call = aioclient_mock.mock_calls[1]
    assert login_call[2] == {"currentPassword": "secret"}
    devices_call = aioclient_mock.mock_calls[2]
    assert devices_call[3]["Authorization"] == "Bearer authenticated"
    assert devices_call[3]["User-Agent"] == "HomeAssistant-eBlocker-Integration"


async def test_vpn_profiles_never_expose_credentials(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
):
    """VPN profiles contain login data; the client keeps only id/name/state."""
    aioclient_mock.get(TOKEN_URL, json=_token("open", password_required=False))
    aioclient_mock.get(
        f"{BASE}/api/adminconsole/vpn/profiles",
        json=[
            {
                "id": 2,
                "name": "Home VPN",
                "enabled": True,
                "loginCredentials": {"username": "secret-user", "password": "***"},
            }
        ],
    )
    profiles = await _client(hass).get_vpn_profiles()
    assert profiles == [
        {
            "id": 2,
            "name": "Home VPN",
            "enabled": True,
            "deleted": False,
            "temporary": False,
        }
    ]


async def test_set_tor_and_vpn_payloads(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
):
    """Tor and VPN use the admin console routes of the eBlocker UI."""
    aioclient_mock.get(TOKEN_URL, json=_token("open", password_required=False))
    aioclient_mock.put(f"{BASE}/api/adminconsole/tor/config/device:1", json={})
    aioclient_mock.put(f"{BASE}/api/adminconsole/vpn/profile/2/status/device:1")
    client = _client(hass)

    await client.set_tor("device:1", True)
    await client.set_vpn(2, "device:1", False)

    assert aioclient_mock.mock_calls[-2][2] == {"sessionUseTor": True}
    assert aioclient_mock.mock_calls[-1][2] is False


async def test_no_password_set(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
):
    """Without an admin password the console token is used directly."""
    aioclient_mock.get(TOKEN_URL, json=_token("open", password_required=False))
    aioclient_mock.get(DEVICES_URL, json=[])

    await _client(hass, password=None).get_devices()

    assert aioclient_mock.call_count == 2  # no login call


async def test_password_required_but_missing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
):
    """Missing password raises an auth error without trying to log in."""
    aioclient_mock.get(TOKEN_URL, json=_token("unauthenticated"))
    with pytest.raises(EBlockerAuthError):
        await _client(hass, password=None).get_devices()


async def test_wrong_password(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    """A 401 on login means invalid credentials."""
    aioclient_mock.get(TOKEN_URL, json=_token("unauthenticated"))
    aioclient_mock.post(LOGIN_URL, status=401, text='"error.credentials.invalid"')
    with pytest.raises(EBlockerAuthError):
        await _client(hass).async_authenticate()


async def test_login_throttled(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
):
    """The eBlocker's password-entry penalty is reported separately."""
    aioclient_mock.get(TOKEN_URL, json=_token("unauthenticated"))
    aioclient_mock.post(LOGIN_URL, status=401, text='"error.credentials.too.soon"')
    with pytest.raises(EBlockerLoginThrottledError):
        await _client(hass).async_authenticate()


async def test_usage_uses_admin_dashboard_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
):
    """Usage is only offered in the dashboard context: a second token is used."""
    dash_token = f"{BASE}/api/adminconsole/authentication/token/ADMINDASHBOARD"
    dash_login = f"{BASE}/api/adminconsole/authentication/login/ADMINDASHBOARD"
    aioclient_mock.get(dash_token, json=_token("unauth-dash"))
    aioclient_mock.post(dash_login, json=_token("dash"))
    aioclient_mock.get(
        f"{BASE}/api/parentalcontrol/usage/200",
        json={"usedTime": 60.0, "maxUsageTime": 3600.0},
    )

    usage = await _client(hass).get_usage(200)

    assert usage["maxUsageTime"] == 3600.0
    assert aioclient_mock.mock_calls[-1][3]["Authorization"] == "Bearer dash"


async def test_set_pause_sends_device_id_and_seconds(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
):
    """Pausing uses the admin console pause route with a deviceId parameter."""
    aioclient_mock.get(TOKEN_URL, json=_token("open", password_required=False))
    aioclient_mock.put(PAUSE_URL, json={"pausing": 900, "pausingAllowed": True})

    result = await _client(hass).set_pause("device:abc", 900)

    assert result == {"pausing": 900, "pausingAllowed": True}
    method, url, body, _ = aioclient_mock.mock_calls[-1]
    assert method == "PUT"
    assert url.query["deviceId"] == "device:abc"
    assert body == {"pausing": 900}
