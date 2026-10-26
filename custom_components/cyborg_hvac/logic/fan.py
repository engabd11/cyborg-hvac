"""Fan speed: quiet at night, slow when humid, fast in a heatwave, gentle on a small duct.

A slower fan leaves air on the cold coil for longer, so it carries away more
moisture. A fast fan moves the most heat when the house is far behind. With
less open duct than the airflow floor (only when the floor has been lowered)
the same fan speed pushes harder through less duct, which is noisy, so the
fan is capped. Everything else is left to the unit's own auto.
"""

from __future__ import annotations

from ..core.model import FAN_AUTO, FAN_SPEEDS_ORDERED, HvacMode
from .snapshot import HouseSnapshot

PULL_DOWN = 2.0  # degrees behind that count as a pull-down
SLEEP_BOOST = 1.5  # degrees a bedroom may be short at bedtime before the fan picks up
SMALL_DUCT_TOTAL = 150  # total opening (%) below which the duct counts as small
HYSTERESIS = 1.0  # degrees a pull-down or bedtime boost must recover before the fan drops


def _rank(speed: str) -> int:
    return (
        FAN_SPEEDS_ORDERED.index(speed) if speed in FAN_SPEEDS_ORDERED else len(FAN_SPEEDS_ORDERED)
    )


def _nearest(speeds: tuple[str, ...], wanted: str) -> str | None:
    """Return the supported speed closest to ``wanted``."""
    if wanted in speeds:
        return wanted
    fixed = [f for f in speeds if f in FAN_SPEEDS_ORDERED]
    if not fixed:
        return None
    return min(fixed, key=lambda f: abs(_rank(f) - _rank(wanted)))


def vent_fan(s: HouseSnapshot, wanted: str) -> str | None:
    """Return the fan speed closest to ``wanted`` for fan-only, which has no auto."""
    return _nearest(s.caps.fan_speeds, wanted)


def choose_fan(
    s: HouseSnapshot,
    mode: HvacMode | None,
    *,
    current: str | None = None,
    humid: bool,
    shortfall: float,
    peak: bool,
    open_total: int,
    open_count: int,
    pull_down: float = PULL_DOWN,
    sleep_boost: float = SLEEP_BOOST,
) -> str | None:
    """Return the fan speed for this moment, or None to leave it to the unit.

    ``pull_down`` and ``sleep_boost`` are the degrees short before the fan picks up;
    the energy mode and Bedtime set them.
    """
    speeds = s.caps.fan_speeds
    if not speeds or mode not in (HvacMode.HEAT, HvacMode.COOL):
        return None
    # A faster fan stays until the rooms have clearly caught up, so it does not flap.
    margin = HYSTERESIS if current is not None and _rank(current) >= _rank("medium") else 0.0
    if mode is HvacMode.COOL and humid:
        wanted = "low"
    elif s.sleep_mode:
        wanted = "medium" if shortfall >= sleep_boost - margin else "low"
    elif peak or shortfall >= pull_down - (HYSTERESIS if current == "high" else 0.0):
        wanted = "high"
    else:
        wanted = FAN_AUTO
    if (open_total < SMALL_DUCT_TOTAL or open_count < 2) and (
        wanted == FAN_AUTO or _rank(wanted) > _rank("medium")
    ):
        wanted = "medium"
    if wanted == FAN_AUTO:
        return FAN_AUTO if FAN_AUTO in speeds else None
    return _nearest(speeds, wanted)
