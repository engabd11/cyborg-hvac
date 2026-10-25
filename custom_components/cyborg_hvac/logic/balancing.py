"""The smart setpoint: what to set the unit to so the rooms, not the return duct, get there.

A ducted unit only knows its return-air temperature. Rooms far from the
return grille, or with a lot of glass, drift away from it. The setpoint is
the rooms' aim corrected by how far return air sits from them, plus a gentle
push while the neediest room is short. Damper openings are decided by the
autopilot engine (logic.autopilot).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..core.model import HvacMode
from .comfort import zone_band, zone_weight
from .snapshot import HouseSnapshot, ZoneInputs

SATISFIED_MARGIN = 0.3
BIAS_LIMIT = 3.0
SETPOINT_SPAN = 4.0  # how far above (heat) or below (cool) the band the unit may be set
SHORTFALL_GAIN = 0.75  # extra setpoint per degree the neediest room is short
# Most the push from a short room may add: enough to keep the unit working, gentle
# enough that rooms arrive without overshooting. The slow trim adds to it only
# while a room stays short (see _trim).
OVERDRIVE_LIMIT = 1.5
OVERSHOOT_BACKOFF = 2.0  # most the setpoint eases off the aim when rooms overshoot


@dataclass(slots=True)
class BalanceMemory:
    """Slow correction of the unit setpoint while the neediest room stays short."""

    setpoint_trim: float = 0.0


def aim(s: HouseSnapshot, zone: ZoneInputs, mode: HvacMode) -> float:
    """Return the temperature a zone should settle at in this mode."""
    band = zone_band(s, zone)
    return band.low + 0.5 if mode is HvacMode.HEAT else band.high - 0.5


def smart_setpoint(
    s: HouseSnapshot,
    zones: list[ZoneInputs],
    mode: HvacMode,
    memory: BalanceMemory | None = None,
    minutes: float = 1.0,
    aims: Mapping[int, float] | None = None,
    *,
    rounded: bool = True,
) -> float | None:
    """Return the unit setpoint that gets the neediest occupied room to its aim.

    The unit stops when return air reaches its setpoint, so the setpoint is the
    room aim corrected by how far return air sits from the rooms. ``aims``
    overrides the band aim of any zone (pre-conditioning, solar, health).
    ``rounded=False`` returns the value before rounding to the unit's step.
    """

    def goal(zone: ZoneInputs) -> float:
        if aims is not None and zone.zone in aims:
            return aims[zone.zone]
        return aim(s, zone, mode)

    returned = s.unit.current_temp
    occupied = [z for z in zones if z.occupied is not False] or zones
    if returned is None or not occupied:
        return None
    total = sum(zone_weight(s, z) for z in occupied)
    rooms = sum(z.temperature * zone_weight(s, z) for z in occupied) / total  # type: ignore[operator]
    bias = max(-BIAS_LIMIT, min(BIAS_LIMIT, returned - rooms))
    if mode is HvacMode.HEAT:
        neediest = max(goal(z) - z.temperature for z in occupied)  # type: ignore[operator]
        base = max(goal(z) for z in occupied)
        drive = _drive(memory, neediest, minutes)
        target = base + _lift(bias, neediest) + drive - _backoff(neediest)
        band_low = min(zone_band(s, z).low for z in occupied)
        target = min(target, max(band_low, base) + SETPOINT_SPAN)
    else:
        neediest = max(z.temperature - goal(z) for z in occupied)  # type: ignore[operator]
        base = min(goal(z) for z in occupied)
        drive = _drive(memory, neediest, minutes)
        target = base - _lift(-bias, neediest) - drive + _backoff(neediest)
        band_high = max(zone_band(s, z).high for z in occupied)
        target = max(target, min(band_high, base) - SETPOINT_SPAN)
    return s.caps.round_temp(target) if rounded else target


def _drive(memory: BalanceMemory | None, shortfall: float, minutes: float) -> float:
    """Return the push towards more output while the neediest room is short."""
    push = min(OVERDRIVE_LIMIT, SHORTFALL_GAIN * max(0.0, shortfall))
    return push + _trim(memory, shortfall, minutes)


def _lift(bias: float, shortfall: float) -> float:
    """Return air correction, signed toward more output.

    Correcting toward less output always applies. Correcting toward more output
    only helps a room that is short, so it fades out as the room passes its aim:
    a bedroom already warm must never get a hotter setpoint from warm return air.
    """
    if bias <= 0:
        return bias
    return bias * max(0.0, min(1.0, 1.0 + shortfall / SATISFIED_MARGIN))


def _backoff(shortfall: float) -> float:
    """How far to ease the setpoint off once every room is past its aim."""
    return min(OVERSHOOT_BACKOFF, SHORTFALL_GAIN * max(0.0, -shortfall - SATISFIED_MARGIN))


TRIM_RATE = 0.02  # degrees of setpoint per degree-minute of shortfall
TRIM_LIMIT = 2.0
TRIM_ZONE = 1.0  # degrees from the aim within which the trim learns


def _trim(memory: BalanceMemory | None, shortfall: float, minutes: float) -> float:
    """Integrate the neediest room's error into a bounded setpoint trim.

    It builds up while the room stays a little short and winds down, then below
    zero, while it stays past its aim: return air that reads far from the rooms
    otherwise leaves a steady error either way. It holds still while the room
    is far off (warming up from cold), so it cannot wind up and overshoot.
    """
    if memory is None:
        return 0.0
    if SATISFIED_MARGIN < abs(shortfall) <= TRIM_ZONE:
        trim = memory.setpoint_trim + shortfall * minutes * TRIM_RATE
        memory.setpoint_trim = max(-TRIM_LIMIT, min(TRIM_LIMIT, trim))
    return memory.setpoint_trim
