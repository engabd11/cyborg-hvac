"""What the weather is doing: the season, heatwaves, cold snaps and the outdoor series."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from .comfort import house_band
from .snapshot import ForecastPoint, HouseSnapshot, Period

LOOKAHEAD = timedelta(hours=24)
# Degrees inside the day band the day's mean outdoor temperature must reach
# before the house counts as being in its heating or cooling season.
SEASON_MARGIN = 2.0
HEATWAVE_FLOOR = 35.0  # a heatwave is at least this hot...
HEATWAVE_MARGIN = 10.0  # ...and this far above the day cooling target
PEAK_MARGIN = 6.0  # degrees above the cooling target that count as the hot part of the day
COLD_SNAP_MARGIN = 13.0  # degrees below the night heating target (5 °C by default)


class Season(StrEnum):
    """Which way the weather pushes the house over the next day."""

    HEATING = "heating"
    COOLING = "cooling"
    SHOULDER = "shoulder"


@dataclass(frozen=True, slots=True)
class Outlook:
    """The weather in terms of what the house will need."""

    season: Season = Season.SHOULDER
    # False without any outdoor reading or forecast: nothing can be predicted.
    known: bool = False
    heatwave: bool = False
    # A heatwave and already hot outside: the hardest hours.
    peak: bool = False
    cold_snap: bool = False
    hottest: float | None = None
    hottest_at: datetime | None = None
    coldest: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly form for the card."""
        return {
            "season": self.season.value if self.known else None,
            "heatwave": self.heatwave,
            "peak": self.peak,
            "cold_snap": self.cold_snap,
            "hottest": self.hottest,
            "hottest_at": self.hottest_at.isoformat() if self.hottest_at else None,
            "coldest": self.coldest,
        }


def heatwave_threshold(s: HouseSnapshot) -> float:
    """Return the outdoor temperature that counts as a heatwave for this house."""
    return max(HEATWAVE_FLOOR, house_band(s, Period.DAY).high + HEATWAVE_MARGIN)


def outlook(s: HouseSnapshot) -> Outlook:
    """Read the season and any extreme weather from the outdoor sensor and forecast."""
    ahead = [
        p for p in s.forecast if p.temperature is not None and s.now <= p.time <= s.now + LOOKAHEAD
    ]
    temps = [p.temperature for p in ahead]
    if s.outdoor_temp is not None:
        temps.append(s.outdoor_temp)
    if not temps:
        return Outlook()
    day = house_band(s, Period.DAY)
    night = house_band(s, Period.NIGHT)
    mean = sum(temps) / len(temps)  # type: ignore[arg-type]
    if mean < day.low - SEASON_MARGIN:
        season = Season.HEATING
    elif mean > day.high - SEASON_MARGIN:
        season = Season.COOLING
    else:
        season = Season.SHOULDER
    hottest = max(temps)  # type: ignore[type-var]
    coldest = min(temps)  # type: ignore[type-var]
    hot_point = max(ahead, key=lambda p: p.temperature) if ahead else None  # type: ignore[arg-type, return-value]
    heatwave = hottest >= heatwave_threshold(s)  # type: ignore[operator]
    return Outlook(
        season=season,
        known=True,
        heatwave=heatwave,
        peak=heatwave and s.outdoor_temp is not None and s.outdoor_temp >= day.high + PEAK_MARGIN,
        cold_snap=coldest <= night.low - COLD_SNAP_MARGIN,  # type: ignore[operator]
        hottest=hottest,
        hottest_at=hot_point.time if hot_point and hot_point.temperature == hottest else None,
        coldest=coldest,
    )


def outdoor_series(s: HouseSnapshot, until: datetime) -> list[float]:
    """Return one outdoor temperature per minute from now until ``until``.

    Empty when there is no outdoor reading or forecast to go by.
    """
    points = [p for p in s.forecast if p.temperature is not None]
    minutes = max(0, int((until - s.now).total_seconds() // 60))
    fallback = s.outdoor_temp
    series: list[float] = []
    for minute in range(minutes):
        at = s.now + timedelta(minutes=minute)
        value = outdoor_at(points, at, fallback)
        if value is None:
            return []
        series.append(value)
    return series


def outdoor_at(points: list[ForecastPoint], at: datetime, fallback: float | None) -> float | None:
    """Return the outdoor temperature at ``at``, between forecast points."""
    before = [p for p in points if p.time <= at]
    after = [p for p in points if p.time > at]
    if before and after:
        a, b = before[-1], after[0]
        span = (b.time - a.time).total_seconds() or 1
        frac = (at - a.time).total_seconds() / span
        return a.temperature + (b.temperature - a.temperature) * frac  # type: ignore[operator]
    if before:
        return before[-1].temperature
    if after:
        return after[0].temperature if fallback is None else fallback
    return fallback
