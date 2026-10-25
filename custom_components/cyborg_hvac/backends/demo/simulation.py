"""A simple thermal simulation of a ducted system with zones.

Good enough to exercise zoning logic: each zone drifts towards the outdoor
temperature and the unit's output is shared between open zones by opening.
The unit regulates on return air, which is the opening-weighted mean of the
open zones, just like a real ducted unit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.model import Feature, HvacMode

# Heat delivered across all zones at full output, in °C per minute.
FULL_OUTPUT = 0.6
POWERFUL_BOOST = 1.4
ECO_LIMIT = 0.7
QUIET_LIMIT = 0.8
FAN_FACTOR = {"low": 0.6, "medium": 0.8, "high": 1.0, "auto": 1.0}


@dataclass(slots=True)
class SimZone:
    """One simulated zone."""

    temperature: float
    loss_rate: float = 0.01
    on: bool = True
    opening: int = 100
    # Heat from sun, people or appliances, in °C per minute.
    gain: float = 0.0


@dataclass(slots=True)
class SimSystem:
    """One simulated ducted unit."""

    zones: dict[int, SimZone]
    outdoor: float = 12.0
    power: bool = False
    mode: HvacMode = HvacMode.HEAT
    target: float = 21.0
    fan: str = "auto"
    features: dict[Feature, object] = field(default_factory=dict)
    output: float = 0.0
    # Heat delivered across all zones at full output, in °C per minute.
    capacity: float = FULL_OUTPUT

    @classmethod
    def create(cls, zone_count: int, start: float = 18.0, outdoor: float = 12.0) -> SimSystem:
        """Build a system whose zones lose heat at slightly different rates."""
        zones = {
            n: SimZone(temperature=start + (n % 3) * 0.4, loss_rate=0.008 + 0.002 * (n % 4))
            for n in range(1, zone_count + 1)
        }
        return cls(zones=zones, outdoor=outdoor)

    def open_weights(self) -> dict[int, float]:
        """Return the share of airflow each open zone receives."""
        openings = {n: z.opening for n, z in self.zones.items() if z.on and z.opening > 0}
        total = sum(openings.values())
        return {n: value / total for n, value in openings.items()} if total else {}

    def return_air(self) -> float:
        """Return the opening-weighted mean temperature of the open zones."""
        weights = self.open_weights()
        if not weights:
            return sum(z.temperature for z in self.zones.values()) / len(self.zones)
        return sum(self.zones[n].temperature * w for n, w in weights.items())

    def step(self, minutes: float) -> None:
        """Advance the simulation."""
        for zone in self.zones.values():
            zone.temperature += (self.outdoor - zone.temperature) * zone.loss_rate * minutes
            zone.temperature += zone.gain * minutes

        self.output = 0.0
        if not self.power or self.mode in (HvacMode.FAN_ONLY, HvacMode.DRY):
            return
        weights = self.open_weights()
        if not weights:
            return
        error = self.target - self.return_air()
        direction = 1.0
        if self.mode is HvacMode.COOL or (self.mode is HvacMode.AUTO and error < 0):
            direction = -1.0
            error = -error
        demand = max(0.0, min(1.0, error / 1.5))
        limit = 1.0
        if self.features.get(Feature.ECO):
            limit = ECO_LIMIT
        if self.features.get(Feature.QUIET):
            limit = min(limit, QUIET_LIMIT)
        if self.features.get(Feature.POWERFUL):
            demand = 1.0 if error > 0 else 0.0
            limit = POWERFUL_BOOST
        self.output = demand * limit * FAN_FACTOR.get(self.fan, 1.0)
        delivered = self.capacity * self.output * direction * minutes
        zone_count = len(self.zones)
        for n, weight in weights.items():
            # Spread per zone so a single open zone heats faster than five.
            self.zones[n].temperature += delivered * weight * zone_count / max(1, len(weights))
