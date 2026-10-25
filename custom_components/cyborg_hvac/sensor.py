"""Sensors: return air temperature, humidity, voltage and zone temperatures."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .comfort_entity import AdviceSensor
from .coordinator import CyborgRuntimeData
from .core.model import Capabilities, SensorKind
from .entity import CyborgEntity, CyborgZoneEntity, zone_subentries


@dataclass(frozen=True, kw_only=True)
class UnitSensorDescription(SensorEntityDescription):
    """Describes a unit sensor."""

    kind: SensorKind
    unit_fn: Callable[[Capabilities], str | None] | None = None


def _temperature_unit(caps: Capabilities) -> str:
    return (
        UnitOfTemperature.FAHRENHEIT if caps.temperature_unit == "F" else UnitOfTemperature.CELSIUS
    )


UNIT_SENSORS = (
    UnitSensorDescription(
        key="return_air_temperature",
        translation_key="return_air_temperature",
        kind=SensorKind.RETURN_AIR_TEMPERATURE,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        unit_fn=_temperature_unit,
    ),
    UnitSensorDescription(
        key="humidity",
        kind=SensorKind.HUMIDITY,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    UnitSensorDescription(
        key="voltage",
        kind=SensorKind.VOLTAGE,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[CyborgRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add unit sensors, and zone temperatures where the brand reports them."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        UnitSensor(coordinator, unit_id, description)
        for unit_id, info in coordinator.units.items()
        for description in UNIT_SENSORS
        if description.kind in info.capabilities.sensors
    )
    async_add_entities(AdviceSensor(coordinator, unit_id) for unit_id in coordinator.units)
    for subentry in zone_subentries(coordinator):
        if coordinator.units[subentry.data["unit_id"]].capabilities.zone_temperatures:
            async_add_entities(
                [ZoneTemperatureSensor(coordinator, subentry)],
                config_subentry_id=subentry.subentry_id,
            )


class UnitSensor(CyborgEntity, SensorEntity):
    """A measurement reported by the unit."""

    entity_description: UnitSensorDescription

    def __init__(self, coordinator, unit_id: str, description: UnitSensorDescription) -> None:
        """Bind to a measurement."""
        super().__init__(coordinator, unit_id, description.key)
        self.entity_description = description
        if description.unit_fn is not None:
            self._attr_native_unit_of_measurement = description.unit_fn(self.caps)

    @property
    def native_value(self) -> float | None:
        """Return the measurement."""
        state = self.unit_state
        return state.sensors.get(self.entity_description.kind) if state else None


class ZoneTemperatureSensor(CyborgZoneEntity, SensorEntity):
    """A zone's own temperature, for brands with a sensor in each zone."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, subentry) -> None:
        """Bind to the zone."""
        super().__init__(coordinator, subentry, "temperature")
        self._attr_native_unit_of_measurement = _temperature_unit(self.caps)

    @property
    def native_value(self) -> float | None:
        """Return the zone temperature."""
        zone = self.zone_state
        return zone.temperature if zone else None
