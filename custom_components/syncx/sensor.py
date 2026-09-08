"""Sensor platform for Luminous ConnectX."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfApparentPower,
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
from .coordinator import (
    SyncXCoordinator,
    SyncXData,
    duration_to_minutes,
    to_float,
)
from .entity import SyncXEntity


@dataclass(frozen=True, kw_only=True)
class SyncXSensorDescription(SensorEntityDescription):
    """Describes one Luminous ConnectX sensor."""

    value_fn: Callable[[SyncXData], Any]
    attrs_fn: Callable[[SyncXData], dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class SyncXEnergyDescription(SensorEntityDescription):
    """Describes an energy total integrated from a power reading.

    ``power_fn`` returns the instantaneous power in watts attributable to this
    total, and never a negative number: import and export are separate counters
    so that each one only ever climbs, which is what the energy dashboard
    expects of a ``total_increasing`` sensor.
    """

    power_fn: Callable[[SyncXData], float | None]


# Ignore a gap longer than this when integrating. After an outage the inverter
# jumps forward hours, and carrying the last known power across that whole
# window would invent energy that was never measured.
MAX_INTEGRATION_GAP = timedelta(hours=1)


def _kw_to_w(value: Any) -> float | None:
    """Convert a kilowatt reading to watts."""
    result = to_float(value)
    return None if result is None else round(result * 1000.0, 1)


def _apparent(amps: Any, volts: Any) -> float | None:
    """Return volt amperes from a raw current and voltage pair."""
    a = to_float(amps)
    v = to_float(volts)
    if a is None or v is None:
        return None
    return round(a * v, 1)


def _battery_power(data: SyncXData) -> float | None:
    """Return battery power in watts, positive while charging.

    Resolved by the coordinator, which prefers an external battery management
    system reading over the inverter's own current sensing when one is set up.
    """
    return data.battery_power


def _grid_power(data: SyncXData) -> float | None:
    """Return grid power in watts, negative while exporting."""
    return data.grid_power


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
        value_fn=lambda d: d.load_power,
    ),
    SyncXSensorDescription(
        key="output_apparent_power",
        translation_key="output_apparent_power",
        device_class=SensorDeviceClass.APPARENT_POWER,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _apparent(d.stat("inverterCurrent"), d.top("outputVoltage")),
    ),
    SyncXSensorDescription(
        key="grid_apparent_power",
        translation_key="grid_apparent_power",
        device_class=SensorDeviceClass.APPARENT_POWER,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _apparent(d.stat("gridCTCurrent"), d.stat("input_voltage")),
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


def _import_power(data: SyncXData) -> float | None:
    """Return grid power in watts while importing, else zero."""
    power = _grid_power(data)
    if power is None:
        return None
    return power if power > 0 else 0.0


def _export_power(data: SyncXData) -> float | None:
    """Return grid power in watts while exporting, else zero."""
    power = _grid_power(data)
    if power is None:
        return None
    return -power if power < 0 else 0.0


def _charge_power(data: SyncXData) -> float | None:
    """Return battery power in watts while charging, else zero."""
    power = _battery_power(data)
    if power is None:
        return None
    return power if power > 0 else 0.0


def _discharge_power(data: SyncXData) -> float | None:
    """Return battery power in watts while discharging, else zero."""
    power = _battery_power(data)
    if power is None:
        return None
    return -power if power < 0 else 0.0


ENERGY_SENSORS: tuple[SyncXEnergyDescription, ...] = (
    SyncXEnergyDescription(
        key="grid_imported_energy",
        translation_key="grid_imported_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        power_fn=_import_power,
    ),
    SyncXEnergyDescription(
        key="grid_exported_energy",
        translation_key="grid_exported_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        power_fn=_export_power,
    ),
    SyncXEnergyDescription(
        key="battery_charged_energy",
        translation_key="battery_charged_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        power_fn=_charge_power,
    ),
    SyncXEnergyDescription(
        key="battery_discharged_energy",
        translation_key="battery_discharged_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        power_fn=_discharge_power,
    ),
    SyncXEnergyDescription(
        key="home_consumed_energy",
        translation_key="home_consumed_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        power_fn=lambda d: d.load_power,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SyncXConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        SyncXSensor(coordinator, description) for description in SENSORS
    ]
    entities.extend(
        SyncXEnergySensor(coordinator, description) for description in ENERGY_SENSORS
    )
    async_add_entities(entities)


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


class SyncXEnergySensor(SyncXEntity, RestoreSensor):
    """An energy total integrated from one of the power readings.

    The service reports no energy counters for grid or battery on this hardware,
    only instantaneous power, so the totals the energy dashboard needs are
    accumulated here instead. Each new sample contributes the trapezoid between
    the previous power and the current one, which is closer than holding either
    value flat across the interval.

    Accuracy is bounded by the five minute sampling rate. Steady loads integrate
    well; short spikes between two polls are not seen at all. The totals are
    restored across restarts so history is not lost.
    """

    entity_description: SyncXEnergyDescription

    def __init__(
        self,
        coordinator: SyncXCoordinator,
        description: SyncXEnergyDescription,
    ) -> None:
        """Initialise the accumulator."""
        super().__init__(coordinator, description)
        self._total: float = 0.0
        self._last_power: float | None = None
        self._last_reading: datetime | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the previous total and seed the integration."""
        await super().async_added_to_hass()

        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            try:
                self._total = float(last.native_value)
            except (TypeError, ValueError):
                self._total = 0.0

        self._accumulate()

    def _handle_coordinator_update(self) -> None:
        """Integrate the new sample before publishing it."""
        self._accumulate()
        super()._handle_coordinator_update()

    def _accumulate(self) -> None:
        """Add the energy represented by the newest sample."""
        data = self.data
        reading = data.last_updated
        power = self.entity_description.power_fn(data)

        if reading is None or power is None:
            return

        previous_time = self._last_reading
        previous_power = self._last_power

        # Always advance the reference point, even when the sample cannot be
        # used, so the next interval is measured from the right place.
        self._last_reading = reading
        self._last_power = power

        if previous_time is None or previous_power is None:
            return
        if reading <= previous_time:
            return
        if reading - previous_time > MAX_INTEGRATION_GAP:
            return

        hours = (reading - previous_time).total_seconds() / 3600.0
        self._total += (previous_power + power) / 2.0 * hours / 1000.0

    @property
    def native_value(self) -> float:
        """Return the accumulated energy in kilowatt hours."""
        return round(self._total, 3)
