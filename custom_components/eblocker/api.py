"""Asynchronous client for the local eBlocker REST API.

The client talks to the same REST API the eBlocker admin console uses.

Authentication (verified against eBlockerOS 4.0.x):

1. ``GET  /api/adminconsole/authentication/token/ADMINCONSOLE`` (public)
   returns a JSON web token.  If no admin password is set, this token is
   already authenticated (``passwordRequired`` is ``false``).
2. ``POST /api/adminconsole/authentication/login/ADMINCONSOLE`` (public)
   with ``{"currentPassword": "..."}`` returns an authenticated token.
3. ``GET  /api/adminconsole/authentication/renew/ADMINCONSOLE`` (bearer)
   returns a fresh token without sending the password again.

All ``adminconsole.*`` routes require ``Authorization: Bearer <token>``.
Tokens are valid for one hour by default.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from .const import TOKEN_RENEW_MARGIN

_LOGGER = logging.getLogger(__name__)

APP_CONTEXT = "ADMINCONSOLE"
USER_AGENT = "HomeAssistant-eBlocker-Integration"
CLOAKING_OFF = "Off"

# Global privacy settings: key -> (path, JSON field of the PUT body)
PRIVACY_SETTINGS = {
    "webrtc_blocked": ("/api/adminconsole/webrtc", "webRTCBlockEnabled"),
    "referrer_removed": ("/api/adminconsole/referrer", "httpRemovingEnabled"),
    "dnt_header": ("/api/adminconsole/dnt", "dntHeaderEnabled"),
}
DEFAULT_TIMEOUT = 15

# Error keys the eBlocker returns in the body of 401 responses.
ERROR_CREDENTIALS_INVALID = "error.credentials.invalid"
ERROR_CREDENTIALS_TOO_SOON = "error.credentials.too.soon"


class EBlockerError(Exception):
    """Base error for the eBlocker client."""


class EBlockerConnectionError(EBlockerError):
    """The eBlocker could not be reached or did not answer in time."""


class EBlockerAuthError(EBlockerError):
    """The password was rejected (or is missing)."""


class EBlockerLoginThrottledError(EBlockerError):
    """The eBlocker refuses password entry for a while after a failed attempt."""


class EBlockerApiError(EBlockerError):
    """The eBlocker answered with an unexpected HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class EBlockerClient:
    """Client for one eBlocker."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        password: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self._session = session
        self._base_url = f"http://{host}:{port}"
        self._password = password or None
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        # One token per app context: {context: (token, valid_until_monotonic)}
        self._tokens: dict[str, tuple[str, float]] = {}
        self._auth_lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        """Return the base URL of the eBlocker (also the admin console URL)."""
        return self._base_url

    # ------------------------------------------------------------------
    # Low level HTTP
    # ------------------------------------------------------------------

    async def _raw_request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        # Own user agent: the eBlocker keys sessions by IP + user agent, and
        # setting a device's cloaked user agent also changes the requesting
        # session. A dedicated agent keeps HA's own web traffic unaffected.
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = f"{self._base_url}{path}"
        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                timeout=self._timeout,
            ) as resp:
                text = await resp.text()
                if resp.status == 401:
                    raise _UnauthorizedError(_error_key(text))
                if resp.status >= 400:
                    raise EBlockerApiError(resp.status, _error_key(text))
                if resp.status == 204 or not text.strip():
                    return None
                try:
                    return await resp.json(content_type=None)
                except ValueError as err:
                    raise EBlockerApiError(
                        resp.status, f"invalid JSON from {path}"
                    ) from err
        except TimeoutError as err:
            raise EBlockerConnectionError(f"Timeout talking to {url}") from err
        except aiohttp.ClientError as err:
            raise EBlockerConnectionError(f"Error talking to {url}: {err}") from err

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        context: str = APP_CONTEXT,
    ) -> Any:
        """Perform an authenticated request, re-authenticating once on 401."""
        token = await self._ensure_token(context)
        try:
            return await self._raw_request(
                method, path, token=token, params=params, json=json
            )
        except _UnauthorizedError:
            _LOGGER.debug("Token rejected for %s, re-authenticating", path)
            self._invalidate_token(context, token)
            token = await self._ensure_token(context)
            try:
                return await self._raw_request(
                    method, path, token=token, params=params, json=json
                )
            except _UnauthorizedError as err:
                raise EBlockerAuthError(
                    f"Request to {path} not authorized: {err}"
                ) from err

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _invalidate_token(self, context: str, token: str | None) -> None:
        current = self._tokens.get(context)
        if current and current[0] == token:
            del self._tokens[context]

    def _store_token(self, context: str, data: dict[str, Any]) -> str:
        token = data.get("token")
        if not token:
            raise EBlockerApiError(200, "no token in authentication response")
        # expiresOn is a unix timestamp in seconds (server clock).  Convert to
        # a remaining lifetime so clock skew between HA and eBlocker does not
        # matter much; a rejected token triggers a re-login anyway.
        lifetime = float(data.get("expiresOn", 0)) - time.time()
        lifetime = max(60.0, lifetime) if lifetime > 0 else 300.0
        self._tokens[context] = (
            token,
            time.monotonic() + lifetime - TOKEN_RENEW_MARGIN.total_seconds(),
        )
        return token

    async def _ensure_token(self, context: str = APP_CONTEXT) -> str:
        async with self._auth_lock:
            current = self._tokens.get(context)
            if current and time.monotonic() < current[1]:
                return current[0]
            if current and context == APP_CONTEXT:
                # Try a renewal first, so the password is not sent every hour.
                # (The renew route only exists for the admin console context.)
                try:
                    data = await self._raw_request(
                        "GET",
                        f"/api/adminconsole/authentication/renew/{context}",
                        token=current[0],
                    )
                    if isinstance(data, dict) and data.get("token"):
                        return self._store_token(context, data)
                except (_UnauthorizedError, EBlockerApiError):
                    _LOGGER.debug("Token renewal failed, logging in again")
            self._tokens.pop(context, None)
            return await self._login(context)

    async def _login(self, context: str = APP_CONTEXT) -> str:
        data = await self._raw_request(
            "GET", f"/api/adminconsole/authentication/token/{context}"
        )
        if not isinstance(data, dict):
            raise EBlockerApiError(200, "unexpected token response")
        if not data.get("passwordRequired", True):
            # No admin password set: the console token is already authenticated.
            return self._store_token(context, data)
        if not self._password:
            raise EBlockerAuthError("The eBlocker requires an admin password")
        try:
            data = await self._raw_request(
                "POST",
                f"/api/adminconsole/authentication/login/{context}",
                json={"currentPassword": self._password},
            )
        except _UnauthorizedError as err:
            if err.key == ERROR_CREDENTIALS_TOO_SOON:
                raise EBlockerLoginThrottledError(
                    "Password entry temporarily blocked after a failed attempt"
                ) from err
            raise EBlockerAuthError("Invalid admin password") from err
        if not isinstance(data, dict):
            raise EBlockerApiError(200, "unexpected login response")
        return self._store_token(context, data)

    async def async_authenticate(self) -> None:
        """Force a fresh login (used by the config flow to validate input)."""
        async with self._auth_lock:
            self._tokens.clear()
            await self._login()

    # ------------------------------------------------------------------
    # Public, read-only endpoints
    # ------------------------------------------------------------------

    async def get_system_status(self) -> dict[str, Any]:
        """Return the system status (public, no authentication needed)."""
        return await self._raw_request("GET", "/api/adminconsole/systemstatus")

    # ------------------------------------------------------------------
    # Admin console endpoints
    # ------------------------------------------------------------------

    async def get_devices(self) -> list[dict[str, Any]]:
        """Return all known network devices (including the eBlocker itself)."""
        return await self._request("GET", "/api/adminconsole/devices") or []

    async def get_device(self, device_id: str) -> dict[str, Any]:
        """Return a single device."""
        return await self._request("GET", f"/api/adminconsole/devices/{device_id}")

    async def update_device(self, device: dict[str, Any]) -> dict[str, Any] | None:
        """Write back a complete device object (as the admin console does)."""
        return await self._request(
            "PUT", f"/api/adminconsole/devices/{device['id']}", json=device
        )

    async def set_device_attributes(
        self, device_id: str, **attributes: Any
    ) -> dict[str, Any] | None:
        """Read a device, change some attributes and write it back.

        The eBlocker API only accepts full device objects, so the current
        state is fetched immediately before the update.
        """
        device = await self.get_device(device_id)
        device.update(attributes)
        return await self.update_device(device)

    async def get_total_stats(self) -> dict[str, Any]:
        """Return the accumulated DNS filter statistics."""
        return await self._request("GET", "/api/adminconsole/stats/total")

    async def get_update_status(self) -> dict[str, Any]:
        """Return update information (versions, automatic updates)."""
        return await self._request("GET", "/api/adminconsole/updates/status")

    async def get_registration(self) -> dict[str, Any]:
        """Return license / registration information."""
        return await self._request("GET", "/api/adminconsole/registration")

    async def get_pause(self, device_id: str) -> dict[str, Any] | None:
        """Return ``{"pausing": seconds, "pausingAllowed": bool}`` for a device."""
        return await self._request(
            "GET", "/api/adminconsole/device/pause", params={"deviceId": device_id}
        )

    async def set_pause(self, device_id: str, seconds: int) -> dict[str, Any] | None:
        """Pause protection for a device for ``seconds`` (0 ends the pause)."""
        return await self._request(
            "PUT",
            "/api/adminconsole/device/pause",
            params={"deviceId": device_id},
            json={"pausing": int(seconds)},
        )

    # ------------------------------------------------------------------
    # Filter lists ("blockers": ads, trackers, malware, parental control)
    # ------------------------------------------------------------------

    async def get_blockers(self) -> list[dict[str, Any]]:
        """Return all filter lists, built-in and custom."""
        return await self._request("GET", "/api/blockers/") or []

    async def update_blocker(self, blocker: dict[str, Any]) -> dict[str, Any] | None:
        """Write back a complete filter list object.

        For built-in lists only ``enabled`` is evaluated by the eBlocker. For
        custom lists name, URL, content, format and update interval can be
        changed as well (a changed URL or content triggers a new download).
        """
        return await self._request(
            "PUT", f"/api/blockers/{blocker['id']}", json=blocker
        )

    async def create_blocker(self, blocker: dict[str, Any]) -> dict[str, Any] | None:
        """Create a custom filter list (downloaded/parsed in the background)."""
        return await self._request("POST", "/api/blockers/", json=blocker)

    async def delete_blocker(self, blocker_id: int) -> None:
        """Delete a custom filter list (built-in lists cannot be deleted)."""
        await self._request("DELETE", f"/api/blockers/{blocker_id}")

    # ------------------------------------------------------------------
    # Parental control: users, profiles, usage
    # ------------------------------------------------------------------

    async def get_users(self) -> list[dict[str, Any]]:
        """Return all users (incl. the system users eBlocker creates per device)."""
        return await self._request("GET", "/api/adminconsole/users") or []

    async def create_user(self, user: dict[str, Any]) -> dict[str, Any]:
        """Create a user (``name``, ``userRole``, ``associatedProfileId``...)."""
        return await self._request("POST", "/api/adminconsole/users", json=user)

    async def update_user(self, user: dict[str, Any]) -> dict[str, Any]:
        """Update a user (full transport object)."""
        return await self._request("PUT", "/api/adminconsole/users", json=user)

    async def delete_user(self, user_id: int) -> None:
        """Delete a (non-system) user."""
        await self._request("DELETE", f"/api/adminconsole/users/{user_id}")

    async def get_profiles(self) -> list[dict[str, Any]]:
        """Return all parental control profiles."""
        return await self._request("GET", "/api/adminconsole/userprofiles") or []

    async def create_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        """Create a parental control profile."""
        return await self._request(
            "POST", "/api/adminconsole/userprofiles", json=profile
        )

    async def update_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        """Write back a complete profile object."""
        return await self._request(
            "PUT", "/api/adminconsole/userprofiles", json=profile
        )

    async def delete_profile(self, profile_id: int) -> None:
        """Delete a custom profile (fails while a user still uses it)."""
        await self._request("DELETE", f"/api/adminconsole/userprofiles/{profile_id}")

    async def get_profile(self, profile_id: int) -> dict[str, Any]:
        """Return one profile (the API only offers the full list)."""
        for profile in await self.get_profiles():
            if profile.get("id") == profile_id:
                return profile
        raise EBlockerApiError(404, f"profile {profile_id} not found")

    async def set_profile_attributes(
        self, profile_id: int, **attributes: Any
    ) -> dict[str, Any]:
        """Read a profile, change some attributes and write it back."""
        profile = await self.get_profile(profile_id)
        profile.update(attributes)
        return await self.update_profile(profile)

    async def add_bonus_time(self, profile_id: int, minutes: int) -> None:
        """Add (or with a negative value: remove) online time for today."""
        await self._request(
            "POST",
            f"/api/adminconsole/userprofile/bonustime/{profile_id}",
            json=int(minutes),
        )

    async def reset_bonus_time(self, profile_id: int) -> None:
        """Remove today's bonus time."""
        await self._request(
            "DELETE", f"/api/adminconsole/userprofile/bonustime/{profile_id}"
        )

    async def get_parental_filter_lists(self) -> list[dict[str, Any]]:
        """Return the domain lists profiles can block or allow."""
        return await self._request("GET", "/api/adminconsole/filterlists") or []

    async def get_usage(self, user_id: int) -> dict[str, Any] | None:
        """Return today's usage account of a user (None without time limit).

        Only offered in the dashboard context, so a second token for the
        password protected ADMINDASHBOARD context is used.
        """
        return await self._request(
            "GET", f"/api/parentalcontrol/usage/{user_id}", context="ADMINDASHBOARD"
        )

    # ------------------------------------------------------------------
    # Anonymization
    # ------------------------------------------------------------------

    async def get_vpn_profiles(self) -> list[dict[str, Any]]:
        """Return VPN profiles (id/name/state only, never the credentials)."""
        profiles = await self._request("GET", "/api/adminconsole/vpn/profiles") or []
        return [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "enabled": p.get("enabled", True),
                "deleted": p.get("deleted", False),
                "temporary": p.get("temporary", False),
            }
            for p in profiles
            if isinstance(p, dict) and "id" in p
        ]

    async def set_tor(self, device_id: str, enabled: bool) -> None:
        """Route all traffic of a device through Tor (or stop doing so)."""
        await self._request(
            "PUT",
            f"/api/adminconsole/tor/config/{device_id}",
            json={"sessionUseTor": bool(enabled)},
        )

    async def set_vpn(self, profile_id: int, device_id: str, enabled: bool) -> None:
        """Route all traffic of a device through a VPN profile (or stop)."""
        await self._request(
            "PUT",
            f"/api/adminconsole/vpn/profile/{profile_id}/status/{device_id}",
            json=bool(enabled),
        )

    async def new_tor_identity(self) -> None:
        """Ask Tor for new circuits (new exit IP)."""
        await self._request("PUT", "/api/adminconsole/tor/newidentity")

    async def get_user_agents(self) -> list[str]:
        """Return the user agents a device can be cloaked as ("Off" first)."""
        return await self._request("GET", "/api/adminconsole/useragent/list") or []

    async def get_cloaked_user_agent(
        self, device_id: str, user_id: int | None
    ) -> dict[str, Any] | None:
        """Return ``{"agentName": ..., "isCustomUserAgent": ...}`` of a device."""
        params: dict[str, Any] = {"deviceId": device_id}
        if user_id is not None:
            params["userId"] = user_id
        return await self._request(
            "GET", "/api/adminconsole/useragent/cloaked", params=params
        )

    async def set_cloaked_user_agent(
        self, device_id: str, user_id: int | None, agent_name: str
    ) -> None:
        """Cloak the browser of a device as ``agent_name`` ("Off" disables)."""
        await self._request(
            "PUT",
            "/api/adminconsole/useragent/cloaked",
            json={
                "deviceId": device_id,
                "userId": user_id,
                "userAgentName": agent_name,
                "userAgentValue": None,
            },
        )

    async def get_privacy_settings(self) -> dict[str, bool]:
        """Return the global WebRTC / referrer / Do-Not-Track settings."""
        webrtc, referrer, dnt = await asyncio.gather(
            self._request("GET", "/api/adminconsole/webrtc"),
            self._request("GET", "/api/adminconsole/referrer"),
            self._request("GET", "/api/adminconsole/dnt"),
        )
        return {
            "webrtc_blocked": bool(webrtc),
            "referrer_removed": bool(referrer),
            "dnt_header": bool(dnt),
        }

    async def set_privacy_setting(self, key: str, enabled: bool) -> None:
        """Change one of the global settings returned by get_privacy_settings."""
        path, field = PRIVACY_SETTINGS[key]
        await self._request("PUT", path, json={field: bool(enabled)})

    async def get_ssl_enabled(self) -> bool:
        """Return whether HTTPS support is enabled on the eBlocker."""
        return bool(await self._request("GET", "/api/adminconsole/ssl/status"))

    # ------------------------------------------------------------------
    # eBlockerOS updates
    # ------------------------------------------------------------------

    async def start_update(self) -> dict[str, Any] | None:
        """Start installing available updates (ignored if already running)."""
        return await self._request(
            "POST", "/api/adminconsole/updates/status", json={"updating": True}
        )

    async def check_for_updates(self) -> dict[str, Any] | None:
        """Ask the eBlocker to look for updates (max. once per minute)."""
        return await self._request("GET", "/api/adminconsole/updates/check")

    async def set_automatic_updates(self, enabled: bool) -> dict[str, Any] | None:
        """Turn automatic updates on or off."""
        return await self._request(
            "POST",
            "/api/adminconsole/updates/automaticUpdatesStatus",
            json={"automaticUpdatesActivated": bool(enabled)},
        )


class _UnauthorizedError(Exception):
    """Internal: HTTP 401 with the eBlocker error key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _error_key(text: str) -> str:
    """Extract the error key from an eBlocker error body (a JSON string)."""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1]
    return text[:200]
