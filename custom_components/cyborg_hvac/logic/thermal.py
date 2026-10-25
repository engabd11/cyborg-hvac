"""A simple thermal model per zone, learned from how rooms behave.

dT/dt = loss * (outdoor - room) + gain * share * output

``loss`` is learned while the unit is off (rooms drift towards outdoor),
``gain`` while it runs (what the unit adds beyond the drift). Both start at
sensible defaults and move slowly, so one odd reading (a door left open)
does little harm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.model import HvacMode

DEFAULT_LOSS = 0.0015  # fraction of the indoor/outdoor gap per minute (about 1 °C an hour at 10 °C)
DEFAULT_GAIN = 0.15  # degrees per minute with all the air going to this zone
LEARN_RATE = 0.05
MIN_GAP = 3.0  # degrees between room and outdoor before loss is learnable
LEARNED_SAMPLES = 36  # three hours of five-minute samples
MAX_FORECAST_MINUTES = 360


@dataclass(slots=True)
class ZoneModel:
    """Learned behaviour of one room."""

    loss: float = DEFAULT_LOSS
    gain: float = DEFAULT_GAIN
    loss_samples: int = 0
    gain_samples: int = 0

    @property
    def confident(self) -> bool:
        """Return True once both terms have enough data."""
        return self.loss_samples >= LEARNED_SAMPLES and self.gain_samples >= LEARNED_SAMPLES


@dataclass(slots=True)
class ThermalSample:
    """Room temperatures and conditions at one moment."""

    temps: dict[int, float]
    outdoor: float | None
    running: bool
    mode: HvacMode | None
    shares: dict[int, float]


@dataclass(slots=True)
class ThermalModel:
    """Models for every zone of a unit."""

    zones: dict[int, ZoneModel] = field(default_factory=dict)

    def zone(self, number: int) -> ZoneModel:
        """Return (creating if needed) a zone's model."""
        return self.zones.setdefault(number, ZoneModel())

    def learn(self, before: ThermalSample, after: ThermalSample, minutes: float) -> None:
        """Update from two samples ``minutes`` apart."""
        if minutes <= 0 or before.outdoor is None:
            return
        for number, start in before.temps.items():
            end = after.temps.get(number)
            if end is None:
                continue
            model = self.zone(number)
            gap = before.outdoor - start
            rate = (end - start) / minutes
            drift = model.loss * gap
            if not before.running or before.mode not in (HvacMode.HEAT, HvacMode.COOL):
                if abs(gap) >= MIN_GAP:
                    estimate = max(0.0005, min(0.05, rate / gap))
                    model.loss += LEARN_RATE * (estimate - model.loss)
                    model.loss_samples += 1
                continue
            share = before.shares.get(number, 0.0)
            if share < 0.1:
                continue
            sign = 1.0 if before.mode is HvacMode.HEAT else -1.0
            estimate = (rate - drift) * sign / share
            estimate = max(0.01, min(1.0, estimate))
            model.gain += LEARN_RATE * (estimate - model.gain)
            model.gain_samples += 1

    def minutes_to_reach(
        self,
        number: int,
        start: float,
        target: float,
        outdoor: float,
        mode: HvacMode,
        share: float,
    ) -> int | None:
        """Return minutes for a room to reach ``target`` at full output, or None."""
        model = self.zone(number)
        temp = start
        sign = 1.0 if mode is HvacMode.HEAT else -1.0
        for minute in range(MAX_FORECAST_MINUTES + 1):
            if (mode is HvacMode.HEAT and temp >= target) or (
                mode is HvacMode.COOL and temp <= target
            ):
                return minute
            temp += model.loss * (outdoor - temp) + sign * model.gain * share
        return None

    def drift(self, number: int, start: float, outdoors: list[float]) -> float:
        """Return a room's temperature after drifting one minute per outdoor value."""
        model = self.zone(number)
        temp = start
        for outdoor in outdoors:
            temp += model.loss * (outdoor - temp)
        return temp

    def as_dict(self) -> dict[str, Any]:
        """Return a storable form."""
        return {
            str(n): {
                "loss": m.loss,
                "gain": m.gain,
                "loss_samples": m.loss_samples,
                "gain_samples": m.gain_samples,
            }
            for n, m in self.zones.items()
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ThermalModel:
        """Rebuild from storage."""
        model = cls()
        for key, value in (data or {}).items():
            try:
                model.zones[int(key)] = ZoneModel(
                    loss=float(value["loss"]),
                    gain=float(value["gain"]),
                    loss_samples=int(value.get("loss_samples", 0)),
                    gain_samples=int(value.get("gain_samples", 0)),
                )
            except (KeyError, TypeError, ValueError):
                continue
        return model
