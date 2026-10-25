"""Generic model of a ducted HVAC unit and its zones."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class HvacMode(StrEnum):
    """Operating mode of a unit. Power is tracked separately from the mode."""

    HEAT = "heat"
    COOL = "cool"
    DRY = "dry"
    FAN_ONLY = "fan_only"
    AUTO = "auto"


class Feature(StrEnum):
    """Optional unit features a brand may support."""

    POWERFUL = "powerful"
    ECO = "eco"
    QUIET = "quiet"
    SLEEP = "sleep"
    FROST_GUARD = "frost_guard"
    AI = "ai"
    HEALTH = "health"
    FRESH_AIR = "fresh_air"
    DISPLAY = "display"
    BEEP = "beep"
    TEMP_OFFSET = "temp_offset"


class FeatureKind(StrEnum):
    """How a feature is controlled."""

    SWITCH = "switch"
    SELECT = "select"
    NUMBER = "number"


class SensorKind(StrEnum):
    """Read-only unit measurements a brand may report."""

    RETURN_AIR_TEMPERATURE = "return_air_temperature"
    HUMIDITY = "humidity"
    VOLTAGE = "voltage"


# Fan speed keys shared by all brands. Brands map their codes onto these.
FAN_AUTO = "auto"
FAN_SILENT = "silent"
FAN_SPEEDS_ORDERED = (
    "silent",
    "low",
    "medium_low",
    "medium",
    "medium_high",
    "high",
    "turbo",
)


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """Describes one feature and when the unit accepts it."""

    feature: Feature
    kind: FeatureKind
    options: tuple[str, ...] = ()
    min_value: float = 0
    max_value: float = 0
    step: float = 1
    # Modes in which the unit accepts the feature being switched on. None means any.
    valid_modes: frozenset[HvacMode] | None = None
    # Whether the unit must be powered on for the feature to be switched on.
    requires_power: bool = True
    # Features not yet confirmed on real hardware start disabled in Home Assistant.
    verified: bool = True

    def accepts(self, power: bool, mode: HvacMode | None) -> bool:
        """Return True when the feature can be enabled in this state."""
        if self.requires_power and not power:
            return False
        return not (self.valid_modes is not None and mode not in self.valid_modes)


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a unit can do, discovered once at setup."""

    hvac_modes: tuple[HvacMode, ...]
    fan_speeds: tuple[str, ...] = ()
    min_temp: float = 16
    max_temp: float = 32
    temp_step: float = 1
    temperature_unit: str = "C"
    features: dict[Feature, FeatureSpec] = field(default_factory=dict)
    sensors: tuple[SensorKind, ...] = ()
    # Number of zone slots the controller supports (0 for units without zones).
    zone_slots: int = 0
    zone_opening_step: int = 5
    zone_opening_min: int = 0
    zone_opening_max: int = 100
    # True when the brand reports a temperature for each zone.
    zone_temperatures: bool = False
    has_filter_alert: bool = False
    has_fault_report: bool = False

    def round_opening(self, value: float) -> int:
        """Clamp and round a zone opening to the controller's step."""
        step = max(1, self.zone_opening_step)
        rounded = int(round(value / step) * step)
        return max(self.zone_opening_min, min(self.zone_opening_max, rounded))

    def round_temp(self, value: float) -> float:
        """Clamp and round a setpoint to the unit's step."""
        step = self.temp_step or 1
        rounded = round(round(value / step) * step, 1)
        return max(self.min_temp, min(self.max_temp, rounded))


@dataclass(frozen=True, slots=True)
class UnitInfo:
    """Static description of a unit."""

    unit_id: str
    name: str
    brand: str
    manufacturer: str
    model: str
    capabilities: Capabilities
    model_id: str | None = None
    sw_version: str | None = None
    room: str | None = None
    # Zones that look installed from the current state, used to pre-fill setup.
    installed_zones_hint: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class ZoneState:
    """State of one zone."""

    on: bool
    opening: int
    temperature: float | None = None


@dataclass(frozen=True, slots=True)
class UnitState:
    """Live state of a unit."""

    online: bool = True
    power: bool = False
    mode: HvacMode | None = None
    target_temp: float | None = None
    current_temp: float | None = None
    fan_speed: str | None = None
    features: dict[Feature, Any] = field(default_factory=dict)
    zones: dict[int, ZoneState] = field(default_factory=dict)
    sensors: dict[SensorKind, float | None] = field(default_factory=dict)
    filter_alert: bool | None = None
    faults: tuple[str, ...] = ()
    # Brand-specific raw status, kept for diagnostics only.
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def running_mode(self) -> HvacMode | None:
        """Return the mode while powered on, otherwise None."""
        return self.mode if self.power else None


@dataclass(frozen=True, slots=True)
class ZoneCommand:
    """Requested change to one zone. None leaves a value unchanged."""

    on: bool | None = None
    opening: int | None = None

    def merge(self, other: ZoneCommand) -> ZoneCommand:
        """Combine with a later command; later values win."""
        return ZoneCommand(
            on=other.on if other.on is not None else self.on,
            opening=other.opening if other.opening is not None else self.opening,
        )


@dataclass(frozen=True, slots=True)
class Command:
    """Requested change to a unit. None and empty fields leave values unchanged."""

    power: bool | None = None
    mode: HvacMode | None = None
    target_temp: float | None = None
    fan_speed: str | None = None
    features: dict[Feature, Any] = field(default_factory=dict)
    zones: dict[int, ZoneCommand] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        """Return True when the command changes nothing."""
        return (
            self.power is None
            and self.mode is None
            and self.target_temp is None
            and self.fan_speed is None
            and not self.features
            and not self.zones
        )

    def merge(self, other: Command) -> Command:
        """Combine with a later command; later values win."""
        zones = dict(self.zones)
        for index, zone in other.zones.items():
            zones[index] = zones[index].merge(zone) if index in zones else zone
        return Command(
            power=other.power if other.power is not None else self.power,
            mode=other.mode if other.mode is not None else self.mode,
            target_temp=(other.target_temp if other.target_temp is not None else self.target_temp),
            fan_speed=other.fan_speed if other.fan_speed is not None else self.fan_speed,
            features={**self.features, **other.features},
            zones=zones,
        )

    def apply_to(self, state: UnitState) -> UnitState:
        """Return the state the unit should reach once this command lands."""
        zones = dict(state.zones)
        for index, change in self.zones.items():
            current = zones.get(index, ZoneState(on=False, opening=0))
            zones[index] = replace(
                current,
                on=change.on if change.on is not None else current.on,
                opening=change.opening if change.opening is not None else current.opening,
            )
        return replace(
            state,
            power=self.power if self.power is not None else state.power,
            mode=self.mode if self.mode is not None else state.mode,
            target_temp=(self.target_temp if self.target_temp is not None else state.target_temp),
            fan_speed=self.fan_speed if self.fan_speed is not None else state.fan_speed,
            features={**state.features, **self.features},
            zones=zones,
        )

    def satisfied_by(self, state: UnitState) -> bool:
        """Return True when the state already shows every value in this command."""
        return self.apply_to(state) == state
