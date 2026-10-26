"""How much comfort is worth right now: Eco, Mid or Max.

Max is for free power (exporting, or plenty of solar): every room is aimed at
the middle of its range, even empty ones and while everyone is out. Mid is the
everyday balance (solar charging the battery). Eco runs on stored or bought
power: rooms are let nearer the edges of their range, and outside gets longer
to do the work.

The mode follows the solar, battery and grid sensors (Auto), or is set by hand.
Bedtime has its own profile for the sleep zones, whatever the mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from .snapshot import HouseSnapshot


class EnergySetting(StrEnum):
    """What a person chose: follow the sensors, or one mode."""

    AUTO = "auto"
    ECO = "eco"
    MID = "mid"
    MAX = "max"


class EnergyMode(StrEnum):
    """How much comfort is worth right now."""

    ECO = "eco"
    MID = "mid"
    MAX = "max"


@dataclass(frozen=True, slots=True)
class Profile:
    """How the engine paces itself in one energy mode."""

    mode: EnergyMode
    # Degrees inside the band edge a room aims for (heating: low + inset, cooling: high - inset).
    inset: float
    # Degrees past its aim before a room needs the unit.
    act: float
    # Extra degrees before the mode the season is against is used.
    gate: float
    # Minutes outside may take to bring a room back before the unit does it.
    horizon: int
    # How long a room must stay stuck before heating switches to cooling (or back)...
    changeover: timedelta
    # ...and the least time between two such switches.
    dwell: timedelta
    # Degrees the neediest room is short before the fan goes high.
    pull_down: float
    # Fan speed while the unit only moves air.
    vent_fan: str = "low"
    # Aim for the middle of the range (within half a degree) instead of near its edges.
    centre: bool = False
    # Empty and second rooms get full comfort, and so does the house while everyone is out.
    everywhere: bool = False


PROFILES = {
    EnergyMode.ECO: Profile(
        EnergyMode.ECO,
        inset=0.3,
        act=0.5,
        gate=2.0,
        horizon=180,
        changeover=timedelta(minutes=20),
        dwell=timedelta(minutes=60),
        pull_down=2.0,
    ),
    EnergyMode.MID: Profile(
        EnergyMode.MID,
        inset=0.5,
        act=0.5,
        gate=1.5,
        horizon=120,
        changeover=timedelta(minutes=20),
        dwell=timedelta(minutes=60),
        pull_down=2.0,
    ),
    EnergyMode.MAX: Profile(
        EnergyMode.MAX,
        inset=0.5,
        act=0.3,
        gate=0.25,
        horizon=30,
        changeover=timedelta(minutes=10),
        dwell=timedelta(minutes=30),
        pull_down=1.0,
        vent_fan="medium",
        centre=True,
        everywhere=True,
    ),
}

# Bedtime holds the sleep zones within half a degree, whatever the energy mode.
BEDTIME = Profile(
    EnergyMode.MAX,
    inset=0.5,
    act=0.3,
    gate=0.0,
    horizon=45,
    changeover=timedelta(minutes=10),
    dwell=timedelta(minutes=30),
    pull_down=1.0,
)

# Auto: export or solar output that counts as free power, and what keeps it there.
EXPORT_START_W = 300.0
EXPORT_STAY_W = 100.0
SOLAR_STAY_MARGIN_W = 1000.0
# Solar charging the battery (or, without a battery sensor, this much solar) is Mid.
SOLAR_MIN_W = 300.0
SOLAR_ONLY_W = 1500.0
CHARGE_MIN_W = 200.0
DISCHARGE_W = -200.0
# A new mode must hold this long before it is used, so a passing cloud changes nothing.
SETTLE = timedelta(minutes=10)


@dataclass(slots=True)
class EnergyMemory:
    """The mode in use, and a different one waiting to settle."""

    mode: EnergyMode | None = None
    pending: EnergyMode | None = None
    since: datetime | None = None


def _kw(watts: float) -> str:
    return f"{watts / 1000:.1f} kW"


def _wanted(s: HouseSnapshot, current: EnergyMode | None) -> tuple[EnergyMode, str]:
    export = s.solar_export_w
    solar = s.solar_w
    battery = s.battery_w
    soc = f" ({s.battery_soc:.0f}%)" if s.battery_soc is not None else ""
    plenty = s.settings.max_solar_w
    staying = current is EnergyMode.MAX
    if export is not None and export >= (EXPORT_STAY_W if staying else EXPORT_START_W):
        return EnergyMode.MAX, f"Exporting {_kw(export)}: comfort comes first."
    if solar is not None and solar >= plenty - (SOLAR_STAY_MARGIN_W if staying else 0.0):
        return EnergyMode.MAX, f"Solar is making {_kw(solar)}: comfort comes first."
    if solar is not None and solar >= SOLAR_MIN_W:
        if battery is None and solar >= SOLAR_ONLY_W:
            return EnergyMode.MID, f"Solar is making {_kw(solar)}: balancing comfort and power."
        if battery is not None and battery >= CHARGE_MIN_W:
            return (
                EnergyMode.MID,
                f"Solar is charging the battery{soc} at {_kw(battery)}: balancing.",
            )
    if battery is not None and battery <= DISCHARGE_W:
        return EnergyMode.ECO, f"Running on the battery{soc}: saving power."
    return EnergyMode.ECO, "Little spare solar: saving power."


def resolve(
    s: HouseSnapshot, setting: EnergySetting, memory: EnergyMemory
) -> tuple[EnergyMode, str]:
    """Return the energy mode for now, and why in a few words."""
    if setting is not EnergySetting.AUTO:
        mode = EnergyMode(setting.value)
        memory.mode, memory.pending, memory.since = mode, None, None
        return mode, f"{mode.value.capitalize()}, set by hand."
    if s.solar_export_w is None and s.solar_w is None and s.battery_w is None:
        memory.mode, memory.pending, memory.since = EnergyMode.MID, None, None
        return EnergyMode.MID, "No solar or battery sensors: balancing comfort and power."
    wanted, reason = _wanted(s, memory.mode)
    if memory.mode is None or wanted is memory.mode:
        memory.mode, memory.pending, memory.since = wanted, None, None
        return wanted, reason
    if memory.pending is not wanted or memory.since is None:
        memory.pending, memory.since = wanted, s.now
    if s.now - memory.since >= SETTLE:
        memory.mode, memory.pending, memory.since = wanted, None, None
        return wanted, reason
    return memory.mode, f"{reason} Switching to {wanted.value.capitalize()} if it lasts."
