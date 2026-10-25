from dataclasses import replace
from datetime import UTC, datetime, timedelta

from cyborg_hvac.backends.demo.backend import demo_capabilities
from cyborg_hvac.backends.demo.simulation import SimSystem
from cyborg_hvac.core.model import Command, HvacMode, UnitState, ZoneCommand, ZoneState
from cyborg_hvac.logic.autopilot import Switches
from cyborg_hvac.logic.balancing import aim
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
from sim_rig import Rig, realistic

T0 = datetime(2026, 7, 10, 8, 0, tzinfo=UTC)


def test_balancing_holds_every_room_in_band_on_the_simulation():
    sim = realistic(SimSystem.create(4, start=16.5, outdoor=11.0))
    sim.zones[4].loss_rate *= 2  # a draughty room far from the return grille
    sim.power, sim.mode, sim.target = True, HvacMode.HEAT, 21.0
    rig = Rig(sim, Switches(balancing=True), T0, zone_kw={n: {"occupied": True} for n in sim.zones})
    min_total = 999
    for minute in range(6 * 60):
        rig.step()
        min_total = min(min_total, sum(z.opening for z in sim.zones.values() if z.on))
        if minute >= 5 * 60:
            snapshot = rig.snapshot()
            for z in snapshot.zones:
                # Inside the day band; the draughty room gets there gently, so it may sit
                # just under the bottom of the band rather than the house being overdriven.
                assert 20.0 - (0.4 if z.zone == 4 else 0.0) <= z.temperature <= 24.0, (minute, z)
            coldest = min(r.temperature for r in snapshot.zones)
            assert abs(coldest - aim(snapshot, snapshot.zones[0], HvacMode.HEAT)) < 0.75
    assert min_total >= 150
    # Far fewer writes than a damper loop: about one every quarter of an hour.
    assert rig.writes() <= 6 * 60 / 12
    assert not rig.budget_hit
    # The draughty room ends up with the most air, the others throttled for it.
    assert sim.zones[4].opening == max(z.opening for z in sim.zones.values())
    assert all(sim.zones[n].opening <= sim.zones[4].opening - 30 for n in (1, 2, 3))
    for zone in range(1, 5):
        assert rig.most_moves_in_an_hour(zone, after=60) <= 3


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
