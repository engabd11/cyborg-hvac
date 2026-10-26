"""How fast each room is really warming or cooling, measured rather than predicted.

The thermal model says what outside should do to a room. Sleepers, sun, a
closed door or a heater can make it wrong, so the engine also watches the
room: a slope fitted to its recent readings, started afresh whenever what the
room is getting changes (the unit's mode, or its damper opening or closing).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math

from .snapshot import HouseSnapshot

KEEP = timedelta(hours=3)
WINDOW = timedelta(minutes=30)  # the slope uses at most this much of the latest readings...
MIN_SPAN = timedelta(minutes=10)  # ...and needs at least this much
MIN_SAMPLES = 6


def regime(s: HouseSnapshot, zone: int) -> str:
    """Return what a zone is getting: the unit's mode, and whether its damper is open."""
    unit = s.unit
    running = unit.mode.value if unit.power and unit.mode else "off"
    inputs = next((z for z in s.zones if z.zone == zone), None)
    open_ = inputs is not None and inputs.on and inputs.opening > 0
    return f"{running}:{'open' if open_ else 'shut'}"


@dataclass(slots=True)
class _Room:
    samples: deque[tuple[datetime, float]] = field(default_factory=deque)
    regime: str | None = None
    since: datetime | None = None


@dataclass(slots=True)
class TrendTracker:
    """Recent readings of every room of one unit."""

    rooms: dict[int, _Room] = field(default_factory=dict)

    def observe(self, s: HouseSnapshot) -> None:
        """Record every room's temperature from a snapshot."""
        for zone in s.sensed_zones:
            room = self.rooms.setdefault(zone.zone, _Room())
            now_regime = regime(s, zone.zone)
            if now_regime != room.regime:
                room.regime, room.since = now_regime, s.now
            if room.samples and room.samples[-1][0] >= s.now:
                continue
            room.samples.append((s.now, zone.temperature))  # type: ignore[arg-type]
            while room.samples and s.now - room.samples[0][0] > KEEP:
                room.samples.popleft()

    def rate(self, zone: int) -> float | None:
        """Return degrees per hour the room is moving now (rising positive), or None."""
        room = self.rooms.get(zone)
        if room is None or not room.samples:
            return None
        newest = room.samples[-1][0]
        start = newest - WINDOW
        if room.since is not None and room.since > start:
            start = room.since
        points = [(t, v) for t, v in room.samples if t >= start]
        if len(points) < MIN_SAMPLES or points[-1][0] - points[0][0] < MIN_SPAN:
            return None
        xs = [(t - points[0][0]).total_seconds() / 3600 for t, _ in points]
        ys = [v for _, v in points]
        mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
        spread = sum((x - mean_x) ** 2 for x in xs)
        if not spread:
            return None
        return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / spread

    def watched_for(self, zone: int) -> timedelta:
        """Return how long the room has been getting what it gets now."""
        room = self.rooms.get(zone)
        if room is None or room.since is None or not room.samples:
            return timedelta(0)
        return room.samples[-1][0] - room.since

    def eta(self, zone: int, temp: float, target: float) -> float | None:
        """Return minutes until the room reaches ``target`` at its current rate.

        Infinity when it is flat or moving away, None before there is a rate.
        """
        rate = self.rate(zone)
        if rate is None:
            return None
        gap = target - temp
        if abs(gap) < 0.05:
            return 0.0
        if rate == 0 or (gap > 0) != (rate > 0):
            return math.inf
        return gap / rate * 60

    def series(
        self, zone: int, now: datetime, minutes: int = 120, step: int = 5
    ) -> list[float | None]:
        """Return the room's temperature every ``step`` minutes, oldest first, for a sparkline."""
        room = self.rooms.get(zone)
        samples = list(room.samples) if room else []
        out: list[float | None] = []
        index = 0
        for slot in range(minutes // step, -1, -1):
            at = now - timedelta(minutes=slot * step)
            while index + 1 < len(samples) and samples[index + 1][0] <= at:
                index += 1
            if (
                samples
                and samples[index][0] <= at
                and at - samples[index][0] <= timedelta(minutes=step * 2)
            ):
                out.append(round(samples[index][1], 2))
            else:
                out.append(None)
        return out
