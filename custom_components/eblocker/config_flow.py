"""Config flow for the eBlocker integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    EBlockerAuthError,
    EBlockerClient,
    EBlockerConnectionError,
    EBlockerError,
    EBlockerLoginThrottledError,
)
from .const import (
    CONF_PAUSE_MINUTES,
    CONF_TRACKED_DEVICES,
    DEFAULT_PAUSE_MINUTES,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .entity import device_label

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _user_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(
                CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Optional(CONF_PASSWORD, default=""): PASSWORD_SELECTOR,
        }
    )


async def _validate(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> tuple[str, str, list[dict[str, Any]]]:
    """Log in and return (unique id, title, client devices)."""
    client = EBlockerClient(
        async_get_clientsession(hass),
        data[CONF_HOST],
        data[CONF_PORT],
        data.get(CONF_PASSWORD),
    )
    await client.async_authenticate()
    devices = await client.get_devices()
    eblocker = next((d for d in devices if d.get("isEblocker")), {})
    unique_id = eblocker.get("id") or f"{data[CONF_HOST]}:{data[CONF_PORT]}"
    title = eblocker.get("name") or f"eBlocker ({data[CONF_HOST]})"
    clients = [d for d in devices if not d.get("isEblocker") and d.get("id")]
    return unique_id, title, clients


def _errors_for(err: Exception) -> dict[str, str]:
    if isinstance(err, EBlockerLoginThrottledError):
        return {"base": "login_throttled"}
    if isinstance(err, EBlockerAuthError):
        return {CONF_PASSWORD: "invalid_auth"}
    if isinstance(err, EBlockerConnectionError):
        return {"base": "cannot_connect"}
    if isinstance(err, EBlockerError):
        return {"base": "unknown"}
    _LOGGER.exception("Unexpected error while connecting to eBlocker")
    return {"base": "unknown"}


def _device_selector(devices: list[dict[str, Any]]) -> SelectSelector:
    ordered = sorted(
        devices,
        key=lambda d: (not d.get("isOnline"), (d.get("name") or "").casefold()),
    )
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=d["id"], label=device_label(d)) for d in ordered
            ],
            multiple=True,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


class EBlockerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for eBlocker."""

    VERSION = 1
    # 2: entities that are disabled by default now get disabled once in
    #    existing installations (see async_migrate_entry)
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._title = ""
        self._devices: list[dict[str, Any]] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> EBlockerOptionsFlow:
        """Return the options flow."""
        return EBlockerOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for host, port and admin password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_HOST] = user_input[CONF_HOST].strip()
            try:
                unique_id, title, devices = await _validate(self.hass, user_input)
            except Exception as err:  # noqa: BLE001 - mapped to form errors
                errors = _errors_for(err)
            else:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured(
                    updates={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_PORT: user_input[CONF_PORT],
                    }
                )
                self._data = user_input
                self._title = title
                self._devices = devices
                return await self.async_step_devices()

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick the devices that get their own entities."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._title,
                data=self._data,
                options={
                    CONF_TRACKED_DEVICES: user_input.get(CONF_TRACKED_DEVICES, []),
                    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                    CONF_PAUSE_MINUTES: DEFAULT_PAUSE_MINUTES,
                },
            )
        return self.async_show_form(
            step_id="devices",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_TRACKED_DEVICES, default=[]): _device_selector(
                        self._devices
                    )
                }
            ),
            description_placeholders={"count": str(len(self._devices))},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start re-authentication after the password was rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the new admin password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input.get(CONF_PASSWORD, "")}
            try:
                unique_id, _, _ = await _validate(self.hass, data)
            except Exception as err:  # noqa: BLE001 - mapped to form errors
                errors = _errors_for(err)
            else:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Optional(CONF_PASSWORD, default=""): PASSWORD_SELECTOR}
            ),
            description_placeholders={"host": entry.data[CONF_HOST]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change host, port or password of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_HOST] = user_input[CONF_HOST].strip()
            if not user_input.get(CONF_PASSWORD):
                # An empty field keeps the stored password.
                user_input[CONF_PASSWORD] = entry.data.get(CONF_PASSWORD, "")
            try:
                unique_id, _, _ = await _validate(self.hass, user_input)
            except Exception as err:  # noqa: BLE001 - mapped to form errors
                errors = _errors_for(err)
            else:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(entry, data=user_input)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_user_schema(user_input or entry.data),
            errors=errors,
        )


class EBlockerOptionsFlow(OptionsFlow):
    """Options: tracked devices, polling interval, default pause duration."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        coordinator = getattr(self.config_entry, "runtime_data", None)
        devices: list[dict[str, Any]] = (
            list(coordinator.data.devices.values())
            if coordinator is not None and coordinator.data
            else []
        )
        options = self.config_entry.options
        tracked = list(options.get(CONF_TRACKED_DEVICES, []))
        # Keep devices that are selected but currently unknown selectable, so
        # opening the options does not silently drop them.
        known = {d["id"] for d in devices}
        devices.extend({"id": d, "name": d} for d in tracked if d not in known)

        schema = vol.Schema(
            {
                vol.Optional(CONF_TRACKED_DEVICES, default=tracked): _device_selector(
                    devices
                ),
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=3600,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_PAUSE_MINUTES,
                    default=options.get(CONF_PAUSE_MINUTES, DEFAULT_PAUSE_MINUTES),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=1440,
                        step=1,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
