"""Drive the autopilot engine against the demo simulation, minute by minute."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise

from cyborg_hvac.backends.demo.backend import demo_capabilities
from cyborg_hvac.backends.demo.simulation import SimSystem
from cyborg_hvac.core.model import Command, HvacMode, UnitState, ZoneState
from cyborg_hvac.logic.autopilot import (
    AutopilotMemory,
    Decision,
    Switches,
    decide,
    note_sent,
)
from cyborg_hvac.logic.guard import GuardConfig, GuardState, filter_command, record
from cyborg_hvac.logic.planner import precondition_blocks
from cyborg_hvac.logic.snapshot import ComfortSettings, ForecastPoint, HouseSnapshot, ZoneInputs
from cyborg_hvac.logic.thermal import ThermalModel


def realistic(sim: SimSystem, loss: float = 0.002, capacity: float = 0.3) -> SimSystem:
    """Slow the simulation to house speed: about a degree an hour of drift."""
    for n, zone in sim.zones.items():
        zone.loss_rate = loss + 0.0005 * n
    sim.capacity = capacity
    return sim


@dataclass
class Rig:
    """One simulated house with autopilot in charge."""

    sim: SimSystem
    switches: Switches
    start: datetime
    settings: ComfortSettings = field(default_factory=ComfortSettings)
    zone_kw: dict[int, dict] = field(default_factory=dict)
    snap_kw: dict = field(default_factory=dict)
    # Outdoor temperature by minute from the start; the forecast follows it.
    outdoor: Callable[[int], float] | None = None
    humidity: Callable[[int, int], float | None] | None = None
    memory: AutopilotMemory = field(default_factory=AutopilotMemory)
    guard: GuardState = field(default_factory=GuardState)
    cfg: GuardConfig = field(default_factory=GuardConfig)
    model: ThermalModel = field(default_factory=ThermalModel)
    minute: int = 0
    sent: list[tuple[int, Command, Decision]] = field(default_factory=list)
    modes: list[HvacMode | None] = field(default_factory=list)
    temps: list[dict[int, float]] = field(default_factory=list)
    targets: list[float] = field(default_factory=list)
    budget_hit: bool = False

    def __post_init__(self) -> None:
        # What the model would have learned about these rooms by now.
        for n, zone in self.sim.zones.items():
            self.model.zone(n).loss = zone.loss_rate

    @property
    def now(self) -> datetime:
        return self.start + timedelta(minutes=self.minute)

    def forecast(self) -> tuple[ForecastPoint, ...]:
        if self.outdoor is None:
            return ()
        return tuple(
            ForecastPoint(self.now + timedelta(hours=h), self.outdoor(self.minute + h * 60))
            for h in range(25)
        )

    def snapshot(self) -> HouseSnapshot:
        sim = self.sim
        unit = UnitState(
            power=sim.power,
            mode=sim.mode,
            target_temp=sim.target,
            current_temp=round(sim.return_air(), 1),
            fan_speed=sim.fan,
            zones={n: ZoneState(q.on, q.opening) for n, q in sim.zones.items()},
        )
        zones = tuple(
            ZoneInputs(
                zone=n,
                name=f"Z{n}",
                on=q.on,
                opening=q.opening,
                temperature=round(q.temperature, 2),
                humidity=self.humidity(n, self.minute) if self.humidity else None,
                **self.zone_kw.get(n, {}),
            )
            for n, q in sim.zones.items()
        )
        return HouseSnapshot(
            now=self.now,
            unit_name="Sim",
            caps=demo_capabilities(),
            unit=unit,
            zones=zones,
            settings=self.settings,
            outdoor_temp=round(sim.outdoor, 1),
            forecast=self.forecast(),
            **self.snap_kw,
        )

    def step(self) -> Decision:
        sim = self.sim
        if self.outdoor is not None:
            sim.outdoor = self.outdoor(self.minute)
        sim.step(1)
        s = self.snapshot()
        blocks = precondition_blocks(s, self.model) if self.switches.weather else []
        decision = decide(s, self.switches, blocks, self.model, self.memory, (), 1.0)
        while self.guard.writes and self.now - self.guard.writes[0] > self.cfg.budget_window:
            self.guard.writes.popleft()
        if not decision.command.is_empty and len(self.guard.writes) >= self.cfg.write_budget:
            self.budget_hit = True
        allowed = filter_command(self.cfg, self.guard, s, decision.command, self.now)
        if allowed is not None:
            before = sim.mode
            if allowed.power is not None:
                sim.power = allowed.power
            if allowed.mode is not None:
                sim.mode = allowed.mode
            if allowed.target_temp is not None:
                sim.target = allowed.target_temp
            if allowed.fan_speed is not None:
                sim.fan = allowed.fan_speed
            for n, change in allowed.zones.items():
                if change.on is not None:
                    sim.zones[n].on = change.on
                if change.opening is not None:
                    sim.zones[n].opening = change.opening
            record(self.guard, allowed, self.now)
            note_sent(self.memory, allowed, self.now, before)
            self.sent.append((self.minute, allowed, decision))
        self.modes.append(sim.mode if sim.power else None)
        self.temps.append({n: z.temperature for n, z in sim.zones.items()})
        self.targets.append(sim.target)
        self.minute += 1
        return decision

    def run(self, minutes: int) -> Rig:
        for _ in range(minutes):
            self.step()
        return self

    # Measures ------------------------------------------------------------------------

    def writes(self, start: int = 0, end: int | None = None) -> int:
        return sum(1 for m, _, _ in self.sent if m >= start and (end is None or m < end))

    def zone_moves(self, zone: int) -> list[int]:
        return [m for m, c, _ in self.sent if zone in c.zones]

    def most_moves_in_an_hour(self, zone: int, after: int = 0) -> int:
        moves = [m for m in self.zone_moves(zone) if m >= after]
        return max((sum(1 for x in moves if m <= x < m + 60) for m in moves), default=0)

    def changeovers(self) -> int:
        running = [m for m in self.modes if m in (HvacMode.HEAT, HvacMode.COOL)]
        return sum(1 for a, b in pairwise(running) if a is not b)

    def range_of(self, zone: int, start: int, end: int | None = None) -> tuple[float, float]:
        values = [t[zone] for t in self.temps[start:end]]
        return min(values), max(values)
