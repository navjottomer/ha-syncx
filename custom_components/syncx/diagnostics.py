"""Diagnostics support for Luminous ConnectX."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import SyncXConfigEntry
from .const import CONF_USER_ID

TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_USER_ID,
    "lat",
    "lon",
    "location",
    "pinCode",
    "ssId",
    "serialNo",
    "deviceId",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SyncXConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "online": data.online,
        "last_updated": data.last_updated.isoformat() if data.last_updated else None,
        "flows": data.flows,
        "soc": None if data.soc is None else vars(data.soc),
        "stats": async_redact_data(data.stats, TO_REDACT),
        "plant": async_redact_data(data.plant, TO_REDACT),
        "battery": async_redact_data(data.battery, TO_REDACT),
        "inverter": async_redact_data(data.inverter, TO_REDACT),
        "solar": async_redact_data(data.solar, TO_REDACT),
        "logger": async_redact_data(data.logger, TO_REDACT),
    }
