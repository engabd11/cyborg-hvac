"""The 24 hour plan: comfort periods and pre-conditioning."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

from ..core.model import Command, HvacMode
from .comfort import Band, house_band, indoor_temperature
from .outlook import heatwave_threshold, outdoor_series
from .snapshot import HouseSnapshot, Period
from .thermal import ThermalModel

SAFETY_FACTOR = 1.25
HOT_SPIKE_MARGIN = 8.0
HEATWAVE_LEAD = 2  # a heatwave pre-cool starts this many times earlier


@dataclass(frozen=True, slots=True)
class PlanBlock:
    """A stretch of time where the plan wants the unit doing something."""

    kind: str  # preheat, precool
    start: datetime
    end: datetime
    mode: HvacMode
    target: float
    reason: str

    def active(self, now: datetime) -> bool:
        """Return True while the block is running."""
        return self.start <= now < self.end

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly form for the card."""
        return {
            "kind": self.kind,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "mode": self.mode.value,
            "target": self.target,
            "reason": self.reason,
        }


@dataclass(slots=True)
class Plan:
    """Everything the card draws on its timeline."""

    blocks: list[PlanBlock] = field(default_factory=list)
    periods: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly form."""
        return {"blocks": [b.as_dict() for b in self.blocks], "periods": self.periods}


def _next_at(now: datetime, at: time) -> datetime:
    candidate = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def periods(s: HouseSnapshot, hours: int = 24) -> list[dict[str, Any]]:
    """Return day and night stretches with their bands for the next ``hours``."""
    settings = s.settings
    end = s.now + timedelta(hours=hours)
    marks = sorted(
        {_next_at(s.now, settings.night_start) + timedelta(days=d) for d in range(2)}
        | {_next_at(s.now, settings.night_end) + timedelta(days=d) for d in range(2)}
    )
    result = []
    cursor = s.now
    for mark in [m for m in marks if m < end] + [end]:
        mid = cursor + (mark - cursor) / 2
        night = _is_night(mid.time(), settings.night_start, settings.night_end)
        period = Period.NIGHT if night else Period.DAY
        band = house_band(s, period)
        result.append(
            {
                "start": cursor.isoformat(),
                "end": mark.isoformat(),
                "period": period.value,
                "band": {"low": band.low, "high": band.high},
            }
        )
        cursor = mark
    return result


def _is_night(at: time, start: time, end: time) -> bool:
    return at >= start or at < end if start > end else start <= at < end


def _share(s: HouseSnapshot, zone_count: int) -> float:
    open_zones = len(s.open_zones) or zone_count or 1
    return 1.0 / open_zones


def precondition_blocks(s: HouseSnapshot, model: ThermalModel) -> list[PlanBlock]:
    """Start early enough to be in band when the next period or a heat spike arrives."""
    blocks: list[PlanBlock] = []
    if s.anyone_home is False:
        return blocks
    zones = [z for z in s.sensed_zones if z.occupied is not False] or list(s.sensed_zones)
    indoor = indoor_temperature(s)
    if indoor is None:
        return blocks
    share = _share(s, len(s.zones))
    settings = s.settings
    for at, period in (
        (_next_at(s.now, settings.night_start), Period.NIGHT),
        (_next_at(s.now, settings.night_end), Period.DAY),
    ):
        outdoors = outdoor_series(s, at)
        if not outdoors:
            continue
        band = house_band(s, period)
        for zone in zones or ():
            # Arriving in the morning means drifting through the night.
            night = period is Period.DAY
            arriving = model.drift(
                zone.zone,
                zone.temperature,  # type: ignore[arg-type]
                outdoors,
                night,
            )
            block = _lift(s, model, zone.zone, arriving, band, at, outdoors[-1], share, period)
            if block:
                blocks.append(block)
                break
        else:
            if not zones:
                arriving = model.drift(0, indoor, outdoors)
                block = _lift(s, model, 0, arriving, band, at, outdoors[-1], share, period)
                if block:
                    blocks.append(block)

    # A heat spike in the next 12 hours: be cool before it arrives.
    spike = [
        p
        for p in s.forecast
        if p.temperature is not None and s.now <= p.time <= s.now + timedelta(hours=12)
    ]
    day = house_band(s, Period.DAY)
    if spike:
        hottest = max(spike, key=lambda p: p.temperature)  # type: ignore[arg-type, return-value]
        if hottest.temperature >= day.high + HOT_SPIKE_MARGIN:  # type: ignore[operator]
            target = s.caps.round_temp(day.high - 1)
            need = model.minutes_to_reach(
                zones[0].zone if zones else 0,
                indoor,
                target,
                hottest.temperature,  # type: ignore[arg-type]
                HvacMode.COOL,
                share,
            )
            lead = timedelta(minutes=(need or 120) * SAFETY_FACTOR)
            heatwave = hottest.temperature >= heatwave_threshold(s)  # type: ignore[operator]
            if heatwave:
                # Start in the cool of the morning: the unit works far better then.
                lead *= HEATWAVE_LEAD
            start = max(s.now, hottest.time - timedelta(hours=3) - lead)
            if start < hottest.time:
                blocks.append(
                    PlanBlock(
                        "precool",
                        start,
                        hottest.time - timedelta(hours=3),
                        HvacMode.COOL,
                        target,
                        f"{hottest.temperature:g} °C forecast at "
                        f"{hottest.time.strftime('%H:%M')}. "
                        + (
                            "Heatwave: banking cool while it is mild outside."
                            if heatwave
                            else "Cooling ahead of the heat costs less than fighting it."
                        ),
                    )
                )
    return [b for b in blocks if b.end > s.now]


def _lift(
    s: HouseSnapshot,
    model: ThermalModel,
    zone: int,
    arriving: float,
    band: Band,
    at: datetime,
    outdoor: float,
    share: float,
    period: Period,
) -> PlanBlock | None:
    label = "night" if period is Period.NIGHT else "the morning"
    if arriving < band.low - 0.3 and HvacMode.HEAT in s.caps.hvac_modes:
        target = s.caps.round_temp(band.low + 0.5)
        need = model.minutes_to_reach(zone, arriving, target, outdoor, HvacMode.HEAT, share)
        lead = timedelta(minutes=(need if need is not None else 180) * SAFETY_FACTOR)
        return PlanBlock(
            "preheat",
            max(s.now, at - lead),
            at,
            HvacMode.HEAT,
            target,
            f"Rooms would be {arriving:.1f} °C by {label}. Warming from "
            f"{(at - lead).strftime('%H:%M')} has them at {target:g} °C in time.",
        )
    if arriving > band.high + 0.3 and HvacMode.COOL in s.caps.hvac_modes:
        target = s.caps.round_temp(band.high - 0.5)
        need = model.minutes_to_reach(zone, arriving, target, outdoor, HvacMode.COOL, share)
        lead = timedelta(minutes=(need if need is not None else 180) * SAFETY_FACTOR)
        return PlanBlock(
            "precool",
            max(s.now, at - lead),
            at,
            HvacMode.COOL,
            target,
            f"Rooms would be {arriving:.1f} °C by {label}. Cooling from "
            f"{(at - lead).strftime('%H:%M')} has them at {target:g} °C in time.",
        )
    return None


def precondition_command(s: HouseSnapshot, blocks: list[PlanBlock]) -> Command:
    """Return what an active pre-conditioning block asks the unit to do."""
    for block in blocks:
        if not block.active(s.now):
            continue
        unit = s.unit
        if unit.power and unit.mode is block.mode:
            if (
                unit.target_temp is None
                or (block.mode is HvacMode.HEAT and unit.target_temp < block.target)
                or (block.mode is HvacMode.COOL and unit.target_temp > block.target)
            ):
                return Command(target_temp=block.target)
            return Command()
        if unit.power and unit.mode is not block.mode:
            return Command()  # someone chose another mode; do not fight it
        return Command(power=True, mode=block.mode, target_temp=block.target)
    return Command()
