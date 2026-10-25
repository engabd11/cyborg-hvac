"""Healthy rooms: mould, condensation, overheating and cold.

Mould grows where surfaces stay damp: 70% relative humidity at the surface
for a couple of hours in every twelve is enough. Outside walls and window
frames are colder than the room, so the air touching them is damper than the
room reading suggests. Their temperature is estimated with the surface factor
building codes use: a surface sits three quarters of the way from the outdoor
to the room temperature. Condensation forms once a surface reaches the dew
point.

Heat and cold have limits too: an occupied room above 28 °C, a bedroom above
26 °C at night, or an occupied room below 16 °C (15 °C at night) are
unhealthy whatever the comfort settings say.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Any

from .comfort import current_period, house_band
from .snapshot import HouseSnapshot, Period, Priority, ZoneInputs

MOULD_RH = 70.0  # surface humidity at which mould can grow
URGENT_RH = 85.0  # surface humidity that is a problem straight away
SURFACE_FACTOR = 0.75
DAMP_MINUTES = 120.0  # damp minutes in the window that count as a mould risk
WINDOW = timedelta(hours=12)
SAMPLE_EVERY = timedelta(minutes=5)
MAX_SAMPLE_MINUTES = 10.0
COLD_SURFACES = 3.0  # degrees outdoor must be below the room for walls to run cold

HOT_ROOM = 28.0
HOT_BEDROOM_NIGHT = 26.0
HOT_EMPTY_ROOM = 30.0
HOT_EMPTY_HOUSE = 32.0
COLD_ROOM = 16.0
COLD_ROOM_NIGHT = 15.0
COLD_ANY_ROOM = 12.0

_A, _B = 17.62, 243.12  # Magnus coefficients over water


def saturation(temp: float) -> float:
    """Return the saturation vapour pressure in hPa at ``temp`` °C."""
    return 6.112 * math.exp(_A * temp / (_B + temp))


def _temp_for(pressure: float) -> float:
    """Return the temperature at which ``pressure`` hPa saturates the air."""
    x = math.log(pressure / 6.112)
    return _B * x / (_A - x)


def dew_point(temp: float, humidity: float) -> float:
    """Return the dew point in °C of air at ``temp`` °C and ``humidity`` %."""
    return _temp_for(max(1.0, humidity) / 100 * saturation(temp))


def surface_temperature(room: float, outdoor: float | None) -> float:
    """Return the estimated temperature of the coldest surface in a room."""
    if outdoor is None or outdoor >= room:
        return room
    return outdoor + SURFACE_FACTOR * (room - outdoor)


def surface_humidity(room: float, humidity: float, outdoor: float | None) -> float:
    """Return the relative humidity of the air touching the coldest surface."""
    surface = surface_temperature(room, outdoor)
    return min(100.0, humidity * saturation(room) / saturation(surface))


def safe_temperature(room: float, humidity: float, outdoor: float | None) -> float:
    """Return the room temperature at which its coldest surface stays below the mould limit.

    Warming a room does not change how much water its air holds, but it warms
    the walls, which lowers the humidity at their surface.
    """
    vapour = humidity / 100 * saturation(room)
    surface = _temp_for(vapour / (MOULD_RH / 100))
    if outdoor is None or outdoor >= surface:
        return surface
    return outdoor + (surface - outdoor) / SURFACE_FACTOR


@dataclass(frozen=True, slots=True)
class ZoneHealth:
    """How healthy one room is right now, and the limits it should be held within."""

    zone: int
    temperature: float
    floor: float
    ceiling: float
    cold_limit: float
    humidity: float | None = None
    dew_point: float | None = None
    surface_humidity: float | None = None
    damp_minutes: float = 0.0
    # Warmest the room needs to be for its walls to stay dry; None when they are.
    safe_temperature: float | None = None

    @property
    def mould(self) -> bool:
        """Return True when surfaces have stayed damp long enough for mould."""
        return self.damp_minutes >= DAMP_MINUTES or (
            self.surface_humidity is not None and self.surface_humidity >= URGENT_RH
        )

    @property
    def condensation(self) -> bool:
        """Return True when the coldest surface is at the dew point."""
        return self.surface_humidity is not None and self.surface_humidity >= 99.5

    @property
    def overheated(self) -> bool:
        """Return True above the healthy ceiling."""
        return self.temperature > self.ceiling

    @property
    def too_cold(self) -> bool:
        """Return True below the healthy minimum (not counting damp walls)."""
        return self.temperature < self.cold_limit

    @property
    def critical(self) -> bool:
        """Return True when the room needs action for health, not just comfort."""
        return self.temperature < self.floor or self.overheated

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly form for the card."""
        return {
            "dew_point": None if self.dew_point is None else round(self.dew_point, 1),
            "surface_humidity": None
            if self.surface_humidity is None
            else round(self.surface_humidity),
            "damp_minutes": round(self.damp_minutes),
            "mould": self.mould,
            "condensation": self.condensation,
            "overheated": self.overheated,
            "too_cold": self.too_cold,
            "floor": round(self.floor, 1),
            "ceiling": round(self.ceiling, 1),
        }


def humidity_of(s: HouseSnapshot, zone: ZoneInputs) -> float | None:
    """Return a room's humidity, falling back to what the unit measures."""
    return zone.humidity if zone.humidity is not None else s.unit_humidity


def _bedroom(zone: ZoneInputs) -> bool:
    return zone.sleep or zone.priority is Priority.BEDROOM


def zone_health(s: HouseSnapshot, zone: ZoneInputs) -> ZoneHealth | None:
    """Return a room's health, or None without a temperature."""
    temp = zone.temperature
    if temp is None:
        return None
    night = s.sleep_mode or current_period(s) is Period.NIGHT
    if s.anyone_home is False:
        ceiling, cold = HOT_EMPTY_HOUSE, COLD_ANY_ROOM
    elif zone.occupied is False or (s.sleep_mode and not zone.sleep):
        # At bedtime everyone is in the sleep zones, so the others count as empty.
        ceiling, cold = HOT_EMPTY_ROOM, COLD_ANY_ROOM
    elif night and _bedroom(zone):
        ceiling, cold = HOT_BEDROOM_NIGHT, COLD_ROOM_NIGHT
    else:
        ceiling, cold = HOT_ROOM, COLD_ROOM_NIGHT if night else COLD_ROOM
    floor = cold
    humidity = humidity_of(s, zone)
    dew = surface = safe = None
    if humidity is not None and humidity > 0:
        outdoor = s.outdoor_temp
        dew = dew_point(temp, humidity)
        surface = surface_humidity(temp, humidity, outdoor)
        cold_walls = outdoor is not None and outdoor <= temp - COLD_SURFACES
        if cold_walls and (surface >= MOULD_RH or zone.damp_minutes > 0):
            safe = safe_temperature(temp, humidity, outdoor)
            # Warm a damp room towards safety, but never past normal comfort:
            # past that, airing the room is the answer.
            floor = max(floor, min(safe, house_band(s).low))
    return ZoneHealth(
        zone=zone.zone,
        temperature=temp,
        floor=floor,
        ceiling=max(ceiling, floor + 2),
        cold_limit=cold,
        humidity=humidity,
        dew_point=dew,
        surface_humidity=surface,
        damp_minutes=zone.damp_minutes,
        safe_temperature=safe,
    )


def assess(s: HouseSnapshot) -> dict[int, ZoneHealth]:
    """Return the health of every room with a temperature."""
    result = {}
    for zone in s.zones:
        health = zone_health(s, zone)
        if health is not None:
            result[zone.zone] = health
    return result


class DampTracker:
    """Counts how long each room's coldest surfaces were damp over the last 12 hours."""

    def __init__(self) -> None:
        """Start with no history."""
        self._samples: dict[int, deque[tuple[datetime, float]]] = {}
        self._last: datetime | None = None

    def observe(
        self,
        now: datetime,
        zones: Iterable[ZoneInputs],
        outdoor: float | None,
        unit_humidity: float | None = None,
    ) -> None:
        """Take a sample every five minutes."""
        if self._last is not None and now - self._last < SAMPLE_EVERY:
            return
        minutes = (
            min(MAX_SAMPLE_MINUTES, (now - self._last).total_seconds() / 60)
            if self._last is not None
            else SAMPLE_EVERY.total_seconds() / 60
        )
        self._last = now
        for zone in zones:
            humidity = zone.humidity if zone.humidity is not None else unit_humidity
            if zone.temperature is None or humidity is None:
                continue
            damp = surface_humidity(zone.temperature, humidity, outdoor) >= MOULD_RH
            samples = self._samples.setdefault(zone.zone, deque())
            samples.append((now, minutes if damp else 0.0))
            while samples and now - samples[0][0] > WINDOW:
                samples.popleft()

    def minutes(self, zone: int) -> float:
        """Return damp minutes for a zone in the window."""
        return sum(m for _, m in self._samples.get(zone, ()))
