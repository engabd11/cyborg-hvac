from dataclasses import replace
from datetime import UTC, datetime, timedelta

from cyborg_hvac.backends.demo.backend import demo_capabilities
from cyborg_hvac.backends.demo.simulation import SimSystem
from cyborg_hvac.core.model import Command, HvacMode, UnitState
from cyborg_hvac.logic.planner import periods, precondition_blocks, precondition_command
from cyborg_hvac.logic.snapshot import ForecastPoint, HouseSnapshot, ZoneInputs
from cyborg_hvac.logic.thermal import ThermalModel, ThermalSample

T0 = datetime(2026, 7, 10, 18, 0, tzinfo=UTC)


def snap(temp=19.0, *, now=T0, power=False, mode=HvacMode.HEAT, target=21.0, **kw):
    zones = (ZoneInputs(1, "Bed", True, 100, temperature=temp, occupied=True),)
    unit = UnitState(power=power, mode=mode, target_temp=target, current_temp=temp)
    return HouseSnapshot(
        now=now, unit_name="U", caps=demo_capabilities(), unit=unit, zones=zones, **kw
    )


def flat_forecast(start, temp, hours=24):
    return tuple(ForecastPoint(start + timedelta(hours=h), temp) for h in range(hours))


def test_model_learns_loss_and_gain_from_the_simulation():
    sim = SimSystem.create(1, start=22.0, outdoor=8.0)
    sim.zones[1].loss_rate = 0.012
    model = ThermalModel()

    def sample():
        z = sim.zones[1]
        return ThermalSample({1: z.temperature}, sim.outdoor, sim.power, sim.mode, {1: 1.0})

    for _ in range(80):  # unit off: drift
        before = sample()
        sim.step(5)
        model.learn(before, sample(), 5)
    assert abs(model.zone(1).loss - 0.012) < 0.003
    sim.power, sim.mode, sim.target = True, HvacMode.HEAT, 30.0  # full output
    for _ in range(80):
        before = sample()
        sim.step(5)
        model.learn(before, sample(), 5)
    assert model.zone(1).gain > 0.2
    restored = ThermalModel.from_dict(model.as_dict())
    assert restored.zone(1).loss == model.zone(1).loss


def test_minutes_to_reach():
    model = ThermalModel()
    fast = model.minutes_to_reach(1, 18.0, 21.0, 10.0, HvacMode.HEAT, 1.0)
    slow = model.minutes_to_reach(1, 18.0, 21.0, 10.0, HvacMode.HEAT, 0.25)
    assert fast is not None
    assert slow is not None
    assert fast < slow
    assert model.minutes_to_reach(1, 18.0, 40.0, 0.0, HvacMode.HEAT, 0.05) is None


def test_cold_evening_plans_preheat_before_night():
    s = snap(18.0, outdoor_temp=6.0, forecast=flat_forecast(T0, 5.0))
    blocks = precondition_blocks(s, ThermalModel())
    preheat = next(b for b in blocks if b.kind == "preheat")
    assert preheat.mode is HvacMode.HEAT
    assert preheat.end == T0.replace(hour=22)
    assert preheat.start < preheat.end
    later = replace(s, now=preheat.start + timedelta(minutes=1))
    assert precondition_command(later, blocks) == Command(
        power=True, mode=HvacMode.HEAT, target_temp=preheat.target
    )


def test_comfortable_evening_needs_no_plan():
    s = snap(21.5, outdoor_temp=21.5, forecast=flat_forecast(T0, 21.5))
    assert precondition_blocks(s, ThermalModel()) == []


def test_heat_spike_plans_precool():
    morning = T0.replace(hour=7)
    forecast = tuple(
        ForecastPoint(morning + timedelta(hours=h), 22.0 + (h * 2 if h < 8 else 16))
        for h in range(24)
    )
    s = snap(23.0, now=morning, outdoor_temp=22.0, forecast=forecast)
    blocks = precondition_blocks(s, ThermalModel())
    assert any(b.kind == "precool" and b.mode is HvacMode.COOL for b in blocks)


def test_someone_choosing_another_mode_is_not_fought():
    s = snap(18.0, outdoor_temp=6.0, forecast=flat_forecast(T0, 5.0))
    blocks = precondition_blocks(s, ThermalModel())
    start = next(b for b in blocks if b.kind == "preheat").start
    busy = replace(
        s,
        now=start + timedelta(minutes=1),
        unit=replace(s.unit, power=True, mode=HvacMode.FAN_ONLY),
    )
    assert precondition_command(busy, blocks) == Command()


def test_periods_cover_the_next_day():
    result = periods(snap())
    assert result[0]["period"] == "day"
    assert result[1]["period"] == "night"
    assert result[1]["band"]["low"] == 18.0
    assert result[-1]["end"] == (T0 + timedelta(hours=24)).isoformat()


def test_naive_forecast_times_do_not_crash_the_planner():
    # BOM sends forecast times without a time zone.
    naive = tuple(
        ForecastPoint(T0.replace(tzinfo=None) + timedelta(hours=h), 5.0) for h in range(24)
    )
    assert naive[0].time.tzinfo is not None
    s = snap(18.0, outdoor_temp=6.0, forecast=naive)
    assert any(b.kind == "preheat" for b in precondition_blocks(s, ThermalModel()))
