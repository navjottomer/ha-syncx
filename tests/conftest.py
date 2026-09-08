"""Fixtures for the Luminous ConnectX tests."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, create_autospec, patch

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

# The load is derived from output current and voltage, so tests set it through
# those primaries rather than through the figure the service publishes.
TEST_OUTPUT_VOLTS = 250.0
TEST_POWER_FACTOR = 0.8
TEST_EFFICIENCY = 0.95


def set_load(stats_payload: dict, watts: float) -> None:
    """Set the household load by adjusting the inverter's output current."""
    amps = watts / (TEST_OUTPUT_VOLTS * TEST_POWER_FACTOR)
    stats_payload["stats"]["inverterCurrent"] = f"{amps:.4f}"
    stats_payload["outputVoltage"] = str(TEST_OUTPUT_VOLTS)
    stats_payload["stats"]["consumptionValue"] = f"{watts / 1000:.4f}"


def set_solar(stats_payload: dict, watts: float) -> None:
    """Set DC solar power."""
    stats_payload["stats"]["solar_power"] = f"{watts / 1000:.4f}"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the custom integration in every test."""
    return


@pytest.fixture
def stats_payload() -> dict:
    """Return a representative statsV1 payload."""
    return {
        "stats": {
            "available_backup": "0:00",
            "batteryState": "0",
            "battery_charge_percentage": "100",
            "battery_discharge_percentage": "100",
            "charging_current": "1.89",
            "consumptionValue": "0.7",
            "current_running_load_percentage": "0.00",
            "discharge": "0.0",
            "generation": "5.3",
            "gridCTCurrent": "6.8",
            "grid_state": "1",
            "input_voltage": "247.9",
            "inverterCurrent": "3.5",
            "last_updated_timestamp": int(time.time()),
            "pvCurrent": "17.23",
            "solar_power": "1.903",
            "solar_state": "1",
            "time_remaining_for_charging": "0:05",
            "wifi_signal_strength": 3,
        },
        "lifetimeGeneration": "940.1 kWh",
        "co2EmissionSaved": "670.48 kg CO2e",
        "coalNotBurned": "592.26 kg",
        "equivalentTreesPlanted": 11.17,
        "solarPecentInConsumption": 94.0,
        "batteryVoltage": "53.16",
        "outputVoltage": "250.0",
        "solarVoltage": "110.48",
        "inverterType": "HYBRID",
        "inverterModel": "Hybrid TX 5kVA/48V",
        "operatingMode": "UPS",
        "animationFlow": "4.12",
        "todayConsumption": "4.22 kWh",
        "lifetimeConsumption": "null kWh",
        "deviceId": "sn0123TEST",
    }


@pytest.fixture
def mock_client(stats_payload):
    """Patch the API client used by both the flow and the entry setup.

    Both modules import the class by name, so each needs its own patch. They are
    pointed at one shared instance so a test can override a single method and
    have it take effect wherever the client is used.
    """
    from custom_components.syncx.api import SyncXApiClient

    instance = create_autospec(SyncXApiClient, instance=True)
    instance.async_login = AsyncMock(return_value=None)
    instance.user_id = "user-1"
    instance.account_name = "Test User"
    instance.async_get_plants = AsyncMock(
        return_value=[
            {
                "id": "plant-1",
                "plantName": "Test Plant",
                "inverterType": "Hybrid",
                "online": True,
                "deleted": False,
            }
        ]
    )
    instance.async_get_stats = AsyncMock(return_value=stats_payload)
    instance.async_get_plant = AsyncMock(return_value={"plantName": "Test Plant"})
    instance.async_get_battery = AsyncMock(
        return_value={"batteryType": "Li_ion 51.2V", "capacity": "100Ah"}
    )
    instance.async_get_inverter = AsyncMock(
        return_value={"model": "Hybrid TX 5kVA/48V", "serialNo": "INV1"}
    )
    instance.async_get_solar = AsyncMock(return_value={})
    instance.async_get_data_logger = AsyncMock(
        return_value={"serialNo": "sn0123TEST", "firmWareVersion": "1.3.0"}
    )
    instance.async_get_generation_trend = AsyncMock(
        return_value={
            "generation": "6.00",
            "todayAvgGeneration": 0.97,
            "maxInstantiateGenerationEntryValue": 2.894,
        }
    )
    instance.async_get_consumption_trend = AsyncMock(
        return_value={
            "consumption": "4.48",
            "todayAvgConsumption": 0.526,
            "maxInstantiateConsumptionEntryValue": 1.45,
        }
    )
    instance.async_get_alerts = AsyncMock(
        return_value=[
            {
                "id": "alert-1",
                "notificationDate": "07/09/2026 15:52:25",
                "data": {
                    "alertName": "FAULT_3012_RESTORED",
                    "blocking": False,
                    "body": "Mains power supply restored",
                    "title": "Electricity Power Restored",
                    "timestamp": 1788796345,
                },
            }
        ]
    )

    with (
        patch(
            "custom_components.syncx.config_flow.SyncXApiClient",
            return_value=instance,
        ) as flow_client,
        patch("custom_components.syncx.SyncXApiClient", return_value=instance),
    ):
        yield flow_client
