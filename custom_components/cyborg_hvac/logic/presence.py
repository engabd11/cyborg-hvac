"""Presence zoning: follow people around the house."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.model import Command, HvacMode, ZoneCommand
from .comfort import house_band
from .snapshot import HouseSnapshot, Period


@dataclass(slots=True)
class PresenceMemory:
    """What presence zoning changed, so it can be put back."""

    away_off: bool = False
    away_setback: bool = False
    before_away: Command | None = None


def presence_command(s: HouseSnapshot, memory: PresenceMemory, *, extreme: bool = False) -> Command:
    """Return zone and unit changes for who is home and where.

    ``extreme`` (a heatwave or cold snap) keeps the away setback rather than
    switching off: recovering from a hot or cold house takes hours, and the
    house itself is better kept within the away band.
    """
    settings = s.settings
    zones: dict[int, ZoneCommand] = {}
    unit = s.unit

    if s.anyone_home is False and unit.power:
        minutes = s.away_minutes or 0
        if memory.before_away is None:
            memory.before_away = Command(power=True, mode=unit.mode, target_temp=unit.target_temp)
        if minutes >= settings.short_away_minutes and not extreme:
            memory.away_off = True
            return Command(power=False)
        if unit.mode in (HvacMode.HEAT, HvacMode.COOL):
            band = house_band(s, Period.AWAY)
            memory.away_setback = True
            target = band.low if unit.mode is HvacMode.HEAT else band.high
            return Command(target_temp=s.caps.round_temp(target))
        return Command()

    if s.anyone_home is not False and memory.before_away is not None:
        restore = memory.before_away
        memory.before_away = None
        changed = memory.away_off or memory.away_setback
        memory.away_off = memory.away_setback = False
        if changed:
            return restore

    if not unit.power:
        return Command()
    for zone in s.zones:
        if s.guest_mode and zone.guest:
            continue
        if zone.occupied is True and not zone.on and not zone.window_open:
            zones[zone.zone] = ZoneCommand(on=True, opening=zone.opening or zone.max_opening)
        elif (
            zone.occupied is False
            and zone.on
            and zone.vacant_minutes is not None
            and zone.vacant_minutes >= settings.vacant_close_minutes
        ):
            if zone.min_opening > 0 and zone.opening > zone.min_opening:
                zones[zone.zone] = ZoneCommand(opening=zone.min_opening)
            elif zone.min_opening == 0:
                zones[zone.zone] = ZoneCommand(on=False)
    return Command(zones=zones)
