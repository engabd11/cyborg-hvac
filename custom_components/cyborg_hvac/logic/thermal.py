"""A simple thermal model per zone, learned from how rooms behave.

dT/dt = loss * (outdoor - room) + bias + gain * share * output

``loss`` is learned while the unit is off (rooms drift towards outdoor),
``gain`` while it runs (what the unit adds beyond the drift). ``bias`` is what
the room adds by itself (sleepers, sun, a computer), learned separately for
the day and the night: a closed bedroom with people asleep may not cool at all
on a cold night, and the engine must not wait for it to. All start at sensible
defaults and move slowly, so one odd reading (a door left open) does little
harm.

Each room also remembers its holding opening: the damper opening that kept it
at its aim in heating and in cooling, learned from how it drifted off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.model import HvacMode

DEFAULT_LOSS = 0.0015  # fraction of the indoor/outdoor gap per minute (about 1 °C an hour at 10 °C)
DEFAULT_GAIN = 0.15  # degrees per minute with all the air going to this zone
LEARN_RATE = 0.05
MIN_GAP = 3.0  # degrees between room and outdoor before loss counts as learned
LEARNED_SAMPLES = 36  # three hours of five-minute samples
MAX_FORECAST_MINUTES = 360
MIN_LOSS, MAX_LOSS = 0.0005, 0.05
BIAS_LIMIT = 0.02  # most a room adds by itself, in degrees per minute (1.2 °C an hour)
# A room's own warmth (or chill) worth telling people about, in degrees per minute, once it
# has this many samples behind it.
BIAS_NOTABLE = 0.005
BIAS_SAMPLES = 24
# Drift is learned with a small Kalman filter over (loss, day bias, night bias): the gap to
# outdoor tells loss and bias apart as it changes through the day. Prior spread of each term,
# how much each may wander per sample, and the noise of one five-minute rate (°C/min squared).
PRIOR = (1e-4, 4e-5, 4e-5)
WANDER = (1e-9, 1e-9, 1e-9)
RATE_NOISE = 1e-4


@dataclass(slots=True)
class ZoneModel:
    """Learned behaviour of one room."""

    loss: float = DEFAULT_LOSS
    gain: float = DEFAULT_GAIN
    loss_samples: int = 0
    gain_samples: int = 0
    hold_heat: int | None = None
    hold_cool: int | None = None
    bias_day: float = 0.0
    bias_night: float = 0.0
    bias_samples_day: int = 0
    bias_samples_night: int = 0
    # The bias last told about, by "day" and "night", so each finding is reported once.
    announced: dict[str, float] = field(default_factory=dict)
    # How sure the drift terms are (the filter's covariance); None before any learning.
    spread: list[list[float]] | None = None

    @property
    def confident(self) -> bool:
        """Return True once both terms have enough data."""
        return self.loss_samples >= LEARNED_SAMPLES and self.gain_samples >= LEARNED_SAMPLES

    def bias(self, night: bool) -> float:
        """Return what the room adds by itself, in degrees per minute."""
        return self.bias_night if night else self.bias_day

    def learn_drift(self, gap: float, rate: float, night: bool) -> None:
        """Learn loss and the day or night bias from one rate while the unit is off.

        ``gap`` is outdoor minus room, ``rate`` how fast the room moved (°C/min).
        """
        theta = [self.loss, self.bias_day, self.bias_night]
        phi = [gap, 0.0 if night else 1.0, 1.0 if night else 0.0]
        p = self.spread or [[PRIOR[i] if i == j else 0.0 for j in range(3)] for i in range(3)]
        p_phi = [sum(p[i][j] * phi[j] for j in range(3)) for i in range(3)]
        total = RATE_NOISE + sum(phi[i] * p_phi[i] for i in range(3))
        gain = [p_phi[i] / total for i in range(3)]
        # One odd reading (a door left open) moves the terms only so far.
        limit = 3 * total**0.5
        error = max(-limit, min(limit, rate - sum(phi[i] * theta[i] for i in range(3))))
        theta = [theta[i] + gain[i] * error for i in range(3)]
        p = [
            [p[i][j] - gain[i] * p_phi[j] + (WANDER[i] if i == j else 0.0) for j in range(3)]
            for i in range(3)
        ]
        for i in range(3):  # a term left unexercised (no nights yet) stays at its prior spread
            if p[i][i] > PRIOR[i]:
                scale = (PRIOR[i] / p[i][i]) ** 0.5
                for j in range(3):
                    p[i][j] *= scale
                    p[j][i] *= scale
        self.spread = p
        self.loss = max(MIN_LOSS, min(MAX_LOSS, theta[0]))
        self.bias_day = max(-BIAS_LIMIT, min(BIAS_LIMIT, theta[1]))
        self.bias_night = max(-BIAS_LIMIT, min(BIAS_LIMIT, theta[2]))
        if abs(gap) >= MIN_GAP:
            self.loss_samples += 1
        if night:
            self.bias_samples_night += 1
        else:
            self.bias_samples_day += 1

    def hold(self, mode: HvacMode) -> int | None:
        """Return the learned holding opening for a mode, if any."""
        return self.hold_heat if mode is HvacMode.HEAT else self.hold_cool

    def set_hold(self, mode: HvacMode, opening: int) -> None:
        """Remember the holding opening for a mode."""
        if mode is HvacMode.HEAT:
            self.hold_heat = opening
        else:
            self.hold_cool = opening


@dataclass(slots=True)
class ThermalSample:
    """Room temperatures and conditions at one moment."""

    temps: dict[int, float]
    outdoor: float | None
    running: bool
    mode: HvacMode | None
    shares: dict[int, float]
    # True during the night window: rooms behave differently with people asleep.
    night: bool = False


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
            bias = model.bias(before.night)
            drift = model.loss * gap + bias
            if not before.running:
                model.learn_drift(gap, rate, before.night)
                continue
            if before.mode not in (HvacMode.HEAT, HvacMode.COOL):
                continue  # fan or drying: the air is mixed, nothing to learn
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
        night: bool = False,
    ) -> int | None:
        """Return minutes for a room to reach ``target`` at full output, or None."""
        model = self.zone(number)
        temp = start
        sign = 1.0 if mode is HvacMode.HEAT else -1.0
        bias = model.bias(night)
        for minute in range(MAX_FORECAST_MINUTES + 1):
            if (mode is HvacMode.HEAT and temp >= target) or (
                mode is HvacMode.COOL and temp <= target
            ):
                return minute
            temp += model.loss * (outdoor - temp) + bias + sign * model.gain * share
        return None

    def minutes_to_drift(
        self, number: int, start: float, target: float, outdoors: list[float], night: bool = False
    ) -> int | None:
        """Return minutes for a room left alone to drift from ``start`` to ``target``.

        ``outdoors`` holds one outdoor temperature per minute. None when the room
        does not get there within that time, for example when outside pushes it
        the other way, or the room's own warmth holds it where it is.
        """
        model = self.zone(number)
        bias = model.bias(night)
        temp = start
        falling = target < start
        for minute, outdoor in enumerate(outdoors):
            if (temp <= target) if falling else (temp >= target):
                return minute
            temp += model.loss * (outdoor - temp) + bias
        return None

    def drift(self, number: int, start: float, outdoors: list[float], night: bool = False) -> float:
        """Return a room's temperature after drifting one minute per outdoor value."""
        model = self.zone(number)
        bias = model.bias(night)
        temp = start
        for outdoor in outdoors:
            temp += model.loss * (outdoor - temp) + bias
        return temp

    def news(self) -> list[tuple[int, bool, float]]:
        """Return (zone, night, bias) for each room whose own warmth is newly worth telling.

        Each finding is told once, and again only once it has changed by as much again.
        """
        found = []
        for number, model in self.zones.items():
            for night in (False, True):
                samples = model.bias_samples_night if night else model.bias_samples_day
                bias = model.bias(night)
                key = "night" if night else "day"
                told = model.announced.get(key, 0.0)
                if samples < BIAS_SAMPLES or abs(bias - told) < BIAS_NOTABLE:
                    continue
                model.announced[key] = bias
                found.append((number, night, bias))
        return found

    def as_dict(self) -> dict[str, Any]:
        """Return a storable form."""
        return {
            str(n): {
                "loss": m.loss,
                "gain": m.gain,
                "loss_samples": m.loss_samples,
                "gain_samples": m.gain_samples,
                "hold_heat": m.hold_heat,
                "hold_cool": m.hold_cool,
                "bias_day": m.bias_day,
                "bias_night": m.bias_night,
                "bias_samples_day": m.bias_samples_day,
                "bias_samples_night": m.bias_samples_night,
                "announced": dict(m.announced),
                "spread": m.spread,
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
                    hold_heat=_opening(value.get("hold_heat")),
                    hold_cool=_opening(value.get("hold_cool")),
                    bias_day=float(value.get("bias_day", 0.0)),
                    bias_night=float(value.get("bias_night", 0.0)),
                    bias_samples_day=int(value.get("bias_samples_day", 0)),
                    bias_samples_night=int(value.get("bias_samples_night", 0)),
                    announced={str(k): float(v) for k, v in (value.get("announced") or {}).items()},
                    spread=_spread(value.get("spread")),
                )
            except (KeyError, TypeError, ValueError):
                continue
        return model


def _opening(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _spread(value: Any) -> list[list[float]] | None:
    try:
        rows = [[float(x) for x in row] for row in value]
    except (TypeError, ValueError):
        return None
    return rows if len(rows) == 3 and all(len(row) == 3 for row in rows) else None
