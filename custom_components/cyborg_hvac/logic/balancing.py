"""Room balancing: hold every room in its band with dampers and a smart setpoint.

A ducted unit only knows its return-air temperature. Rooms far from the
return grille, or with a lot of glass, drift away from it. Balancing gives
each room the airflow it needs, and moves the unit's setpoint so the rooms,
not the return duct, reach the target.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.model import Command, HvacMode, ZoneCommand
from .comfort import zone_band, zone_weight
from .snapshot import HouseSnapshot, ZoneInputs

KP = 0.6  # opening fraction per degree of error
KI = 0.05  # per degree-minute
INTEGRAL_LIMIT = 10.0
SATISFIED_MARGIN = 0.3
OVERSHOOT_MARGIN = 1.0
SATISFIED_SIGNAL = 0.3
MAX_MOVE = 30
BIAS_LIMIT = 3.0
SETPOINT_SPAN = 4.0  # how far above (heat) or below (cool) the band the unit may be set
SHORTFALL_GAIN = 0.75  # extra setpoint per degree the neediest room is short


@dataclass(slots=True)
class BalanceMemory:
    """Integral terms per zone."""

    integral: dict[int, float] = field(default_factory=dict)
    # Slow correction of the unit setpoint while the neediest room stays short.
    setpoint_trim: float = 0.0


def aim(s: HouseSnapshot, zone: ZoneInputs, mode: HvacMode) -> float:
    """Return the temperature a zone should settle at in this mode."""
    band = zone_band(s, zone)
    return band.low + 0.5 if mode is HvacMode.HEAT else band.high - 0.5


def _error(s: HouseSnapshot, zone: ZoneInputs, mode: HvacMode) -> float:
    """Positive when the zone needs more of what the unit is doing."""
    assert zone.temperature is not None
    target = aim(s, zone, mode)
    return target - zone.temperature if mode is HvacMode.HEAT else zone.temperature - target


def balance_command(
    s: HouseSnapshot, memory: BalanceMemory, minutes: float, paused: set[int]
) -> Command:
    """Return damper openings and a setpoint for rooms with sensors."""
    mode = s.unit.mode
    if not s.unit.power or mode not in (HvacMode.HEAT, HvacMode.COOL):
        memory.integral.clear()
        memory.setpoint_trim = 0.0
        return Command()
    managed = [
        z
        for z in s.zones
        if z.has_sensor
        and z.on
        and not z.window_open
        and z.zone not in paused
        and not (s.guest_mode and not z.guest and z.occupied is not True)
    ]
    if not managed:
        return Command()
    step = max(1, s.caps.zone_opening_step)
    zones: dict[int, ZoneCommand] = {}
    signals: dict[int, float] = {}
    for zone in managed:
        error = _error(s, zone, mode)
        integral = memory.integral.get(zone.zone, 0.0)
        if error > -SATISFIED_MARGIN:
            integral = max(-INTEGRAL_LIMIT, min(INTEGRAL_LIMIT, integral + error * minutes))
        else:
            integral = min(integral, 0.0)
        memory.integral[zone.zone] = integral
        if error <= -OVERSHOOT_MARGIN:
            signal = 0.0
        elif error <= -SATISFIED_MARGIN:
            signal = SATISFIED_SIGNAL
        else:
            signal = max(SATISFIED_SIGNAL, 0.5 + KP * error + KI * integral)
        signals[zone.zone] = signal
    # Share air by need: the neediest room gets its full opening, others relative to it.
    top = max(signals.values()) or 1.0
    for zone in managed:
        low = max(zone.min_opening, step)
        high = zone.max_opening or 100
        fraction = min(1.0, signals[zone.zone] / top) if top > 0 else 0.0
        wanted = low + fraction * (high - low)
        # Move gradually: big swings are noisy and waste cloud writes.
        wanted = max(zone.opening - MAX_MOVE, min(zone.opening + MAX_MOVE, wanted))
        opening = s.caps.round_opening(max(low, min(high, wanted)))
        # Ignore single-step wobble unless it reaches a limit.
        if abs(opening - zone.opening) >= 2 * step or (
            opening != zone.opening and opening in (low, high)
        ):
            zones[zone.zone] = ZoneCommand(opening=opening)

    target = smart_setpoint(s, managed, mode, memory, minutes)
    current = s.unit.target_temp
    if target is not None and current is not None and abs(target - current) < 2 * s.caps.temp_step:
        target = None  # half-degree nudges are not worth a write
    return Command(target_temp=target, zones=zones)


def smart_setpoint(
    s: HouseSnapshot,
    zones: list[ZoneInputs],
    mode: HvacMode,
    memory: BalanceMemory | None = None,
    minutes: float = 1.0,
) -> float | None:
    """Return the unit setpoint that gets the neediest occupied room to its aim.

    The unit stops when return air reaches its setpoint, so the setpoint is the
    room aim corrected by how far return air sits from the rooms.
    """
    returned = s.unit.current_temp
    occupied = [z for z in zones if z.occupied is not False] or zones
    if returned is None or not occupied:
        return None
    total = sum(zone_weight(s, z) for z in occupied)
    rooms = sum(z.temperature * zone_weight(s, z) for z in occupied) / total  # type: ignore[operator]
    bias = max(-BIAS_LIMIT, min(BIAS_LIMIT, returned - rooms))
    if mode is HvacMode.HEAT:
        neediest = max(aim(s, z, mode) - z.temperature for z in occupied)  # type: ignore[operator]
        base = max(aim(s, z, mode) for z in occupied)
        trim = _trim(memory, neediest, minutes)
        target = base + bias + SHORTFALL_GAIN * max(0.0, neediest) + trim
        band_low = min(zone_band(s, z).low for z in occupied)
        target = min(target, band_low + SETPOINT_SPAN)
    else:
        neediest = max(z.temperature - aim(s, z, mode) for z in occupied)  # type: ignore[operator]
        base = min(aim(s, z, mode) for z in occupied)
        trim = _trim(memory, neediest, minutes)
        target = base + bias - SHORTFALL_GAIN * max(0.0, neediest) - trim
        band_high = max(zone_band(s, z).high for z in occupied)
        target = max(target, band_high - SETPOINT_SPAN)
    return s.caps.round_temp(target)


TRIM_RATE = 0.02  # degrees of setpoint per degree-minute of shortfall
TRIM_LIMIT = 2.0


def _trim(memory: BalanceMemory | None, shortfall: float, minutes: float) -> float:
    """Integrate the neediest room's shortfall into a bounded setpoint trim."""
    if memory is None:
        return 0.0
    if shortfall > SATISFIED_MARGIN:
        memory.setpoint_trim = min(
            TRIM_LIMIT, memory.setpoint_trim + shortfall * minutes * TRIM_RATE
        )
    elif shortfall < -SATISFIED_MARGIN:
        memory.setpoint_trim = max(0.0, memory.setpoint_trim - minutes * TRIM_RATE)
    return memory.setpoint_trim
