"""Binary sensors: cloud connection, filter and faults."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CyborgRuntimeData
from .entity import CyborgEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[CyborgRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add status binary sensors for every unit."""
    coordinator = entry.runtime_data.coordinator
    entities: list[BinarySensorEntity] = []
    for unit_id, info in coordinator.units.items():
        entities.append(ConnectivitySensor(coordinator, unit_id))
        if info.capabilities.has_filter_alert:
            entities.append(FilterSensor(coordinator, unit_id))
        if info.capabilities.has_fault_report:
            entities.append(FaultSensor(coordinator, unit_id))
    async_add_entities(entities)


class ConnectivitySensor(CyborgEntity, BinarySensorEntity):
    """Whether the unit is reachable through its cloud or controller."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, unit_id: str) -> None:
        """Bind to the unit."""
        super().__init__(coordinator, unit_id, "connected")

    @property
    def available(self) -> bool:
        """Stay available so it can report the unit going offline."""
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        """Return True while the unit is online."""
        state = self.unit_state
        return bool(state and state.online)


class FilterSensor(CyborgEntity, BinarySensorEntity):
    """The unit asks for its filter to be cleaned."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "filter"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # The meaning of the filter flag on ducted units is still being confirmed.
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, unit_id: str) -> None:
        """Bind to the unit."""
        super().__init__(coordinator, unit_id, "filter")

    @property
    def is_on(self) -> bool | None:
        """Return True when cleaning is due."""
        state = self.unit_state
        return state.filter_alert if state else None


class FaultSensor(CyborgEntity, BinarySensorEntity):
    """The unit reports one or more faults."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "fault"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, unit_id: str) -> None:
        """Bind to the unit."""
        super().__init__(coordinator, unit_id, "fault")

    @property
    def is_on(self) -> bool | None:
        """Return True when any fault is active."""
        state = self.unit_state
        return bool(state.faults) if state else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the active fault codes."""
        state = self.unit_state
        return {"faults": list(state.faults)} if state else {}
