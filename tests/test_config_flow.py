"""Tests for the config and options flow."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.syncx.api import SyncXAuthError, SyncXConnectionError
from custom_components.syncx.const import (
    CONF_PLANT_ID,
    CONF_SOC_CELLS,
    CONF_SOC_CURVE,
    CONF_SOC_ENABLED,
    CONF_SOC_RESISTANCE,
    CONF_SOC_SMOOTHING,
    DOMAIN,
)

CREDENTIALS = {CONF_EMAIL: "user@example.com", CONF_PASSWORD: "secret"}


async def test_single_plant_creates_entry(hass: HomeAssistant, mock_client) -> None:
    """One plant on the account skips the selection step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Plant"
    assert result["data"][CONF_PLANT_ID] == "plant-1"
    assert result["data"][CONF_PASSWORD] == "secret"


async def test_duplicate_plant_aborts(hass: HomeAssistant, mock_client) -> None:
    """Adding the same plant twice is refused."""
    for _ in range(2):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_multiple_plants_prompts_for_choice(
    hass: HomeAssistant, mock_client
) -> None:
    """More than one plant triggers the selection step."""
    mock_client.return_value.async_get_plants = AsyncMock(
        return_value=[
            {"id": "plant-1", "plantName": "Roof", "deleted": False},
            {"id": "plant-2", "plantName": "Shed", "deleted": False},
        ]
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "plant"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PLANT_ID: "plant-2"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Shed"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (SyncXAuthError("nope"), "invalid_auth"),
        (SyncXConnectionError("down"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_login_errors_are_surfaced(
    hass: HomeAssistant, mock_client, error, expected
) -> None:
    """Each failure mode maps to its own message rather than a generic one."""
    mock_client.return_value.async_login = AsyncMock(side_effect=error)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_account_without_plants_is_rejected(
    hass: HomeAssistant, mock_client
) -> None:
    """Signing in successfully but owning nothing is an error, not an entry."""
    mock_client.return_value.async_get_plants = AsyncMock(return_value=[])

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )
    assert result["errors"] == {"base": "no_plants"}


async def test_options_flow_rejects_a_malformed_curve(
    hass: HomeAssistant, mock_client
) -> None:
    """A curve that cannot be parsed is refused before it is stored."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SOC_ENABLED: True,
            CONF_SOC_CELLS: 16,
            CONF_SOC_RESISTANCE: 0.02,
            CONF_SOC_SMOOTHING: 0.25,
            CONF_SOC_CURVE: "this is not a curve",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_SOC_CURVE: "invalid_curve"}


async def test_options_flow_saves_valid_settings(
    hass: HomeAssistant, mock_client
) -> None:
    """Valid estimator settings are written back to the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SOC_ENABLED: True,
            CONF_SOC_CELLS: 15,
            CONF_SOC_RESISTANCE: 0.03,
            CONF_SOC_SMOOTHING: 0.5,
            CONF_SOC_CURVE: "2.12:100, 1.90:0",
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SOC_CELLS] == 15
    assert entry.options[CONF_SOC_CURVE] == "2.12:100, 1.90:0"
