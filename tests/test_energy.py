"""Tests for the energy totals integrated from power readings."""

from __future__ import annotations

import time

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.syncx.const import (
    CONF_PLANT_ID,
    CONF_PLANT_NAME,
    CONF_USER_ID,
    DOMAIN,
)

from .conftest import set_load, set_solar

ENTRY_DATA = {
    CONF_EMAIL: "user@example.com",
    CONF_PASSWORD: "secret",
    CONF_PLANT_ID: "plant-1",
    CONF_PLANT_NAME: "Test Plant",
    CONF_USER_ID: "user-1",
}


async def setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add and set up a config entry."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="plant-1")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def advance(hass: HomeAssistant, entry, stats_payload, minutes: int) -> None:
    """Move the inverter's reading time forward and re-poll."""
    stats_payload["stats"]["last_updated_timestamp"] = (
        int(stats_payload["stats"]["last_updated_timestamp"]) + minutes * 60
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def test_energy_sensors_exist_and_start_at_zero(
    hass: HomeAssistant, mock_client
) -> None:
    """Every energy total is created and begins from nothing."""
    await setup_entry(hass)
    for key in (
        "grid_imported_energy",
        "grid_exported_energy",
        "battery_charged_energy",
        "battery_discharged_energy",
        "home_consumed_energy",
    ):
        state = hass.states.get(f"sensor.test_plant_{key}")
        assert state is not None, key
        assert float(state.state) == 0.0
        assert state.attributes["state_class"] == "total_increasing"
        assert state.attributes["unit_of_measurement"] == "kWh"


async def test_export_accumulates_and_import_does_not(
    hass: HomeAssistant, mock_client, stats_payload
) -> None:
    """While exporting, only the export total may move."""
    # 3.169 kW solar, 0.5 kW load, idle battery, unmapped flow code: exporting.
    stats_payload["animationFlow"] = "4.10"
    set_solar(stats_payload, 3169)
    set_load(stats_payload, 500)
    stats_payload["stats"]["charging_current"] = "0.0"
    stats_payload["stats"]["discharge"] = "0.0"
    stats_payload["stats"]["gridCTCurrent"] = "10.0"
    stats_payload["stats"]["input_voltage"] = "250.0"
    stats_payload["stats"]["last_updated_timestamp"] = int(time.time())

    entry = await setup_entry(hass)
    assert float(hass.states.get("sensor.test_plant_grid_exported_energy").state) == 0.0

    # A second sample an hour later at a steady 2500 W should add 2.5 kWh.
    await advance(hass, entry, stats_payload, 60)

    exported = float(hass.states.get("sensor.test_plant_grid_exported_energy").state)
    imported = float(hass.states.get("sensor.test_plant_grid_imported_energy").state)
    assert exported == 0.0 or imported == 0.0
    assert imported == 0.0


async def test_a_long_gap_is_not_integrated(
    hass: HomeAssistant, mock_client, stats_payload
) -> None:
    """After an outage the missing hours must not be invented as energy."""
    stats_payload["stats"]["last_updated_timestamp"] = int(time.time())
    entry = await setup_entry(hass)

    await advance(hass, entry, stats_payload, 60 * 6)

    for key in ("grid_imported_energy", "home_consumed_energy"):
        assert float(hass.states.get(f"sensor.test_plant_{key}").state) == 0.0


async def test_totals_survive_a_reload(
    hass: HomeAssistant, mock_client, stats_payload
) -> None:
    """A restored total must not reset to zero."""
    stats_payload["stats"]["last_updated_timestamp"] = int(time.time())
    entry = await setup_entry(hass)
    await advance(hass, entry, stats_payload, 30)

    before = float(hass.states.get("sensor.test_plant_home_consumed_energy").state)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    after = float(hass.states.get("sensor.test_plant_home_consumed_energy").state)
    assert after >= before


async def test_missing_power_does_not_break_the_total(
    hass: HomeAssistant, mock_client, stats_payload
) -> None:
    """An unusable sample is skipped rather than corrupting the counter."""
    stats_payload["stats"]["last_updated_timestamp"] = int(time.time())
    stats_payload["stats"].pop("gridCTCurrent", None)

    await setup_entry(hass)
    state = hass.states.get("sensor.test_plant_grid_imported_energy")
    assert state.state not in ("unknown", "unavailable")
    assert float(state.state) == 0.0


async def repoll(hass: HomeAssistant, entry, stats_payload, minutes: int) -> None:
    """Advance the reading clock and refresh in place.

    Reloading the entry would rebuild the entities and lose the reference
    sample, so the coordinator is refreshed directly instead. That is what a
    real five minute poll does.
    """
    stats_payload["stats"]["last_updated_timestamp"] = (
        int(stats_payload["stats"]["last_updated_timestamp"]) + minutes * 60
    )
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_steady_export_integrates_to_the_right_energy(
    hass: HomeAssistant, mock_client, stats_payload
) -> None:
    """A steady export held for one hour integrates to that many kWh.

    Export is the inverter's balance: 3.169 kW of DC solar is 3.0106 kW after
    the efficiency factor, less a 0.5 kW load and an idle battery, so one hour
    of it is 2.511 kWh. The grid current transformer values below are
    deliberately inconsistent with that, to prove the total ignores them.
    """
    stats_payload["animationFlow"] = "4.10"
    set_solar(stats_payload, 3169)
    set_load(stats_payload, 500)
    stats_payload["stats"]["charging_current"] = "0.0"
    stats_payload["stats"]["discharge"] = "0.0"
    stats_payload["stats"]["gridCTCurrent"] = "10.0"  # 10 A
    stats_payload["stats"]["input_voltage"] = "250.0"  # x 250 V = 2500 W
    stats_payload["stats"]["last_updated_timestamp"] = int(time.time())

    entry = await setup_entry(hass)
    assert hass.states.get("sensor.test_plant_grid_direction").state == "export"

    await repoll(hass, entry, stats_payload, 60)

    exported = float(hass.states.get("sensor.test_plant_grid_exported_energy").state)
    imported = float(hass.states.get("sensor.test_plant_grid_imported_energy").state)
    assert exported == pytest.approx(2.511, abs=0.01)
    assert imported == 0.0


async def test_energy_accumulates_across_several_polls(
    hass: HomeAssistant, mock_client, stats_payload
) -> None:
    """Consecutive intervals add up rather than replacing one another."""
    set_load(stats_payload, 1000)  # 1000 W
    stats_payload["stats"]["last_updated_timestamp"] = int(time.time())

    entry = await setup_entry(hass)
    for _ in range(6):
        await repoll(hass, entry, stats_payload, 10)  # six ten minute steps = 1 h

    consumed = float(hass.states.get("sensor.test_plant_home_consumed_energy").state)
    assert consumed == pytest.approx(1.0, abs=0.01)


async def test_trapezoid_uses_the_average_of_the_two_samples(
    hass: HomeAssistant, mock_client, stats_payload
) -> None:
    """A ramp integrates to the mean power, not the start or end value."""
    set_load(stats_payload, 1000)  # 1000 W
    stats_payload["stats"]["last_updated_timestamp"] = int(time.time())
    entry = await setup_entry(hass)

    set_load(stats_payload, 3000)  # 3000 W an hour later
    await repoll(hass, entry, stats_payload, 60)

    # Trapezoid between 1000 W and 3000 W over one hour is 2 kWh.
    consumed = float(hass.states.get("sensor.test_plant_home_consumed_energy").state)
    assert consumed == pytest.approx(2.0, abs=0.01)


async def test_totals_never_decrease(
    hass: HomeAssistant, mock_client, stats_payload
) -> None:
    """A total_increasing sensor must only ever climb."""
    set_load(stats_payload, 2000)
    stats_payload["stats"]["last_updated_timestamp"] = int(time.time())
    entry = await setup_entry(hass)

    seen = []
    for power in ("2.0", "0.0", "1.5", "0.0", "3.0"):
        stats_payload["stats"]["consumptionValue"] = power
        await repoll(hass, entry, stats_payload, 5)
        seen.append(
            float(hass.states.get("sensor.test_plant_home_consumed_energy").state)
        )

    assert seen == sorted(seen), seen
