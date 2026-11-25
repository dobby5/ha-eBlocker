"""Diagnostics support for the eBlocker integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .coordinator import EBlockerConfigEntry

TO_REDACT = {
    CONF_PASSWORD,
    CONF_HOST,
    "token",
    "deviceId",
    "deviceRegisteredBy",
    "hardwareAddress",
    "ipAddresses",
    "gateway",
    "loginCredentials",
    "username",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EBlockerConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = asdict(coordinator.data) if coordinator.data else {}
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "data": async_redact_data(data, TO_REDACT),
    }
