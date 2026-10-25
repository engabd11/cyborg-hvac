"""Select entities for multi-choice features such as Sleep."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CyborgRuntimeData
from .core.model import Command, Feature, FeatureKind
from .entity import CyborgEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[CyborgRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a select for every multi-choice feature."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        FeatureSelect(coordinator, unit_id, feature)
        for unit_id, info in coordinator.units.items()
        for feature, spec in info.capabilities.features.items()
        if spec.kind is FeatureKind.SELECT and spec.options
    )


class FeatureSelect(CyborgEntity, SelectEntity):
    """Chooses one option of a unit feature."""

    def __init__(self, coordinator, unit_id: str, feature: Feature) -> None:
        """Bind to a feature."""
        super().__init__(coordinator, unit_id, feature.value)
        self._feature = feature
        spec = self.caps.features[feature]
        self._attr_translation_key = feature.value
        self._attr_options = list(spec.options)
        self._attr_entity_registry_enabled_default = spec.verified

    @property
    def current_option(self) -> str | None:
        """Return the active option."""
        state = self.unit_state
        value = state.features.get(self._feature) if state else None
        return value if value in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        """Choose an option."""
        await self.async_send(Command(features={self._feature: option}))
