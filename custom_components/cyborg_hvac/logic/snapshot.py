"""Everything the comfort logic knows about the house at one moment."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from enum import StrEnum

from ..core.model import Capabilities, SensorKind, UnitState


class Period(StrEnum):
    """Which comfort targets apply."""

    DAY = "day"
    NIGHT = "night"
    AWAY = "away"


class Priority(StrEnum):
    """How much a zone matters when deciding what the unit should do."""

    BEDROOM = "bedroom"
    LIVING = "living"
    UTILITY = "utility"


PRIORITY_WEIGHT = {Priority.BEDROOM: 1.0, Priority.LIVING: 1.0, Priority.UTILITY: 0.4}


@dataclass(frozen=True, slots=True)
class ComfortSettings:
    """Comfort targets. Below the heat target is cold; above the cool target is warm."""

    heat_day: float = 20.0
    heat_night: float = 18.0
    heat_away: float = 16.0
    cool_day: float = 24.0
    cool_night: float = 25.0
    cool_away: float = 28.0
    humidity_max: float = 65.0
    night_start: time = time(22, 0)
    night_end: time = time(6, 30)
    # Opening of all open zones added together, below which airflow is too low.
    min_total_opening: int = 150
    min_open_zones: int = 2
    vacant_close_minutes: int = 30
    short_away_minutes: int = 120
    # Solar output that counts as plenty (Max energy) even without export, in watts.
    max_solar_w: float = 6000.0
    # House battery charge (%) above which the fan keeps air moving while rooms are comfortable.
    vent_min_battery: float = 30.0


@dataclass(frozen=True, slots=True)
class ZoneInputs:
    """One zone with whatever room sensors it has."""

    zone: int
    name: str
    on: bool
    opening: int
    temperature: float | None = None
    humidity: float | None = None
    occupied: bool | None = None
    vacant_minutes: float | None = None
    window_open: bool = False
    offset: float = 0.0
    priority: Priority = Priority.LIVING
    min_opening: int = 0
    max_opening: int = 100
    spill: bool = False
    # Guest zones get priority while guest mode is on (living and dining areas).
    guest: bool = False
    # Sleep zones are held at the sleep temperature while sleep mode is on.
    sleep: bool = False
    # True when the open window or door is a door (for wording only).
    door_open: bool = False
    # Minutes in the last 12 hours that the room's cold surfaces were damp enough
    # for mould (tracked by the Home Assistant layer, see logic.health).
    damp_minutes: float = 0.0

    @property
    def has_sensor(self) -> bool:
        """Return True when the zone has its own temperature."""
        return self.temperature is not None


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    """One hour of the weather forecast."""

    time: datetime
    temperature: float | None
    humidity: float | None = None
    condition: str | None = None

    def __post_init__(self) -> None:
        """Treat a forecast time without a time zone as UTC (Home Assistant's convention).

        Some weather integrations (BOM among them) send naive times, which cannot be
        compared with the aware times everything else uses.
        """
        if self.time.tzinfo is None:
            object.__setattr__(self, "time", self.time.replace(tzinfo=UTC))


@dataclass(frozen=True, slots=True)
class HouseSnapshot:
    """Inputs for one unit and its zones."""

    now: datetime
    unit_name: str
    caps: Capabilities
    unit: UnitState
    zones: tuple[ZoneInputs, ...]
    settings: ComfortSettings = field(default_factory=ComfortSettings)
    outdoor_temp: float | None = None
    forecast: tuple[ForecastPoint, ...] = ()
    anyone_home: bool | None = None
    away_minutes: float | None = None
    # Power being exported to the grid in watts; None when unknown.
    solar_export_w: float | None = None
    # Solar output, and battery power (positive while charging), in watts; None when unknown.
    solar_w: float | None = None
    battery_w: float | None = None
    # House battery charge in percent; None when unknown.
    battery_soc: float | None = None
    period_override: Period | None = None
    guest_mode: bool = False
    sleep_mode: bool = False
    sleep_target: float = 22.0

    def secondary(self, zone: ZoneInputs) -> bool:
        """Return True when guest or sleep mode puts a zone second."""
        return (self.sleep_mode and not zone.sleep) or (
            self.guest_mode and not self.sleep_mode and not zone.guest
        )

    @property
    def sensed_zones(self) -> tuple[ZoneInputs, ...]:
        """Return zones with a room temperature."""
        return tuple(z for z in self.zones if z.has_sensor)

    @property
    def unit_humidity(self) -> float | None:
        """Return the humidity the unit measures in its return air, if it reports one."""
        return self.unit.sensors.get(SensorKind.HUMIDITY)

    @property
    def open_zones(self) -> tuple[ZoneInputs, ...]:
        """Return zones that are switched on with some opening."""
        return tuple(z for z in self.zones if z.on and z.opening > 0)
