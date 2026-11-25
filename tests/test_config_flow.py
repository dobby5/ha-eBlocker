"""Tests for the eBlocker config flow."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.eblocker.api import (
    EBlockerAuthError,
    EBlockerConnectionError,
    EBlockerLoginThrottledError,
)
from custom_components.eblocker.const import CONF_TRACKED_DEVICES, DOMAIN

from .conftest import EBLOCKER_ID, HOST, LAPTOP_ID

USER_INPUT = {CONF_HOST: HOST, CONF_PORT: 3000, CONF_PASSWORD: "secret"}


async def test_full_flow(hass: HomeAssistant, mock_client):
    """User enters host + password, then selects devices."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "devices"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TRACKED_DEVICES: [LAPTOP_ID]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "My eBlocker"
    assert result["data"] == USER_INPUT
    assert result["options"][CONF_TRACKED_DEVICES] == [LAPTOP_ID]
    assert result["result"].unique_id == EBLOCKER_ID


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (EBlockerAuthError("bad"), {CONF_PASSWORD: "invalid_auth"}),
        (EBlockerConnectionError("down"), {"base": "cannot_connect"}),
        (EBlockerLoginThrottledError("wait"), {"base": "login_throttled"}),
        (RuntimeError("boom"), {"base": "unknown"}),
    ],
)
async def test_flow_errors(hass: HomeAssistant, mock_client, error, expected):
    """Errors are shown on the form and the flow can recover."""
    mock_client.async_authenticate.side_effect = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == expected

    mock_client.async_authenticate.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["step_id"] == "devices"


async def test_already_configured(hass: HomeAssistant, mock_client, config_entry):
    """The same eBlocker cannot be added twice."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, mock_client, config_entry):
    """Re-authentication stores the new password."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-secret"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "new-secret"


async def test_options_flow(hass: HomeAssistant, mock_client, config_entry):
    """Options can change the tracked devices and the pause duration."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_TRACKED_DEVICES: [LAPTOP_ID], "scan_interval": 30, "pause_minutes": 60},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_TRACKED_DEVICES] == [LAPTOP_ID]
    assert config_entry.options["pause_minutes"] == 60
