"""Comfort bands, periods and demand."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from .snapshot import PRIORITY_WEIGHT, HouseSnapshot, Period, ZoneInputs

# Degrees outside the band that count as full demand.
FULL_DEMAND_DEGREES = 2.0
# Degrees outside the band before a zone counts as needing heat or cool.
NEED_MARGIN = 0.5
# Sleep zones are held within this many degrees of the sleep temperature.
SLEEP_TOLERANCE = 0.5


@dataclass(frozen=True, slots=True)
class Band:
    """Comfortable range: heat below ``low``, cool above ``high``."""

    low: float
    high: float

    @property
    def middle(self) -> float:
        """Return the centre of the band."""
        return round((self.low + self.high) / 2, 1)


def _in_window(now: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def current_period(snapshot: HouseSnapshot) -> Period:
    """Return the period whose targets apply now."""
    if snapshot.period_override is not None:
        return snapshot.period_override
    if snapshot.anyone_home is False:
        return Period.AWAY
    settings = snapshot.settings
    if _in_window(snapshot.now.time(), settings.night_start, settings.night_end):
        return Period.NIGHT
    return Period.DAY


def house_band(snapshot: HouseSnapshot, period: Period | None = None) -> Band:
    """Return the house band for a period."""
    settings = snapshot.settings
    period = period or current_period(snapshot)
    if period is Period.NIGHT:
        return Band(settings.heat_night, settings.cool_night)
    if period is Period.AWAY:
        return Band(settings.heat_away, settings.cool_away)
    return Band(settings.heat_day, settings.cool_day)


def zone_band(snapshot: HouseSnapshot, zone: ZoneInputs, period: Period | None = None) -> Band:
    """Return a zone's band: the house band shifted by the zone offset.

    An empty zone in an occupied house uses the away band, so it is kept
    tolerable rather than comfortable.
    """
    if snapshot.sleep_mode and zone.sleep:
        target = snapshot.sleep_target
        return Band(round(target - SLEEP_TOLERANCE, 1), round(target + SLEEP_TOLERANCE, 1))
    period = period or current_period(snapshot)
    secondary = snapshot.secondary(zone)
    if (zone.occupied is False or secondary) and period is not Period.AWAY:
        base = house_band(snapshot, Period.AWAY)
        live = house_band(snapshot, period)
        base = Band(min(base.low, live.low), max(base.high, live.high))
    else:
        base = house_band(snapshot, period)
    return Band(round(base.low + zone.offset, 1), round(base.high + zone.offset, 1))


def zone_error(snapshot: HouseSnapshot, zone: ZoneInputs) -> float:
    """Return degrees outside the band: positive is too cold, negative too warm."""
    if zone.temperature is None:
        return 0.0
    band = zone_band(snapshot, zone)
    if zone.temperature < band.low:
        return round(band.low - zone.temperature, 2)
    if zone.temperature > band.high:
        return round(band.high - zone.temperature, 2)
    return 0.0


def zone_demand(snapshot: HouseSnapshot, zone: ZoneInputs) -> int:
    """Return demand from -100 (needs full cooling) to 100 (needs full heating)."""
    error = zone_error(snapshot, zone)
    return int(max(-100, min(100, round(error / FULL_DEMAND_DEGREES * 100))))


def zone_weight(snapshot: HouseSnapshot, zone: ZoneInputs) -> float:
    """Return how much a zone counts when deciding what the unit should do."""
    weight = PRIORITY_WEIGHT[zone.priority]
    if snapshot.sleep_mode:
        return 2.0 if zone.sleep else weight * 0.05
    if snapshot.guest_mode:
        return weight * (1.5 if zone.guest else 0.3)
    if zone.occupied is False:
        weight *= 0.3
    return weight


def house_demand(snapshot: HouseSnapshot) -> float:
    """Return the weighted demand across zones with room sensors (-100 to 100)."""
    zones = snapshot.sensed_zones
    if not zones:
        return 0.0
    total = sum(zone_weight(snapshot, z) for z in zones)
    if not total:
        return 0.0
    return sum(zone_demand(snapshot, z) * zone_weight(snapshot, z) for z in zones) / total


def indoor_temperature(snapshot: HouseSnapshot) -> float | None:
    """Return the weighted room temperature, or the unit's reading without sensors."""
    zones = snapshot.sensed_zones
    if not zones:
        return snapshot.unit.current_temp
    total = sum(zone_weight(snapshot, z) for z in zones)
    return round(
        sum(z.temperature * zone_weight(snapshot, z) for z in zones) / total,  # type: ignore[operator]
        1,
    )


def deciding_zones(snapshot: HouseSnapshot) -> list[ZoneInputs]:
    """Return the rooms with sensors that decide what the house needs right now."""
    zones = [z for z in snapshot.sensed_zones if z.occupied is not False]
    if snapshot.sleep_mode:
        zones = [z for z in snapshot.sensed_zones if z.sleep] or zones
    elif snapshot.guest_mode:
        zones = [z for z in snapshot.sensed_zones if z.guest] or zones
    return zones


def needs(snapshot: HouseSnapshot) -> tuple[bool, bool]:
    """Return (needs heat, needs cool) across occupied zones with sensors.

    Without room sensors the unit's own reading stands in for the house.
    """
    zones = deciding_zones(snapshot)
    if not zones and not snapshot.sensed_zones:
        temp = snapshot.unit.current_temp
        if temp is None:
            return False, False
        band = house_band(snapshot)
        return temp < band.low - NEED_MARGIN, temp > band.high + NEED_MARGIN
    errors = [zone_error(snapshot, z) for z in zones]
    return any(e > NEED_MARGIN for e in errors), any(e < -NEED_MARGIN for e in errors)
