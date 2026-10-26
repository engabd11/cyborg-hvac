"""Energy modes, room trends and the Climate Butler's journal."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import math

from cyborg_hvac.backends.demo.backend import demo_capabilities
from cyborg_hvac.core.model import Command, HvacMode, UnitState, ZoneCommand
from cyborg_hvac.logic.energy import EnergyMemory, EnergyMode, EnergySetting, resolve
from cyborg_hvac.logic.journal import Journal, JournalEntry, Kind, describe
from cyborg_hvac.logic.snapshot import HouseSnapshot, ZoneInputs
from cyborg_hvac.logic.trend import TrendTracker

T0 = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def snap(temps=None, *, now=T0, power=False, mode=HvacMode.COOL, **kw):
    zones = tuple(
        ZoneInputs(zone=n, name=f"Z{n}", on=True, opening=100, temperature=t)
        for n, t in (temps or {1: 22.0}).items()
    )
    unit = UnitState(power=power, mode=mode)
    return HouseSnapshot(
        now=now, unit_name="U", caps=demo_capabilities(), unit=unit, zones=zones, **kw
    )


# Energy ----------------------------------------------------------------------------------


def auto(**kw):
    return resolve(snap(**kw), EnergySetting.AUTO, EnergyMemory())


def test_energy_follows_solar_battery_and_grid():
    assert auto()[0] is EnergyMode.MID  # no sensors: today's balance
    assert auto(solar_export_w=2000.0)[0] is EnergyMode.MAX
    assert auto(solar_export_w=0.0, solar_w=7000.0)[0] is EnergyMode.MAX
    mode, reason = auto(solar_export_w=0.0, solar_w=2500.0, battery_w=1200.0, battery_soc=60.0)
    assert mode is EnergyMode.MID
    assert "charging the battery (60%)" in reason
    mode, reason = auto(solar_export_w=0.0, solar_w=0.0, battery_w=-900.0, battery_soc=55.0)
    assert mode is EnergyMode.ECO
    assert "battery (55%)" in reason
    assert auto(solar_export_w=0.0, solar_w=2000.0)[0] is EnergyMode.MID  # no battery sensor
    assert auto(solar_export_w=0.0)[0] is EnergyMode.ECO


def test_a_mode_set_by_hand_wins():
    memory = EnergyMemory()
    mode, reason = resolve(snap(solar_export_w=5000.0), EnergySetting.ECO, memory)
    assert mode is EnergyMode.ECO
    assert "by hand" in reason


def test_a_passing_cloud_does_not_change_the_mode():
    memory = EnergyMemory()
    assert resolve(snap(solar_export_w=0.0), EnergySetting.AUTO, memory)[0] is EnergyMode.ECO
    sunny = snap(solar_export_w=2500.0, now=T0 + timedelta(minutes=1))
    mode, reason = resolve(sunny, EnergySetting.AUTO, memory)
    assert mode is EnergyMode.ECO
    assert "if it lasts" in reason
    later = replace(sunny, now=T0 + timedelta(minutes=12))
    assert resolve(later, EnergySetting.AUTO, memory)[0] is EnergyMode.MAX
    # Export dips but stays above the lower mark: Max stays.
    dip = replace(later, solar_export_w=150.0, now=T0 + timedelta(minutes=13))
    assert resolve(dip, EnergySetting.AUTO, memory)[0] is EnergyMode.MAX


# Trend -----------------------------------------------------------------------------------


def feed(trend, temps_at, minutes, start=T0, **kw):
    for minute in range(minutes):
        trend.observe(snap(temps_at(minute), now=start + timedelta(minutes=minute), **kw))


def test_trend_measures_a_room_and_starts_afresh_when_its_conditions_change():
    trend = TrendTracker()
    feed(trend, lambda m: {1: 22.0 + m / 60}, 8)
    assert trend.rate(1) is None  # not long enough to say
    feed(trend, lambda m: {1: 22.0 + m / 60}, 20)
    assert math.isclose(trend.rate(1), 1.0, abs_tol=0.01)
    assert trend.watched_for(1) == timedelta(minutes=19)
    # The unit starts: what the room does now has nothing to do with before.
    later = T0 + timedelta(minutes=20)
    feed(trend, lambda m: {1: 22.33 - m / 30}, 5, start=later, power=True)
    assert trend.rate(1) is None


def test_trend_eta_and_series():
    trend = TrendTracker()
    feed(trend, lambda m: {1: 23.0 - m / 60}, 31)  # falling a degree an hour
    assert math.isclose(trend.eta(1, 22.5, 22.0), 30.0, abs_tol=0.5)
    assert trend.eta(1, 22.5, 23.0) == math.inf  # moving away
    series = trend.series(1, T0 + timedelta(minutes=30), minutes=60, step=10)
    assert len(series) == 7
    assert series[:3] == [None, None, None]  # before the first reading
    assert series[-1] == 22.5


# Journal ---------------------------------------------------------------------------------


NAMES = {1: "Bedroom", 2: "Living"}


def test_commands_read_as_short_chips():
    command = Command(
        power=True,
        mode=HvacMode.COOL,
        target_temp=21.5,
        fan_speed="medium_high",
        zones={
            1: ZoneCommand(on=None, opening=100),
            2: ZoneCommand(on=False),
            3: ZoneCommand(on=True),
        },
    )
    assert describe(command, NAMES) == (
        "Cool",
        "21.5°",
        "Fan medium high",
        "Bedroom 100%",
        "Living closed",
        "Zone 3 open",
    )
    assert describe(Command(power=False), NAMES) == ("Off",)


def test_journal_writes_actions_and_new_plans_but_not_the_same_thought_twice():
    journal = Journal()
    t = T0
    cool = Command(mode=HvacMode.COOL)
    assert journal.observe(t, "cool", "Cooling Bedroom", "…", cool, NAMES).kind is Kind.ACT
    assert journal.observe(t, "cool", "Cooling Bedroom", "…", None, NAMES) is None
    watching = journal.observe(t, "watch", "Watching Bedroom", "…", None, NAMES)
    assert watching is not None
    assert watching.kind is Kind.THINK
    journal.observe(t + timedelta(minutes=5), "rest", "Resting", "…", None, NAMES)
    # Back to watching within half an hour: not written again.
    assert (
        journal.observe(t + timedelta(minutes=9), "watch", "Watching Bedroom", "", None, NAMES)
        is None
    )
    assert len(journal.entries) == 3
    newest = journal.recent(2)
    assert [e.headline for e in newest] == ["Resting", "Watching Bedroom"]
    older = journal.recent(5, before=newest[-1].time)
    assert [e.headline for e in older] == []
    assert journal.recent(5, before=t + timedelta(minutes=1))[0].kind is Kind.THINK


def test_journal_survives_a_restart_and_forgets_old_entries():
    journal = Journal()
    journal.add(JournalEntry(T0 - timedelta(days=8), Kind.LEARN, "Old"))
    journal.add(JournalEntry(T0, Kind.ENERGY, "Max", "Exporting 3.1 kW"))
    assert [e.headline for e in journal.entries] == ["Max"]
    restored = Journal.from_list([*journal.as_list(), {"bad": True}])
    assert [e.as_dict() for e in restored.entries] == journal.as_list()
