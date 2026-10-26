"""The Climate Butler (0.7): watching rooms, learning them, energy modes and ventilation."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import math

from cyborg_hvac.backends.demo.backend import demo_capabilities
from cyborg_hvac.backends.demo.simulation import SimSystem
from cyborg_hvac.core.model import Command, HvacMode, UnitState, ZoneCommand
from cyborg_hvac.logic.autopilot import Activity, AutopilotMemory, Role, Switches, decide
from cyborg_hvac.logic.energy import EnergyMode
from cyborg_hvac.logic.snapshot import ForecastPoint, HouseSnapshot, ZoneInputs
from cyborg_hvac.logic.thermal import ThermalModel
from cyborg_hvac.logic.trend import TrendTracker
from sim_rig import Rig, realistic

MORNING = datetime(2026, 7, 10, 8, 0, tzinfo=UTC)
NIGHT = datetime(2026, 7, 10, 22, 30, tzinfo=UTC)
SUMMER = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
BUTLER = Switches(balancing=True)
BEDTIME = Switches(sleep=True)


def snap(zones, *, power=True, mode=HvacMode.HEAT, target=22.0, current=21.0, now=MORNING, **kw):
    unit = UnitState(power=power, mode=mode, target_temp=target, current_temp=current)
    return HouseSnapshot(
        now=now, unit_name="U", caps=demo_capabilities(), unit=unit, zones=tuple(zones), **kw
    )


def z(n, temp=21.0, on=True, opening=100, **kw):
    return ZoneInputs(zone=n, name=f"Z{n}", on=on, opening=opening, temperature=temp, **kw)


def flat(start, temp, hours=25):
    return tuple(ForecastPoint(start + timedelta(hours=h), temp) for h in range(hours))


# The reported case -------------------------------------------------------------------------


def warm_bedroom(sim: SimSystem) -> None:
    """A bedroom that holds its warmth (sleepers, door shut): it will not cool on its own."""
    sim.zones[1].temperature = 23.0
    sim.zones[1].loss_rate = 0.0006
    sim.zones[1].gain = 0.0006 * (23.0 - 8.0)


def test_a_bedroom_that_does_not_cool_on_its_own_is_cooled_within_half_an_hour():
    # Bedtime at 22 °C, the bedroom a degree warm, 8 °C outside, the unit heating the house.
    # The model expects outside to cool the bedroom; the bedroom does not oblige.
    sim = realistic(SimSystem.create(4, start=21.5, outdoor=8.0))
    warm_bedroom(sim)
    sim.power, sim.mode, sim.target = True, HvacMode.HEAT, 22.0
    rig = Rig(
        sim,
        BEDTIME,
        NIGHT,
        zone_kw={1: {"sleep": True, "occupied": True}, 4: {"spill": True}},
        snap_kw={"sleep_mode": True, "sleep_target": 22.0},
    )
    rig.model.zone(1).loss = 0.0025  # what the model believes before it has learned the room
    rig.run(8 * 60)

    first_cool = rig.modes.index(HvacMode.COOL)
    assert first_cool <= 25
    decision = next(d for m, c, d in rig.sent if c.mode is HvacMode.COOL)
    assert "hasn't cooled on its own" in decision.reason
    after = [t[1] for t in rig.temps[60:]]
    inside = sum(1 for t in after if abs(t - 22.0) <= 0.5) / len(after)
    assert inside >= 0.85, inside
    assert not rig.budget_hit


def test_a_failed_coast_is_handed_to_the_unit_and_stays_handed():
    # A warm room on a cool day, unit off. The model says outside will cool it; the room
    # does not move. It must not be left to coast again (the stall in 0.6).
    zones = (z(1, 25.5), z(2, 22.0), z(3, 22.0))
    s = snap(zones, power=False, mode=HvacMode.HEAT, outdoor_temp=12.0, forecast=flat(MORNING, 12))
    memory, trend, model = AutopilotMemory(fresh=False), TrendTracker(), ThermalModel()
    started = None
    for minute in range(40):
        now = MORNING + timedelta(minutes=minute)
        at = replace(s, now=now, forecast=flat(now, 12))
        trend.observe(at)
        decision = decide(at, BUTLER, [], model, memory, (), 1.0, trend)
        if minute == 0:
            assert decision.command.is_empty
            assert "on its own" in decision.reason
            assert decision.watch[0].coasting
            assert decision.activity is Activity.WATCH
        if decision.command.mode is HvacMode.COOL:
            started = minute
            assert "hasn't cooled on its own" in decision.reason
            break
    assert started is not None
    assert started <= 16


def test_what_it_learns_about_a_room_makes_the_next_bedtime_act_at_once():
    def outdoor(minute):  # 8 °C before dawn, 18 °C in the afternoon
        hour = (8 + minute / 60) % 24
        return 13.0 - 5.0 * math.cos(2 * math.pi * (hour - 3) / 24)

    sim = realistic(SimSystem.create(4, start=22.0, outdoor=outdoor(0)))
    sim.zones[1].loss_rate = 0.0008
    rig = Rig(
        sim,
        Switches(),  # nothing on: the house just drifts, and the model learns it
        MORNING,
        learn=True,
        outdoor=outdoor,
        zone_kw={1: {"sleep": True, "occupied": True}, 4: {"spill": True}},
    )
    rig.model.zone(1).loss = 0.0015
    for minute in range(24 * 60):
        hour = (8 + minute / 60) % 24
        # Asleep in the bedroom from ten: enough warmth to hold it where it is.
        sim.zones[1].gain = 0.0008 * (23.0 - outdoor(minute)) if hour >= 22 or hour < 6.5 else 0
        rig.step()
    learned = rig.model.zone(1)
    assert learned.bias_night * 60 > 0.25  # degrees an hour the room adds by itself at night
    assert learned.bias_night > learned.bias_day + 0.003
    assert (1, True) in [(n, night) for n, night, _ in rig.model.news()]
    assert rig.model.news() == []  # told once

    s = rig.snapshot()
    bedtime = replace(
        s,
        now=NIGHT + timedelta(days=1),
        sleep_mode=True,
        sleep_target=22.0,
        zones=tuple(replace(q, temperature=23.0 if q.zone == 1 else 21.0) for q in s.zones),
        unit=replace(s.unit, power=False),
        outdoor_temp=8.0,
        forecast=(),
    )
    fresh = decide(bedtime, BEDTIME, [], ThermalModel(), AutopilotMemory(), (), 1.0, TrendTracker())
    assert fresh.command.is_empty  # knowing nothing, it would have waited for outside
    knowing = decide(bedtime, BEDTIME, [], rig.model, AutopilotMemory(), (), 1.0, TrendTracker())
    assert knowing.command.mode is HvacMode.COOL
    assert "Outside won't cool" in knowing.reason


# Ventilation ---------------------------------------------------------------------------------


def comfy(**kw):
    zones = (z(1, 22.0, opening=50), z(2, 22.5, opening=50), z(3, 21.8, opening=100))
    return snap(zones, power=False, outdoor_temp=19.0, forecast=flat(MORNING, 19), **kw)


def test_the_fan_keeps_air_moving_while_the_battery_has_charge():
    memory = AutopilotMemory()
    decision = decide(comfy(battery_soc=80.0), BUTLER, [], ThermalModel(), memory)
    command = decision.command
    assert command.power is True
    assert command.mode is HvacMode.FAN_ONLY
    assert command.fan_speed == "low"
    assert command.zones == {1: ZoneCommand(opening=100), 2: ZoneCommand(opening=100)}
    assert decision.activity is Activity.VENT
    assert all(role is Role.VENT for role in decision.roles.values())
    # Below its floor the battery is kept: the fan stops.
    running = replace(comfy(battery_soc=25.0), unit=UnitState(power=True, mode=HvacMode.FAN_ONLY))
    assert decide(running, BUTLER, [], ThermalModel(), memory).command == Command(power=False)
    # It starts again only a little above the floor.
    memory.vent_ok = False
    assert decide(comfy(battery_soc=31.0), BUTLER, [], ThermalModel(), memory).command.is_empty
    assert decide(comfy(battery_soc=34.0), BUTLER, [], ThermalModel(), memory).command.power


def test_without_a_battery_the_fan_keeps_going_only_on_free_power():
    rest = decide(comfy(), BUTLER, [], ThermalModel(), AutopilotMemory())
    assert rest.command.is_empty
    assert rest.activity is Activity.REST
    free = Switches(balancing=True, energy=EnergyMode.MAX)
    command = decide(comfy(), free, [], ThermalModel(), AutopilotMemory()).command
    assert command.mode is HvacMode.FAN_ONLY
    assert command.fan_speed == "medium"


def test_ventilation_closes_a_room_the_house_air_would_push_out_of_its_range():
    # The house air is cooler than Z1, which sits just above the bottom of its range.
    zones = (z(1, 20.25), z(2, 19.6, offset=-1.5), z(3, 19.5, offset=-1.5))
    s = snap(zones, power=False, battery_soc=90.0, outdoor_temp=19.0)
    decision = decide(s, BUTLER, [], ThermalModel(), AutopilotMemory())
    assert decision.command.mode is HvacMode.FAN_ONLY
    assert decision.roles[1] is Role.SHED
    assert decision.command.zones[1] == ZoneCommand(on=False)
    assert decision.roles[2] is Role.VENT


def test_ventilation_evens_out_a_warm_bedroom_with_the_house_air():
    sim = realistic(SimSystem.create(4, start=21.5, outdoor=20.0))
    sim.zones[1].temperature = 23.4
    rig = Rig(
        sim,
        BUTLER,
        MORNING,
        outdoor=lambda minute: 20.0,
        snap_kw={"battery_soc": 70.0},
    )
    rig.run(60)
    assert HvacMode.FAN_ONLY in rig.modes
    assert HvacMode.COOL not in rig.modes
    assert HvacMode.HEAT not in rig.modes
    assert rig.temps[-1][1] < 22.6  # mixed down towards the rest of the house


# Energy modes -------------------------------------------------------------------------------


def test_max_on_a_40_degree_day_keeps_every_room_near_the_middle_even_while_away():
    def outdoor(minute):
        hour = 9 + minute / 60
        return 30.0 + 10.0 * math.exp(-(((hour - 15.0) / 4.0) ** 2))  # 40 °C at 15:00

    away = {"occupied": False, "vacant_minutes": 300}
    sim = realistic(SimSystem.create(4, start=27.0, outdoor=30.0))
    rig = Rig(
        sim,
        Switches(balancing=True, presence=True, energy=EnergyMode.MAX),
        SUMMER,
        zone_kw={n: away for n in range(1, 5)},
        snap_kw={"anyone_home": False, "away_minutes": 300, "solar_export_w": 4000.0},
        outdoor=outdoor,
    ).run(10 * 60)
    assert HvacMode.HEAT not in rig.modes
    for zone in range(1, 5):
        low, high = rig.range_of(zone, 2 * 60)
        # The day range is 20 to 24, its middle 22.
        assert low >= 21.5, (zone, low)
        assert high <= 23.2, (zone, high)
    assert rig.writes() <= 30
    assert not rig.budget_hit


def test_mid_away_still_rests_but_max_keeps_the_house_ready():
    zones = (z(1, 26.0), z(2, 26.0))
    away = dict(anyone_home=False, away_minutes=300, outdoor_temp=30.0, now=SUMMER)
    s = snap(zones, power=False, mode=HvacMode.COOL, forecast=flat(SUMMER, 30), **away)
    switches = Switches(balancing=True, presence=True)
    assert decide(s, switches, [], ThermalModel(), AutopilotMemory()).command.is_empty
    free = replace(switches, energy=EnergyMode.MAX)
    command = decide(s, free, [], ThermalModel(), AutopilotMemory()).command
    assert command.power is True
    assert command.mode is HvacMode.COOL


def test_aims_follow_the_energy_mode():
    zones = (z(1, 22.0), z(2, 22.0))
    s = snap(zones, outdoor_temp=15.0)
    aims = {}
    for mode in EnergyMode:
        switches = Switches(balancing=True, energy=mode)
        decision = decide(s, switches, [], ThermalModel(), AutopilotMemory())
        aims[mode] = decision.aims[1]
    # Heating, day range 20 to 24: Eco lets the room sit lowest, Max closest to the middle.
    assert aims[EnergyMode.ECO] < aims[EnergyMode.MID] < aims[EnergyMode.MAX] <= 22.0
    assert aims[EnergyMode.MAX] == 21.5


# Modes set by hand ---------------------------------------------------------------------------


def test_fan_only_or_drying_set_by_hand_is_taken_over_when_a_room_needs_cooling():
    forecast = flat(SUMMER, 30)
    zones = (z(1, 26.5), z(2, 26.0))
    for mode in (HvacMode.FAN_ONLY, HvacMode.DRY):
        s = snap(zones, mode=mode, now=SUMMER, outdoor_temp=30.0, forecast=forecast)
        command = decide(s, BUTLER, [], ThermalModel(), AutopilotMemory()).command
        assert command.mode is HvacMode.COOL, mode
        assert command.power is None  # already on: only the mode changes
    # Comfortable and no battery to spare: switched off rather than left in fan or dry.
    calm = snap((z(1, 22.0), z(2, 22.0)), mode=HvacMode.FAN_ONLY, now=SUMMER, forecast=forecast)
    assert decide(calm, BUTLER, [], ThermalModel(), AutopilotMemory()).command == Command(
        power=False
    )


# What it says --------------------------------------------------------------------------------


def test_the_decision_explains_itself_for_the_card():
    zones = (z(1, 19.0, sleep=True), z(2, 21.0), z(3, 21.0, spill=True))
    s = snap(zones, now=NIGHT, sleep_mode=True, sleep_target=22.0, outdoor_temp=8.0)
    decision = decide(s, BEDTIME, [], ThermalModel(), AutopilotMemory())
    data = decision.as_dict()
    assert data["activity"] == "heat"
    assert data["headline"] == "Heating Z1"
    assert data["energy"] == "mid"
    assert data["aims"]["1"] == 22.0
    watch = data["watch"][0]
    assert watch["zone"] == 1
    assert watch["mode"] == "heat"
    assert watch["aim"] == 22.0
    assert watch["coasting"] is False
