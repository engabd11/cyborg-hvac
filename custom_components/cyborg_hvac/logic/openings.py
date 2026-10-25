"""Windows and doors: stop conditioning rooms that are open to outside.

A zone counts as open only after its window or door has stayed open past a
delay (the Home Assistant layer applies it), so walking through a sliding door
does nothing. Autopilot closes an open zone and puts it back once it shuts. If
every open zone is open to outside, the unit pauses until one closes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.model import Command, ZoneCommand
from .snapshot import HouseSnapshot


@dataclass(slots=True)
class OpeningMemory:
    """Zones closed because of an open window or door, and how to restore them."""

    closed: dict[int, tuple[bool, int]] = field(default_factory=dict)
    paused_unit: bool = False


def openings_command(s: HouseSnapshot, memory: OpeningMemory) -> Command:
    """Return closures for open rooms and restores for rooms that shut again."""
    zones: dict[int, ZoneCommand] = {}
    by_number = {z.zone: z for z in s.zones}

    for number, (was_on, opening) in list(memory.closed.items()):
        zone = by_number.get(number)
        if zone is None or not zone.window_open:
            del memory.closed[number]
            if zone is not None and was_on and not zone.on:
                zones[number] = ZoneCommand(on=True, opening=opening or None)

    open_zones = [z for z in s.zones if z.on and z.opening > 0]
    exempt = {z.zone for z in s.zones if s.sleep_mode and z.sleep}
    for zone in open_zones:
        if zone.window_open and zone.zone not in exempt and zone.zone not in memory.closed:
            memory.closed[zone.zone] = (True, zone.opening)
            zones[zone.zone] = ZoneCommand(on=False)

    power = None
    candidates = [z for z in open_zones if z.zone not in exempt]
    if s.unit.power and candidates and all(z.window_open for z in candidates) and not exempt:
        memory.paused_unit = True
        power = False
    elif memory.paused_unit and not all(
        z.window_open for z in s.zones if z.on or z.zone in memory.closed
    ):
        memory.paused_unit = False
        power = True
    return Command(power=power, zones=zones)
