from dataclasses import replace
from datetime import UTC, datetime, timedelta

from cyborg_hvac.backends.demo.backend import demo_capabilities
from cyborg_hvac.backends.demo.simulation import SimSystem
from cyborg_hvac.core.model import Command, HvacMode, UnitState, ZoneCommand, ZoneState
from cyborg_hvac.logic.balancing import BalanceMemory, aim, balance_command
from cyborg_hvac.logic.guard import (
    GuardConfig,
    GuardState,
    detect_override,
    filter_command,
    manual_command,
    record,
)
from cyborg_hvac.logic.presence import PresenceMemory, presence_command
from cyborg_hvac.logic.snapshot import HouseSnapshot, ZoneInputs

T0 = datetime(2026, 7, 10, 8, 0, tzinfo=UTC)


def sim_snapshot(sim: SimSystem, now: datetime, **kw) -> HouseSnapshot:
    unit = UnitState(
        power=sim.power,
        mode=sim.mode,
        target_temp=sim.target,
        current_temp=round(sim.return_air(), 1),
        zones={n: ZoneState(z.on, z.opening, z.temperature) for n, z in sim.zones.items()},
    )
    zones = tuple(
        ZoneInputs(
            zone=n,
            name=f"Zone {n}",
            on=z.on,
            opening=z.opening,
            temperature=round(z.temperature, 2),
            occupied=True,
        )
        for n, z in sim.zones.items()
    )
    return HouseSnapshot(
        now=now, unit_name="Sim", caps=demo_capabilities(), unit=unit, zones=zones, **kw
    )


def apply(sim: SimSystem, command: Command) -> None:
    if command.power is not None:
        sim.power = command.power
    if command.target_temp is not None:
        sim.target = command.target_temp
    if command.mode is not None:
        sim.mode = command.mode
    for n, change in command.zones.items():
        if change.on is not None:
            sim.zones[n].on = change.on
        if change.opening is not None:
            sim.zones[n].opening = change.opening


def test_balancing_holds_every_room_in_band_on_the_simulation():
    sim = SimSystem.create(4, start=16.5, outdoor=11.0)
    sim.zones[4].loss_rate = 0.016  # a draughty room far from the return grille
    sim.power, sim.mode, sim.target = True, HvacMode.HEAT, 21.0
    memory, guard, cfg = BalanceMemory(), GuardState(), GuardConfig()
    now, writes = T0, 0
    min_total = 999
    for minute in range(6 * 60):
        sim.step(1)
        now = T0 + timedelta(minutes=minute)
        snapshot = sim_snapshot(sim, now)
        proposed = balance_command(snapshot, memory, 1, paused=set())
        command = filter_command(cfg, guard, snapshot, proposed, now)
        if command:
            apply(sim, command)
            record(guard, command, now)
            writes += 1
        min_total = min(min_total, sum(z.opening for z in sim.zones.values() if z.on))
        if minute >= 5 * 60:
            rooms = sim_snapshot(sim, now).zones
            for z in rooms:
                assert 20.0 <= z.temperature <= 24.0, (minute, z)  # inside the day band
            coldest = min(r.temperature for r in rooms)
            assert abs(coldest - aim(snapshot, rooms[0], HvacMode.HEAT)) < 0.5
    assert min_total >= 150
    # Far fewer writes than the budget allows: about one every three minutes or less.
    assert writes <= 6 * 60 / 3
    # The draughty room ends up with the most air.
    assert sim.zones[4].opening == max(z.opening for z in sim.zones.values())
    # And the smart setpoint sits above the aim to make up for return-air bias.
    assert sim.target >= 20.5


def snap(zones, **kw):
    unit = UnitState(power=True, mode=HvacMode.HEAT, target_temp=21.0, current_temp=20.0)
    unit = replace(unit, **kw.pop("unit", {}))
    return HouseSnapshot(
        now=T0, unit_name="U", caps=demo_capabilities(), unit=unit, zones=tuple(zones), **kw
    )


def z(n, on=True, opening=100, **kw):
    return ZoneInputs(zone=n, name=f"Z{n}", on=on, opening=opening, temperature=21.0, **kw)


def test_guard_never_starves_airflow_and_respects_budget():
    cfg, state = GuardConfig(), GuardState()
    s = snap((z(1), z(2, opening=60)))
    closing = Command(zones={2: ZoneCommand(on=False)})
    assert filter_command(cfg, state, s, closing, T0) is None
    s = snap((z(1), z(2), z(3)))
    assert filter_command(cfg, state, s, closing, T0) == closing
    for i in range(cfg.write_budget):
        record(state, Command(target_temp=20 + i), T0)
    assert filter_command(cfg, state, s, closing, T0) is None
    assert filter_command(cfg, state, s, closing, T0 + timedelta(minutes=11)) == closing


def test_guard_spaces_setpoints_and_compressor_starts():
    cfg, state = GuardConfig(), GuardState()
    s = snap((z(1), z(2), z(3)))
    record(state, Command(target_temp=22.0, power=True), T0)
    soon = T0 + timedelta(minutes=2)
    assert filter_command(cfg, state, s, Command(target_temp=23.0), soon) is None
    assert filter_command(cfg, state, s, Command(power=False), soon) is None
    later = T0 + timedelta(minutes=9)
    assert filter_command(cfg, state, s, Command(power=False), later) == Command(power=False)


def test_manual_change_pauses_autopilot():
    cfg, state = GuardConfig(), GuardState()
    before = UnitState(power=True, mode=HvacMode.HEAT, zones={1: ZoneState(True, 100)})
    after = replace(before, zones={1: ZoneState(True, 40)})
    assert detect_override(cfg, state, before, after, T0) == ["zone1"]
    assert state.zone_paused(1, T0 + timedelta(minutes=30))
    s = snap((z(1), z(2), z(3)))
    assert filter_command(cfg, state, s, Command(zones={1: ZoneCommand(opening=80)}), T0) is None
    # A change autopilot made itself is not an override.
    state2 = GuardState()
    record(state2, Command(zones={1: ZoneCommand(opening=40)}), T0)
    assert detect_override(cfg, state2, before, after, T0 + timedelta(seconds=20)) == []
    # Changes a person makes through Home Assistant hold the unit too.
    state3 = GuardState()
    manual_command(cfg, state3, Command(target_temp=24.0), T0)
    assert state3.unit_paused(T0 + timedelta(minutes=10))


def test_presence_closes_empty_rooms_and_reopens_occupied():
    s = snap(
        (
            z(1, occupied=True),
            z(2, occupied=False, vacant_minutes=40),
            z(3, occupied=False, vacant_minutes=40, min_opening=20),
            z(4, on=False, opening=60, occupied=True),
        )
    )
    command = presence_command(s, PresenceMemory())
    assert command.zones == {
        2: ZoneCommand(on=False),
        3: ZoneCommand(opening=20),
        4: ZoneCommand(on=True, opening=60),
    }


def test_presence_away_setback_then_restore():
    memory = PresenceMemory()
    s = snap((z(1),), anyone_home=False, away_minutes=20)
    assert presence_command(s, memory) == Command(target_temp=16.0)
    s = snap((z(1),), anyone_home=False, away_minutes=200)
    assert presence_command(s, memory) == Command(power=False)
    back = snap((z(1),), anyone_home=True, unit={"power": False})
    restore = presence_command(back, memory)
    assert restore == Command(power=True, mode=HvacMode.HEAT, target_temp=21.0)
