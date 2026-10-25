"""Guard rails for everything autopilot sends.

Autopilot proposes commands; the guard trims them so the unit is never
short-cycled or flipped between modes, the fan never starves, the cloud is not
flooded, and a person's manual change is respected for a while.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from ..core.model import Command, UnitState, ZoneCommand
from .snapshot import HouseSnapshot, ZoneInputs


@dataclass(frozen=True, slots=True)
class GuardConfig:
    """Limits for autopilot writes."""

    write_budget: int = 6
    budget_window: timedelta = timedelta(minutes=10)
    setpoint_interval: timedelta = timedelta(minutes=5)
    compressor_min: timedelta = timedelta(minutes=8)
    # Least time between two mode changes, whatever autopilot thinks.
    mode_interval: timedelta = timedelta(minutes=30)
    zone_interval: timedelta = timedelta(minutes=2)
    override_hold: timedelta = timedelta(hours=2)
    # Commands sent within this window explain changes that arrive by push.
    echo_window: timedelta = timedelta(seconds=90)


@dataclass(slots=True)
class GuardState:
    """What the guard remembers for one unit."""

    writes: deque[datetime] = field(default_factory=deque)
    last_setpoint: datetime | None = None
    last_power: datetime | None = None
    last_mode: datetime | None = None
    last_zone: dict[int, datetime] = field(default_factory=dict)
    unit_paused_until: datetime | None = None
    zone_paused_until: dict[int, datetime] = field(default_factory=dict)
    fan_paused_until: datetime | None = None
    recent: deque[tuple[datetime, Command]] = field(default_factory=deque)

    def unit_paused(self, now: datetime) -> bool:
        """Return True while a manual change holds unit-level autopilot."""
        return self.unit_paused_until is not None and now < self.unit_paused_until

    def fan_paused(self, now: datetime) -> bool:
        """Return True while a person's fan choice is being respected."""
        return self.fan_paused_until is not None and now < self.fan_paused_until

    def zone_paused(self, zone: int, now: datetime) -> bool:
        """Return True while a manual change holds a zone."""
        until = self.zone_paused_until.get(zone)
        return until is not None and now < until

    def resume(self) -> None:
        """Clear every hold."""
        self.unit_paused_until = None
        self.fan_paused_until = None
        self.zone_paused_until.clear()


def _airflow_ok(s: HouseSnapshot, zones: list[ZoneInputs]) -> bool:
    open_zones = [z for z in zones if z.on and z.opening > 0]
    return (
        len(open_zones) >= s.settings.min_open_zones
        and sum(z.opening for z in open_zones) >= s.settings.min_total_opening
    )


def _apply_zone(zone: ZoneInputs, change: ZoneCommand) -> ZoneInputs:
    return replace(
        zone,
        on=change.on if change.on is not None else zone.on,
        opening=change.opening if change.opening is not None else zone.opening,
    )


def filter_command(
    cfg: GuardConfig, state: GuardState, s: HouseSnapshot, command: Command, now: datetime
) -> Command | None:
    """Return the part of ``command`` that may be sent now, or None."""
    while state.writes and now - state.writes[0] > cfg.budget_window:
        state.writes.popleft()
    if len(state.writes) >= cfg.write_budget:
        return None
    unit = s.unit
    power, mode, target = command.power, command.mode, command.target_temp
    fan = command.fan_speed

    if state.unit_paused(now):
        power = mode = target = fan = None
    if state.fan_paused(now) or fan == unit.fan_speed:
        fan = None
    if power is not None and power == unit.power:
        power = None
    if power is not None and state.last_power and now - state.last_power < cfg.compressor_min:
        power = None
    if mode is not None and mode == unit.mode:
        mode = None
    starting = power is True and not unit.power
    if (
        mode is not None
        and not starting
        and state.last_mode
        and now - state.last_mode < cfg.mode_interval
    ):
        # Too soon to flip modes: the setpoint and fan were meant for the new one.
        mode = target = fan = None
    too_soon = state.last_setpoint and now - state.last_setpoint < cfg.setpoint_interval
    if (
        target is not None
        and unit.target_temp is not None
        and (abs(target - unit.target_temp) < s.caps.temp_step / 2 or too_soon)
    ):
        target = None

    step = max(1, s.caps.zone_opening_step)
    zones: dict[int, ZoneCommand] = {}
    projected = list(s.zones)
    by_number = {z.zone: z for z in s.zones}
    for number, change in command.zones.items():
        zone = by_number.get(number)
        if zone is None or state.zone_paused(number, now):
            continue
        last = state.last_zone.get(number)
        if last and now - last < cfg.zone_interval:
            continue
        on = change.on if change.on != zone.on else None
        opening = change.opening
        if opening is not None and abs(opening - zone.opening) < step:
            opening = None
        if on is None and opening is None:
            continue
        trimmed = ZoneCommand(on=on, opening=opening)
        trial = [_apply_zone(z, trimmed) if z.zone == number else z for z in projected]
        closing = (on is False) or (opening is not None and opening < zone.opening)
        if unit.power and closing and not _airflow_ok(s, trial):
            continue
        zones[number] = trimmed
        projected = trial

    result = Command(
        power=power,
        mode=mode,
        target_temp=target,
        fan_speed=fan,
        features=dict(command.features),
        zones=zones,
    )
    return None if result.is_empty else result


def record(state: GuardState, command: Command, now: datetime) -> None:
    """Remember an autopilot command once sent."""
    state.writes.append(now)
    if command.power is not None:
        state.last_power = now
    if command.mode is not None:
        state.last_mode = now
    if command.target_temp is not None:
        state.last_setpoint = now
    for number in command.zones:
        state.last_zone[number] = now
    note_command(state, command, now)


def note_command(state: GuardState, command: Command, now: datetime) -> None:
    """Remember any command (autopilot or person) to explain pushed changes."""
    state.recent.append((now, command))


def _explained(cfg: GuardConfig, state: GuardState, now: datetime, test) -> bool:
    while state.recent and now - state.recent[0][0] > cfg.echo_window:
        state.recent.popleft()
    return any(test(cmd) for _, cmd in state.recent)


def detect_override(
    cfg: GuardConfig, state: GuardState, before: UnitState, after: UnitState, now: datetime
) -> list[str]:
    """Pause autopilot for changes no recent command explains. Return what was paused."""
    paused: list[str] = []
    unit_changed = (
        before.power != after.power
        and not _explained(cfg, state, now, lambda c: c.power == after.power)
    ) or (
        after.power
        and before.mode != after.mode
        and not _explained(cfg, state, now, lambda c: c.mode == after.mode)
    )
    if unit_changed:
        state.unit_paused_until = now + cfg.override_hold
        paused.append("unit")
    elif (
        after.power
        and before.power
        and before.fan_speed != after.fan_speed
        and not _explained(
            cfg,
            state,
            now,
            # A unit may pick its own fan speed when its mode or power changes.
            lambda c: c.fan_speed == after.fan_speed or c.mode is not None or c.power is not None,
        )
    ):
        state.fan_paused_until = now + cfg.override_hold
        paused.append("fan")
    for number, zone in after.zones.items():
        old = before.zones.get(number)
        if old is None or (old.on == zone.on and old.opening == zone.opening):
            continue

        def explains(cmd: Command, n: int = number, z=zone) -> bool:
            change = cmd.zones.get(n)
            return change is not None and (
                (change.on is None or change.on == z.on)
                and (change.opening is None or change.opening == z.opening or not z.on)
            )

        if not _explained(cfg, state, now, explains):
            state.zone_paused_until[number] = now + cfg.override_hold
            paused.append(f"zone{number}")
    return paused


def manual_command(cfg: GuardConfig, state: GuardState, command: Command, now: datetime) -> None:
    """Hold what a person changed through Home Assistant."""
    note_command(state, command, now)
    if command.power is not None or command.mode is not None or command.target_temp is not None:
        state.unit_paused_until = now + cfg.override_hold
    if command.fan_speed is not None:
        state.fan_paused_until = now + cfg.override_hold
    for number in command.zones:
        state.zone_paused_until[number] = now + cfg.override_hold
