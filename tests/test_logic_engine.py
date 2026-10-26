"""The autopilot engine on the simulation: the reported cases, the seasons and health."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import math

from cyborg_hvac.backends.demo.backend import demo_capabilities
from cyborg_hvac.backends.demo.simulation import SimSystem
from cyborg_hvac.core.model import Command, HvacMode, UnitState, ZoneCommand
from cyborg_hvac.logic.advisor import evaluate
from cyborg_hvac.logic.autopilot import (
    AutopilotMemory,
    Role,
    Switches,
    decide,
    note_sent,
)
from cyborg_hvac.logic.snapshot import (
    ComfortSettings,
    ForecastPoint,
    HouseSnapshot,
    Priority,
    ZoneInputs,
)
from cyborg_hvac.logic.thermal import ThermalModel
from sim_rig import Rig, realistic

MORNING = datetime(2026, 7, 10, 8, 0, tzinfo=UTC)
NIGHT = datetime(2026, 7, 10, 22, 30, tzinfo=UTC)
SUMMER = datetime(2026, 1, 15, 6, 0, tzinfo=UTC)
BALANCING = Switches(balancing=True)
BEDTIME = Switches(sleep=True)


def snap(zones, *, power=True, mode=HvacMode.HEAT, target=22.0, current=21.0, now=MORNING, **kw):
    unit = UnitState(power=power, mode=mode, target_temp=target, current_temp=current)
    return HouseSnapshot(
        now=now, unit_name="U", caps=demo_capabilities(), unit=unit, zones=tuple(zones), **kw
    )


def z(n, temp=21.0, on=True, opening=100, **kw):
    return ZoneInputs(zone=n, name=f"Z{n}", on=on, opening=opening, temperature=temp, **kw)


def forecast(start, temps):
    return tuple(ForecastPoint(start + timedelta(hours=h), t) for h, t in enumerate(temps))


# The reported cases ----------------------------------------------------------------------


def test_warm_room_closes_in_one_write_and_rejoins_level_with_the_house():
    # Heating to 22 with the house at 20, one room at 24, cold outside.
    sim = realistic(SimSystem.create(4, start=20.0, outdoor=6.0))
    sim.zones[4].temperature = 24.0
    sim.power, sim.mode, sim.target = True, HvacMode.HEAT, 22.0
    rig = Rig(sim, BALANCING, MORNING, ComfortSettings(heat_day=21.5, cool_day=25.0)).run(6 * 60)

    first = rig.sent[0][1]
    assert first.zones == {4: ZoneCommand(on=False)}  # straight off, not 100, 70, 40...
    reopened = next(m for m in rig.zone_moves(4) if m > 0)
    house = sum(t for n, t in rig.temps[reopened].items() if n != 4) / 3
    assert rig.temps[reopened][4] - house < 1.0  # back level with the house first
    assert HvacMode.COOL not in rig.modes
    for zone in range(1, 5):
        low, high = rig.range_of(zone, 60)
        assert low >= 21.0, (zone, low)
        assert high <= 24.0, (zone, high)
        assert rig.most_moves_in_an_hour(zone, after=60) <= 2
    assert rig.writes() <= 24
    assert not rig.budget_hit


def test_bedtime_on_a_cold_night_coasts_instead_of_cooling():
    # Bedroom at 24, bedtime at 22, 8 °C outside, unit heating the house.
    sim = realistic(SimSystem.create(4, start=21.5, outdoor=8.0))
    sim.zones[1].temperature = 24.0
    sim.power, sim.mode, sim.target = True, HvacMode.HEAT, 22.0
    rig = Rig(
        sim,
        BEDTIME,
        NIGHT,
        zone_kw={1: {"sleep": True, "occupied": True}, 4: {"spill": True}},
        snap_kw={"sleep_mode": True, "sleep_target": 22.0},
    ).run(8 * 60)

    assert HvacMode.COOL not in rig.modes  # outside does the cooling
    minute, first, decision = rig.sent[0]
    assert minute == 0
    assert first.power is False or (first.target_temp or 99) < 22.0
    assert "on its own" in decision.reason
    low, high = rig.range_of(1, 3 * 60)
    assert low >= 21.4, low
    assert high <= 22.7, high
    assert rig.writes() <= 14
    assert not rig.budget_hit


def test_bedtime_cools_when_outside_is_warmer():
    s = snap(
        (z(1, 25.0, sleep=True, occupied=True), z(2, 24.0), z(3, 24.0, spill=True)),
        power=False,
        now=NIGHT,
        sleep_mode=True,
        sleep_target=23.0,
        outdoor_temp=27.0,
    )
    command = decide(s, BEDTIME, [], ThermalModel(), AutopilotMemory()).command
    assert command.power is True
    assert command.mode is HvacMode.COOL


def test_bedtime_holds_the_bedroom_overnight_on_the_simulation():
    sim = realistic(SimSystem.create(4, start=19.0, outdoor=7.0))
    sim.power, sim.mode, sim.target = True, HvacMode.HEAT, 21.0
    rig = Rig(
        sim,
        BEDTIME,
        NIGHT,
        zone_kw={1: {"sleep": True, "occupied": True}, 4: {"spill": True}},
        snap_kw={"sleep_mode": True, "sleep_target": 23.0},
    ).run(8 * 60)
    low, high = rig.range_of(1, 3 * 60)
    assert abs(low - 23.0) <= 0.6, low
    assert abs(high - 23.0) <= 0.6, high
    assert HvacMode.COOL not in rig.modes  # a cold night: easing off, never switching
    # Holding within half a degree takes a few more writes: one setpoint or damper change
    # each time the bedroom reaches an edge, about three an hour.
    assert rig.writes() <= 28
    assert not rig.budget_hit
    # The other rooms only get a trickle (or nothing): the bedroom gets the air.
    assert all(q.opening <= 50 or not q.on for n, q in sim.zones.items() if n not in (1, 4))


# Seasons ---------------------------------------------------------------------------------


def test_shoulder_day_changes_mode_at_most_once():
    # A cold morning and a sunny afternoon: heat early, then rest, at most one changeover.
    def outdoor(minute):
        return 18.0 - 8.0 * math.cos(2 * math.pi * (minute - 60) / (24 * 60))

    sim = realistic(SimSystem.create(4, start=19.0, outdoor=10.0))
    sun_from, sun_to = 5 * 60, 10 * 60
    rig = Rig(sim, BALANCING, MORNING.replace(hour=6), outdoor=outdoor)
    for minute in range(14 * 60):
        for n in (1, 2):
            sim.zones[n].gain = 0.012 if sun_from <= minute < sun_to else 0.0
        rig.step()
    assert rig.changeovers() <= 1
    assert rig.writes() <= 40
    assert not rig.budget_hit


def test_heatwave_precools_and_keeps_the_setpoint_sensible():
    def outdoor(minute):
        hour = 6 + minute / 60
        return 26.0 + 18.0 * math.exp(-(((hour - 15.0) / 4.0) ** 2))  # 44 °C at 15:00

    sim = realistic(SimSystem.create(4, start=24.0, outdoor=26.0))
    rig = Rig(
        sim,
        Switches(balancing=True, weather=True, presence=True),
        SUMMER,
        zone_kw={
            1: {"occupied": True},
            2: {"occupied": True},
            3: {"occupied": False, "vacant_minutes": 120},
        },
        snap_kw={"anyone_home": True},
        outdoor=outdoor,
    ).run(15 * 60)
    assert HvacMode.HEAT not in rig.modes
    cooling = [t for t, m in zip(rig.targets, rig.modes, strict=True) if m is HvacMode.COOL]
    assert cooling
    assert min(cooling) >= 22.0  # never below band high minus 2 in a heatwave
    assert min(rig.targets[: 6 * 60]) <= 23.0  # banked cool before the peak
    peak = 9 * 60  # 15:00
    for zone in (1, 2):
        assert rig.range_of(zone, peak - 60, peak + 60)[1] <= 25.0 + 1.0
    # The empty room gets a trickle, or at most what the airflow floor needs: never full air.
    assert not sim.zones[3].on or sim.zones[3].opening <= 50
    assert not rig.budget_hit


def test_away_in_a_heatwave_holds_the_away_band_instead_of_switching_off():
    hot = forecast(SUMMER, [30, 34, 38, 41, 43, 42, 40, 36, 32] + [30] * 16)
    zones = (z(1, 29.0), z(2, 29.0), z(3, 28.5))
    away = dict(anyone_home=False, away_minutes=240, outdoor_temp=33.0, now=SUMMER)
    switches = Switches(balancing=True, presence=True)
    s = snap(zones, power=False, mode=HvacMode.COOL, forecast=hot, **away)
    command = decide(s, switches, [], ThermalModel(), AutopilotMemory()).command
    assert command.power is True
    assert command.mode is HvacMode.COOL
    mild = snap(
        (z(1, 23.0), z(2, 23.0), z(3, 23.0)),
        mode=HvacMode.COOL,
        forecast=forecast(SUMMER, [24] * 25),
        **{**away, "outdoor_temp": 24.0},
    )
    assert decide(mild, switches, [], ThermalModel(), AutopilotMemory()).command == Command(
        power=False
    )


# Health ----------------------------------------------------------------------------------


def test_humid_summer_dries_without_overcooling():
    warm = forecast(SUMMER, [27] * 25)
    zones = (z(1, 23.0, humidity=76.0), z(2, 23.0, humidity=74.0), z(3, 23.0))
    memory = AutopilotMemory()
    s = snap(zones, power=False, mode=HvacMode.COOL, outdoor_temp=27.0, forecast=warm, now=SUMMER)
    first = decide(s, BALANCING, [], ThermalModel(), memory).command
    assert first.mode is not HvacMode.DRY  # a shower is not a reason: wait for it to last
    later = replace(s, now=SUMMER + timedelta(minutes=20))
    command = decide(later, BALANCING, [], ThermalModel(), memory).command
    assert command.power is True
    assert command.mode is HvacMode.DRY
    # Rooms that need cooling as well are cooled with a slow fan, which dries them too.
    hot = tuple(replace(zone, temperature=26.0) for zone in zones)
    memory = AutopilotMemory(drying=True)
    command = decide(replace(later, zones=hot), BALANCING, [], ThermalModel(), memory).command
    assert command.mode is HvacMode.COOL
    assert command.fan_speed == "low"
    # No fan-only mixing while it is humid.
    assert "mode_fan_mix" not in [r.id for r in evaluate(replace(s, zones=zones))]


def test_damp_room_in_winter_is_kept_warm_and_flagged():
    zones = (
        z(1, 21.0, occupied=True),
        z(2, 21.0, occupied=True),
        z(
            3,
            15.0,
            on=False,
            opening=60,
            humidity=80.0,
            occupied=False,
            vacant_minutes=90,
            damp_minutes=180,
        ),
    )
    s = snap(zones, outdoor_temp=5.0, forecast=forecast(MORNING, [5] * 25))
    decision = decide(
        s, Switches(balancing=True, presence=True), [], ThermalModel(), AutopilotMemory()
    )
    assert decision.roles[3] is Role.SERVE  # empty, but its walls are damp
    assert decision.command.zones[3].on is True
    rec = next(r for r in evaluate(s) if r.id == "health_mould_3")
    assert "air" in rec.reason.lower()


def test_overheated_bedroom_at_night_is_cooled_even_by_presence_alone():
    zones = (z(1, 27.5, priority=Priority.BEDROOM), z(2, 25.0))
    s = snap(zones, power=False, mode=HvacMode.COOL, now=NIGHT, outdoor_temp=26.0)
    memory = AutopilotMemory()
    command = decide(s, Switches(presence=True), [], ThermalModel(), memory).command
    assert command.power is True
    assert command.mode is HvacMode.COOL
    assert memory.health_started
    cool = replace(
        s,
        zones=(z(1, 24.0, priority=Priority.BEDROOM), z(2, 24.0)),
        unit=replace(s.unit, power=True),
    )
    assert decide(cool, Switches(presence=True), [], ThermalModel(), memory).command.power is False


# Pacing and hysteresis -------------------------------------------------------------------


def test_changeover_waits_and_a_coasting_room_never_changes_mode():
    cold = forecast(MORNING, [8] * 25)
    memory = AutopilotMemory(fresh=False)
    zones = (z(1, 26.5), z(2, 26.0))  # warm, but 8 °C outside cools them
    s = snap(zones, outdoor_temp=8.0, forecast=cold)
    for minute in range(0, 40, 5):
        now = MORNING + timedelta(minutes=minute)
        command = decide(replace(s, now=now), BALANCING, [], ThermalModel(), memory).command
        assert command.mode is None
    # Outside as warm as the rooms: nothing will cool them, so after a while it changes.
    warm = forecast(MORNING, [27] * 25)
    memory = AutopilotMemory(fresh=False)
    s = snap(zones, outdoor_temp=27.0, forecast=warm)
    modes = []
    for minute in range(0, 40, 5):
        now = MORNING + timedelta(minutes=minute)
        decision = decide(replace(s, now=now), BALANCING, [], ThermalModel(), memory)
        modes.append(decision.command.mode)
        if decision.command.mode:
            note_sent(memory, decision.command, now)
    assert modes[0] is None  # not straight away
    assert HvacMode.COOL in modes


def test_holding_opening_is_learned_from_overshoot():
    model = ThermalModel()
    memory = AutopilotMemory(fresh=False)
    t0 = MORNING
    zones = (z(1, 20.6, opening=50), z(2, 19.0, opening=100), z(3, 20.5, opening=50))
    s = snap(zones, target=22.0, current=20.0, outdoor_temp=8.0)
    decide(s, BALANCING, [], model, memory)
    assert memory.zone(1).role is Role.HOLD
    later = t0 + timedelta(minutes=45)
    hot = (z(1, 22.3, opening=50), z(2, 19.0, opening=100), z(3, 20.5, opening=50))
    decision = decide(replace(s, zones=hot, now=later), BALANCING, [], model, memory)
    assert decision.roles[1] is Role.SHED
    assert model.zone(1).hold(HvacMode.HEAT) == 40


def test_no_outdoor_data_never_switches_modes_on_a_small_excess():
    memory = AutopilotMemory(fresh=False)
    s = snap((z(1, 25.0), z(2, 24.6)), target=22.0)
    for minute in range(0, 60, 5):
        now = MORNING + timedelta(minutes=minute)
        decision = decide(replace(s, now=now), BALANCING, [], ThermalModel(), memory)
        assert decision.command.mode is None


def test_idle_fan_air_goes_to_the_rooms_that_are_too_warm():
    # The reported house: heating, nothing needs heat, three rooms well ahead, the fan still runs.
    settings = ComfortSettings(heat_day=20, cool_day=21.5, heat_away=18, cool_away=24)
    temps = {1: 20.5, 2: 20.9, 3: 22.2, 4: 22.9}
    state = {n: (True, 50) for n in temps}
    memory, model = AutopilotMemory(fresh=False, last_mode=HvacMode.HEAT), ThermalModel()
    now = MORNING.replace(hour=14)

    def step(minutes_later):
        zones = (
            z(
                1,
                temps[1],
                on=state[1][0],
                opening=state[1][1],
                sleep=True,
                occupied=True,
                priority=Priority.BEDROOM,
            ),
            z(2, temps[2], on=state[2][0], opening=state[2][1], offset=-1.5, occupied=True),
            z(3, temps[3], on=state[3][0], opening=state[3][1], occupied=True),
            z(4, temps[4], on=state[4][0], opening=state[4][1], occupied=True),
        )
        at = now + timedelta(minutes=minutes_later)
        s = snap(zones, target=21.0, current=23.0, now=at, outdoor_temp=14.5, settings=settings)
        decision = decide(s, BALANCING, [], model, memory)
        for n, change in decision.command.zones.items():
            on, opening = state[n]
            state[n] = (change.on if change.on is not None else on, change.opening or opening)
        note_sent(memory, decision.command, at, HvacMode.HEAT)
        return decision

    assert step(0).command.zones == {}  # resting for a moment is not enough
    idle = step(11)  # resting for ten minutes: its fan's air can be used
    assert [idle.roles[n] for n in (1, 2, 3, 4)] == [Role.HOLD, Role.MIX, Role.MIX, Role.MIX]
    assert idle.command.zones == {
        n: ZoneCommand(on=None, opening=100) for n in (2, 3, 4)
    }  # one command; the bedroom keeps its opening
    assert "Sending more air to" in idle.reason
    temps[1] = 19.9  # the bedroom now needs heat: the warm rooms close in one go
    heating = step(21)
    assert heating.roles[1] is Role.SERVE
    assert all(heating.command.zones[n] == ZoneCommand(on=False) for n in (2, 4))
    # Without a spill zone the fan needs a second zone: the least ahead stays, trimmed.
    assert heating.roles[3] is Role.KEEPER
    assert heating.command.zones[3] == ZoneCommand(on=None, opening=50)
    temps[1] = 20.2  # still warming: nothing flips at the threshold
    assert step(31).command.zones == {}
    temps[1] = 20.6  # back at its aim: the fan's air goes to the warm rooms again
    again = step(52)  # back at its aim, and ten minutes of rest again
    assert all(again.roles[n] is Role.MIX for n in (2, 3, 4))
    assert all(again.command.zones[n].on is True for n in (2, 4))
    assert again.command.zones[3] == ZoneCommand(on=None, opening=100)
