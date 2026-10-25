"""Good-practice advice for a ducted system.

Each rule looks at a HouseSnapshot and may return recommendations. A
recommendation carries a plain-language reason and, when there is a clear
fix, a Command that applies it. Rules never act on their own; autopilot
behaviours decide separately what to apply.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum
from typing import Any

from ..core.model import Command, Feature, HvacMode, ZoneCommand
from .comfort import (
    NEED_MARGIN,
    current_period,
    deciding_zones,
    house_band,
    house_demand,
    indoor_temperature,
    needs,
    zone_band,
    zone_error,
)
from .health import assess
from .outlook import outdoor_series, outlook
from .snapshot import HouseSnapshot, Period, Priority, ZoneInputs
from .thermal import ThermalModel

HIGH_HEAT_SETPOINT = 26.0
LOW_COOL_SETPOINT = 21.0
SPREAD_DEGREES = 2.0
HOT_DAY_MARGIN = 8.0
COLD_NIGHT_MARGIN = 10.0
SOLAR_SURPLUS_W = 1500.0
COAST_MINUTES = 120  # outside bringing rooms back within this long counts as coasting


class Severity(StrEnum):
    """How urgent a recommendation is."""

    ALERT = "alert"
    SUGGEST = "suggest"
    INFO = "info"


SEVERITY_ORDER = {Severity.ALERT: 0, Severity.SUGGEST: 1, Severity.INFO: 2}


@dataclass(frozen=True, slots=True)
class Recommendation:
    """One piece of advice."""

    id: str
    category: str
    severity: Severity
    title: str
    reason: str
    action: Command | None = None
    zone: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly form for the card and attributes."""
        return {
            "id": self.id,
            "category": self.category,
            "severity": self.severity.value,
            "title": self.title,
            "reason": self.reason,
            "zone": self.zone,
            "action": command_as_dict(self.action) if self.action else None,
        }


def command_as_dict(command: Command) -> dict[str, Any]:
    """Return a Command as plain data."""
    data: dict[str, Any] = {}
    if command.power is not None:
        data["power"] = command.power
    if command.mode is not None:
        data["mode"] = command.mode.value
    if command.target_temp is not None:
        data["target_temp"] = command.target_temp
    if command.fan_speed is not None:
        data["fan_speed"] = command.fan_speed
    if command.features:
        data["features"] = {f.value: v for f, v in command.features.items()}
    if command.zones:
        data["zones"] = {
            str(n): {k: v for k, v in (("on", z.on), ("opening", z.opening)) if v is not None}
            for n, z in command.zones.items()
        }
    return data


def fmt(value: float | None) -> str:
    """Format a temperature for advice text."""
    if value is None:
        return "unknown"
    rounded = round(value * 2) / 2
    text = f"{rounded:g}"
    return f"{text} °C" if rounded >= 0 else f"{text.lstrip('-')} °C below zero"


def _supports(snapshot: HouseSnapshot, mode: HvacMode) -> bool:
    return mode in snapshot.caps.hvac_modes


def _target(snapshot: HouseSnapshot, value: float) -> float:
    return snapshot.caps.round_temp(value)


# Unit health ---------------------------------------------------------------------


def rule_unit_health(s: HouseSnapshot) -> Iterable[Recommendation]:
    """Offline and fault alerts."""
    if not s.unit.online:
        yield Recommendation(
            "unit_offline",
            "unit",
            Severity.ALERT,
            f"{s.unit_name} is offline",
            "The unit is not reporting to the cloud. Check its power and Wi-Fi.",
        )
    if s.unit.faults:
        yield Recommendation(
            "unit_fault",
            "unit",
            Severity.ALERT,
            "The unit reports a fault",
            "Fault codes: " + ", ".join(s.unit.faults) + ". Check the wall controller.",
        )
    if s.unit.filter_alert:
        yield Recommendation(
            "unit_filter",
            "unit",
            Severity.SUGGEST,
            "Clean the filter",
            "A clogged filter cuts airflow, so rooms take longer to reach comfort, and it lets "
            "dust and mould spores back into the house.",
        )


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def describe_rooms(s: HouseSnapshot, warm: bool) -> str:
    """Name the rooms outside their range, for example "MASTER (24.1 °C) is above 23 °C"."""
    zones = deciding_zones(s)
    offenders = [
        z
        for z in zones
        if (zone_error(s, z) < -NEED_MARGIN if warm else zone_error(s, z) > NEED_MARGIN)
    ]
    if not offenders:
        temp = s.unit.current_temp
        word = "above" if warm else "below"
        band = house_band(s)
        return f"The unit reads {fmt(temp)}, {word} {fmt(band.high if warm else band.low)}."
    edges = {zone_band(s, z).high if warm else zone_band(s, z).low for z in offenders}
    named = _join([f"{z.name} ({fmt(z.temperature)})" for z in offenders])
    verb = "is" if len(offenders) == 1 else "are"
    word = "above" if warm else "below"
    limit = f" {word} {fmt(edges.pop())}" if len(edges) == 1 else f" {word} their comfort range"
    rest = len(zones) - len(offenders)
    tail = " The other rooms are comfortable." if rest > 0 else ""
    return f"{named} {verb}{limit}.{tail}"


# Mode -----------------------------------------------------------------------------


def _coast_minutes(s: HouseSnapshot, model: ThermalModel, warm: bool) -> float | None:
    """Return minutes until outside brings the rooms out of range back, or None if it will not."""
    offenders = [
        z
        for z in deciding_zones(s)
        if (zone_error(s, z) < -NEED_MARGIN if warm else zone_error(s, z) > NEED_MARGIN)
    ]
    outdoors = outdoor_series(s, s.now + timedelta(minutes=COAST_MINUTES))
    if not offenders or not outdoors:
        return None
    longest = 0
    for zone in offenders:
        band = zone_band(s, zone)
        edge = band.high if warm else band.low
        minutes = model.minutes_to_drift(zone.zone, zone.temperature, edge, outdoors)  # type: ignore[arg-type]
        if minutes is None:
            return None
        longest = max(longest, minutes)
    return longest


def rule_mode(s: HouseSnapshot, model: ThermalModel | None = None) -> Iterable[Recommendation]:
    """Pick the right mode for how the rooms and outdoor air are right now."""
    model = model or ThermalModel()
    need_heat, need_cool = needs(s)
    band = house_band(s)
    indoor = indoor_temperature(s)
    outdoor = s.outdoor_temp
    running = s.unit.power
    mode = s.unit.mode if running else None

    if mode is HvacMode.HEAT and need_cool and not need_heat:
        yield Recommendation(
            "mode_heating_warm",
            "mode",
            Severity.ALERT,
            "Heating while rooms are too warm",
            f"{describe_rooms(s, warm=True)} Turn heating off.",
            Command(power=False),
        )
        return
    if mode is HvacMode.HEAT and need_cool and need_heat:
        yield from _close_ahead(s, warm=True)
    elif mode is HvacMode.COOL and need_cool and need_heat:
        yield from _close_ahead(s, warm=False)
    if mode is HvacMode.COOL and need_heat and not need_cool:
        yield Recommendation(
            "mode_cooling_cold",
            "mode",
            Severity.ALERT,
            "Cooling while rooms are too cold",
            f"{describe_rooms(s, warm=False)} Turn cooling off.",
            Command(power=False),
        )
        return

    if mode is HvacMode.AUTO and _supports(s, HvacMode.HEAT) and _supports(s, HvacMode.COOL):
        demand = house_demand(s)
        warm_outside = outdoor is not None and outdoor > band.middle
        pick = HvacMode.HEAT if demand > 0 or (demand == 0 and not warm_outside) else HvacMode.COOL
        yield Recommendation(
            "mode_avoid_auto",
            "mode",
            Severity.SUGGEST,
            f"Use {pick.value} instead of auto",
            "Ducted units judge auto on return air, which can swing between heating "
            "and cooling. A fixed mode holds rooms steadier.",
            Command(power=True, mode=pick),
        )

    if not running and need_heat and not need_cool:
        if outdoor is not None and indoor is not None and outdoor >= indoor + 1:
            yield Recommendation(
                "free_heating",
                "mode",
                Severity.INFO,
                "Open windows to warm up",
                f"It is {fmt(outdoor)} outside, warmer than inside ({fmt(indoor)}).",
            )
        elif _supports(s, HvacMode.HEAT):
            target = _target(s, band.low + 1)
            yield Recommendation(
                "mode_heat",
                "mode",
                Severity.SUGGEST,
                "Heating recommended",
                f"{describe_rooms(s, warm=False)} Heat to {fmt(target)}.",
                Command(power=True, mode=HvacMode.HEAT, target_temp=target),
            )
    elif not running and need_cool and not need_heat:
        coast = _coast_minutes(s, model, warm=True)
        if coast is not None and outdoor is not None and outdoor < band.low - 2:
            yield Recommendation(
                "mode_coast",
                "mode",
                Severity.INFO,
                "Rooms will cool on their own",
                f"{describe_rooms(s, warm=True)} It is {fmt(outdoor)} outside, so they are back "
                f"in range in about {max(5, round(coast / 5) * 5)} minutes without cooling.",
            )
        elif (
            outdoor is not None
            and indoor is not None
            and outdoor <= indoor - 2
            and outdoor >= band.low - 2
        ):
            yield Recommendation(
                "free_cooling",
                "mode",
                Severity.SUGGEST,
                "Open windows instead of cooling",
                f"It is {fmt(outdoor)} outside, cooler than inside ({fmt(indoor)}). "
                "Fresh air cools the house for free.",
            )
        elif _supports(s, HvacMode.COOL):
            target = _target(s, band.high - 1)
            yield Recommendation(
                "mode_cool",
                "mode",
                Severity.SUGGEST,
                "Cooling recommended",
                f"{describe_rooms(s, warm=True)} Cool to {fmt(target)}.",
                Command(power=True, mode=HvacMode.COOL, target_temp=target),
            )

    comfortable = not need_heat and not need_cool and indoor is not None
    outdoor_mild = outdoor is None or band.low <= outdoor <= band.high
    if (
        running
        and mode in (HvacMode.HEAT, HvacMode.COOL)
        and comfortable
        and outdoor_mild
        and indoor is not None
        and band.low <= indoor <= band.high
    ):
        yield Recommendation(
            "mode_comfortable_off",
            "mode",
            Severity.SUGGEST,
            "Rooms are comfortable",
            f"Inside is {fmt(indoor)} and outside is mild. Turn the unit off "
            "and let the house coast.",
            Command(power=False),
        )

    occupied = [z for z in s.sensed_zones if z.occupied is not False]
    temps = [z.temperature for z in occupied if z.temperature is not None]
    humid = [z.humidity for z in occupied if z.humidity is not None]
    if s.unit_humidity is not None:
        humid.append(s.unit_humidity)
    if (
        len(temps) >= 2
        # Fan-only in humid air puts moisture from the coil back into the rooms.
        and not (humid and max(humid) > s.settings.humidity_max)
        and max(temps) - min(temps) > SPREAD_DEGREES
        and comfortable
        and mode is not HvacMode.FAN_ONLY
        and _supports(s, HvacMode.FAN_ONLY)
        and (not running or mode is None)
    ):
        yield Recommendation(
            "mode_fan_mix",
            "mode",
            Severity.SUGGEST,
            "Run the fan to even out rooms",
            f"Rooms differ by {max(temps) - min(temps):.1f} degrees. The fan mixes air "
            "between them using little power.",
            Command(power=True, mode=HvacMode.FAN_ONLY),
        )

    if (
        humid
        and max(humid) > s.settings.humidity_max
        and not need_heat
        and mode not in (HvacMode.DRY, HvacMode.COOL)
        and _supports(s, HvacMode.DRY)
    ):
        yield Recommendation(
            "mode_dry",
            "mode",
            Severity.SUGGEST,
            "Dry mode for humid rooms",
            f"Humidity is {max(humid):.0f}%, above {s.settings.humidity_max:.0f}%. "
            "Dry mode removes moisture without much cooling.",
            Command(power=True, mode=HvacMode.DRY),
        )


def _close_ahead(s: HouseSnapshot, warm: bool) -> Iterable[Recommendation]:
    """Suggest closing a room that is ahead while the others still need the unit."""
    for zone in deciding_zones(s):
        error = zone_error(s, zone)
        ahead = error < -NEED_MARGIN if warm else error > NEED_MARGIN
        if not ahead or not (zone.on and zone.opening > 0) or not airflow_ok(s, _without(s, zone)):
            continue
        band = zone_band(s, zone)
        word, verb = ("above", "heat") if warm else ("below", "cool")
        yield Recommendation(
            f"zone_ahead_{zone.zone}",
            "zoning",
            Severity.SUGGEST,
            f"Close {zone.name} while the other rooms {verb}",
            f"It is {fmt(zone.temperature)}, {word} {fmt(band.high if warm else band.low)}. "
            "Closed, it settles on its own while the rest of the house catches up.",
            Command(zones={zone.zone: ZoneCommand(on=False)}),
            zone.zone,
        )


# Setpoint -------------------------------------------------------------------------


def rule_setpoint(s: HouseSnapshot) -> Iterable[Recommendation]:
    """Flag setpoints that waste energy without heating or cooling faster."""
    unit = s.unit
    if not unit.power or unit.target_temp is None or unit.features.get(Feature.POWERFUL):
        return
    band = house_band(s)
    powerful = Feature.POWERFUL in s.caps.features
    tip = " Use Powerful for a quick boost instead." if powerful else ""
    if unit.mode is HvacMode.HEAT and unit.target_temp >= HIGH_HEAT_SETPOINT:
        target = _target(s, band.low + 1)
        yield Recommendation(
            "setpoint_heat_high",
            "setpoint",
            Severity.SUGGEST,
            f"Heating set to {fmt(unit.target_temp)}",
            "A high setpoint does not heat faster, and each degree adds about 10% "
            f"to running costs. Try {fmt(target)}.{tip}",
            Command(target_temp=target),
        )
    elif unit.mode is HvacMode.COOL and unit.target_temp <= LOW_COOL_SETPOINT:
        target = _target(s, band.high - 1)
        yield Recommendation(
            "setpoint_cool_low",
            "setpoint",
            Severity.SUGGEST,
            f"Cooling set to {fmt(unit.target_temp)}",
            "A low setpoint does not cool faster, and each degree adds about 10% "
            f"to running costs. Try {fmt(target)}.{tip}",
            Command(target_temp=target),
        )


# Zoning ---------------------------------------------------------------------------


def _total_opening(zones: Iterable[ZoneInputs]) -> int:
    return sum(z.opening for z in zones if z.on and z.opening > 0)


def airflow_ok(s: HouseSnapshot, zones: Iterable[ZoneInputs]) -> bool:
    """Return True when the open zones give enough airflow for the fan."""
    zones = [z for z in zones if z.on and z.opening > 0]
    return (
        len(zones) >= s.settings.min_open_zones
        and _total_opening(zones) >= s.settings.min_total_opening
    )


def _without(s: HouseSnapshot, zone: ZoneInputs) -> list[ZoneInputs]:
    return [z for z in s.zones if z.zone != zone.zone]


def rule_zoning(s: HouseSnapshot) -> Iterable[Recommendation]:
    """Airflow floor, empty rooms, open windows and rooms left out."""
    if not s.unit.power or not s.zones:
        return
    if not airflow_ok(s, s.zones):
        candidates = sorted(
            (z for z in s.zones if not (z.on and z.opening > 0)),
            key=lambda z: (not z.spill, z.occupied is False, z.priority is Priority.UTILITY),
        )
        changes: dict[int, ZoneCommand] = {}
        current = list(s.zones)
        for zone in candidates:
            if airflow_ok(s, current):
                break
            opening = zone.max_opening or 100
            changes[zone.zone] = ZoneCommand(on=True, opening=opening)
            current = [z for z in current if z.zone != zone.zone] + [
                ZoneInputs(zone.zone, zone.name, True, opening)
            ]
        yield Recommendation(
            "zoning_low_airflow",
            "zoning",
            Severity.ALERT,
            "Too few zones open",
            "Running with little open duct strains the fan and is noisy. "
            + (
                "Open " + ", ".join(z.name for z in candidates if z.zone in changes) + "."
                if changes
                else "Open another zone."
            ),
            Command(zones=changes) if changes else None,
        )

    for zone in s.zones:
        if not (zone.on and zone.opening > 0):
            continue
        if zone.window_open and airflow_ok(s, _without(s, zone)):
            what = "door" if zone.door_open else "window"
            yield Recommendation(
                f"zone_window_{zone.zone}",
                "zoning",
                Severity.SUGGEST,
                f"Close {zone.name} while its {what} is open",
                f"Conditioned air is escaping through the open {what}.",
                Command(zones={zone.zone: ZoneCommand(on=False)}),
                zone.zone,
            )
        elif (
            zone.occupied is False
            and zone.vacant_minutes is not None
            and zone.vacant_minutes >= s.settings.vacant_close_minutes
            and airflow_ok(s, _without(s, zone))
        ):
            yield Recommendation(
                f"zone_vacant_{zone.zone}",
                "zoning",
                Severity.SUGGEST,
                f"Close {zone.name}",
                f"No one has been there for {int(zone.vacant_minutes)} minutes.",
                Command(zones={zone.zone: ZoneCommand(on=False)}),
                zone.zone,
            )

    mode = s.unit.mode
    for zone in s.zones:
        if zone.on or not zone.occupied or zone.temperature is None:
            continue
        error = zone_error(s, zone)
        if (mode is HvacMode.HEAT and error > NEED_MARGIN) or (
            mode is HvacMode.COOL and error < -NEED_MARGIN
        ):
            band = zone_band(s, zone)
            word = "cold" if error > 0 else "warm"
            yield Recommendation(
                f"zone_open_{zone.zone}",
                "zoning",
                Severity.SUGGEST,
                f"Open {zone.name}",
                f"Someone is there and it is {word} ({fmt(zone.temperature)}, comfort is "
                f"{fmt(band.low)} to {fmt(band.high)}).",
                Command(zones={zone.zone: ZoneCommand(on=True, opening=zone.max_opening or 100)}),
                zone.zone,
            )


# Health ---------------------------------------------------------------------------


def _damp_advice(
    s: HouseSnapshot, zone: ZoneInputs, humidity: float, safe: float | None, hours: float
) -> tuple[str, Command | None]:
    damp = f" for {hours:.1f} hours" if hours >= 0.5 else ""
    start = f"Humidity is {humidity:.0f}% and its walls have been damp enough for mould{damp}."
    cold_outside = s.outdoor_temp is not None and s.outdoor_temp <= (zone.temperature or 0) - 3
    if cold_outside:
        warmth = f" and keep it at {fmt(safe)} or warmer" if safe is not None else ""
        action = (
            Command(zones={zone.zone: ZoneCommand(on=True, opening=zone.max_opening or 100)})
            if not zone.on and s.unit.power and s.unit.mode is HvacMode.HEAT
            else None
        )
        return (
            f"{start} Air it for 10 minutes (a window or the exhaust fan){warmth}: heating "
            "alone does not remove the moisture.",
            action,
        )
    action = Command(power=True, mode=HvacMode.DRY) if _supports(s, HvacMode.DRY) else None
    return f"{start} Dry mode removes moisture without much cooling.", action


def rule_health(s: HouseSnapshot) -> Iterable[Recommendation]:
    """Mould, condensation, overheated and cold rooms."""
    health = assess(s)
    for zone in s.zones:
        h = health.get(zone.zone)
        if h is None:
            continue
        n, name = zone.zone, zone.name
        if h.condensation and h.humidity is not None:
            yield Recommendation(
                f"health_condensation_{n}",
                "health",
                Severity.ALERT,
                f"Condensation likely in {name}",
                f"Humidity is {h.humidity:.0f}% and its coldest surfaces are at the dew point "
                f"({fmt(h.dew_point)}). Wipe windows dry and air the room for 10 minutes.",
                zone=n,
            )
        elif h.mould and h.humidity is not None:
            reason, action = _damp_advice(
                s, zone, h.humidity, h.safe_temperature, h.damp_minutes / 60
            )
            yield Recommendation(
                f"health_mould_{n}",
                "health",
                Severity.ALERT,
                f"Mould risk in {name}",
                reason,
                action,
                n,
            )
        running = s.unit.mode if s.unit.power else None
        if h.overheated:
            yield Recommendation(
                f"health_overheat_{n}",
                "health",
                Severity.ALERT,
                f"{name} is too hot",
                f"It is {fmt(h.temperature)}, above {fmt(h.ceiling)}, which disturbs sleep and "
                "is hard on health.",
                Command(power=True, mode=HvacMode.COOL, target_temp=_target(s, h.ceiling - 1))
                if _supports(s, HvacMode.COOL) and running is not HvacMode.COOL
                else None,
                n,
            )
        elif h.too_cold:
            yield Recommendation(
                f"health_cold_{n}",
                "health",
                Severity.ALERT,
                f"{name} is too cold",
                f"It is {fmt(h.temperature)}, below {fmt(h.cold_limit)}, which is hard on health "
                "and lets damp settle on cold walls.",
                Command(power=True, mode=HvacMode.HEAT, target_temp=_target(s, h.cold_limit + 2))
                if _supports(s, HvacMode.HEAT) and running is not HvacMode.HEAT
                else None,
                n,
            )


# Guest mode -----------------------------------------------------------------------


def rule_guest(s: HouseSnapshot) -> Iterable[Recommendation]:
    """Give guest zones full airflow and trim the rest, keeping the airflow floor."""
    if not s.guest_mode or s.sleep_mode or not s.unit.power or not s.zones:
        return
    guests = [z for z in s.zones if z.guest]
    if not guests:
        yield Recommendation(
            "guest_no_zones",
            "guest",
            Severity.INFO,
            "Choose guest zones",
            "Guest mode is on but no zone is marked as a guest zone. Mark the living "
            "and dining zones in their zone settings.",
        )
        return
    changes: dict[int, ZoneCommand] = {}
    for zone in guests:
        target = zone.max_opening or 100
        if not zone.on or zone.opening < target:
            changes[zone.zone] = ZoneCommand(on=True, opening=target)
    projected = [
        replace(z, on=True, opening=changes[z.zone].opening or z.opening)
        if z.zone in changes
        else z
        for z in s.zones
    ]
    for zone in sorted(
        (z for z in s.zones if not z.guest and z.on and z.opening > 0 and z.occupied is not True),
        key=lambda z: z.priority is not Priority.UTILITY,
    ):
        trimmed = max(zone.min_opening, s.caps.zone_opening_step if zone.spill else 0)
        if zone.opening <= trimmed:
            continue
        trial = [
            replace(z, on=trimmed > 0, opening=trimmed) if z.zone == zone.zone else z
            for z in projected
        ]
        if airflow_ok(s, trial):
            changes[zone.zone] = ZoneCommand(on=trimmed > 0, opening=trimmed or None)
            projected = trial
    if changes:
        names = ", ".join(z.name for z in guests)
        yield Recommendation(
            "guest_focus",
            "guest",
            Severity.SUGGEST,
            "Focus airflow on guests",
            f"Open {names} fully and turn the other zones down while guests are here.",
            Command(zones=changes),
        )


# Away -----------------------------------------------------------------------------


def rule_away(s: HouseSnapshot) -> Iterable[Recommendation]:
    """Set back or switch off while everyone is out."""
    if s.anyone_home is not False or not s.unit.power:
        return
    minutes = s.away_minutes or 0
    settings = s.settings
    view = outlook(s)
    extreme = view.heatwave or view.cold_snap
    if (minutes < settings.short_away_minutes or extreme) and s.unit.mode in (
        HvacMode.HEAT,
        HvacMode.COOL,
    ):
        target = settings.heat_away if s.unit.mode is HvacMode.HEAT else settings.cool_away
        if s.unit.target_temp is not None and s.unit.target_temp == _target(s, target):
            return
        if extreme and minutes >= settings.short_away_minutes:
            what = "A heatwave" if view.heatwave else "A cold snap"
            why = (
                f"{what} is coming: holding {fmt(target)} keeps the house safe and quick to "
                "recover, where off would not."
            )
        else:
            why = (
                f"For a short outing a setback to {fmt(target)} saves energy and recovers quickly."
            )
        yield Recommendation(
            "away_setback",
            "away",
            Severity.SUGGEST,
            "Everyone is out: set back",
            why,
            Command(target_temp=_target(s, target)),
        )
    else:
        yield Recommendation(
            "away_off",
            "away",
            Severity.SUGGEST,
            "Everyone is out: turn off",
            f"The house has been empty for {int(minutes)} minutes.",
            Command(power=False),
        )


# Weather and solar ----------------------------------------------------------------


def _forecast_window(s: HouseSnapshot, hours: int) -> list[float]:
    end = s.now + timedelta(hours=hours)
    return [
        p.temperature for p in s.forecast if p.temperature is not None and s.now <= p.time <= end
    ]


def rule_weather(s: HouseSnapshot) -> Iterable[Recommendation]:
    """Pre-condition ahead of a hot day or a cold night."""
    if s.anyone_home is False:
        return
    view = outlook(s)
    if view.heatwave and view.hottest is not None:
        band = house_band(s, Period.DAY)
        when = f" at {view.hottest_at.strftime('%H:%M')}" if view.hottest_at else ""
        yield Recommendation(
            "weather_heatwave",
            "weather",
            Severity.INFO,
            f"Heatwave: {fmt(view.hottest)}{when}",
            "Close blinds on sunny windows and keep outside doors shut. Pre-cool in the "
            f"morning while it is mild, then keep cooling at {fmt(band.high - 1)} or above: a "
            "lower setpoint does not cool faster and overshoots once the heat breaks.",
        )
    temps = _forecast_window(s, 12)
    if not temps:
        return
    day = house_band(s, Period.DAY)
    night = house_band(s, Period.NIGHT)
    indoor = indoor_temperature(s)
    solar = (
        " Spare solar makes it nearly free." if (s.solar_export_w or 0) >= SOLAR_SURPLUS_W else ""
    )
    hot = max(temps)
    if (
        hot >= day.high + HOT_DAY_MARGIN
        and s.now.hour < 12
        and not (s.unit.power and s.unit.mode is HvacMode.COOL)
        and indoor is not None
        and indoor <= day.high + 1
        and _supports(s, HvacMode.COOL)
    ):
        target = _target(s, day.high - 1)
        outside = f" while it is {fmt(s.outdoor_temp)} outside" if s.outdoor_temp else ""
        yield Recommendation(
            "weather_precool",
            "weather",
            Severity.SUGGEST,
            f"Hot day ahead: {fmt(hot)}",
            f"Cooling the house now{outside} costs less than fighting the afternoon "
            f"heat. Pre-cool to {fmt(target)}.{solar}",
            Command(power=True, mode=HvacMode.COOL, target_temp=target),
        )
    cold = min(temps)
    if (
        cold <= night.low - COLD_NIGHT_MARGIN
        and 15 <= s.now.hour < s.settings.night_start.hour
        and not (s.unit.power and s.unit.mode is HvacMode.HEAT)
        and current_period(s) is Period.DAY
        and _supports(s, HvacMode.HEAT)
    ):
        target = _target(s, night.low + 1)
        yield Recommendation(
            "weather_preheat",
            "weather",
            Severity.SUGGEST,
            f"Cold night ahead: {fmt(cold)}",
            f"Warm the bedrooms to {fmt(target)} before bedtime so the unit is not "
            f"working hardest overnight.{solar}",
            Command(power=True, mode=HvacMode.HEAT, target_temp=target),
        )


def rule_solar(s: HouseSnapshot) -> Iterable[Recommendation]:
    """Bank comfort while spare solar is being exported."""
    if (s.solar_export_w or 0) < SOLAR_SURPLUS_W or s.unit.power or s.anyone_home is False:
        return
    band = house_band(s)
    indoor = indoor_temperature(s)
    outdoor = s.outdoor_temp
    if indoor is None or outdoor is None:
        return
    if outdoor > band.high and indoor > band.middle and _supports(s, HvacMode.COOL):
        target = _target(s, band.middle)
        yield Recommendation(
            "solar_precool",
            "solar",
            Severity.INFO,
            "Spare solar: pre-cool now",
            f"You are exporting {s.solar_export_w / 1000:.1f} kW. Cooling to {fmt(target)} now "
            "banks comfort for later.",
            Command(power=True, mode=HvacMode.COOL, target_temp=target),
        )
    elif outdoor < band.low and indoor < band.middle and _supports(s, HvacMode.HEAT):
        target = _target(s, band.middle)
        yield Recommendation(
            "solar_preheat",
            "solar",
            Severity.INFO,
            "Spare solar: pre-heat now",
            f"You are exporting {s.solar_export_w / 1000:.1f} kW. Heating to {fmt(target)} now "
            "banks warmth for the evening.",
            Command(power=True, mode=HvacMode.HEAT, target_temp=target),
        )


RULES: tuple[Callable[[HouseSnapshot], Iterable[Recommendation]], ...] = (
    rule_unit_health,
    rule_health,
    rule_setpoint,
    rule_zoning,
    rule_guest,
    rule_away,
    rule_weather,
    rule_solar,
)


def evaluate(snapshot: HouseSnapshot, model: ThermalModel | None = None) -> list[Recommendation]:
    """Run every rule and return advice, most urgent first, without duplicates.

    ``model`` is what has been learned about the rooms, for advice about coasting.
    """
    seen: dict[str, Recommendation] = {}
    for rec in rule_mode(snapshot, model):
        seen.setdefault(rec.id, rec)
    for rule in RULES:
        for rec in rule(snapshot):
            seen.setdefault(rec.id, rec)
    if not snapshot.unit.online:
        seen = {k: v for k, v in seen.items() if k == "unit_offline"}
    return sorted(seen.values(), key=lambda r: SEVERITY_ORDER[r.severity])
