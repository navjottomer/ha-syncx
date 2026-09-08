"""Binary sensor platform for Luminous ConnectX."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SyncXConfigEntry
from .const import (
    FLOW_BATTERY_TO_CENTER,
    FLOW_CENTER_TO_BATTERY,
    FLOW_CENTER_TO_GRID,
    FLOW_GRID_TO_CENTER,
    FLOW_SOLAR_TO_CENTER,
)
from .coordinator import SyncXData
from .entity import SyncXEntity


@dataclass(frozen=True, kw_only=True)
class SyncXBinarySensorDescription(BinarySensorEntityDescription):
    """Describes one Luminous ConnectX binary sensor."""

    value_fn: Callable[[SyncXData], bool | None]


BINARY_SENSORS: tuple[SyncXBinarySensorDescription, ...] = (
    SyncXBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.online,
    ),
    SyncXBinarySensorDescription(
        key="grid_available",
        translation_key="grid_available",
        device_class=BinarySensorDeviceClass.POWER,
        # The dashboard paints the grid tile green on 1 and red on 0, so a 1
        # means mains power is present.
        value_fn=lambda d: (
            None if d.stat("grid_state") is None else str(d.stat("grid_state")) == "1"
        ),
    ),
    SyncXBinarySensorDescription(
        key="solar_producing",
        translation_key="solar_producing",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda d: (
            None if d.stat("solar_state") is None else str(d.stat("solar_state")) == "1"
        ),
    ),
    SyncXBinarySensorDescription(
        key="battery_charging",
        translation_key="battery_charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda d: d.flows.get(FLOW_CENTER_TO_BATTERY),
    ),
    SyncXBinarySensorDescription(
        key="battery_discharging",
        translation_key="battery_discharging",
        value_fn=lambda d: d.flows.get(FLOW_BATTERY_TO_CENTER),
    ),
    SyncXBinarySensorDescription(
        key="exporting_to_grid",
        translation_key="exporting_to_grid",
        value_fn=lambda d: d.flows.get(FLOW_CENTER_TO_GRID),
    ),
    SyncXBinarySensorDescription(
        key="importing_from_grid",
        translation_key="importing_from_grid",
        value_fn=lambda d: d.flows.get(FLOW_GRID_TO_CENTER),
    ),
    SyncXBinarySensorDescription(
        key="solar_contributing",
        translation_key="solar_contributing",
        value_fn=lambda d: d.flows.get(FLOW_SOLAR_TO_CENTER),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SyncXConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinator = entry.runtime_data
    async_add_entities(
        SyncXBinarySensor(coordinator, description) for description in BINARY_SENSORS
    )


class SyncXBinarySensor(SyncXEntity, BinarySensorEntity):
    """A single on or off state derived from the plant."""

    entity_description: SyncXBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        """Return the current state."""
        return self.entity_description.value_fn(self.data)
