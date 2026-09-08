"""The Luminous ConnectX integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SyncXApiClient
from .coordinator import SyncXCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

type SyncXConfigEntry = ConfigEntry[SyncXCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SyncXConfigEntry) -> bool:
    """Set up Luminous ConnectX from a config entry."""
    client = SyncXApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
    )

    coordinator = SyncXCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SyncXConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: SyncXConfigEntry) -> None:
    """Apply new options without tearing the entry down."""
    coordinator = entry.runtime_data
    await coordinator.async_options_updated()
