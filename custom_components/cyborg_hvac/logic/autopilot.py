"""One decision for the whole house: mode, setpoint, fan and every damper.

The Climate Butler (room balancing) and Bedtime hand the unit to this engine.
Presence, weather, the energy mode and health feed it: they change what each
room should reach, not what the unit does. The engine then picks the gentlest
lever that gets there, and watches that it works:

- a room past its aim that outside will soon bring back is left to coast,
  its damper closed, rather than switching heating to cooling; the room's own
  trend is checked, and once it is not getting there in time the unit acts;
- rooms that differ from each other get different damper openings;
- the whole house off its aim moves the setpoint;
- mode and power change only when nothing gentler works;
- once every room is comfortable, the unit's own thermostat does the holding:
  in heating or cooling it rests its compressor at the setpoint, keeps its fan
  going and picks up again by itself. Fan-only only recirculates the house's
  air through the ducts with nothing behind it, so it is used in mild weather
  alone; in cold or hot weather a comfortable house stays in its mode while
  power allows, and the unit switches off otherwise.

Changes are few and decisive: a damper jumps straight to where it should be
and stays there for a while, and small corrections are ignored. Everything
goes out as one command.

The energy mode (Eco, Mid, Max) sets how close to perfect the rooms are kept
and how long outside gets to do the work; Bedtime holds the sleep zones within
half a degree whatever the mode.

Without the Butler or Bedtime the other switches keep their lighter touch
(pre-conditioning blocks, presence), and health still acts.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
import math
from typing import Any

from ..core.model import Command, Feature, HvacMode, ZoneCommand
from .advisor import fmt
from .balancing import BIAS_LIMIT, BalanceMemory, smart_setpoint
from .comfort import (
    SLEEP_TOLERANCE,
    Band,
    current_period,
    deciding_zones,
    house_band,
    in_night,
    zone_band,
    zone_weight,
)
from .energy import BEDTIME, PROFILES, EnergyMode, Profile
from .fan import choose_fan, vent_fan
from .health import ZoneHealth, assess, humidity_of
from .outlook import Outlook, Season, outdoor_series, outlook as read_outlook
from .planner import PlanBlock, precondition_command
from .presence import PresenceMemory, presence_command
from .snapshot import HouseSnapshot, Period, ZoneInputs
from .thermal import ThermalModel
from .trend import TrendTracker

# Needs, in degrees short of a room's aim in the running mode. When a room starts needing the
# unit at all is the energy mode's (Profile.act).
NEED_NOW = 1.0  # a resting unit starts straight away this far short, otherwise after START_DELAY
FULL_NEED = 1.0  # a room this far short gets its full opening
SHED_ENTER = 1.5  # degrees past its aim before a room is closed
SHED_ENTER_SLEEP = 0.5  # bedtime holds the bedroom closer
SHED_EXIT = 0.3  # a closed room rejoins once this close to where the house is
SHED_CLEAR = 0.4  # ...and always this far inside the point where it would close again
SECOND_EXIT = 1.0  # a spare room given a trickle keeps it until this far past its aim
# While no room needs the unit, a room this far past its aim gets the fan's air in full to
# even it out with the rest, and keeps it until back within MIX_CLEAR of that.
MIX_AHEAD = 1.0
MIX_CLEAR = 0.4
# The unit counts as idle (compressor off, fan still running) once its own reading is this far
# past its setpoint, and working again once it is back this far on the other side.
MIX_ENTER = 0.5
MIX_EXIT = -0.25
MIX_DELAY = timedelta(minutes=10)  # ...for this long, so a setpoint just written does not count
REST_MARGIN = 1.0  # every room this far past its aim counts as well ahead...
IDLE_EXIT = 0.3  # ...and an idling unit picks up again once a room is back this close
SEASON_GATE = 1.5  # extra degrees to switch away from the last mode while the season is unknown

# Coasting: what outside does to a room left alone, from the thermal model, checked against
# what the room really does.
COAST_MIN = 15  # minutes a coasting room is given at least...
COAST_SLACK = 1.5  # ...or this many times what the model predicts, up to the profile's horizon
COAST_CHECK = timedelta(minutes=15)  # after this long the room's own trend must agree
COAST_CHECK_SLEEP = timedelta(minutes=10)
COAST_GRACE = timedelta(minutes=10)  # a trend arriving this late after the deadline still counts
COAST_MOVING = 0.1  # degrees an hour towards its aim before a room counts as moving at all
OFF_AFTER = 45  # a rest predicted to last this long switches off rather than idles
LONG_COAST = 120  # rooms comfortable for this long rest even if not well ahead
# Bedtime acts as soon as the bedroom's trend says it leaves its half degree this soon.
SLEEP_LOOKAHEAD = timedelta(minutes=15)

# Ventilation: every room comfortable, power to spare, so the fan keeps air moving.
VENT_EDGE = 0.3  # a room this close to its range's edge, where mixed air pushes it, is closed...
VENT_REOPEN = 0.6  # ...until it is this far inside again
VENT_RESUME = 3.0  # battery % above the floor before ventilation starts again
VENT_SETTLE = timedelta(minutes=10)  # the least time after a mode change before fan-only
# Fan-only only while outside stays within this of the house's range for the next hour: past
# it the ducts lose (or gain) heat and nothing is behind the air.
VENT_OUTSIDE = 2.0
VENT_AHEAD = 60
# In cold or hot weather a comfortable house stays in its mode while the battery is at least
# this full (always on Max), and the unit switches off otherwise.
HOLD_MIN_BATTERY = 50.0

# Pacing: few, decisive changes.
START_DELAY = timedelta(minutes=5)
DRY_DWELL = timedelta(minutes=30)
HUMID_DELAY = timedelta(minutes=15)
ZONE_DWELL = timedelta(minutes=15)
ZONE_FLAP = timedelta(minutes=5)
MIN_MOVE = 20  # damper changes smaller than this wait until the zone opens or closes
LEVEL_STEP = 10  # damper targets are rounded to this
SETPOINT_DELTA = 1.0
SETPOINT_DELTA_SLEEP = 0.5
SETPOINT_DWELL = timedelta(minutes=15)
FAN_DWELL = timedelta(minutes=30)
IDLE_OFFSET = 1.0  # an idle unit is set this far past the rooms' aim

# Holding openings, learned per room.
HOLD_DEFAULT = 50
HOLD_MIN = 20
TRICKLE_MAX = 30  # most a spare room gets just to stay tolerable
HOLD_STEP = 10
HOLD_LEARN_AFTER = timedelta(minutes=30)
# A holding room this far behind (or ahead of) the other rooms for HOLD_LEARN_AFTER gets
# more (or less) air next time. The setpoint looks after the house as a whole.
HOLD_SPREAD = 0.3
HOLD_MAX_STEPS = 3

DRY_STOP_MARGIN = 5.0  # % humidity below the limit before drying stops
MAX_HEAT_SETPOINT = 25.5
MIN_COOL_SETPOINT = 21.5
HEATWAVE_SPAN = 2.0  # cooling is never set more than this below the band in a heatwave...
HEATWAVE_SPAN_MAX = 3.0  # ...or this on free power, when comfort comes first
PEAK_RELAX = 0.5  # the cooling aim eases up to the band edge in a heatwave's hottest hours

HORIZON = max(LONG_COAST, BEDTIME.horizon, *(p.horizon for p in PROFILES.values()))


@dataclass(frozen=True, slots=True)
class Switches:
    """Which autopilot behaviours are on, and what comfort is worth right now."""

    presence: bool = False
    balancing: bool = False
    weather: bool = False
    sleep: bool = False
    energy: EnergyMode = EnergyMode.MID

    @property
    def any(self) -> bool:
        """Return True when any behaviour is on."""
        return self.presence or self.balancing or self.weather or self.sleep

    @property
    def core(self) -> bool:
        """Return True when the engine runs the unit (Climate Butler or Bedtime)."""
        return self.balancing or self.sleep


class Role(StrEnum):
    """What autopilot is doing with one zone."""

    SERVE = "serve"  # needs what the unit is doing: more air
    HOLD = "hold"  # at its aim: the air that keeps it there
    SHED = "shed"  # past its aim: closed until it is back level with the house
    SETBACK = "setback"  # empty, or second while guests or sleepers come first
    MIX = "mix"  # ahead while no room needs the unit: the fan's air evens it out
    VENT = "vent"  # every room comfortable: open to the fan's air
    KEEPER = "keeper"  # kept open so the fan has enough duct
    SHUT = "shut"  # a window or door is open
    HELD = "held"  # a person's change is being respected
    FREE = "free"  # not autopilot's to move


class Action(StrEnum):
    """What happens to the unit."""

    START = "start"
    CHANGE = "change"
    KEEP = "keep"
    IDLE = "idle"
    VENT = "vent"
    REST = "rest"
    WAIT = "wait"
    LEAVE = "leave"


class Activity(StrEnum):
    """What the house is doing, in one word for the card and history."""

    HEAT = "heat"
    COOL = "cool"
    DRY = "dry"
    VENT = "vent"  # the fan keeps air moving
    IDLE = "idle"  # running, compressor resting: rooms are past their aim
    WATCH = "watch"  # waiting on a room: coasting, or a need that may pass
    REST = "rest"
    AWAY = "away"
    HOLD = "hold"  # leaving the unit as it is


@dataclass(slots=True)
class Coast:
    """A room left for outside to bring back, and when the unit takes over."""

    started: datetime
    temperature: float
    mode: HvacMode  # what the room needs: it coasts the other way
    deadline: datetime
    # Why it stopped coasting: "trend" (not moving), "deadline" (too slow) or "outside".
    failed: str | None = None
    rate: float | None = None  # its trend when it failed, °C an hour


@dataclass(slots=True)
class ZoneMemory:
    """Hysteresis and pacing for one zone."""

    role: Role | None = None
    since: datetime | None = None
    # The role the zone's damper was last set for.
    applied: Role | None = None
    moved: datetime | None = None
    # Aim when the zone started holding, so learning ignores band changes.
    aim: float | None = None
    # When its holding opening was last nudged, and how far behind the other rooms
    # it has been on average since (degrees, positive when behind).
    nudged: datetime | None = None
    behind: float = 0.0
    coast: Coast | None = None


@dataclass(slots=True)
class AutopilotMemory:
    """Everything autopilot remembers about one unit between decisions."""

    zones: dict[int, ZoneMemory] = field(default_factory=dict)
    timers: dict[str, datetime] = field(default_factory=dict)
    # Any power or mode change, and a switch between heating and cooling.
    mode_at: datetime | None = None
    mode_changed_at: datetime | None = None
    setpoint_at: datetime | None = None
    fan_at: datetime | None = None
    # The last heating, cooling or drying mode used (fan-only is not counted).
    last_mode: HvacMode | None = None
    balance: BalanceMemory = field(default_factory=BalanceMemory)
    drying: bool = False
    idling: bool = False
    # True while the unit is satisfied and its fan's air is being sent to rooms that are ahead.
    mixing: bool = False
    # True while there is power enough to keep the fan moving air (battery above its floor).
    vent_ok: bool = False
    health_started: bool = False
    # True until the first decision after a behaviour is switched on: act at once.
    fresh: bool = True
    presence: PresenceMemory = field(default_factory=PresenceMemory)

    def zone(self, number: int) -> ZoneMemory:
        """Return (creating if needed) a zone's memory."""
        return self.zones.setdefault(number, ZoneMemory())


@dataclass(frozen=True, slots=True)
class Watch:
    """A room autopilot is keeping an eye on, and what it expects."""

    zone: int
    name: str
    temperature: float
    aim: float
    mode: HvacMode  # what the room needs to get there: heat or cool
    rate: float | None  # °C an hour, rising positive
    eta: float | None  # minutes to the aim at that rate
    deadline: datetime | None  # when the unit takes over if it is not there
    coasting: bool  # left for outside to bring back

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly form for the card."""
        eta = self.eta if self.eta is not None and math.isfinite(self.eta) else None
        return {
            "zone": self.zone,
            "name": self.name,
            "temperature": self.temperature,
            "aim": self.aim,
            "mode": self.mode.value,
            "rate": round(self.rate, 2) if self.rate is not None else None,
            "eta": round(eta) if eta is not None else None,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "coasting": self.coasting,
        }


@dataclass(slots=True)
class Decision:
    """What autopilot does now, and why."""

    command: Command = field(default_factory=Command)
    # What the house is doing: heat, cool, dry or fan_only, or None while resting.
    mode: HvacMode | None = None
    reason: str = ""
    roles: dict[int, Role] = field(default_factory=dict)
    outlook: Outlook = field(default_factory=Outlook)
    health: dict[int, ZoneHealth] = field(default_factory=dict)
    activity: Activity | None = None
    # A few words for the card's headline and history ("Cooling Bedroom").
    headline: str = ""
    watch: list[Watch] = field(default_factory=list)
    # The temperature each room is aimed at now.
    aims: dict[int, float] = field(default_factory=dict)
    energy: EnergyMode | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly form for the card."""
        return {
            "mode": self.mode.value if self.mode else None,
            "reason": self.reason,
            "roles": {str(n): role.value for n, role in self.roles.items()},
            "activity": self.activity.value if self.activity else None,
            "headline": self.headline,
            "watch": [w.as_dict() for w in self.watch],
            "aims": {str(n): aim for n, aim in self.aims.items()},
            "energy": self.energy.value if self.energy else None,
        }


@dataclass(frozen=True, slots=True)
class _Choice:
    mode: HvacMode | None
    action: Action
    reason: str
    headline: str = ""


@dataclass(slots=True)
class _Context:
    s: HouseSnapshot
    switches: Switches
    view: Outlook
    health: dict[int, ZoneHealth]
    model: ThermalModel
    memory: AutopilotMemory
    paused: set[int]
    minutes: float
    block: PlanBlock | None
    outdoors: list[float]
    away_off: bool
    # What rooms aim for (the energy mode), and how the unit is paced (Bedtime or the mode).
    energy: Profile
    profile: Profile
    trend: TrendTracker | None
    night: bool


def decide(
    s: HouseSnapshot,
    switches: Switches,
    blocks: Iterable[PlanBlock],
    model: ThermalModel,
    memory: AutopilotMemory,
    paused: Collection[int] = (),
    minutes: float = 1.0,
    trend: TrendTracker | None = None,
) -> Decision:
    """Return what autopilot does now, and why.

    ``trend`` holds the rooms' recent readings; without it, coasting rooms are
    judged on the thermal model and their deadline alone.
    """
    view = read_outlook(s)
    health = assess(s)
    if not switches.any:
        return Decision(outlook=view, health=health)
    if switches.core:
        settings = s.settings
        energy = PROFILES[switches.energy]
        focus = s.sleep_mode and any(z.sleep and z.has_sensor for z in s.zones)
        ctx = _Context(
            s=s,
            switches=switches,
            view=view,
            health=health,
            model=model,
            memory=memory,
            paused=set(paused),
            minutes=minutes,
            block=_active_block(blocks, s.now) if switches.weather else None,
            outdoors=outdoor_series(s, s.now + timedelta(minutes=HORIZON)),
            away_off=switches.presence
            and s.anyone_home is False
            and (s.away_minutes or 0) >= settings.short_away_minutes
            and not (view.heatwave or view.cold_snap)
            and not energy.everywhere,
            energy=energy,
            profile=BEDTIME if focus else energy,
            trend=trend,
            night=in_night(s),
        )
        decision = _conduct(ctx)
    else:
        decision = _light(s, switches, list(blocks), view, health, memory)
    decision.energy = switches.energy
    memory.fresh = False
    return decision


def note_sent(
    memory: AutopilotMemory, command: Command, now: datetime, mode: HvacMode | None = None
) -> None:
    """Remember what autopilot actually sent, for pacing.

    ``mode`` is the unit's mode before the command, for a start that kept it.
    """
    for number in command.zones:
        zone = memory.zone(number)
        zone.moved = now
        zone.applied = zone.role
    if command.target_temp is not None:
        memory.setpoint_at = now
    if command.fan_speed is not None:
        memory.fan_at = now
    if command.mode is not None or command.power is not None:
        memory.mode_at = now
        new = command.mode if command.mode is not None else (mode if command.power else None)
        compressor = (HvacMode.HEAT, HvacMode.COOL)
        if new in compressor and memory.last_mode in compressor and new is not memory.last_mode:
            memory.mode_changed_at = now
        if new in (HvacMode.HEAT, HvacMode.COOL, HvacMode.DRY):
            memory.last_mode = new


# The engine -------------------------------------------------------------------------


def _conduct(ctx: _Context) -> Decision:
    s = ctx.s
    deciding = _deciding(ctx)
    choice = _choose(ctx, deciding)
    judge = _judge(ctx, choice)
    served_mean = _served_mean(ctx, deciding)
    unit = s.unit
    venting = choice.action is Action.VENT
    roles = _roles(
        ctx,
        judge,
        {z.zone for z in deciding},
        served_mean,
        _fan_only_now(ctx, choice, judge),
        venting,
    )
    if choice.mode is HvacMode.DRY:
        for zone in s.sensed_zones:
            if roles[zone.zone] in (Role.HOLD, Role.SHED, Role.SETBACK) and _humid(ctx, zone):
                roles[zone.zone] = Role.SERVE
    runs = choice.action in (Action.START, Action.CHANGE, Action.VENT) or (
        choice.action in (Action.KEEP, Action.IDLE, Action.LEAVE)
        and unit.power
        and unit.mode in (HvacMode.HEAT, HvacMode.COOL, HvacMode.DRY)
    )
    targets, ideal = _targets(ctx, roles, judge, deciding)
    kept = _keep_airflow(ctx, targets, roles, judge) if runs else set()
    zones = _zone_changes(ctx, roles, targets, ideal, kept) if runs else {}
    command = _unit_command(ctx, choice, roles, deciding, targets, zones)
    shown = {n: Role.KEEPER if n in kept else role for n, role in roles.items()}
    reason = choice.reason
    mixing = [z for z in s.zones if roles[z.zone] is Role.MIX]
    if mixing:
        reason += f" Sending more air to {_rooms(mixing)} to even them out with the rest."
    if choice.action is Action.REST:
        doing = None
    elif choice.action in (Action.WAIT, Action.LEAVE):
        doing = unit.mode if unit.power else None
    else:
        doing = choice.mode
    watch = _watch(ctx, deciding, judge)
    activity, headline = _activity(ctx, choice, watch)
    return Decision(
        command=command,
        mode=doing,
        reason=reason,
        roles=shown,
        outlook=ctx.view,
        health=ctx.health,
        activity=activity,
        headline=headline,
        watch=watch,
        aims={z.zone: round(_aim(ctx, z, judge), 1) for z in s.sensed_zones},
    )


def _fan_only_now(ctx: _Context, choice: _Choice, judge: HvacMode) -> bool:
    """Return True while the unit is satisfied but its fan still moves air.

    The unit's own thermostat decides when its compressor runs: once its reading
    is past its setpoint it rests and the fan keeps circulating the house's air.
    Only then can that air even the rooms out without carrying heat (or cooling)
    into rooms that are already ahead.
    """
    unit, memory = ctx.s.unit, ctx.memory
    running = (
        unit.power
        and unit.mode is judge
        and choice.mode is judge
        and choice.action in (Action.KEEP, Action.IDLE)
        # In a heatwave's hottest hours every bit of cooling goes where it is needed.
        and not ctx.view.peak
    )
    if not running or unit.current_temp is None or unit.target_temp is None:
        memory.mixing = False
        memory.timers.pop("mix", None)
        return False
    past = unit.current_temp - unit.target_temp
    if judge is HvacMode.COOL:
        past = -past
    if choice.action is Action.IDLE:
        past = max(past, MIX_ENTER)  # set to idle on purpose
    if memory.mixing:
        memory.mixing = past >= MIX_EXIT
    else:
        memory.mixing = _sustained(memory, "mix", past >= MIX_ENTER, ctx.s.now, MIX_DELAY)
    return memory.mixing


def _active_block(blocks: Iterable[PlanBlock], now: datetime) -> PlanBlock | None:
    return next((b for b in blocks if b.active(now)), None)


# What each room should reach ---------------------------------------------------------


def _opposite(mode: HvacMode) -> HvacMode:
    return HvacMode.COOL if mode is HvacMode.HEAT else HvacMode.HEAT


def _sleep_focus(ctx: _Context, zone: ZoneInputs) -> bool:
    """Return True for a sleep zone while Bedtime holds it within half a degree."""
    return ctx.s.sleep_mode and zone.sleep


def _house_band(ctx: _Context) -> Band:
    """Return the house band rooms are held to: in Max a lived-in one, even while away."""
    s = ctx.s
    period = current_period(s)
    if ctx.energy.everywhere and period is Period.AWAY:
        period = Period.NIGHT if ctx.night else Period.DAY
    return house_band(s, period)


def _band(ctx: _Context, zone: ZoneInputs) -> Band:
    """Return the zone's comfort band, held within healthy limits.

    In Max every room gets the band of a lived-in room, even empty ones and
    while everyone is out.
    """
    s = ctx.s
    if ctx.energy.everywhere and not _sleep_focus(ctx, zone):
        base = _house_band(ctx)
        band = Band(round(base.low + zone.offset, 1), round(base.high + zone.offset, 1))
    else:
        band = zone_band(s, zone)
    health = ctx.health.get(zone.zone)
    if health is None:
        return band
    low, high = band.low, band.high
    if low < health.floor < high - 1:
        low = health.floor
    if low + 1 < health.ceiling < high:
        high = health.ceiling
    return Band(low, high)


def _aim(ctx: _Context, zone: ZoneInputs, mode: HvacMode) -> float:
    """Return the temperature a zone should settle at in this mode."""
    s = ctx.s
    band = _band(ctx, zone)
    heat = mode is HvacMode.HEAT
    energy = ctx.energy
    if _sleep_focus(ctx, zone):
        return band.low + SLEEP_TOLERANCE if heat else band.high - SLEEP_TOLERANCE
    if energy.centre:
        # Within half a degree of the middle: close to perfect, without heating and cooling
        # taking turns over a tenth of a degree.
        value = (
            max(band.low + energy.inset, band.middle - 0.5)
            if heat
            else min(band.high - energy.inset, band.middle + 0.5)
        )
    else:
        value = band.low + energy.inset if heat else band.high - energy.inset
    if not energy.everywhere and (s.secondary(zone) or zone.occupied is False):
        return value
    block = ctx.block
    if block is not None and block.mode is mode:
        value = max(value, block.target) if heat else min(value, block.target)
    elif ctx.view.peak and not heat and not energy.everywhere:
        value = min(band.high, value + PEAK_RELAX)
    return value


def _act(ctx: _Context, zone: ZoneInputs) -> float:
    """Return degrees past its aim before a room needs the unit."""
    return BEDTIME.act if _sleep_focus(ctx, zone) else ctx.energy.act


def _act_against(ctx: _Context, zone: ZoneInputs) -> float:
    """Return degrees past its aim before a room needs the opposite of what is running.

    A bedroom at Bedtime is let use its whole half degree first: easing off is
    gentler than switching.
    """
    return SLEEP_TOLERANCE if _sleep_focus(ctx, zone) else _act(ctx, zone)


def _need(ctx: _Context, zone: ZoneInputs, mode: HvacMode) -> float:
    """Return degrees a zone is short of its aim in ``mode`` (negative: past it)."""
    assert zone.temperature is not None
    target = _aim(ctx, zone, mode)
    return target - zone.temperature if mode is HvacMode.HEAT else zone.temperature - target


def _short(ctx: _Context, zone: ZoneInputs, mode: HvacMode) -> float:
    """Return how short a room counts as in ``mode`` when deciding to act.

    A bedroom at Bedtime counts as needing the unit as soon as its trend says
    it will leave its half degree within SLEEP_LOOKAHEAD, so it never gets there.
    """
    need = _need(ctx, zone, mode)
    if not _sleep_focus(ctx, zone) or ctx.trend is None:
        return need
    unit = ctx.s.unit
    if unit.power and unit.mode is _opposite(mode):
        return need  # the unit is causing that trend, and easing off undoes it
    rate = ctx.trend.rate(zone.zone)
    if rate is None:
        return need
    growing = rate if mode is HvacMode.COOL else -rate  # °C an hour away from the aim
    ahead = need + growing * SLEEP_LOOKAHEAD.total_seconds() / 3600
    if growing > 0 and ahead >= SLEEP_TOLERANCE:
        return max(need, SLEEP_TOLERANCE)
    return need


def _wants(ctx: _Context, zone: ZoneInputs, mode: HvacMode) -> bool:
    """Return True while a room needs ``mode``: once short enough, until it reaches its aim."""
    return _short(ctx, zone, mode) >= _act(ctx, zone) or (
        ctx.memory.zone(zone.zone).role is Role.SERVE and _need(ctx, zone, mode) > 0
    )


def _critical(ctx: _Context, zone: ZoneInputs) -> bool:
    health = ctx.health.get(zone.zone)
    return health is not None and health.critical and not zone.window_open


def _humid(ctx: _Context, zone: ZoneInputs) -> bool:
    humidity = humidity_of(ctx.s, zone)
    return humidity is not None and humidity > ctx.s.settings.humidity_max


def _deciding(ctx: _Context) -> list[ZoneInputs]:
    """Return the rooms that decide what the unit does, health cases included.

    In Max every room decides, unless Bedtime puts the bedroom first.
    """
    s = ctx.s
    if ctx.away_off:
        base: list[ZoneInputs] = []
    elif ctx.energy.everywhere and not s.sleep_mode:
        base = list(s.sensed_zones)
    else:
        base = deciding_zones(s)
    ids = {z.zone for z in base}
    base += [z for z in s.sensed_zones if z.zone not in ids and _critical(ctx, z)]
    return [z for z in base if not z.window_open and z.zone not in ctx.paused]


# Coasting ----------------------------------------------------------------------------


def _drift(ctx: _Context, zone: ZoneInputs, target: float) -> float | None:
    """Return minutes for a room left alone to reach ``target``.

    Infinity when it will not get there, None without an outdoor temperature.
    """
    if not ctx.outdoors:
        return None
    minutes = ctx.model.minutes_to_drift(
        zone.zone,
        zone.temperature,  # type: ignore[arg-type]
        target,
        ctx.outdoors,
        ctx.night,
    )
    return math.inf if minutes is None else float(minutes)


def _coast_target(ctx: _Context, zone: ZoneInputs, mode: HvacMode) -> float:
    """Return where a coasting room must get to.

    Its aim; a bedroom at Bedtime only back to where it stops needing the unit,
    which is already well inside its half degree.
    """
    aim = _aim(ctx, zone, mode)
    if _sleep_focus(ctx, zone):
        act = _act(ctx, zone)
        return aim - act if mode is HvacMode.HEAT else aim + act
    return aim


def _coastable(ctx: _Context, zone: ZoneInputs, mode: HvacMode) -> bool:
    """Return True while outside is bringing a room that needs ``mode`` back on its own.

    The model has to say it gets there within the horizon. The room then gets a
    fixed deadline, and once it has been watched for a while its own trend has
    to agree: a room that is not moving, or too slowly, is handed to the unit.
    That stays so until the room no longer needs ``mode``.
    """
    memory = ctx.memory.zone(zone.zone)
    now, temp = ctx.s.now, zone.temperature
    assert temp is not None
    coast = memory.coast
    if coast is not None and coast.mode is mode and coast.failed:
        return False
    sleep = _sleep_focus(ctx, zone)
    horizon = BEDTIME.horizon if sleep else ctx.energy.horizon
    target = _coast_target(ctx, zone, mode)
    if (temp >= target) if mode is HvacMode.HEAT else (temp <= target):
        # Already there, so only a trend heading out (a bedroom about to leave its half
        # degree) can make it need the unit: outside is not bringing that back.
        rate = ctx.trend.rate(zone.zone) if ctx.trend is not None else None
        leaving = rate is not None and (-rate if mode is HvacMode.HEAT else rate) > COAST_MOVING
        return not leaving
    minutes = _drift(ctx, zone, target)
    if minutes is None or minutes > horizon:
        if coast is not None and coast.mode is mode:
            coast.failed = "outside"
        return False
    if coast is None or coast.mode is not mode:
        span = min(float(horizon), max(COAST_MIN, minutes * COAST_SLACK))
        memory.coast = Coast(now, temp, mode, now + timedelta(minutes=span))
        return True
    if now >= coast.deadline:
        coast.failed = "deadline"
        return False
    check = COAST_CHECK_SLEEP if sleep else COAST_CHECK
    rate = ctx.trend.rate(zone.zone) if ctx.trend is not None else None
    if rate is not None and now - coast.started >= check:
        toward = rate if mode is HvacMode.HEAT else -rate
        left = abs(temp - target)
        if toward < COAST_MOVING or now + timedelta(hours=left / toward) > (
            coast.deadline + COAST_GRACE
        ):
            coast.failed, coast.rate = "trend", rate
            return False
    return True


def _coast_failed(ctx: _Context, zone: ZoneInputs, mode: HvacMode) -> bool:
    coast = ctx.memory.zone(zone.zone).coast
    return coast is not None and coast.mode is mode and coast.failed is not None


def _coast_time(ctx: _Context, zones: list[ZoneInputs], mode: HvacMode) -> float | None:
    """Return minutes until any room needs ``mode`` again, or None when unknown."""
    times = []
    for zone in zones:
        act = _act(ctx, zone)
        if _short(ctx, zone, mode) >= act:
            return 0.0
        target = _aim(ctx, zone, mode)
        edge = target - act if mode is HvacMode.HEAT else target + act
        minutes = _drift(ctx, zone, edge)
        if minutes is None:
            return None
        times.append(minutes)
    return min(times) if times else None


def _forget_coasts(ctx: _Context, keep: Iterable[ZoneInputs]) -> None:
    """Forget coasting for rooms that no longer need the other mode."""
    ids = {z.zone for z in keep}
    for number, memory in ctx.memory.zones.items():
        if number not in ids:
            memory.coast = None


# Mode --------------------------------------------------------------------------------


def _sustained(
    memory: AutopilotMemory, key: str, active: bool, now: datetime, delay: timedelta
) -> bool:
    """Return True once ``active`` has held for ``delay``."""
    if not active:
        memory.timers.pop(key, None)
        return False
    since = memory.timers.setdefault(key, now)
    return now - since >= delay


def _dwelt(memory: AutopilotMemory, now: datetime, dwell: timedelta) -> bool:
    return memory.mode_at is None or now - memory.mode_at >= dwell


def _flip_allowed(memory: AutopilotMemory, now: datetime, dwell: timedelta) -> bool:
    """Return True once the last switch between heating and cooling is ``dwell`` ago."""
    return memory.mode_changed_at is None or now - memory.mode_changed_at >= dwell


def _gate(ctx: _Context, mode: HvacMode, last: HvacMode | None) -> float:
    """Return extra degrees ``mode`` needs because the season is against it.

    The energy mode sets how much (none at Bedtime). Without any outdoor reading
    the season is unknown, so only a switch away from the mode last used
    (``last``) has to clear the extra margin.
    """
    gate = ctx.profile.gate
    if not gate:
        return 0.0
    view = ctx.view
    if not view.known:
        return SEASON_GATE if last is not None and mode is not last else 0.0
    against = (mode is HvacMode.COOL and view.season is Season.HEATING) or (
        mode is HvacMode.HEAT and view.season is Season.COOLING
    )
    return gate if against else 0.0


def _season_mode(ctx: _Context, zones: list[ZoneInputs]) -> HvacMode:
    if ctx.view.season is Season.HEATING:
        return HvacMode.HEAT
    if ctx.view.season is Season.COOLING:
        return HvacMode.COOL
    heat = sum(max(0.0, _need(ctx, z, HvacMode.HEAT)) for z in zones)
    cool = sum(max(0.0, _need(ctx, z, HvacMode.COOL)) for z in zones)
    if heat != cool:
        return HvacMode.HEAT if heat > cool else HvacMode.COOL
    outdoor = ctx.s.outdoor_temp
    warm = outdoor is not None and outdoor > _house_band(ctx).middle
    return HvacMode.COOL if warm else HvacMode.HEAT


def _choose(ctx: _Context, deciding: list[ZoneInputs]) -> _Choice:
    s = ctx.s
    unit = s.unit
    running = unit.mode if unit.power else None
    if ctx.away_off and not deciding:
        minutes = int(s.away_minutes or 0)
        return _Choice(
            None,
            Action.REST,
            f"Everyone has been out for {minutes} minutes: resting.",
            "Away: resting",
        )
    if not deciding:
        return _Choice(running, Action.LEAVE, _unsensed_reason(ctx), "Leaving the unit as it is")
    drying = _drying(ctx, deciding, running)
    if drying is not None:
        return drying
    if running is HvacMode.AUTO:
        pick = _season_mode(ctx, deciding)
        choice = _while_running(ctx, deciding, pick)
        if choice.action in (Action.KEEP, Action.IDLE):
            return _Choice(
                pick,
                Action.CHANGE,
                f"{_verb(pick)} instead of auto. {choice.reason}".strip(),
                f"{_verb(pick)} instead of auto",
            )
        return choice
    if running in (HvacMode.HEAT, HvacMode.COOL):
        return _while_running(ctx, deciding, running)
    # Off, fan-only or drying that is no longer needed: start what is needed, or rest.
    return _from_rest(ctx, deciding, running)


def _while_running(ctx: _Context, zones: list[ZoneInputs], mode: HvacMode) -> _Choice:
    s, memory, profile = ctx.s, ctx.memory, ctx.profile
    other = _opposite(mode)
    want = [z for z in zones if _wants(ctx, z, mode)]
    wrong = [z for z in zones if _short(ctx, z, other) >= _act_against(ctx, z)]
    _forget_coasts(ctx, wrong)
    stuck = [z for z in wrong if not _coastable(ctx, z, other)]
    gate = _gate(ctx, other, mode)
    strong = [z for z in stuck if _short(ctx, z, other) >= _act_against(ctx, z) + gate]
    switch = not want and bool(strong) and other in s.caps.hvac_modes
    settled = _sustained(memory, "changeover", switch, s.now, profile.changeover)
    # A room that was watched and did not come back on its own has already waited.
    proven = switch and any(_coast_failed(ctx, z, other) for z in strong)
    if (settled or proven) and _flip_allowed(memory, s.now, profile.dwell):
        return _Choice(
            other,
            Action.CHANGE,
            f"Switching to {_word(other)}: {_rooms(strong)} "
            f"{'has' if len(strong) == 1 else 'have'} stayed "
            f"{'warm' if other is HvacMode.COOL else 'cold'}.{_because(ctx, strong, other)}",
            f"Switching to {_word(other)} for {_names(strong)}",
        )
    note = _coast_note(ctx, [z for z in wrong if z not in stuck], other)
    if want:
        memory.idling = False
        reason = f"{_verb(mode)} {_rooms(want)}.{note}"
        return _Choice(mode, Action.KEEP, reason, f"{_verb(mode)} {_names(want)}")
    coast = _coast_time(ctx, zones, mode)
    margin = IDLE_EXIT if memory.idling else REST_MARGIN
    deep = all(_need(ctx, z, mode) <= -margin for z in zones)
    memory.idling = deep
    if coast is not None and (coast >= LONG_COAST or (deep and coast >= OFF_AFTER)):
        weather = _weather_mode(ctx)
        rest = _Choice(None, Action.REST, f"Resting: {_stay_reason(coast)}{note}", "Resting")
        if weather is None:
            if not _vent_ok(ctx):
                return rest
            if _dwelt(memory, s.now, VENT_SETTLE):
                return _vent(f"{_stay_reason(coast).capitalize()}{note}")
        elif weather is not mode or not _hold_ok(ctx):
            return rest
        # Otherwise it stays in its mode: at its setpoint the unit rests its compressor, keeps
        # its fan going and picks up again by itself.
    if deep:
        return _Choice(
            mode, Action.IDLE, f"Rooms are past their aim: idling.{note}", "Idling: rooms are ahead"
        )
    return _Choice(mode, Action.KEEP, f"Holding rooms at their aim.{note}", "Holding rooms steady")


def _from_rest(ctx: _Context, zones: list[ZoneInputs], running: HvacMode | None) -> _Choice:
    s, memory, profile = ctx.s, ctx.memory, ctx.profile
    options: list[tuple[HvacMode, list[ZoneInputs]]] = []
    coasting: list[tuple[ZoneInputs, HvacMode]] = []
    needing_any: list[ZoneInputs] = []
    for mode in (HvacMode.HEAT, HvacMode.COOL):
        if mode not in s.caps.hvac_modes:
            continue
        gate = _gate(ctx, mode, memory.last_mode)
        needing = [z for z in zones if _short(ctx, z, mode) >= _act(ctx, z) + gate]
        needing_any += needing
        stuck = [z for z in needing if not _coastable(ctx, z, mode)]
        coasting += [(z, mode) for z in needing if z not in stuck]
        if stuck:
            options.append((mode, stuck))
    # Rooms still needing a mode keep their coasting record, failed or not: a failed coast
    # must not start afresh next minute.
    _forget_coasts(ctx, needing_any)
    if not options:
        memory.timers.pop("start_heat", None)
        memory.timers.pop("start_cool", None)
        note = "".join(_coast_note(ctx, [z], mode) for z, mode in coasting)
        dry = running is HvacMode.DRY and memory.last_mode is HvacMode.DRY
        reason = ("Dry enough." if dry else "Rooms are comfortable.") + note
        weather = _weather_mode(ctx)
        if weather is None:
            if _vent_ok(ctx):
                return _vent(reason)
        elif running is not None and weather in s.caps.hvac_modes and _hold_ok(ctx):
            word = "cold" if weather is HvacMode.HEAT else "warm"
            return _Choice(
                weather,
                Action.CHANGE,
                f"{reason} Too {word} outside for the fan alone: "
                f"{_word(weather)} holds the rooms instead.",
                f"{_verb(weather)} instead of the fan",
            )
        return _Choice(None, Action.REST, reason, "Resting")
    if len(options) == 2:
        preferred = _season_mode(ctx, zones)
        mode, stuck = next(o for o in options if o[0] is preferred)
    else:
        mode, stuck = options[0]
    memory.timers.pop(f"start_{_opposite(mode).value}", None)
    biggest = max(_short(ctx, z, mode) for z in stuck)
    ready = _sustained(memory, f"start_{mode.value}", True, s.now, START_DELAY)
    switching = memory.last_mode not in (None, mode) and not _flip_allowed(
        memory, s.now, profile.dwell
    )
    block = ctx.block is not None and ctx.block.mode is mode
    # A room that was watched and did not get there, or a bedroom, is not kept waiting.
    proven = any(_coast_failed(ctx, z, mode) or _sleep_focus(ctx, z) for z in stuck)
    if not switching and (memory.fresh or biggest >= NEED_NOW or ready or block or proven):
        return _Choice(
            mode,
            Action.START,
            f"{_verb(mode)} {_rooms(stuck)}.{_because(ctx, stuck, mode)}",
            f"{_verb(mode)} {_names(stuck)}",
        )
    word = "cool" if mode is HvacMode.HEAT else "warm"
    verb = "is" if len(stuck) == 1 else "are"
    if switching and memory.mode_changed_at is not None:
        at = memory.mode_changed_at + profile.dwell
        later = f"{_word(mode)} starts at {at:%H:%M} (the unit changed mode recently)"
    else:
        later = f"{_word(mode)} starts if it stays that way"
    return _Choice(
        running,
        Action.WAIT,
        f"{_rooms(stuck)} {verb} a little {word}: {later}.",
        f"Watching {_names(stuck)}",
    )


def _vent(reason: str) -> _Choice:
    return _Choice(
        HvacMode.FAN_ONLY,
        Action.VENT,
        f"{reason} Keeping air moving through the house.".strip(),
        "Keeping air moving",
    )


def _vent_ok(ctx: _Context) -> bool:
    """Return True while the fan may keep air moving through a comfortable house.

    With a house battery, while it is above its floor (with a little margin
    before starting again); without one, only while power is free (Max).
    """
    s, memory = ctx.s, ctx.memory
    if HvacMode.FAN_ONLY not in s.caps.hvac_modes or memory.drying:
        return False
    if ctx.away_off or (s.anyone_home is False and not ctx.energy.everywhere):
        return False
    soc = s.battery_soc
    if soc is None:
        memory.vent_ok = ctx.energy.mode is EnergyMode.MAX
    else:
        floor = s.settings.vent_min_battery
        memory.vent_ok = soc > (floor if memory.vent_ok else floor + VENT_RESUME)
    return memory.vent_ok


def _weather_mode(ctx: _Context) -> HvacMode | None:
    """Return the mode outside pushes the house towards over the next hour, None while mild.

    None as well without any outdoor reading or forecast: nothing to go by.
    """
    band = _house_band(ctx)
    ahead = [t for t in (ctx.s.outdoor_temp, *ctx.outdoors[:VENT_AHEAD]) if t is not None]
    if any(t < band.low - VENT_OUTSIDE for t in ahead):
        return HvacMode.HEAT
    if any(t > band.high + VENT_OUTSIDE for t in ahead):
        return HvacMode.COOL
    return None


def _hold_ok(ctx: _Context) -> bool:
    """Return True while a comfortable house in cold or hot weather stays in its mode.

    Always on Max; otherwise while the house battery is at least half full, and
    never while everyone is out.
    """
    s = ctx.s
    if ctx.away_off or (s.anyone_home is False and not ctx.energy.everywhere):
        return False
    if ctx.energy.everywhere:
        return True
    return s.battery_soc is not None and s.battery_soc >= HOLD_MIN_BATTERY


def _drying(ctx: _Context, zones: list[ZoneInputs], running: HvacMode | None) -> _Choice | None:
    """Return a drying choice while rooms are humid and drying will not chill them."""
    s, memory = ctx.s, ctx.memory
    limit = s.settings.humidity_max
    readings = [(z, h) for z in zones if (h := humidity_of(s, z)) is not None]
    if not readings:
        memory.drying = False
        return None
    wettest, top = max(readings, key=lambda r: r[1])
    mould = any((h := ctx.health.get(z.zone)) is not None and h.mould for z in zones)
    if memory.drying:
        memory.drying = top > limit - DRY_STOP_MARGIN
    else:
        memory.drying = _sustained(memory, "humid", top > limit, s.now, HUMID_DELAY) or (
            mould and top > limit
        )
    modes = s.caps.hvac_modes
    if (
        not memory.drying
        or ctx.view.season is Season.HEATING
        or HvacMode.DRY not in modes
        or any(_short(ctx, z, HvacMode.HEAT) >= _act(ctx, z) for z in zones)
        # Rooms that need cooling are cooled with a slow fan, which dries them too.
        or any(_short(ctx, z, HvacMode.COOL) >= _act(ctx, z) for z in zones)
    ):
        return None
    reason = f"Drying: {wettest.name} is at {top:.0f}% humidity."
    if running is HvacMode.DRY:
        return _Choice(HvacMode.DRY, Action.KEEP, reason, "Drying the air")
    if running in (HvacMode.HEAT, HvacMode.COOL) and not _dwelt(memory, s.now, DRY_DWELL):
        return None
    return _Choice(
        HvacMode.DRY,
        Action.START if not s.unit.power else Action.CHANGE,
        reason,
        "Drying the air",
    )


def _judge(ctx: _Context, choice: _Choice) -> HvacMode:
    """Return the mode rooms are judged in (the one running, or the one to come)."""
    if choice.mode in (HvacMode.HEAT, HvacMode.COOL):
        return choice.mode  # type: ignore[return-value]
    if choice.mode is HvacMode.DRY:
        return HvacMode.COOL
    if ctx.memory.last_mode in (HvacMode.HEAT, HvacMode.COOL):
        return ctx.memory.last_mode  # type: ignore[return-value]
    unit = ctx.s.unit
    if unit.mode in (HvacMode.HEAT, HvacMode.COOL):
        return unit.mode  # type: ignore[return-value]
    return _season_mode(ctx, list(ctx.s.sensed_zones))


# Rooms -------------------------------------------------------------------------------


def _served_mean(ctx: _Context, deciding: list[ZoneInputs]) -> float | None:
    """Return where the rooms being conditioned are, weighted as for the house."""
    rows = [
        (z, zone_weight(ctx.s, z))
        for z in deciding
        if ctx.memory.zone(z.zone).role in (None, Role.SERVE, Role.HOLD)
    ]
    total = sum(w for _, w in rows)
    if not total:
        return None
    return sum(z.temperature * w for z, w in rows) / total  # type: ignore[operator]


def _roles(
    ctx: _Context,
    judge: HvacMode,
    deciding: set[int],
    served_mean: float | None,
    idling: bool = False,
    venting: bool = False,
) -> dict[int, Role]:
    s, memory = ctx.s, ctx.memory
    needing = {z.zone for z in s.sensed_zones if z.zone in deciding and _wants(ctx, z, judge)}
    mixing = idling and not needing
    primary = [z for z in s.sensed_zones if z.zone in deciding and not _second(ctx, z, True)]
    mean_need = (
        sum(_need(ctx, z, judge) for z in primary) / len(primary) if len(primary) > 1 else None
    )
    sensed = s.sensed_zones
    mixed = sum(z.temperature for z in sensed) / len(sensed) if sensed else None  # type: ignore[misc]
    roles: dict[int, Role] = {}
    for zone in s.zones:
        zm = memory.zone(zone.zone)
        if zone.zone in ctx.paused:
            role = Role.HELD
        elif zone.window_open:
            role = Role.SHUT
        elif venting:
            role = _vent_role(ctx, zone, zm, mixed) if zone.has_sensor else Role.VENT
        elif not zone.has_sensor:
            role = _unsensed_role(ctx, zone)
        else:
            others = bool(needing - {zone.zone})
            role = _sensed_role(
                ctx, zone, judge, zm, zone.zone in deciding, served_mean, others, mixing
            )
            _learn_hold(ctx, zone, zm, role, judge, mean_need)
        if role is not zm.role:
            zm.role, zm.since = role, s.now
            zm.aim = _aim(ctx, zone, judge) if role is Role.HOLD else None
        roles[zone.zone] = role
    return roles


def _vent_role(ctx: _Context, zone: ZoneInputs, zm: ZoneMemory, mixed: float | None) -> Role:
    """Return VENT, or SHED for a room the house's mixed air would push out of its range."""
    temp = zone.temperature
    if mixed is None or temp is None:
        return Role.VENT
    band = _band(ctx, zone)
    if mixed < temp:
        room = temp - band.low
    elif mixed > temp:
        room = band.high - temp
    else:
        return Role.VENT
    limit = VENT_REOPEN if zm.role is Role.SHED else VENT_EDGE
    return Role.SHED if room <= limit else Role.VENT


def _vacant(ctx: _Context, zone: ZoneInputs) -> bool:
    return (
        ctx.switches.presence
        and not ctx.energy.everywhere
        and zone.occupied is False
        and (zone.vacant_minutes or 0) >= ctx.s.settings.vacant_close_minutes
    )


def _unsensed_role(ctx: _Context, zone: ZoneInputs) -> Role:
    s = ctx.s
    if s.sleep_mode:
        return Role.SERVE if zone.sleep else Role.SETBACK
    if _vacant(ctx, zone):
        return Role.SETBACK
    if ctx.switches.presence and zone.occupied is True and not zone.on:
        return Role.SERVE
    return Role.FREE


def _second(ctx: _Context, zone: ZoneInputs, deciding: bool) -> bool:
    """Return True for a room that only has to stay tolerable (empty, or not first).

    In Max only Bedtime puts a room second: every other room counts in full.
    """
    s = ctx.s
    if ctx.energy.everywhere:
        return (s.sleep_mode and not zone.sleep) or not deciding
    return _vacant(ctx, zone) or s.secondary(zone) or not deciding


def _sensed_role(
    ctx: _Context,
    zone: ZoneInputs,
    judge: HvacMode,
    zm: ZoneMemory,
    deciding: bool,
    served_mean: float | None,
    others: bool,
    mixing: bool = False,
) -> Role:
    """Return a room's role. ``others`` is True while other rooms still need the unit.

    A room is closed for being ahead only while others need what the unit is
    doing: when it is the only one, the setpoint is the gentler lever. While no
    room needs the unit (``mixing``), a room well ahead gets the fan's air in
    full instead: air from the rest of the house evens it out for free.
    """
    s = ctx.s
    need = _need(ctx, zone, judge)
    act = _act(ctx, zone)
    if _second(ctx, zone, deciding) and not _critical(ctx, zone):
        if zm.role in (Role.SERVE, Role.HOLD):
            return Role.SERVE if need > -SECOND_EXIT else Role.SETBACK
        return Role.SERVE if need >= act else Role.SETBACK
    if mixing and (need <= -MIX_AHEAD or (zm.role is Role.MIX and need <= -MIX_AHEAD + MIX_CLEAR)):
        return Role.MIX
    coast = zm.coast
    if coast is not None and coast.mode is not judge and not coast.failed:
        # Left for outside to bring back while the unit serves others, or with a change of
        # mode riding on it: none of the unit's air meanwhile, so its trend shows what the
        # room really does.
        decisive = _short(ctx, zone, coast.mode) >= _act_against(ctx, zone) + _gate(
            ctx, coast.mode, judge
        )
        if others or decisive:
            return Role.SHED
    enter = SHED_ENTER_SLEEP if s.sleep_mode and zone.sleep else SHED_ENTER
    if zm.role is Role.SHED:
        if not _back_with_house(ctx, zone, judge, deciding, served_mean, enter):
            return Role.SHED
    elif need <= -enter and others:
        return Role.SHED
    if _short(ctx, zone, judge) >= act or (zm.role is Role.SERVE and need > 0):
        return Role.SERVE
    return Role.HOLD


def _back_with_house(
    ctx: _Context,
    zone: ZoneInputs,
    mode: HvacMode,
    deciding: bool,
    served_mean: float | None,
    enter: float,
) -> bool:
    """Return True once a closed room is level with the rooms being conditioned.

    While the house is still on its way to the aim, a room that was ahead
    rejoins once the others reach it, not only once it has dropped back to the
    aim, so the house arrives together.
    """
    target = _aim(ctx, zone, mode)
    level = target
    if deciding and served_mean is not None:
        level = max(target, served_mean) if mode is HvacMode.HEAT else min(target, served_mean)
    temp = zone.temperature
    assert temp is not None
    if mode is HvacMode.HEAT:
        return temp <= min(level + SHED_EXIT, target + enter - SHED_CLEAR)
    return temp >= max(level - SHED_EXIT, target - enter + SHED_CLEAR)


def _learn_hold(
    ctx: _Context,
    zone: ZoneInputs,
    zm: ZoneMemory,
    role: Role,
    judge: HvacMode,
    mean_need: float | None,
) -> None:
    """Nudge a room's holding opening after it drifted off while holding.

    It overshot, or sat ahead of the other rooms: less air next time. It fell
    short, or sat behind them: more. Learning only counts a hold that lasted,
    at an unchanged aim, with the unit in the same mode.
    """
    s = ctx.s
    if zm.role is not Role.HOLD or role not in (Role.SHED, Role.SERVE, Role.HOLD):
        zm.behind = 0.0
        return
    if mean_need is not None:
        # Average over the learning window, so passing swings do not count.
        weight = min(1.0, ctx.minutes / (HOLD_LEARN_AFTER.total_seconds() / 60))
        zm.behind += (_need(ctx, zone, judge) - mean_need - zm.behind) * weight
    last = zm.nudged if zm.nudged and zm.since and zm.nudged > zm.since else zm.since
    if s.sleep_mode or last is None or s.now - last < HOLD_LEARN_AFTER:
        return
    if zm.aim is None or abs(zm.aim - _aim(ctx, zone, judge)) > 0.1:
        return
    if not s.unit.power or s.unit.mode is not judge or not zone.on:
        return
    model = ctx.model.zone(zone.zone)
    current = model.hold(judge) or HOLD_DEFAULT
    if role is Role.HOLD:
        if mean_need is None or abs(zm.behind) < HOLD_SPREAD:
            return
        # Further out of step means a bigger nudge: one decisive change, not several.
        steps = min(HOLD_MAX_STEPS, int(abs(zm.behind) / HOLD_SPREAD))
        change = HOLD_STEP * steps * (1 if zm.behind > 0 else -1)
    else:
        change = -HOLD_STEP if role is Role.SHED else HOLD_STEP
    high = zone.max_opening or 100
    low = max(zone.min_opening, HOLD_MIN)
    model.set_hold(judge, max(low, min(high, current + change)))
    zm.nudged, zm.behind = s.now, 0.0


# Dampers -----------------------------------------------------------------------------


def _level(ctx: _Context, zone: ZoneInputs, value: float) -> int:
    caps = ctx.s.caps
    step = max(1, caps.zone_opening_step)
    rounded = caps.round_opening(round(value / LEVEL_STEP) * LEVEL_STEP)
    return max(max(zone.min_opening, step), min(zone.max_opening or 100, rounded))


def _closed(ctx: _Context, zone: ZoneInputs) -> tuple[bool, int]:
    if zone.min_opening <= 0:
        return False, zone.opening
    return True, _level(ctx, zone, zone.min_opening)


def _hold(ctx: _Context, zone: ZoneInputs, mode: HvacMode, sole: bool) -> int:
    """Return the opening that keeps a room at its aim, as learned for this room.

    The only room being conditioned gets its full opening: the setpoint then
    does the fine work.
    """
    high = zone.max_opening or 100
    if sole:
        return high
    learned = ctx.model.zone(zone.zone).hold(mode)
    value = learned if learned is not None else HOLD_DEFAULT
    return max(max(zone.min_opening, HOLD_MIN), min(high, value))


def _primary_served(ctx: _Context, roles: dict[int, Role], deciding: list[ZoneInputs]) -> list[int]:
    """Return the rooms conditioned for comfort (not just kept tolerable or healthy)."""
    ids = {z.zone for z in deciding}
    return [
        z.zone
        for z in deciding
        if roles.get(z.zone) in (Role.SERVE, Role.HOLD) and not _second(ctx, z, z.zone in ids)
    ]


def _targets(
    ctx: _Context, roles: dict[int, Role], judge: HvacMode, deciding: list[ZoneInputs]
) -> tuple[dict[int, tuple[bool, int]], dict[int, float]]:
    """Return the (on, opening) each zone's role asks for, and the unrounded openings.

    Moves within a role are judged on the unrounded opening, so rounding to
    LEVEL_STEP never makes a damper move by itself.
    """
    s = ctx.s
    deciding_ids = {z.zone for z in deciding}
    served = _primary_served(ctx, roles, deciding)
    # The only room being conditioned opens fully, unless others are being mixed: then it keeps
    # its holding opening so the fan's air goes mostly to them.
    sole = not any(role is Role.MIX for role in roles.values())
    targets: dict[int, tuple[bool, int]] = {}
    ideal: dict[int, float] = {}
    for zone in s.zones:
        role = roles[zone.zone]
        high = zone.max_opening or 100
        if role in (Role.HELD, Role.FREE):
            targets[zone.zone] = (zone.on, zone.opening)
        elif role is Role.SHUT:
            targets[zone.zone] = (False, zone.opening)
        elif role in (Role.SHED, Role.SETBACK):
            targets[zone.zone] = _closed(ctx, zone)
        elif role in (Role.MIX, Role.VENT):
            targets[zone.zone] = (True, _level(ctx, zone, high))
        elif not zone.has_sensor:
            reopen = not s.sleep_mode and zone.opening > 0
            targets[zone.zone] = (True, zone.opening if reopen else high)
        else:
            hold = _hold(ctx, zone, judge, sole and served == [zone.zone])
            value: float = hold
            need = max(0.0, _need(ctx, zone, judge))
            if role is Role.SERVE and _second(ctx, zone, zone.zone in deciding_ids):
                # A spare room only needs a trickle to stay tolerable; more would
                # also skew the return air the unit regulates on.
                floor = max(zone.min_opening, HOLD_MIN)
                cap = max(floor, min(hold, TRICKLE_MAX))
                value = floor + (cap - floor) * min(1.0, need / FULL_NEED)
            elif role is Role.SERVE:
                value = hold + (high - hold) * min(1.0, need / FULL_NEED)
            targets[zone.zone] = (True, _level(ctx, zone, value))
            ideal[zone.zone] = value
    return targets, ideal


_KEEPER_ORDER = {Role.SETBACK: 0, Role.FREE: 1, Role.SHED: 2}


def _keep_airflow(
    ctx: _Context, targets: dict[int, tuple[bool, int]], roles: dict[int, Role], judge: HvacMode
) -> set[int]:
    """Open zones until the fan has enough duct. Return the zones kept open for it.

    Spill zones go first, then zones already open (no write needed), then the
    empty or second rooms closest to needing what the unit is doing, and rooms
    that are past their aim only as a last resort.
    """
    s = ctx.s
    settings = s.settings
    step = max(1, s.caps.zone_opening_step)

    def shortfall() -> tuple[int, int]:
        open_ = [o for on, o in targets.values() if on and o > 0]
        return (
            max(0, settings.min_open_zones - len(open_)),
            max(0, settings.min_total_opening - sum(open_)),
        )

    def need(zone: ZoneInputs) -> float:
        return _need(ctx, zone, judge) if zone.has_sensor else 0.0

    movable = [z for z in s.zones if roles[z.zone] not in (Role.HELD, Role.SHUT)]
    kept: set[int] = set()
    closed = [z for z in movable if not (targets[z.zone][0] and targets[z.zone][1] > 0)]
    closed.sort(
        key=lambda z: (
            not z.spill,
            _KEEPER_ORDER.get(roles[z.zone], 0),
            not (z.on and z.opening > 0),
            -need(z),
        )
    )
    for zone in closed:
        zones_short, total_short = shortfall()
        if not zones_short:
            break
        share = (
            math.ceil(total_short / zones_short / LEVEL_STEP) * LEVEL_STEP
            if total_short
            else LEVEL_STEP
        )
        high = zone.max_opening or 100
        if zone.on and zone.opening >= share and roles[zone.zone] is not Role.SHED:
            opening = zone.opening  # already open enough: leave it as it is
        else:
            opening = max(zone.min_opening, step, min(high, share))
        targets[zone.zone] = (True, opening)
        kept.add(zone.zone)

    # Then enough opening in total: spill zones first, then zones already open.
    def planned(zone: ZoneInputs) -> int:
        on, opening = targets[zone.zone]
        return opening if on and opening > 0 else 0

    topping = [z for z in movable if planned(z) or roles[z.zone] is not Role.FREE]
    topping.sort(
        key=lambda z: (not z.spill, not (z.on and z.opening > 0), not planned(z), -need(z))
    )
    for zone in topping:
        _, total_short = shortfall()
        if not total_short:
            break
        base = planned(zone)
        high = zone.max_opening or 100
        if base >= high:
            continue
        wanted = base + total_short
        if zone.on and zone.opening >= wanted and roles[zone.zone] is not Role.SHED:
            raised = zone.opening  # already open enough: no write
        else:
            raised = max(zone.min_opening, step, math.ceil(wanted / LEVEL_STEP) * LEVEL_STEP)
        targets[zone.zone] = (True, min(high, raised))
        kept.add(zone.zone)
    return kept


_CLOSED_ROLES = (Role.SHED, Role.SETBACK, Role.SHUT)
_AIRING_ROLES = (Role.MIX, Role.VENT)


def _flipped(before: Role | None, after: Role | None) -> bool:
    """Return True when a role change opens or closes a zone, or starts or ends airing.

    Airing (mixing or ventilating) ends the moment the unit works again, so those
    rooms must not wait out the dwell with the fan's air (now warm or cool) still
    going to them.
    """
    return (
        before is None
        or (before in _CLOSED_ROLES) != (after in _CLOSED_ROLES)
        or (before in _AIRING_ROLES) != (after in _AIRING_ROLES)
    )


def _matches(zone: ZoneInputs, on: bool, opening: int) -> bool:
    if not on:
        return not (zone.on and zone.opening > 0)
    return zone.on and zone.opening == opening


def _zone_changes(
    ctx: _Context,
    roles: dict[int, Role],
    targets: dict[int, tuple[bool, int]],
    ideal: dict[int, float],
    kept: set[int],
) -> dict[int, ZoneCommand]:
    """Return the damper moves worth making now, openings before closings.

    A zone moves straight to its target when it is opened or closed, then
    stays put for a while; otherwise only moves of MIN_MOVE or more are made.
    Zones opened for airflow always move. Openings go first so the guard sees
    the extra duct before anything closes.
    """
    s, memory = ctx.s, ctx.memory
    grows: dict[int, ZoneCommand] = {}
    shrinks: dict[int, ZoneCommand] = {}
    for zone in s.zones:
        number = zone.zone
        role = roles[number]
        if role in (Role.HELD, Role.SHUT) or (role is Role.FREE and number not in kept):
            continue
        on, opening = targets[number]
        zm = memory.zone(number)
        if _matches(zone, on, opening):
            zm.applied = zm.role
            continue
        gap = s.now - zm.moved if zm.moved else None
        if number in kept:
            pass
        elif _flipped(zm.applied, zm.role) or _critical(ctx, zone):
            if gap is not None and gap < ZONE_FLAP:
                continue
        elif (gap is not None and gap < ZONE_DWELL) or (
            zone.on and on and abs(ideal.get(number, opening) - zone.opening) < MIN_MOVE
        ):
            continue
        if not on:
            shrinks[number] = ZoneCommand(on=False)
            continue
        change = ZoneCommand(
            on=True if not zone.on else None,
            opening=opening if opening != zone.opening else None,
        )
        if not zone.on or opening > zone.opening:
            grows[number] = change
        else:
            shrinks[number] = change
    return {**grows, **shrinks}


# The unit ----------------------------------------------------------------------------


def _unit_command(
    ctx: _Context,
    choice: _Choice,
    roles: dict[int, Role],
    deciding: list[ZoneInputs],
    targets: dict[int, tuple[bool, int]],
    zones: dict[int, ZoneCommand],
) -> Command:
    s, memory = ctx.s, ctx.memory
    unit = s.unit
    action = choice.action
    if action is Action.REST:
        return Command(power=False) if unit.power else Command()
    if action is Action.WAIT:
        return Command()
    if action is Action.LEAVE:
        return Command(target_temp=_unsensed_setpoint(ctx), zones=zones)
    if action is Action.VENT:
        start = not unit.power or unit.mode is not HvacMode.FAN_ONLY
        return Command(
            power=True if not unit.power else None,
            mode=HvacMode.FAN_ONLY if start else None,
            fan_speed=_vent_speed(ctx, start),
            zones=zones,
        )
    force = action in (Action.START, Action.CHANGE)
    if force:
        memory.balance = BalanceMemory()
    return Command(
        power=True if not unit.power else None,
        mode=choice.mode if choice.mode is not unit.mode or not unit.power else None,
        target_temp=_setpoint(ctx, choice, roles, deciding, force),
        fan_speed=_fan(ctx, choice, roles, deciding, targets, force),
        zones=zones,
    )


def _setpoint(
    ctx: _Context,
    choice: _Choice,
    roles: dict[int, Role],
    deciding: list[ZoneInputs],
    force: bool,
) -> float | None:
    mode = choice.mode
    if mode not in (HvacMode.HEAT, HvacMode.COOL):
        return None
    s, memory = ctx.s, ctx.memory
    unit = s.unit
    heat = mode is HvacMode.HEAT
    # The setpoint follows the rooms kept comfortable; spare rooms get a trickle of whatever
    # that delivers, unless they are all there is.
    primary = set(_primary_served(ctx, roles, deciding))
    served = [z for z in deciding if z.zone in primary] or [
        z for z in deciding if roles.get(z.zone) in (Role.SERVE, Role.HOLD)
    ]
    aims = {z.zone: _aim(ctx, z, mode) for z in (served or deciding)}
    idle = choice.action is Action.IDLE or not served
    if idle:
        # Set past the rooms' aim, corrected for return air, so the unit idles.
        edge = min(aims.values()) if heat else max(aims.values())
        bias = _return_bias(ctx, deciding)
        value = (
            (min(edge, edge + bias) - IDLE_OFFSET)
            if heat
            else (max(edge, edge + bias) + IDLE_OFFSET)
        )
        # Keep learning while idle how far past their aim the rooms went, so the next
        # hold starts from a setpoint that does not overshoot again.
        rooms = [z for z in deciding if z.has_sensor]
        if rooms:
            smart_setpoint(
                s,
                rooms,
                mode,
                memory.balance,
                ctx.minutes,
                {z.zone: _aim(ctx, z, mode) for z in rooms},
                rounded=False,
            )
    else:
        value = smart_setpoint(  # type: ignore[assignment]
            s, served, mode, memory.balance, ctx.minutes, aims, rounded=False
        )
        if value is None:
            value = max(aims.values()) if heat else min(aims.values())
    raw = _limit(ctx, mode, value, aims, _return_bias(ctx, served or deciding))
    value = s.caps.round_temp(raw)
    current = unit.target_temp
    if current is None or force:
        return value
    if idle:
        # Within a degree of the idle setpoint counts as idling, so the value moving with
        # return air does not rewrite it.
        idling = current <= value + IDLE_OFFSET if heat else current >= value - IDLE_OFFSET
        return None if idling else value
    delta = SETPOINT_DELTA_SLEEP if s.sleep_mode else SETPOINT_DELTA
    # Easing off once the rooms go past their aim cannot wait: that is how overshoot happens.
    arrived = all(_need(ctx, z, mode) <= -SHED_EXIT for z in served)
    easing = (value < current) if heat else (value > current)
    dwelt = (
        memory.setpoint_at is None
        or s.now - memory.setpoint_at >= SETPOINT_DWELL
        or (easing and arrived)
    )
    # Judged before rounding, so a reading on the edge of a step does not toggle it.
    return value if abs(raw - current) >= delta - 1e-9 and value != current and dwelt else None


def _return_bias(ctx: _Context, zones: list[ZoneInputs]) -> float:
    returned = ctx.s.unit.current_temp
    weights = [(z, zone_weight(ctx.s, z)) for z in zones if z.has_sensor]
    total = sum(w for _, w in weights)
    if returned is None or not total:
        return 0.0
    rooms = sum(z.temperature * w for z, w in weights) / total  # type: ignore[operator]
    return returned - rooms


def _limit(
    ctx: _Context, mode: HvacMode, value: float, aims: dict[int, float], bias: float
) -> float:
    """Keep the setpoint where it helps: never far past the rooms, gentle in a heatwave.

    The limits are for the rooms, so they move with ``bias`` (return air minus the
    rooms): cooling one warm bedroom while the rest of the house is cold, the unit
    reads cold return air and must be set lower than the bedroom's aim to cool it.
    """
    bias = max(-BIAS_LIMIT, min(BIAS_LIMIT, bias))
    if mode is HvacMode.HEAT:
        return min(value, max(MAX_HEAT_SETPOINT, max(aims.values()) + 0.5) + max(0.0, bias))
    colder = min(0.0, bias)
    value = max(value, min(MIN_COOL_SETPOINT, min(aims.values()) - 0.5) + colder)
    if ctx.view.heatwave:
        # Below this the unit only runs flat out longer and overshoots once the heat breaks.
        span = HEATWAVE_SPAN_MAX if ctx.energy.everywhere else HEATWAVE_SPAN
        floor = min(_house_band(ctx).high - span, min(aims.values()))
        value = max(value, floor + colder)
    return value


def _fan(
    ctx: _Context,
    choice: _Choice,
    roles: dict[int, Role],
    deciding: list[ZoneInputs],
    targets: dict[int, tuple[bool, int]],
    force: bool,
) -> str | None:
    s, memory = ctx.s, ctx.memory
    unit = s.unit
    mode = choice.mode
    if choice.action is Action.IDLE or unit.features.get(Feature.QUIET):
        return None
    open_ = [o for on, o in targets.values() if on and o > 0]
    primary = set(_primary_served(ctx, roles, deciding))
    shortfall = max(
        (_need(ctx, z, mode) for z in deciding if z.zone in primary),  # type: ignore[arg-type]
        default=0.0,
    )
    wanted = choose_fan(
        s,
        mode,
        current=unit.fan_speed,
        humid=memory.drying,
        shortfall=shortfall,
        peak=ctx.view.peak,
        open_total=sum(open_),
        open_count=len(open_),
        pull_down=ctx.energy.pull_down,
        sleep_boost=BEDTIME.pull_down,
    )
    if wanted is None or wanted == unit.fan_speed:
        return None
    if force or memory.fan_at is None or s.now - memory.fan_at >= FAN_DWELL:
        return wanted
    return None


def _vent_speed(ctx: _Context, force: bool) -> str | None:
    """Return the fan speed for fan-only: low at Bedtime, the energy mode's otherwise."""
    s, memory = ctx.s, ctx.memory
    unit = s.unit
    if unit.features.get(Feature.QUIET):
        return None
    wanted = "low" if ctx.profile is BEDTIME else ctx.energy.vent_fan
    speed = vent_fan(s, wanted)
    if speed is None or speed == unit.fan_speed:
        return None
    if force or memory.fan_at is None or s.now - memory.fan_at >= FAN_DWELL:
        return speed
    return None


def _unsensed_setpoint(ctx: _Context) -> float | None:
    """Without bedroom sensors, bedtime can only hold the unit at the sleep temperature."""
    s = ctx.s
    unit = s.unit
    if not s.sleep_mode or not unit.power or unit.mode not in (HvacMode.HEAT, HvacMode.COOL):
        return None
    if any(z.sleep and z.has_sensor for z in s.zones):
        return None
    value = s.caps.round_temp(s.sleep_target)
    return value if unit.target_temp != value else None


# What it is watching -------------------------------------------------------------------


def _watch(ctx: _Context, deciding: list[ZoneInputs], judge: HvacMode) -> list[Watch]:
    """Return the rooms worth keeping an eye on, with their trend and what comes next.

    Rooms left to coast (and those whose coast did not work), rooms being
    conditioned towards their aim, and at Bedtime the bedroom always.
    """
    memory, trend = ctx.memory, ctx.trend
    out = []
    for zone in deciding:
        zm = memory.zone(zone.zone)
        temp = zone.temperature
        assert temp is not None
        coast = zm.coast
        if coast is not None:
            mode, target = coast.mode, _coast_target(ctx, zone, coast.mode)
        elif zm.role is Role.SERVE or _sleep_focus(ctx, zone):
            mode, target = judge, _aim(ctx, zone, judge)
        else:
            continue
        out.append(
            Watch(
                zone=zone.zone,
                name=zone.name,
                temperature=temp,
                aim=round(target, 1),
                mode=mode,
                rate=trend.rate(zone.zone) if trend else None,
                eta=trend.eta(zone.zone, temp, target) if trend else None,
                deadline=coast.deadline if coast is not None and not coast.failed else None,
                coasting=coast is not None and not coast.failed,
            )
        )
    return out


def _activity(ctx: _Context, choice: _Choice, watch: list[Watch]) -> tuple[Activity, str]:
    action, mode = choice.action, choice.mode
    if action is Action.REST:
        if ctx.away_off:
            return Activity.AWAY, choice.headline
        coasting = [w.name for w in watch if w.coasting]
        if coasting:
            return Activity.WATCH, f"Watching {_join(coasting)}"
        return Activity.REST, choice.headline or "Resting"
    if action is Action.VENT:
        return Activity.VENT, choice.headline
    if action is Action.WAIT:
        return Activity.WATCH, choice.headline
    if action is Action.LEAVE:
        return Activity.HOLD, choice.headline
    if action is Action.IDLE:
        return Activity.IDLE, choice.headline
    if mode is HvacMode.HEAT:
        return Activity.HEAT, choice.headline
    if mode is HvacMode.COOL:
        return Activity.COOL, choice.headline
    if mode is HvacMode.DRY:
        return Activity.DRY, choice.headline
    return Activity.HOLD, choice.headline


# Words -------------------------------------------------------------------------------


def _word(mode: HvacMode) -> str:
    return {HvacMode.HEAT: "heating", HvacMode.COOL: "cooling", HvacMode.DRY: "drying"}.get(
        mode, mode.value
    )


def _verb(mode: HvacMode) -> str:
    return _word(mode).capitalize()


def _rooms(zones: list[ZoneInputs]) -> str:
    names = [f"{z.name} ({fmt(z.temperature)})" for z in zones]
    if not names:
        return "the rooms"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _join(names: list[str]) -> str:
    if len(names) > 3:
        return f"{', '.join(names[:2])} and {len(names) - 2} more"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _names(zones: list[ZoneInputs]) -> str:
    """Return room names only, for a headline: "Bedroom", "Bedroom and Office"."""
    return _join([z.name for z in zones]) if zones else "the rooms"


def _rate_words(rate: float | None) -> str:
    if rate is None:
        return ""
    if abs(rate) < 0.05:
        return "not moving"
    return f"{'rising' if rate > 0 else 'falling'} {abs(rate):.1f} °C an hour"


def _coast_note(ctx: _Context, zones: list[ZoneInputs], mode: HvacMode) -> str:
    """Explain a room left to coast.

    For example "Letting LIVING (24 °C) cool on its own (8 °C outside, falling
    0.6 °C an hour); cooling it at 23:40 if not."
    """
    if not zones:
        return ""
    outdoor = ctx.s.outdoor_temp
    notes = []
    for zone in zones:
        bits = [f"{fmt(outdoor)} outside"] if outdoor is not None else []
        rate = ctx.trend.rate(zone.zone) if ctx.trend is not None else None
        if rate is not None:
            bits.append(_rate_words(rate))
        detail = f" ({', '.join(bits)})" if bits else ""
        verb = "cool" if mode is HvacMode.COOL else "warm up"
        coast = ctx.memory.zone(zone.zone).coast
        until = (
            f"; {_word(mode)} it at {coast.deadline:%H:%M} if not"
            if coast is not None and coast.mode is mode and not coast.failed
            else ""
        )
        notes.append(f" Letting {_rooms([zone])} {verb} on its own{detail}{until}.")
    return "".join(notes)


def _because(ctx: _Context, zones: list[ZoneInputs], mode: HvacMode) -> str:
    """Return why rooms are not left to outside, as a sentence, from the first that says."""
    outdoor = ctx.s.outdoor_temp
    done = "cooled" if mode is HvacMode.COOL else "warmed up"
    for zone in zones:
        coast = ctx.memory.zone(zone.zone).coast
        if coast is not None and coast.mode is mode and coast.failed:
            minutes = int((ctx.s.now - coast.started).total_seconds() // 60)
            if coast.failed == "trend":
                rate = _rate_words(coast.rate)
                return f" {zone.name} hasn't {done} on its own ({rate} in {minutes} min)."
            if coast.failed == "deadline":
                return f" {zone.name} hasn't {done} on its own in {minutes} min."
        helps = outdoor is not None and (
            outdoor < zone.temperature  # type: ignore[operator]
            if mode is HvacMode.COOL
            else outdoor > zone.temperature  # type: ignore[operator]
        )
        if helps:
            word = "cool" if mode is HvacMode.COOL else "warm"
            return f" Outside won't {word} {zone.name} soon enough."
    return ""


def _stay_reason(coast: float) -> str:
    if math.isinf(coast):
        return "rooms are comfortable and will stay that way."
    return f"rooms will stay comfortable for about {int(coast)} minutes."


def _unsensed_reason(ctx: _Context) -> str:
    if ctx.s.sleep_mode and not any(z.sleep and z.has_sensor for z in ctx.s.zones):
        return "No bedroom sensor: holding the unit at the sleep temperature."
    return ""


# Without the Butler or Bedtime -----------------------------------------------------------


def _light(
    s: HouseSnapshot,
    switches: Switches,
    blocks: list[PlanBlock],
    view: Outlook,
    health: dict[int, ZoneHealth],
    memory: AutopilotMemory,
) -> Decision:
    """Pre-conditioning and presence with their light touch, and health."""
    command = Command()
    reason = ""
    if switches.weather:
        command = precondition_command(s, blocks)
        active = _active_block(blocks, s.now)
        if active is not None and not command.is_empty:
            reason = active.reason
    if switches.presence:
        command = command.merge(
            presence_command(s, memory.presence, extreme=view.heatwave or view.cold_snap)
        )
    care, why = _care(s, view, health, memory)
    command = command.merge(care)
    unit = s.unit
    mode = unit.mode if unit.power else None
    activity = {
        HvacMode.HEAT: Activity.HEAT,
        HvacMode.COOL: Activity.COOL,
        HvacMode.DRY: Activity.DRY,
        HvacMode.FAN_ONLY: Activity.VENT,
    }.get(mode, Activity.REST if mode is None else Activity.HOLD)  # type: ignore[arg-type]
    reason = why or reason
    return Decision(
        command=command,
        mode=mode,
        reason=reason,
        outlook=view,
        health=health,
        activity=activity,
        headline=reason.split(":")[0].split(".")[0] if reason else "",
    )


def _care(
    s: HouseSnapshot, view: Outlook, health: dict[int, ZoneHealth], memory: AutopilotMemory
) -> tuple[Command, str]:
    """Act on unhealthy rooms when no behaviour owns the unit."""
    unit = s.unit
    modes = s.caps.hvac_modes
    rooms = [z for z in s.sensed_zones if not z.window_open and z.zone in health]
    cold = [z for z in rooms if z.temperature < health[z.zone].floor]  # type: ignore[operator]
    hot = [z for z in rooms if health[z.zone].overheated]
    damp = view.season is not Season.HEATING and any(health[z.zone].mould for z in rooms)
    if memory.health_started:
        if not unit.power:
            memory.health_started = False
        else:
            settling = [
                z
                for z in rooms
                if z.temperature < health[z.zone].floor + 1  # type: ignore[operator]
                or z.temperature > health[z.zone].ceiling - 1  # type: ignore[operator]
            ]
            if not settling and not damp:
                memory.health_started = False
                return Command(power=False), "Rooms are healthy again: off."
    if cold and HvacMode.HEAT in modes:
        mode, rooms_out = HvacMode.HEAT, cold
        target = max(health[z.zone].floor for z in cold) + 1
        reason = f"{_rooms(cold)} {'is' if len(cold) == 1 else 'are'} too cold for health."
    elif hot and HvacMode.COOL in modes:
        mode, rooms_out = HvacMode.COOL, hot
        target = min(health[z.zone].ceiling for z in hot) - 1
        reason = f"{_rooms(hot)} {'is' if len(hot) == 1 else 'are'} too hot for health."
    elif damp and HvacMode.DRY in modes and not unit.power:
        memory.health_started = True
        return Command(power=True, mode=HvacMode.DRY), "Drying: damp walls risk mould."
    else:
        return Command(), ""
    opens = {
        z.zone: ZoneCommand(on=True, opening=z.max_opening or 100) for z in rooms_out if not z.on
    }
    if unit.power and unit.mode is mode:
        return Command(zones=opens), reason if opens else ""
    if unit.power:
        return Command(), ""  # someone chose another mode; do not fight it
    memory.health_started = True
    return (
        Command(power=True, mode=mode, target_temp=s.caps.round_temp(target), zones=opens),
        reason,
    )
