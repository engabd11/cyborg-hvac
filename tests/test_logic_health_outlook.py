from dataclasses import replace
from datetime import UTC, datetime, timedelta

from cyborg_hvac.backends.demo.backend import demo_capabilities
from cyborg_hvac.core.model import Command, HvacMode, SensorKind, UnitState, ZoneCommand
from cyborg_hvac.logic.advisor import evaluate
from cyborg_hvac.logic.fan import choose_fan
from cyborg_hvac.logic.guard import (
    GuardConfig,
    GuardState,
    detect_override,
    filter_command,
    manual_command,
    record,
)
from cyborg_hvac.logic.health import (
    DampTracker,
    assess,
    dew_point,
    safe_temperature,
    saturation,
    surface_humidity,
)
from cyborg_hvac.logic.outlook import Season, outlook
from cyborg_hvac.logic.snapshot import ForecastPoint, HouseSnapshot, ZoneInputs
from cyborg_hvac.logic.thermal import ThermalModel
import pytest

T0 = datetime(2026, 7, 10, 9, 0, tzinfo=UTC)


def snap(zones=(), *, power=True, mode=HvacMode.HEAT, target=21.0, **kw):
    unit = UnitState(power=power, mode=mode, target_temp=target, current_temp=20.0)
    unit = replace(unit, **kw.pop("unit", {}))
    return HouseSnapshot(
        now=kw.pop("now", T0),
        unit_name="U",
        caps=demo_capabilities(),
        unit=unit,
        zones=tuple(zones),
        **kw,
    )


def z(n, temp=21.0, **kw):
    return ZoneInputs(
        zone=n, name=f"Z{n}", on=kw.pop("on", True), opening=100, temperature=temp, **kw
    )


def flat(temp, hours=25):
    return tuple(ForecastPoint(T0 + timedelta(hours=h), temp) for h in range(hours))


# Health ----------------------------------------------------------------------------------


def test_dew_point_and_surface_humidity():
    assert dew_point(20.0, 50.0) == pytest.approx(9.3, abs=0.1)
    # Walls three quarters of the way from 0 °C outside to 20 °C inside: 15 °C, so damper.
    assert surface_humidity(20.0, 60.0, 0.0) == pytest.approx(82.3, abs=0.5)
    assert surface_humidity(20.0, 60.0, None) == pytest.approx(60.0)
    assert surface_humidity(20.0, 60.0, 25.0) == pytest.approx(60.0)  # warm outside: no cold walls


def test_safe_temperature_dries_the_walls():
    safe = safe_temperature(15.0, 80.0, 5.0)
    assert safe == pytest.approx(21.0, abs=0.3)
    # At that temperature the coldest surface sits right at the mould limit: warming the
    # room keeps the same water in its air, which then reads as lower humidity.
    humidity_at_safe = 80.0 * saturation(15.0) / saturation(safe)
    assert surface_humidity(safe, humidity_at_safe, 5.0) == pytest.approx(70.0, abs=0.5)


def test_room_health_limits_and_flags():
    night = T0.replace(hour=23)
    zones = (
        z(1, 27.0, sleep=True),
        z(2, 29.0),
        z(3, 14.0),
        z(4, 13.0, occupied=False),
        z(5, 20.0, humidity=90.0, damp_minutes=150),
    )
    health = assess(snap(zones, now=night, outdoor_temp=4.0))
    assert health[1].overheated  # a bedroom above 26 °C at night
    assert health[2].overheated
    assert health[3].too_cold  # occupied rooms below 15 °C at night
    assert not health[4].too_cold  # empty rooms only need 12 °C
    assert health[5].mould
    assert health[5].condensation
    assert health[5].floor > 16.0  # damp walls raise the room's healthy minimum
    assert health[5].floor <= 18.0  # ...but never past normal comfort (night band low)


def test_damp_tracker_counts_minutes_in_the_window():
    tracker = DampTracker()
    damp = (z(1, 18.0, humidity=78.0),)
    dry = (z(1, 22.0, humidity=45.0),)
    for minute in range(0, 180, 5):
        tracker.observe(T0 + timedelta(minutes=minute), damp, 6.0)
    assert tracker.minutes(1) == pytest.approx(180, abs=5)
    tracker.observe(T0 + timedelta(minutes=181), dry, 6.0)  # too soon: ignored
    assert tracker.minutes(1) == pytest.approx(180, abs=5)
    for minute in range(185, 16 * 60, 5):
        tracker.observe(T0 + timedelta(minutes=minute), dry, 6.0)
    assert tracker.minutes(1) == 0  # older than 12 hours drops out


# Outlook ---------------------------------------------------------------------------------


def test_season_and_extremes():
    assert outlook(snap()).known is False
    winter = outlook(snap(outdoor_temp=8.0, forecast=flat(9.0)))
    assert winter.season is Season.HEATING
    assert winter.cold_snap is False
    summer = outlook(snap(outdoor_temp=27.0, forecast=flat(28.0)))
    assert summer.season is Season.COOLING
    hot = tuple(ForecastPoint(T0 + timedelta(hours=h), 30.0 + h) for h in range(10))
    wave = outlook(snap(outdoor_temp=31.0, forecast=hot))
    assert wave.heatwave
    assert wave.peak
    assert wave.hottest == 39.0
    assert outlook(snap(outdoor_temp=6.0, forecast=flat(5.0))).cold_snap
    assert not outlook(snap(outdoor_temp=8.0, forecast=flat(7.0))).cold_snap  # just winter


def test_minutes_to_drift():
    model = ThermalModel()
    outside = [8.0] * 300
    minutes = model.minutes_to_drift(1, 24.0, 22.0, outside)
    assert minutes == pytest.approx(89, abs=2)  # ln(16/14) / 0.0015
    assert model.minutes_to_drift(1, 24.0, 22.0, [26.0] * 300) is None  # outside warms it
    assert model.minutes_to_drift(1, 24.0, 22.0, outside[:30]) is None  # not within the horizon
    restored = ThermalModel.from_dict({"1": {"loss": 0.002, "gain": 0.2, "hold_heat": 40}})
    assert restored.zone(1).hold(HvacMode.HEAT) == 40
    assert restored.zone(1).hold(HvacMode.COOL) is None
    assert ThermalModel.from_dict(restored.as_dict()).zone(1).hold_heat == 40


# Fan -------------------------------------------------------------------------------------


def fan(s, mode=HvacMode.COOL, **kw):
    args = dict(humid=False, shortfall=0.0, peak=False, open_total=300, open_count=3)
    return choose_fan(s, mode, **{**args, **kw})


def test_fan_rules():
    s = snap()
    assert fan(s) == "auto"
    assert fan(s, humid=True) == "low"  # slow air dries better
    assert fan(s, mode=HvacMode.HEAT, humid=True) == "auto"
    assert fan(s, peak=True) == "high"
    assert fan(s, shortfall=2.5) == "high"
    assert fan(s, shortfall=1.5, current="high") == "high"  # stays until clearly caught up
    assert fan(s, shortfall=0.5, current="high") == "auto"
    assert fan(s, peak=True, open_total=100) == "medium"  # little duct: gentler
    assert fan(replace(s, sleep_mode=True)) == "low"
    assert fan(replace(s, sleep_mode=True), shortfall=2.0) == "medium"
    assert fan(s, mode=HvacMode.DRY) is None
    assert (
        choose_fan(
            replace(s, caps=replace(s.caps, fan_speeds=())),
            HvacMode.COOL,
            humid=True,
            shortfall=0,
            peak=False,
            open_total=300,
            open_count=3,
        )
        is None
    )


# Guard -----------------------------------------------------------------------------------


def test_guard_holds_a_persons_fan_choice():
    cfg, state = GuardConfig(), GuardState()
    s = snap((z(1), z(2), z(3)), unit={"fan_speed": "auto"})
    before = UnitState(power=True, mode=HvacMode.HEAT, fan_speed="auto")
    after = replace(before, fan_speed="high")
    assert detect_override(cfg, state, before, after, T0) == ["fan"]
    assert filter_command(cfg, state, s, Command(fan_speed="low"), T0) is None
    later = T0 + cfg.override_hold + timedelta(minutes=1)
    assert filter_command(cfg, state, s, Command(fan_speed="low"), later) == Command(
        fan_speed="low"
    )
    # A fan change through Home Assistant is held too, and autopilot's own is not.
    state2 = GuardState()
    manual_command(cfg, state2, Command(fan_speed="medium"), T0)
    assert state2.fan_paused(T0 + timedelta(minutes=5))
    state3 = GuardState()
    record(state3, Command(fan_speed="high"), T0)
    assert detect_override(cfg, state3, before, after, T0 + timedelta(seconds=10)) == []


def test_guard_never_flips_modes_quickly():
    cfg, state = GuardConfig(), GuardState()
    s = snap((z(1), z(2), z(3)), mode=HvacMode.COOL)
    record(state, Command(mode=HvacMode.COOL), T0)
    flip = Command(mode=HvacMode.HEAT, target_temp=23.0, fan_speed="high")
    soon = T0 + timedelta(minutes=10)
    # The setpoint and fan were meant for the new mode, so they wait with it.
    assert filter_command(cfg, state, s, flip, soon + timedelta(minutes=5)) is None
    later = T0 + cfg.mode_interval + timedelta(minutes=1)
    assert (
        filter_command(
            cfg, state, replace(s, unit=replace(s.unit, mode=HvacMode.COOL)), flip, later
        )
        == flip
    )
    # Starting a unit that is off is not a flip.
    off = replace(s, unit=replace(s.unit, power=False, mode=HvacMode.COOL))
    start = Command(power=True, mode=HvacMode.HEAT)
    assert (
        filter_command(
            cfg, GuardState(last_mode=soon, last_power=T0 - timedelta(hours=1)), off, start, soon
        )
        == start
    )


# Advice ----------------------------------------------------------------------------------


def ids(s, model=None):
    return [r.id for r in evaluate(s, model)]


def test_advice_lets_a_warm_house_cool_on_its_own_when_it_is_cold_outside():
    zones = (z(1, 25.5), z(2, 25.0))
    cold = snap(zones, power=False, outdoor_temp=6.0, forecast=flat(6.0))
    assert "mode_coast" in ids(cold)
    assert "mode_cool" not in ids(cold)
    warm = snap(zones, power=False, outdoor_temp=30.0, forecast=flat(30.0))
    assert "mode_cool" in ids(warm)


def test_advice_closes_a_room_that_is_ahead_while_others_heat():
    zones = (z(1, 25.0), z(2, 18.5), z(3, 18.5))
    recs = {r.id: r for r in evaluate(snap(zones, outdoor_temp=8.0))}
    assert "zone_ahead_1" in recs
    assert recs["zone_ahead_1"].action.zones == {1: ZoneCommand(on=False)}


def test_advice_health_heatwave_filter_and_away():
    night = T0.replace(hour=23)
    hot = (z(1, 27.5, sleep=True), z(2, 24.0))
    assert "health_overheat_1" in ids(snap(hot, now=night, power=False))
    cold = (z(1, 14.0), z(2, 20.0))
    assert "health_cold_1" in ids(snap(cold, power=False))
    wave = tuple(ForecastPoint(T0 + timedelta(hours=h), 30.0 + h) for h in range(10))
    recs = {r.id: r for r in evaluate(snap(outdoor_temp=30.0, forecast=wave))}
    assert "weather_heatwave" in recs
    assert "blinds" in recs["weather_heatwave"].reason
    assert "unit_filter" in ids(snap(unit={"filter_alert": True}))
    away = snap(
        (z(1),),
        mode=HvacMode.COOL,
        anyone_home=False,
        away_minutes=300,
        outdoor_temp=33.0,
        forecast=wave,
    )
    rec = next(r for r in evaluate(away) if r.category == "away")
    assert rec.id == "away_setback"  # not off: a heatwave is coming
    assert "heatwave" in rec.reason.lower()


def test_unit_humidity_stands_in_for_room_sensors():
    s = snap(
        (z(1, 23.0),),
        power=False,
        unit={"sensors": {SensorKind.HUMIDITY: 75.0}},
        outdoor_temp=27.0,
    )
    assert s.unit_humidity == 75.0
    assert assess(s)[1].humidity == 75.0
    assert "mode_dry" in ids(s)
