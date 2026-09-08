"""Shared entity base for Luminous ConnectX."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_PLANT_NAME, DOMAIN
from .coordinator import SyncXCoordinator, SyncXData


class SyncXEntity(CoordinatorEntity[SyncXCoordinator]):
    """Base entity tying every reading to the one plant device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SyncXCoordinator,
        description: EntityDescription,
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.plant_id}_{description.key}"

    @property
    def data(self) -> SyncXData:
        """Return the most recent poll."""
        return self.coordinator.data

    @property
    def device_info(self) -> DeviceInfo:
        """Describe the plant as a single device."""
        data = self.coordinator.data
        entry = self.coordinator.config_entry

        model = data.stats.get("inverterModel") or data.inverter.get("model")
        logger_serial = data.logger.get("serialNo") or data.stats.get("deviceId")

        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.plant_id)},
            name=entry.data.get(CONF_PLANT_NAME) or entry.title,
            manufacturer="Luminous",
            model=model,
            serial_number=data.inverter.get("serialNo") or logger_serial,
            sw_version=data.logger.get("firmWareVersion"),
            configuration_url="https://luminousconnectx.com/",
        )

    @property
    def available(self) -> bool:
        """Report unavailable when the poll failed outright."""
        return super().available and self.coordinator.data is not None
