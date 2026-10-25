from dataclasses import replace
from datetime import UTC, datetime, timedelta

from cyborg_hvac.backends.demo.backend import demo_capabilities
from cyborg_hvac.core.model import Feature, HvacMode, UnitState, ZoneCommand
from cyborg_hvac.logic.advisor import Severity, command_as_dict, evaluate, fmt
from cyborg_hvac.logic.comfort import current_period, house_demand, zone_band, zone_demand
from cyborg_hvac.logic.snapshot import (
    ForecastPoint,
    HouseSnapshot,
    Period,
    Priority,
    ZoneInputs,
)

DAY = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)


def zone(n, temp=21.0, *, on=True, opening=100, **kw):
    return ZoneInputs(zone=n, name=f"Zone {n}", on=on, opening=opening, temperature=temp, **kw)


def snap(zones=None, *, power=False, mode=HvacMode.HEAT, target=21.0, now=DAY, **kw):
    unit = UnitState(power=power, mode=mode, target_temp=target, current_temp=20.0)
    unit = replace(unit, **kw.pop("unit", {}))
    zones = zones if zones is not None else (zone(1), zone(2), zone(3))
    return HouseSnapshot(
        now=now,
        unit_name="Duct",
        caps=demo_capabilities(),
        unit=unit,
        zones=tuple(zones),
        **kw,
    )


def ids(snapshot):
    return [r.id for r in evaluate(snapshot)]


def test_periods_and_bands():
    s = snap()
    assert current_period(s) is Period.DAY
    assert current_period(snap(now=DAY.replace(hour=23))) is Period.NIGHT
    assert current_period(snap(anyone_home=False)) is Period.AWAY
    z = zone(1, offset=-1.0)
    band = zone_band(s, z)
    assert (band.low, band.high) == (19.0, 23.0)
    empty = zone(1, occupied=False)
    band = zone_band(s, empty)
    assert (band.low, band.high) == (16.0, 28.0)


def test_demand_sign_and_weighting():
    s = snap((zone(1, 18.0), zone(2, 26.0, priority=Priority.UTILITY)))
    assert zone_demand(s, s.zones[0]) == 100
    assert zone_demand(s, s.zones[1]) == -100
    assert house_demand(s) > 0  # the utility zone counts for less


def test_comfortable_house_gets_no_advice():
    assert ids(snap()) == []


def test_cold_rooms_suggest_heat_with_target():
    recs = evaluate(snap((zone(1, 18.0), zone(2, 18.5))))
    assert recs[0].id == "mode_heat"
    assert recs[0].action.mode is HvacMode.HEAT
    assert recs[0].action.target_temp == 21.0


def test_warm_outside_suggests_windows_not_heat():
    assert ids(snap((zone(1, 18.0),), outdoor_temp=22.0)) == ["free_heating"]


def test_cool_evening_suggests_free_cooling():
    assert ids(snap((zone(1, 26.0), zone(2, 26.0)), outdoor_temp=21.0)) == ["free_cooling"]


def test_heating_while_warm_is_an_alert():
    recs = evaluate(snap((zone(1, 26.0), zone(2, 25.5)), power=True, mode=HvacMode.HEAT))
    assert recs[0].id == "mode_heating_warm"
    assert recs[0].severity is Severity.ALERT
    assert recs[0].action.power is False


def test_running_in_comfort_with_mild_outdoor_suggests_off():
    assert "mode_comfortable_off" in ids(snap(power=True, mode=HvacMode.HEAT, outdoor_temp=21.0))


def test_auto_mode_suggests_fixed_mode():
    recs = evaluate(snap((zone(1, 19.0),), power=True, mode=HvacMode.AUTO))
    rec = next(r for r in recs if r.id == "mode_avoid_auto")
    assert rec.action.mode is HvacMode.HEAT


def test_uneven_rooms_suggest_fan():
    assert "mode_fan_mix" in ids(snap((zone(1, 20.2), zone(2, 23.0))))


def test_humid_rooms_suggest_dry():
    assert "mode_dry" in ids(snap((zone(1, 23.0, humidity=72.0), zone(2, 23.0))))


def test_high_heat_setpoint():
    recs = evaluate(snap(power=True, mode=HvacMode.HEAT, target=28.0, outdoor_temp=10.0))
    rec = next(r for r in recs if r.id == "setpoint_heat_high")
    assert rec.action.target_temp == 21.0
    assert "Powerful" in rec.reason


def test_powerful_setpoint_is_not_flagged():
    s = snap(
        power=True, target=30.0, outdoor_temp=10.0, unit={"features": {Feature.POWERFUL: True}}
    )
    assert "setpoint_heat_high" not in ids(s)


def test_low_airflow_opens_spill_zone_first():
    zones = (
        zone(1, opening=50),
        zone(2, on=False, opening=0),
        zone(3, on=False, opening=0, spill=True),
    )
    rec = evaluate(snap(zones, power=True, outdoor_temp=10.0))[0]
    assert rec.id == "zoning_low_airflow"
    assert rec.action.zones == {3: ZoneCommand(on=True, opening=100)}


def test_vacant_zone_suggests_closing_when_airflow_allows():
    zones = (zone(1), zone(2), zone(3, occupied=False, vacant_minutes=45))
    recs = evaluate(snap(zones, power=True, outdoor_temp=10.0))
    rec = next(r for r in recs if r.id == "zone_vacant_3")
    assert rec.action.zones == {3: ZoneCommand(on=False)}
    # Not when closing it would starve the fan.
    two = (zone(1), zone(3, occupied=False, vacant_minutes=45))
    assert "zone_vacant_3" not in ids(snap(two, power=True, outdoor_temp=10.0))


def test_open_window_suggests_closing_zone():
    zones = (zone(1), zone(2), zone(3, window_open=True))
    assert "zone_window_3" in ids(snap(zones, power=True, outdoor_temp=10.0))


def test_occupied_cold_closed_zone_suggests_opening():
    zones = (zone(1), zone(2), zone(3, 18.0, on=False, opening=0, occupied=True))
    assert "zone_open_3" in ids(snap(zones, power=True, mode=HvacMode.HEAT, outdoor_temp=10.0))


def test_away_short_setback_then_off():
    s = snap(power=True, anyone_home=False, away_minutes=30, outdoor_temp=10.0)
    rec = next(r for r in evaluate(s) if r.category == "away")
    assert rec.id == "away_setback"
    assert rec.action.target_temp == 16.0
    s = replace(s, away_minutes=200)
    assert next(r for r in evaluate(s) if r.category == "away").id == "away_off"


def test_hot_day_forecast_suggests_precool_in_the_morning():
    forecast = tuple(ForecastPoint(DAY + timedelta(hours=h), 24.0 + h * 1.2) for h in range(1, 10))
    s = snap(forecast=forecast, outdoor_temp=22.0, solar_export_w=2500)
    rec = next(r for r in evaluate(s) if r.id == "weather_precool")
    assert rec.action.mode is HvacMode.COOL
    assert "solar" in rec.reason


def test_cold_night_forecast_suggests_preheat_in_the_afternoon():
    afternoon = DAY.replace(hour=16)
    forecast = tuple(ForecastPoint(afternoon + timedelta(hours=h), 12.0 - h) for h in range(1, 12))
    s = snap(now=afternoon, forecast=forecast, outdoor_temp=12.0)
    assert "weather_preheat" in ids(s)


def test_solar_surplus_banks_cooling():
    s = snap((zone(1, 23.5), zone(2, 23.0)), outdoor_temp=31.0, solar_export_w=3000)
    assert "solar_precool" in ids(s)


def test_offline_hides_everything_else():
    s = snap((zone(1, 18.0),), unit={"online": False})
    assert ids(s) == ["unit_offline"]


def test_command_serialisation_and_formatting():
    rec = evaluate(snap((zone(1, 18.0),)))[0]
    data = rec.as_dict()
    assert data["action"] == {"power": True, "mode": "heat", "target_temp": 21.0}
    assert command_as_dict(rec.action)["mode"] == "heat"
    assert fmt(21.26) == "21.5 °C"
    assert fmt(-2) == "2 °C below zero"


def test_guest_mode_focuses_guest_zones():
    zones = (
        zone(1, 21.0, opening=60, guest=True),
        zone(2, 21.0, opening=100),
        zone(3, 21.0, opening=100),
        zone(4, 21.0, on=False, opening=40, guest=True),
    )
    s = snap(zones, power=True, outdoor_temp=10.0, guest_mode=True)
    rec = next(r for r in evaluate(s) if r.id == "guest_focus")
    assert rec.action.zones[1] == ZoneCommand(on=True, opening=100)
    assert rec.action.zones[4] == ZoneCommand(on=True, opening=100)
    # The other zones are trimmed while the airflow floor still holds.
    assert rec.action.zones[2] == ZoneCommand(on=False)
    assert rec.action.zones[3] == ZoneCommand(on=False)


def test_guest_mode_ranks_guest_rooms_first():
    zones = (zone(1, 18.0, guest=True), zone(2, 26.0), zone(3, 26.0))
    s = snap(zones, guest_mode=True)
    assert house_demand(s) > 0
    # Without guest mode the warm rooms dominate.
    assert house_demand(snap(zones)) < 0
    # Secondary zones only need to stay tolerable.
    band = zone_band(s, zones[1])
    assert band.high == 28.0


def test_guest_mode_without_guest_zones_explains():
    s = snap(power=True, outdoor_temp=10.0, guest_mode=True)
    assert "guest_no_zones" in ids(s)
