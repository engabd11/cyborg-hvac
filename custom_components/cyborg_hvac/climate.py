"""Climate entity for each unit."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    PRESET_BOOST,
    PRESET_ECO,
    PRESET_NONE,
    PRESET_SLEEP,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CyborgRuntimeData
from .core.model import FAN_SILENT, Command, Feature, FeatureKind, HvacMode
from .entity import CyborgEntity

TO_HA_MODE = {
    HvacMode.HEAT: HVACMode.HEAT,
    HvacMode.COOL: HVACMode.COOL,
    HvacMode.DRY: HVACMode.DRY,
    HvacMode.FAN_ONLY: HVACMode.FAN_ONLY,
    HvacMode.AUTO: HVACMode.HEAT_COOL,
}
FROM_HA_MODE = {ha: core for core, ha in TO_HA_MODE.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[CyborgRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a climate entity for every unit."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(UnitClimate(coordinator, unit_id) for unit_id in coordinator.units)


class UnitClimate(CyborgEntity, ClimateEntity):
    """The unit: power, mode, setpoint, fan and presets."""

    _attr_name = None
    _attr_translation_key = "unit"

    def __init__(self, coordinator, unit_id: str) -> None:
        """Describe the unit from its capabilities."""
        super().__init__(coordinator, unit_id, "climate")
        caps = self.caps
        self._attr_hvac_modes = [HVACMode.OFF] + [TO_HA_MODE[m] for m in caps.hvac_modes]
        self._attr_min_temp = caps.min_temp
        self._attr_max_temp = caps.max_temp
        self._attr_target_temperature_step = caps.temp_step
        self._attr_temperature_unit = (
            UnitOfTemperature.FAHRENHEIT
            if caps.temperature_unit == "F"
            else UnitOfTemperature.CELSIUS
        )
        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if caps.fan_speeds:
            self._attr_fan_modes = list(caps.fan_speeds)
            features |= ClimateEntityFeature.FAN_MODE
        presets = [PRESET_NONE]
        if Feature.POWERFUL in caps.features:
            presets.append(PRESET_BOOST)
        if Feature.ECO in caps.features:
            presets.append(PRESET_ECO)
        if Feature.SLEEP in caps.features:
            presets.append(PRESET_SLEEP)
        if len(presets) > 1:
            self._attr_preset_modes = presets
            features |= ClimateEntityFeature.PRESET_MODE
        self._attr_supported_features = features

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return off while powered down, otherwise the unit's mode."""
        state = self.unit_state
        if state is None:
            return None
        if not state.power:
            return HVACMode.OFF
        return TO_HA_MODE.get(state.mode) if state.mode else None

    @property
    def current_temperature(self) -> float | None:
        """Return the temperature the unit measures (return air on ducted units)."""
        state = self.unit_state
        return state.current_temp if state else None

    @property
    def target_temperature(self) -> float | None:
        """Return the setpoint."""
        state = self.unit_state
        return state.target_temp if state else None

    @property
    def fan_mode(self) -> str | None:
        """Return the fan speed."""
        state = self.unit_state
        return state.fan_speed if state else None

    @property
    def preset_mode(self) -> str | None:
        """Return the preset that matches the active features."""
        state = self.unit_state
        if state is None:
            return None
        if state.features.get(Feature.POWERFUL):
            return PRESET_BOOST
        if state.features.get(Feature.ECO):
            return PRESET_ECO
        sleep = state.features.get(Feature.SLEEP)
        if sleep and sleep != "off":
            return PRESET_SLEEP
        return PRESET_NONE

    def _mode_command(self, hvac_mode: HVACMode) -> Command:
        if hvac_mode == HVACMode.OFF:
            return Command(power=False)
        return Command(power=True, mode=FROM_HA_MODE[hvac_mode])

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Turn off, or turn on in a mode."""
        await self.async_send(self._mode_command(hvac_mode))

    async def async_turn_on(self) -> None:
        """Turn on in the last mode."""
        await self.async_send(Command(power=True))

    async def async_turn_off(self) -> None:
        """Turn off."""
        await self.async_send(Command(power=False))

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the setpoint, optionally with a mode in the same write."""
        command = Command(target_temp=self.caps.round_temp(float(kwargs[ATTR_TEMPERATURE])))
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            command = self._mode_command(hvac_mode).merge(command)
        await self.async_send(command)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the fan speed. Leaving the silent speed also turns Quiet off."""
        state = self.unit_state
        features: dict[Feature, Any] = {}
        if fan_mode != FAN_SILENT and state is not None and state.features.get(Feature.QUIET):
            features[Feature.QUIET] = False
        await self.async_send(Command(fan_speed=fan_mode, features=features))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Map a preset onto the unit's features."""
        features: dict[Feature, Any] = {}
        caps_features = self.caps.features
        sleep_spec = caps_features.get(Feature.SLEEP)
        sleep_on: Any = True
        sleep_off: Any = False
        if sleep_spec is not None and sleep_spec.kind is FeatureKind.SELECT:
            sleep_on, sleep_off = "general", "off"

        wanted = {
            Feature.POWERFUL: preset_mode == PRESET_BOOST,
            Feature.ECO: preset_mode == PRESET_ECO,
            Feature.SLEEP: sleep_on if preset_mode == PRESET_SLEEP else sleep_off,
        }
        state = self.unit_state
        for feature, value in wanted.items():
            if feature not in caps_features:
                continue
            current = state.features.get(feature) if state else None
            # Only send changes, so turning a preset off never trips mode rules.
            if current != value:
                features[feature] = value
        await self.async_send(Command(features=features))
