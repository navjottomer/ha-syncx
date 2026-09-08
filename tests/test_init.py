"""Tests for entry setup and the resulting entities."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.syncx.api import SyncXAuthError, SyncXConnectionError
from custom_components.syncx.const import (
    CONF_PLANT_ID,
    CONF_PLANT_NAME,
    CONF_USER_ID,
    DOMAIN,
)

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


async def test_entry_sets_up_and_unloads(hass: HomeAssistant, mock_client) -> None:
    """A healthy entry loads and then unloads cleanly."""
    entry = await setup_entry(hass)
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_sensors_report_expected_values(hass: HomeAssistant, mock_client) -> None:
    """Readings are converted into the units Home Assistant expects."""
    await setup_entry(hass)

    # Reported in kW by the service, published in watts.
    assert hass.states.get("sensor.test_plant_solar_power").state == "1903.0"
    assert hass.states.get("sensor.test_plant_load_power").state == "700.0"

    assert hass.states.get("sensor.test_plant_battery_voltage").state == "53.16"
    assert hass.states.get("sensor.test_plant_grid_voltage").state == "247.9"
    assert hass.states.get("sensor.test_plant_lifetime_generation").state == "940.1"

    # The service spells a missing lifetime figure as the text "null kWh".
    assert hass.states.get("sensor.test_plant_lifetime_consumption").state == "unknown"


async def test_battery_estimate_disagrees_with_the_inverter(
    hass: HomeAssistant, mock_client
) -> None:
    """The whole point of the estimate is not to read back a pinned 100%."""
    await setup_entry(hass)

    reported = hass.states.get("sensor.test_plant_battery_level_reported_by_inverter")
    estimated = hass.states.get("sensor.test_plant_battery_level")

    assert reported.state == "100.0"
    assert 0.0 < float(estimated.state) < 100.0
    assert "cell_voltage" in estimated.attributes


async def test_battery_power_is_signed_by_direction(
    hass: HomeAssistant, mock_client
) -> None:
    """Charging is positive, and the sign follows the net current."""
    await setup_entry(hass)
    assert float(hass.states.get("sensor.test_plant_battery_power").state) > 0


async def test_grid_power_is_negative_while_exporting(
    hass: HomeAssistant, mock_client
) -> None:
    """The flow code says this sample is exporting, so grid power is negative."""
    await setup_entry(hass)
    assert float(hass.states.get("sensor.test_plant_grid_power").state) < 0


async def test_binary_sensors_follow_the_flow_code(
    hass: HomeAssistant, mock_client
) -> None:
    """Flow derived states agree with the vendor dashboard for the same code."""
    await setup_entry(hass)

    assert hass.states.get("binary_sensor.test_plant_battery_charging").state == "on"
    assert (
        hass.states.get("binary_sensor.test_plant_battery_discharging").state == "off"
    )
    assert hass.states.get("binary_sensor.test_plant_exporting_to_grid").state == "on"
    assert (
        hass.states.get("binary_sensor.test_plant_importing_from_grid").state == "off"
    )
    assert hass.states.get("binary_sensor.test_plant_grid_available").state == "on"


async def test_stale_reading_marks_the_plant_offline(
    hass: HomeAssistant, mock_client, stats_payload
) -> None:
    """A sample older than an hour means the data logger has stopped reporting."""
    stats_payload["stats"]["last_updated_timestamp"] = 0
    await setup_entry(hass)
    assert hass.states.get("binary_sensor.test_plant_online").state == "off"


async def test_connection_error_retries_rather_than_reauth(
    hass: HomeAssistant, mock_client
) -> None:
    """A transient outage leaves the entry retrying, not asking for a password."""
    mock_client.return_value.async_get_stats = AsyncMock(
        side_effect=SyncXConnectionError("service down")
    )

    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="plant-1")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_rejected_credentials_trigger_reauth(
    hass: HomeAssistant, mock_client
) -> None:
    """Credentials the service refuses open the reauth flow."""
    mock_client.return_value.async_get_stats = AsyncMock(
        side_effect=SyncXAuthError("rejected")
    )

    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="plant-1")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_device_registry_entry(hass: HomeAssistant, mock_client) -> None:
    """The plant appears as one device carrying the hardware details."""
    from homeassistant.helpers import device_registry as dr

    await setup_entry(hass)
    registry = dr.async_get(hass)
    device = registry.async_get_device(identifiers={(DOMAIN, "plant-1")})

    assert device is not None
    assert device.manufacturer == "Luminous"
    assert device.model == "Hybrid TX 5kVA/48V"
    assert device.sw_version == "1.3.0"


async def test_daily_aggregate_sensors(hass: HomeAssistant, mock_client) -> None:
    """Peak and average figures come from the trend endpoints, in watts."""
    await setup_entry(hass)

    assert hass.states.get("sensor.test_plant_peak_solar_power_today").state == "2894.0"
    assert hass.states.get("sensor.test_plant_peak_load_power_today").state == "1450.0"
    assert (
        hass.states.get("sensor.test_plant_average_solar_power_today").state == "970.0"
    )


async def test_latest_alert_exposes_detail(hass: HomeAssistant, mock_client) -> None:
    """The newest alert becomes a state with its message as an attribute."""
    await setup_entry(hass)

    alert = hass.states.get("sensor.test_plant_latest_alert")
    assert alert.state == "Electricity Power Restored"
    assert alert.attributes["message"] == "Mains power supply restored"
    assert alert.attributes["alert_name"] == "FAULT_3012_RESTORED"


async def test_supplementary_failure_does_not_fail_the_poll(
    hass: HomeAssistant, mock_client
) -> None:
    """Losing the trend endpoints must not take the core readings down."""
    mock_client.return_value.async_get_generation_trend = AsyncMock(
        side_effect=SyncXConnectionError("trend unavailable")
    )
    mock_client.return_value.async_get_alerts = AsyncMock(
        side_effect=SyncXConnectionError("alerts unavailable")
    )

    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="plant-1")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.test_plant_solar_power").state == "1903.0"
    assert (
        hass.states.get("sensor.test_plant_peak_solar_power_today").state == "unknown"
    )
    assert hass.states.get("sensor.test_plant_latest_alert").state == "unknown"


async def test_unmapped_flow_code_still_signs_grid_power(
    hass: HomeAssistant, mock_client, stats_payload
) -> None:
    """A flow code absent from the vendor table must not flip grid power.

    Code 4.10 is one of several the vendor dashboard itself has no entry for.
    On such a sample the direction has to come from the power balance: 3.169 kW
    of solar against a 0.5 kW load and an idle battery can only be exporting.
    """
    stats_payload["animationFlow"] = "4.10"
    stats_payload["stats"]["solar_power"] = "3.169"
    stats_payload["stats"]["consumptionValue"] = "0.5"
    stats_payload["stats"]["charging_current"] = "0.0"
    stats_payload["stats"]["discharge"] = "0.0"
    stats_payload["stats"]["gridCTCurrent"] = "11.9"
    stats_payload["stats"]["input_voltage"] = "255.2"

    await setup_entry(hass)

    assert hass.states.get("sensor.test_plant_grid_direction").state == "export"
    assert float(hass.states.get("sensor.test_plant_grid_power").state) < 0
    assert hass.states.get("binary_sensor.test_plant_exporting_to_grid").state == "on"
    assert (
        hass.states.get("binary_sensor.test_plant_importing_from_grid").state == "off"
    )


async def test_unmapped_flow_code_detects_import(
    hass: HomeAssistant, mock_client, stats_payload
) -> None:
    """The same fallback has to work the other way round."""
    stats_payload["animationFlow"] = "4.10"
    stats_payload["stats"]["solar_power"] = "0.2"
    stats_payload["stats"]["consumptionValue"] = "1.8"
    stats_payload["stats"]["charging_current"] = "0.0"
    stats_payload["stats"]["discharge"] = "0.0"

    await setup_entry(hass)

    assert hass.states.get("sensor.test_plant_grid_direction").state == "import"
    assert float(hass.states.get("sensor.test_plant_grid_power").state) > 0
    assert hass.states.get("binary_sensor.test_plant_importing_from_grid").state == "on"


async def test_direction_ignores_the_flow_code(
    hass: HomeAssistant, mock_client, stats_payload
) -> None:
    """Direction follows the balance even when the flow code disagrees.

    Code 4.12 claims centre to grid, but 0.2 kW of solar against a 1.8 kW load
    can only be importing. Trusting the code here would contradict the grid
    power figure shown beside it.
    """
    stats_payload["animationFlow"] = "4.12"
    stats_payload["stats"]["solar_power"] = "0.2"
    stats_payload["stats"]["consumptionValue"] = "1.8"
    stats_payload["stats"]["charging_current"] = "0.0"
    stats_payload["stats"]["discharge"] = "0.0"

    await setup_entry(hass)

    assert hass.states.get("sensor.test_plant_grid_direction").state == "import"
    assert float(hass.states.get("sensor.test_plant_grid_power").state) > 0


async def test_balanced_house_reports_idle_grid(
    hass: HomeAssistant, mock_client, stats_payload
) -> None:
    """Inside the deadband neither direction is claimed."""
    stats_payload["animationFlow"] = "4.10"
    stats_payload["stats"]["solar_power"] = "0.55"
    stats_payload["stats"]["consumptionValue"] = "0.5"
    stats_payload["stats"]["charging_current"] = "0.0"
    stats_payload["stats"]["discharge"] = "0.0"

    await setup_entry(hass)

    assert hass.states.get("sensor.test_plant_grid_direction").state == "idle"
    assert hass.states.get("binary_sensor.test_plant_exporting_to_grid").state == "off"
    assert (
        hass.states.get("binary_sensor.test_plant_importing_from_grid").state == "off"
    )


async def test_battery_charging_absorbs_surplus(
    hass: HomeAssistant, mock_client, stats_payload
) -> None:
    """Solar going into the battery is not surplus and must not read as export."""
    stats_payload["animationFlow"] = "4.10"
    stats_payload["stats"]["solar_power"] = "2.0"
    stats_payload["stats"]["consumptionValue"] = "0.4"
    stats_payload["stats"]["charging_current"] = "30.0"  # ~1.6 kW at 53 V
    stats_payload["stats"]["discharge"] = "0.0"
    stats_payload["batteryVoltage"] = "53.16"

    await setup_entry(hass)

    assert hass.states.get("sensor.test_plant_grid_direction").state == "idle"


async def test_grid_power_comes_from_the_balance_not_the_ct(
    hass: HomeAssistant, mock_client, stats_payload
) -> None:
    """Grid power is the inverter's own balance, not its current transformer.

    The CT here would read 11.9 A x 255.2 V = 3037 W, but solar minus load
    minus battery is 2669 W. The published figure has to be the latter, or the
    flow diagram does not add up.
    """
    stats_payload["stats"]["solar_power"] = "3.169"
    stats_payload["stats"]["consumptionValue"] = "0.5"
    stats_payload["stats"]["charging_current"] = "0.0"
    stats_payload["stats"]["discharge"] = "0.0"
    stats_payload["stats"]["gridCTCurrent"] = "11.9"
    stats_payload["stats"]["input_voltage"] = "255.2"

    await setup_entry(hass)

    assert float(hass.states.get("sensor.test_plant_grid_power").state) == -2669.0
    # The raw CT stays available as its own reading.
    assert float(hass.states.get("sensor.test_plant_grid_current").state) == 11.9


async def test_flow_always_adds_up(
    hass: HomeAssistant, mock_client, stats_payload
) -> None:
    """Solar must equal home plus battery plus grid, for any sample."""
    stats_payload["stats"]["solar_power"] = "2.154"
    stats_payload["stats"]["consumptionValue"] = "0.77"
    stats_payload["stats"]["charging_current"] = "2.52"
    stats_payload["stats"]["discharge"] = "0.0"
    stats_payload["batteryVoltage"] = "53.16"

    await setup_entry(hass)

    def g(key: str) -> float:
        return float(hass.states.get(f"sensor.test_plant_{key}").state)

    solar, home, battery, grid = (
        g("solar_power"),
        g("load_power"),
        g("battery_power"),
        g("grid_power"),
    )
    # grid is positive importing, so it enters the balance with a minus sign.
    assert solar == pytest.approx(home + battery - grid, abs=1.0)
