"""Number entities for numeric features such as the temperature offset."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .comfort_entity import SleepTargetNumber
from .coordinator import CyborgRuntimeData
from .core.model import Command, Feature, FeatureKind
from .entity import CyborgEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[CyborgRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a number for every numeric feature."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(SleepTargetNumber(coordinator, unit_id) for unit_id in coordinator.units)
    async_add_entities(
        FeatureNumber(coordinator, unit_id, feature)
        for unit_id, info in coordinator.units.items()
        for feature, spec in info.capabilities.features.items()
        if spec.kind is FeatureKind.NUMBER
    )


class FeatureNumber(CyborgEntity, NumberEntity):
    """Sets a numeric unit feature."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, unit_id: str, feature: Feature) -> None:
        """Bind to a feature."""
        super().__init__(coordinator, unit_id, feature.value)
        self._feature = feature
        spec = self.caps.features[feature]
        self._attr_translation_key = feature.value
        self._attr_native_min_value = spec.min_value
        self._attr_native_max_value = spec.max_value
        self._attr_native_step = spec.step
        self._attr_entity_registry_enabled_default = spec.verified
        if feature is Feature.TEMP_OFFSET:
            # An offset, so no temperature device class (it must not be converted
            # as an absolute temperature).
            self._attr_native_unit_of_measurement = (
                UnitOfTemperature.FAHRENHEIT
                if self.caps.temperature_unit == "F"
                else UnitOfTemperature.CELSIUS
            )

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        state = self.unit_state
        value = state.features.get(self._feature) if state else None
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        await self.async_send(Command(features={self._feature: value}))
