"""Feature switches (Powerful, Quiet, Eco...) and a power switch per zone."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .comfort_entity import GuestModeSwitch
from .coordinator import CyborgRuntimeData
from .core.model import Command, Feature, FeatureKind, ZoneCommand
from .entity import CyborgEntity, CyborgZoneEntity, zone_subentries

CONFIG_FEATURES = {Feature.DISPLAY, Feature.BEEP}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[CyborgRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add feature switches for each unit and a power switch for each zone."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        FeatureSwitch(coordinator, unit_id, feature)
        for unit_id, info in coordinator.units.items()
        for feature, spec in info.capabilities.features.items()
        if spec.kind is FeatureKind.SWITCH
    )
    async_add_entities(GuestModeSwitch(coordinator, unit_id) for unit_id in coordinator.units)
    for subentry in zone_subentries(coordinator):
        async_add_entities(
            [ZoneSwitch(coordinator, subentry)], config_subentry_id=subentry.subentry_id
        )


class FeatureSwitch(CyborgEntity, SwitchEntity):
    """Turns one unit feature on or off."""

    def __init__(self, coordinator, unit_id: str, feature: Feature) -> None:
        """Bind to a feature."""
        super().__init__(coordinator, unit_id, feature.value)
        self._feature = feature
        spec = self.caps.features[feature]
        self._attr_translation_key = feature.value
        self._attr_entity_registry_enabled_default = spec.verified
        if feature in CONFIG_FEATURES:
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool | None:
        """Return True when the feature is on."""
        state = self.unit_state
        if state is None or self._feature not in state.features:
            return None
        return bool(state.features[self._feature])

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the feature on. Mode rules are checked before sending."""
        await self.async_send(Command(features={self._feature: True}))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the feature off."""
        await self.async_send(Command(features={self._feature: False}))


class ZoneSwitch(CyborgZoneEntity, SwitchEntity):
    """Switches a zone on or off without changing its opening."""

    _attr_translation_key = "zone_power"

    def __init__(self, coordinator, subentry) -> None:
        """Bind to the zone."""
        super().__init__(coordinator, subentry, "power")

    @property
    def is_on(self) -> bool | None:
        """Return True when the zone is on."""
        zone = self.zone_state
        return zone.on if zone else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the zone on, restoring a closed zone to fully open."""
        zone = self.zone_state
        opening = None if zone and zone.opening > 0 else self.caps.zone_opening_max
        await self.async_send(Command(zones={self.zone: ZoneCommand(on=True, opening=opening)}))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the zone off."""
        await self.async_send(Command(zones={self.zone: ZoneCommand(on=False)}))
