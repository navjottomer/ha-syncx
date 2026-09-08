"""Sensor platform for Luminous ConnectX."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfMass,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SyncXConfigEntry
from .coordinator import SyncXData, duration_to_minutes, to_float
from .entity import SyncXEntity


@dataclass(frozen=True, kw_only=True)
class SyncXSensorDescription(SensorEntityDescription):
    """Describes one Luminous ConnectX sensor."""

    value_fn: Callable[[SyncXData], Any]
    attrs_fn: Callable[[SyncXData], dict[str, Any]] | None = None


def _kw_to_w(value: Any) -> float | None:
    """Convert a kilowatt reading to watts."""
    result = to_float(value)
    return None if result is None else round(result * 1000.0, 1)


def _battery_power(data: SyncXData) -> float | None:
    """Return battery power in watts, positive while charging."""
    volts = to_float(data.top("batteryVoltage"))
    charging = to_float(data.stat("charging_current"))
    discharging = to_float(data.stat("discharge"))
    if volts is None or (charging is None and discharging is None):
        return None
    net = (charging or 0.0) - (discharging or 0.0)
    return round(volts * net, 1)


def _grid_power(data: SyncXData) -> float | None:
    """Return grid power in watts, negative while exporting.

    The inverter reports only an unsigned current transformer reading, so the
    sign comes from the direction the coordinator resolved for this sample.
    """
    volts = to_float(data.stat("input_voltage"))
    amps = to_float(data.stat("gridCTCurrent"))
    if volts is None or amps is None:
        return None
    magnitude = round(volts * amps, 1)
    return -magnitude if data.grid_direction == "export" else magnitude


def _latest_alert(data: SyncXData) -> str | None:
    """Return the title of the most recent alert."""
    for alert in data.alerts:
        payload = alert.get("data")
        if isinstance(payload, dict) and payload.get("title"):
            return str(payload["title"])
    return None


def _latest_alert_attrs(data: SyncXData) -> dict[str, Any]:
    """Return supporting detail for the most recent alert."""
    for alert in data.alerts:
        payload = alert.get("data")
        if isinstance(payload, dict) and payload.get("title"):
            timestamp = to_float(payload.get("timestamp"))
            return {
                "message": payload.get("body"),
                "alert_name": payload.get("alertName"),
                "blocking": payload.get("blocking"),
                "occurred_at": (
                    datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
                    if timestamp
                    else None
                ),
                "resolved": payload.get("resolvedTimestamp") is not None,
                "notified_at": alert.get("notificationDate"),
            }
    return {}


SENSORS: tuple[SyncXSensorDescription, ...] = (
    # Solar
    SyncXSensorDescription(
        key="solar_power",
        translation_key="solar_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _kw_to_w(d.stat("solar_power")),
    ),
    SyncXSensorDescription(
        key="solar_voltage",
        translation_key="solar_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: to_float(d.top("solarVoltage")),
    ),
    SyncXSensorDescription(
        key="solar_current",
        translation_key="solar_current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: to_float(d.stat("pvCurrent")),
    ),
    SyncXSensorDescription(
        key="generation_today",
        translation_key="generation_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: to_float(d.stat("generation")),
    ),
    SyncXSensorDescription(
        key="generation_lifetime",
        translation_key="generation_lifetime",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: to_float(d.top("lifetimeGeneration")),
    ),
    SyncXSensorDescription(
        key="solar_share_of_consumption",
        translation_key="solar_share_of_consumption",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: to_float(d.top("solarPecentInConsumption")),
    ),
    # Battery
    SyncXSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: to_float(d.top("batteryVoltage")),
    ),
    SyncXSensorDescription(
        key="battery_charging_current",
        translation_key="battery_charging_current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: to_float(d.stat("charging_current")),
    ),
    SyncXSensorDescription(
        key="battery_discharging_current",
        translation_key="battery_discharging_current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: to_float(d.stat("discharge")),
    ),
    SyncXSensorDescription(
        key="battery_power",
        translation_key="battery_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_battery_power,
    ),
    SyncXSensorDescription(
        key="battery_level_estimated",
        translation_key="battery_level_estimated",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: None if d.soc is None else d.soc.percent,
        attrs_fn=lambda d: (
            {}
            if d.soc is None
            else {
                "rested_pack_voltage": d.soc.rested_pack_volts,
                "cell_voltage": d.soc.cell_volts,
                "unsmoothed_percent": d.soc.raw_percent,
                "net_current": d.soc.net_current,
            }
        ),
    ),
    SyncXSensorDescription(
        key="battery_level_reported",
        translation_key="battery_level_reported",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: to_float(d.stat("battery_charge_percentage")),
    ),
    SyncXSensorDescription(
        key="backup_time_remaining",
        translation_key="backup_time_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda d: duration_to_minutes(d.stat("available_backup")),
    ),
    SyncXSensorDescription(
        key="charging_time_remaining",
        translation_key="charging_time_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda d: duration_to_minutes(d.stat("time_remaining_for_charging")),
    ),
    # Grid
    SyncXSensorDescription(
        key="grid_voltage",
        translation_key="grid_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: to_float(d.stat("input_voltage")),
    ),
    SyncXSensorDescription(
        key="grid_current",
        translation_key="grid_current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: to_float(d.stat("gridCTCurrent")),
    ),
    SyncXSensorDescription(
        key="grid_power",
        translation_key="grid_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_grid_power,
    ),
    # Load and inverter output
    SyncXSensorDescription(
        key="load_power",
        translation_key="load_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _kw_to_w(d.stat("consumptionValue")),
    ),
    SyncXSensorDescription(
        key="load_percentage",
        translation_key="load_percentage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: to_float(d.stat("current_running_load_percentage")),
    ),
    SyncXSensorDescription(
        key="output_voltage",
        translation_key="output_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: to_float(d.top("outputVoltage")),
    ),
    SyncXSensorDescription(
        key="output_current",
        translation_key="output_current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: to_float(d.stat("inverterCurrent")),
    ),
    SyncXSensorDescription(
        key="consumption_today",
        translation_key="consumption_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: to_float(d.top("todayConsumption")),
    ),
    SyncXSensorDescription(
        key="consumption_lifetime",
        translation_key="consumption_lifetime",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: to_float(d.top("lifetimeConsumption")),
    ),
    SyncXSensorDescription(
        key="inverter_frequency",
        translation_key="inverter_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda d: to_float(d.stat("inverterFrequency")),
    ),
    # Environmental totals
    SyncXSensorDescription(
        key="co2_saved",
        translation_key="co2_saved",
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: to_float(d.top("co2EmissionSaved")),
    ),
    SyncXSensorDescription(
        key="coal_not_burned",
        translation_key="coal_not_burned",
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: to_float(d.top("coalNotBurned")),
    ),
    SyncXSensorDescription(
        key="trees_equivalent",
        translation_key="trees_equivalent",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: to_float(d.top("equivalentTreesPlanted")),
    ),
    SyncXSensorDescription(
        key="peak_solar_power_today",
        translation_key="peak_solar_power_today",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda d: _kw_to_w(
            d.generation_trend.get("maxInstantiateGenerationEntryValue")
        ),
    ),
    SyncXSensorDescription(
        key="average_solar_power_today",
        translation_key="average_solar_power_today",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda d: _kw_to_w(d.generation_trend.get("todayAvgGeneration")),
    ),
    SyncXSensorDescription(
        key="peak_load_power_today",
        translation_key="peak_load_power_today",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda d: _kw_to_w(
            d.consumption_trend.get("maxInstantiateConsumptionEntryValue")
        ),
    ),
    SyncXSensorDescription(
        key="average_load_power_today",
        translation_key="average_load_power_today",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda d: _kw_to_w(d.consumption_trend.get("todayAvgConsumption")),
    ),
    SyncXSensorDescription(
        key="latest_alert",
        translation_key="latest_alert",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_latest_alert,
        attrs_fn=_latest_alert_attrs,
    ),
    # Diagnostics
    SyncXSensorDescription(
        key="wifi_signal_strength",
        translation_key="wifi_signal_strength",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: to_float(d.stat("wifi_signal_strength")),
    ),
    SyncXSensorDescription(
        key="last_reading",
        translation_key="last_reading",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.last_updated,
    ),
    SyncXSensorDescription(
        key="operating_mode",
        translation_key="operating_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.top("operatingMode"),
    ),
    SyncXSensorDescription(
        key="grid_direction",
        translation_key="grid_direction",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.grid_direction,
    ),
    SyncXSensorDescription(
        key="energy_flow",
        translation_key="energy_flow",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (
            ", ".join(
                name.replace("_", " ") for name, active in d.flows.items() if active
            )
            or "idle"
        ),
        attrs_fn=lambda d: {"flow_code": d.top("animationFlow"), **d.flows},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SyncXConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator = entry.runtime_data
    async_add_entities(SyncXSensor(coordinator, description) for description in SENSORS)


class SyncXSensor(SyncXEntity, SensorEntity):
    """A single reading from the plant."""

    entity_description: SyncXSensorDescription

    @property
    def native_value(self) -> Any:
        """Return the current value."""
        return self.entity_description.value_fn(self.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return supporting detail where the description supplies it."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.data)
