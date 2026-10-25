"""Demo backend: a simulated ducted unit.

It doubles as the reference implementation for new brands: everything a backend
must provide is here in its smallest form.
"""

from __future__ import annotations

from collections.abc import Callable
import time

from ...core.backend import HvacBackend
from ...core.errors import CannotConnect
from ...core.model import (
    Capabilities,
    Command,
    Feature,
    FeatureKind,
    FeatureSpec,
    HvacMode,
    SensorKind,
    UnitInfo,
    UnitState,
    ZoneState,
)
from .simulation import SimSystem

BRAND = "demo"
UNIT_ID = "demo_unit"
ZONE_SLOTS = 8

_HEAT_COOL = frozenset({HvacMode.HEAT, HvacMode.COOL})


def demo_capabilities() -> Capabilities:
    """Return the capabilities of the simulated unit."""
    return Capabilities(
        hvac_modes=(
            HvacMode.HEAT,
            HvacMode.COOL,
            HvacMode.DRY,
            HvacMode.FAN_ONLY,
            HvacMode.AUTO,
        ),
        fan_speeds=("auto", "low", "medium", "high"),
        min_temp=16,
        max_temp=30,
        temp_step=0.5,
        features={
            Feature.POWERFUL: FeatureSpec(
                Feature.POWERFUL, FeatureKind.SWITCH, valid_modes=_HEAT_COOL
            ),
            Feature.ECO: FeatureSpec(Feature.ECO, FeatureKind.SWITCH),
            Feature.QUIET: FeatureSpec(Feature.QUIET, FeatureKind.SWITCH),
            Feature.SLEEP: FeatureSpec(
                Feature.SLEEP, FeatureKind.SELECT, options=("off", "general")
            ),
            Feature.TEMP_OFFSET: FeatureSpec(
                Feature.TEMP_OFFSET,
                FeatureKind.NUMBER,
                min_value=-7,
                max_value=7,
                requires_power=False,
            ),
        },
        sensors=(SensorKind.RETURN_AIR_TEMPERATURE,),
        zone_slots=ZONE_SLOTS,
        zone_opening_step=5,
        zone_temperatures=True,
    )


class DemoBackend(HvacBackend):
    """One simulated unit. Time runs ``speed`` times faster than real time."""

    def __init__(
        self,
        zone_count: int = 5,
        speed: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create the simulation."""
        self.system = SimSystem.create(zone_count)
        self._speed = speed
        self._clock = clock
        self._last = clock()
        self._zone_count = zone_count
        self.fail_next_send = False

    async def async_discover(self) -> list[UnitInfo]:
        """Return the single simulated unit."""
        return [
            UnitInfo(
                unit_id=UNIT_ID,
                name="Demo ducted unit",
                brand=BRAND,
                manufacturer="Cyborg HVAC",
                model="Simulated ducted unit",
                capabilities=demo_capabilities(),
                installed_zones_hint=tuple(range(1, self._zone_count + 1)),
            )
        ]

    def advance(self) -> None:
        """Run the simulation up to now."""
        now = self._clock()
        minutes = (now - self._last) / 60 * self._speed
        self._last = now
        # Step in small slices so long gaps stay stable.
        while minutes > 0:
            slice_minutes = min(1.0, minutes)
            self.system.step(slice_minutes)
            minutes -= slice_minutes

    def state(self) -> UnitState:
        """Return the simulated unit as a UnitState."""
        system = self.system
        return UnitState(
            online=True,
            power=system.power,
            mode=system.mode,
            target_temp=system.target,
            current_temp=round(system.return_air(), 1),
            fan_speed=system.fan,
            features={
                Feature.POWERFUL: bool(system.features.get(Feature.POWERFUL)),
                Feature.ECO: bool(system.features.get(Feature.ECO)),
                Feature.QUIET: bool(system.features.get(Feature.QUIET)),
                Feature.SLEEP: system.features.get(Feature.SLEEP, "off"),
                Feature.TEMP_OFFSET: float(system.features.get(Feature.TEMP_OFFSET, 0)),
            },
            zones={
                n: ZoneState(on=z.on, opening=z.opening, temperature=round(z.temperature, 1))
                for n, z in system.zones.items()
            },
            sensors={SensorKind.RETURN_AIR_TEMPERATURE: round(system.return_air(), 1)},
        )

    async def async_fetch(self) -> dict[str, UnitState]:
        """Advance time and return the state."""
        self.advance()
        return {UNIT_ID: self.state()}

    async def async_send(self, unit_id: str, command: Command) -> None:
        """Apply a command to the simulation."""
        if self.fail_next_send:
            self.fail_next_send = False
            raise CannotConnect("Simulated failure")
        self.advance()
        system = self.system
        if command.power is not None:
            system.power = command.power
        if command.mode is not None:
            system.mode = command.mode
        if command.target_temp is not None:
            system.target = command.target_temp
        if command.fan_speed is not None:
            system.fan = command.fan_speed
        system.features.update(command.features)
        if command.features.get(Feature.POWERFUL):
            system.features[Feature.ECO] = False
        for n, change in command.zones.items():
            zone = system.zones.get(n)
            if zone is None:
                continue
            if change.on is not None:
                zone.on = change.on
            if change.opening is not None:
                zone.opening = change.opening
