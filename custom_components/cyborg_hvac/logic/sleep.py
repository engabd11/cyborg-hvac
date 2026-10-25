"""Sleep autopilot: hold the bedroom at one temperature for the night.

While sleep mode is on, sleep zones get full air and are held at the sleep
temperature whatever the rest of the house does. The unit's mode and setpoint
follow the bedroom alone, and other zones close down as far as the airflow
floor allows, keeping spill zones (then utility rooms) as the minimum air path.
"""

from __future__ import annotations

import math

from ..core.model import Command, HvacMode, ZoneCommand
from .balancing import BalanceMemory, smart_setpoint
from .snapshot import HouseSnapshot, Priority, ZoneInputs

START_MARGIN = 0.3  # degrees off target before an idle unit is started
SWITCH_MARGIN = 1.0  # degrees past target before heating switches to cooling or back


def _keepers(s: HouseSnapshot, sleepers: list[ZoneInputs]) -> dict[int, int]:
    """Return the other zones (and openings) needed to keep the airflow floor."""
    settings = s.settings
    step = max(1, s.caps.zone_opening_step)
    need_zones = max(0, settings.min_open_zones - len(sleepers))
    need_total = max(0, settings.min_total_opening - sum(z.max_opening or 100 for z in sleepers))
    others = sorted(
        (z for z in s.zones if not z.sleep),
        key=lambda z: (not z.spill, z.priority is not Priority.UTILITY, -z.opening),
    )
    keep: dict[int, int] = {}
    for zone in others:
        if len(keep) >= need_zones and sum(keep.values()) >= need_total:
            break
        remaining = max(0, need_total - sum(keep.values()))
        slots = max(1, need_zones - len(keep))
        share = math.ceil(remaining / slots / step) * step if remaining else step
        opening = max(zone.min_opening, step, min(zone.max_opening or 100, share))
        keep[zone.zone] = opening
    return keep


def sleep_command(s: HouseSnapshot, memory: BalanceMemory, minutes: float) -> Command:
    """Return zone and unit changes that hold the sleep zones at the sleep temperature."""
    sleepers = [z for z in s.zones if z.sleep]
    if not sleepers:
        return Command()
    zones: dict[int, ZoneCommand] = {}
    keep = _keepers(s, sleepers)
    for zone in s.zones:
        if zone.sleep:
            target = zone.max_opening or 100
            if not zone.on or zone.opening != target:
                zones[zone.zone] = ZoneCommand(on=True, opening=target)
        elif zone.zone in keep:
            opening = keep[zone.zone]
            if not zone.on or zone.opening != opening:
                zones[zone.zone] = ZoneCommand(on=True, opening=opening)
        elif zone.on:
            zones[zone.zone] = ZoneCommand(on=False)

    sensed = [z for z in sleepers if z.temperature is not None]
    target = s.sleep_target
    unit = s.unit
    if not sensed:
        # No bedroom sensor: the unit's own reading is all there is.
        set_to = s.caps.round_temp(target)
        return Command(target_temp=set_to if unit.target_temp != set_to else None, zones=zones)

    room = sum(z.temperature for z in sensed) / len(sensed)  # type: ignore[misc]
    modes = s.caps.hvac_modes
    power = mode = None
    current = unit.mode if unit.power else None
    if current is HvacMode.HEAT and room > target + SWITCH_MARGIN and HvacMode.COOL in modes:
        mode = HvacMode.COOL
    elif current is HvacMode.COOL and room < target - SWITCH_MARGIN and HvacMode.HEAT in modes:
        mode = HvacMode.HEAT
    elif current not in (HvacMode.HEAT, HvacMode.COOL):
        if room < target - START_MARGIN and HvacMode.HEAT in modes:
            power, mode = True, HvacMode.HEAT
        elif room > target + START_MARGIN and HvacMode.COOL in modes:
            power, mode = True, HvacMode.COOL
    running_mode = mode or current
    set_to = None
    if running_mode in (HvacMode.HEAT, HvacMode.COOL):
        if mode is not None and mode is not current:
            memory.setpoint_trim = 0.0
        set_to = smart_setpoint(s, sensed, running_mode, memory, minutes)
        if (
            set_to is not None
            and unit.target_temp is not None
            and mode is None
            and abs(set_to - unit.target_temp) < s.caps.temp_step / 2
        ):  # sleep favours precision: half-degree moves are allowed
            set_to = None
    return Command(power=power, mode=mode, target_temp=set_to, zones=zones)
