"""Collects house inputs, runs the advisor and publishes advice for each unit."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_ON, UnitOfPower
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ADVICE_INTERVAL_SECONDS,
    CONF_BATTERY_CHARGING_NEGATIVE,
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_SOC_SENSOR,
    CONF_COOL_AWAY,
    CONF_COOL_DAY,
    CONF_COOL_NIGHT,
    CONF_DOOR_DELAY,
    CONF_DOOR_SENSORS,
    CONF_GUEST,
    CONF_HEAT_AWAY,
    CONF_HEAT_DAY,
    CONF_HEAT_NIGHT,
    CONF_HUMIDITY_MAX,
    CONF_HUMIDITY_SENSOR,
    CONF_MAX_OPENING,
    CONF_MAX_SOLAR_KW,
    CONF_MIN_OPENING,
    CONF_MIN_TOTAL,
    CONF_MIN_ZONES,
    CONF_NIGHT_END,
    CONF_NIGHT_START,
    CONF_OFFSET,
    CONF_OUTDOOR_SENSOR,
    CONF_PEOPLE,
    CONF_PRESENCE_HOLD,
    CONF_PRESENCE_SENSORS,
    CONF_PRIORITY,
    CONF_SLEEP_ZONE,
    CONF_SOLAR_EXPORT_NEGATIVE,
    CONF_SOLAR_POWER_SENSOR,
    CONF_SOLAR_SENSOR,
    CONF_SPILL,
    CONF_TEMP_SENSOR,
    CONF_UNIT_ID,
    CONF_VENT_MIN_BATTERY,
    CONF_WEATHER,
    CONF_WINDOW_DELAY,
    CONF_WINDOW_SENSORS,
    CONF_ZONE,
    DEFAULT_DISMISS_MINUTES,
    DEFAULT_DOOR_DELAY,
    DEFAULT_MAX_SOLAR_KW,
    DEFAULT_PRESENCE_HOLD,
    DEFAULT_SLEEP_TARGET,
    DEFAULT_VENT_MIN_BATTERY,
    DEFAULT_WINDOW_DELAY,
    DOMAIN,
    FORECAST_REFRESH_MINUTES,
    JOURNAL_IN_FEED,
    WS_SCHEMA_VERSION,
)
from .core.model import Command
from .entity import zone_subentries
from .logic.advisor import Recommendation, command_as_dict, evaluate
from .logic.autopilot import AutopilotMemory, Decision, Switches, decide, note_sent
from .logic.comfort import (
    current_period,
    deciding_zones,
    house_band,
    house_demand,
    in_night,
    indoor_temperature,
    zone_band,
    zone_demand,
    zone_weight,
)
from .logic.energy import EnergyMemory, EnergyMode, EnergySetting, resolve
from .logic.guard import (
    GuardConfig,
    GuardState,
    detect_override,
    filter_command,
    manual_command,
    note_command,
    record,
)
from .logic.health import DampTracker, assess
from .logic.journal import Journal, JournalEntry, Kind
from .logic.openings import OpeningMemory, openings_command
from .logic.outlook import outlook
from .logic.planner import Plan, periods, precondition_blocks
from .logic.snapshot import (
    ComfortSettings,
    ForecastPoint,
    HouseSnapshot,
    Priority,
    ZoneInputs,
)
from .logic.thermal import ThermalModel, ThermalSample
from .logic.trend import TrendTracker
from .ws import signal_update

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import HvacCoordinator

_LOGGER = logging.getLogger(__name__)

PRESENCE_ZONING = "presence_zoning"
ROOM_BALANCING = "room_balancing"
WEATHER_PRECONDITION = "weather_precondition"
SLEEP_MODE = "sleep_mode"
BEHAVIOURS = (PRESENCE_ZONING, ROOM_BALANCING, WEATHER_PRECONDITION, SLEEP_MODE)
# Gone in 0.7: the energy mode does its job.
SOLAR_SHIFT = "solar_shift"
BEHAVIOUR_NAMES = {
    PRESENCE_ZONING: "Presence zoning",
    ROOM_BALANCING: "Climate Butler",
    WEATHER_PRECONDITION: "Weather pre-conditioning",
    SLEEP_MODE: "Bedtime",
}
HISTORY_MINUTES = 120  # each room's sparkline on the card
HISTORY_STEP = 5
SAMPLE_EVERY = timedelta(minutes=5)
SAVE_EVERY = timedelta(minutes=30)
STORE_VERSION = 1
AUTOPILOT_MIN_GAP = timedelta(seconds=55)

HOME_STATES = {"home", "on"}
UNAVAILABLE = {"unknown", "unavailable", ""}
POWER_SCALE = {UnitOfPower.KILO_WATT: 1000.0, UnitOfPower.WATT: 1.0}


def _float(state: State | None) -> float | None:
    if state is None or state.state in UNAVAILABLE:
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


def _time(value: Any, default: time) -> time:
    if isinstance(value, str):
        try:
            return time.fromisoformat(value)
        except ValueError:
            return default
    return default


def settings_from_options(options: dict[str, Any]) -> ComfortSettings:
    """Build comfort settings from the entry options."""
    base = ComfortSettings()
    return ComfortSettings(
        heat_day=float(options.get(CONF_HEAT_DAY, base.heat_day)),
        heat_night=float(options.get(CONF_HEAT_NIGHT, base.heat_night)),
        heat_away=float(options.get(CONF_HEAT_AWAY, base.heat_away)),
        cool_day=float(options.get(CONF_COOL_DAY, base.cool_day)),
        cool_night=float(options.get(CONF_COOL_NIGHT, base.cool_night)),
        cool_away=float(options.get(CONF_COOL_AWAY, base.cool_away)),
        humidity_max=float(options.get(CONF_HUMIDITY_MAX, base.humidity_max)),
        night_start=_time(options.get(CONF_NIGHT_START), base.night_start),
        night_end=_time(options.get(CONF_NIGHT_END), base.night_end),
        min_open_zones=int(options.get(CONF_MIN_ZONES, base.min_open_zones)),
        min_total_opening=int(options.get(CONF_MIN_TOTAL, base.min_total_opening)),
        max_solar_w=float(options.get(CONF_MAX_SOLAR_KW, DEFAULT_MAX_SOLAR_KW)) * 1000,
        vent_min_battery=float(options.get(CONF_VENT_MIN_BATTERY, DEFAULT_VENT_MIN_BATTERY)),
    )


class ComfortManager:
    """Keeps advice fresh for every unit of one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: HvacCoordinator):
        """Bind to the entry and its coordinator."""
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.advice: dict[str, list[Recommendation]] = {}
        self.snapshots: dict[str, HouseSnapshot] = {}
        self._dismissed: dict[tuple[str, str], datetime] = {}
        self.guest_modes: dict[str, bool] = {}
        self.sleep_targets: dict[str, float] = {}
        self._openings: dict[str, OpeningMemory] = {}
        self.autopilot: dict[str, dict[str, bool]] = {}
        self.last_action: dict[str, dict[str, Any]] = {}
        self.decisions: dict[str, Decision] = {}
        self.guard_config = GuardConfig()
        self._guards: dict[str, GuardState] = {}
        self._memory: dict[str, AutopilotMemory] = {}
        self._damp: dict[str, DampTracker] = {}
        self._previous: dict[str, Any] = {}
        self._last_run: dict[str, datetime] = {}
        self.models: dict[str, ThermalModel] = {}
        self.plans: dict[str, Plan] = {}
        self.trends: dict[str, TrendTracker] = {}
        self.journals: dict[str, Journal] = {}
        self.energy_settings: dict[str, EnergySetting] = {}
        # The energy mode in use for each unit, and why.
        self.energy: dict[str, tuple[EnergyMode, str]] = {}
        self._energy_memory: dict[str, EnergyMemory] = {}
        self._samples: dict[str, tuple[datetime, ThermalSample]] = {}
        self._saved_at: datetime | None = None
        self._store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}.thermal")
        self._journal_store: Store = Store(
            hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}.journal"
        )
        self._forecast: tuple[ForecastPoint, ...] = ()
        self._forecast_at: datetime | None = None
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._debouncer = Debouncer(
            hass, _LOGGER, cooldown=3, immediate=False, function=self._async_evaluate
        )

    # Lifecycle -------------------------------------------------------------------

    async def async_start(self) -> None:
        """Track inputs and evaluate once."""
        watched = self._watched_entities()
        if watched:
            self._unsubs.append(
                async_track_state_change_event(self.hass, watched, self._on_input_change)
            )
        stored = await self._store.async_load() or {}
        history = await self._journal_store.async_load() or {}
        for unit_id in self.coordinator.units:
            self.models[unit_id] = ThermalModel.from_dict(stored.get(unit_id))
            self.journals[unit_id] = Journal.from_list(history.get(unit_id))
        self._unsubs.append(self.coordinator.async_add_listener(self._on_unit_update))
        self._unsubs.append(self.coordinator.async_add_command_listener(self._on_command))
        for unit_id in self.coordinator.units:
            self._previous[unit_id] = self.coordinator.state(unit_id)
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._on_interval, timedelta(seconds=ADVICE_INTERVAL_SECONDS)
            )
        )
        await self._async_refresh_forecast()
        await self._async_evaluate()

    async def async_stop(self) -> None:
        """Stop tracking and save what was learned."""
        await self._async_save()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._debouncer.async_cancel()

    def _watched_entities(self) -> list[str]:
        options = self.entry.options
        ids: list[str] = []
        for key in (
            CONF_OUTDOOR_SENSOR,
            CONF_SOLAR_SENSOR,
            CONF_SOLAR_POWER_SENSOR,
            CONF_BATTERY_POWER_SENSOR,
            CONF_BATTERY_SOC_SENSOR,
            CONF_WEATHER,
        ):
            if options.get(key):
                ids.append(options[key])
        ids.extend(options.get(CONF_PEOPLE) or [])
        for subentry in zone_subentries(self.coordinator):
            data = subentry.data
            for key in (CONF_TEMP_SENSOR, CONF_HUMIDITY_SENSOR):
                if data.get(key):
                    ids.append(data[key])
            ids.extend(data.get(CONF_PRESENCE_SENSORS) or [])
            ids.extend(data.get(CONF_WINDOW_SENSORS) or [])
            ids.extend(data.get(CONF_DOOR_SENSORS) or [])
        return sorted(set(ids))

    @callback
    def _on_input_change(self, _event: Event) -> None:
        self._schedule()

    def _guard(self, unit_id: str) -> GuardState:
        return self._guards.setdefault(unit_id, GuardState())

    def journal(self, unit_id: str) -> Journal:
        """Return a unit's journal of what the Climate Butler did and why."""
        return self.journals.setdefault(unit_id, Journal())

    def _note(self, unit_id: str, kind: Kind, headline: str, reason: str = "") -> None:
        self.journal(unit_id).add(JournalEntry(dt_util.now(), kind, headline, reason))

    def _zone_names(self, unit_id: str) -> dict[int, str]:
        snapshot = self.snapshots.get(unit_id)
        return {z.zone: z.name for z in snapshot.zones} if snapshot else {}

    def _note_hold(self, unit_id: str, changed: list[str], where: str) -> None:
        """Note a person's change autopilot now leaves alone for a while."""
        names = self._zone_names(unit_id)
        things = [
            "the unit" if c == "unit" else "the fan" if c == "fan" else names.get(int(c[4:]), c)
            for c in changed
        ]
        until = dt_util.as_local(dt_util.utcnow() + self.guard_config.override_hold)
        listed = things[0] if len(things) == 1 else ", ".join(things[:-1]) + " and " + things[-1]
        self._note(
            unit_id,
            Kind.HOLD,
            "Leaving your change alone",
            f"{listed[0].upper()}{listed[1:]} changed {where}: left as you set "
            f"{'it' if len(things) == 1 else 'them'} until {until:%H:%M}.",
        )

    def autopilot_on(self, unit_id: str) -> bool:
        """Return True when any autopilot behaviour is enabled for the unit."""
        return any(self.autopilot.get(unit_id, {}).values())

    @callback
    def _on_command(self, unit_id: str, command: Any, source: str) -> None:
        """Hold what a person changed through Home Assistant.

        Autopilot's own commands are noted straight away: the coordinator shows
        them before the send returns, and that update must not look like a
        person's change.
        """
        if source == "autopilot":
            note_command(self._guard(unit_id), command, dt_util.utcnow())
        elif self.autopilot_on(unit_id):
            manual_command(self.guard_config, self._guard(unit_id), command, dt_util.utcnow())
            changed = (
                ["unit"]
                if command.power is not None
                or command.mode is not None
                or command.target_temp is not None
                else []
            )
            changed += ["fan"] if command.fan_speed is not None else []
            changed += [f"zone{n}" for n in command.zones]
            if changed:
                self._note_hold(unit_id, changed, "in Home Assistant")

    @callback
    def _on_unit_update(self) -> None:
        """Spot changes made in the app or on the wall controller."""
        now = dt_util.utcnow()
        for unit_id in self.coordinator.units:
            after = self.coordinator.state(unit_id)
            before = self._previous.get(unit_id)
            self._previous[unit_id] = after
            if before is None or after is None or not self.autopilot_on(unit_id):
                continue
            guard = self._guard(unit_id)
            paused = detect_override(self.guard_config, guard, before, after, now)
            # The controller opens spill zones by itself; that is not a person's change.
            for number in self._spill_zones(unit_id):
                if f"zone{number}" in paused:
                    guard.zone_paused_until.pop(number, None)
                    paused.remove(f"zone{number}")
            if paused:
                _LOGGER.debug("Autopilot paused for %s after a manual change: %s", unit_id, paused)
                self._note_hold(unit_id, paused, "in the app or on the wall controller")
        self._schedule()

    @callback
    def _schedule(self) -> None:
        self.hass.async_create_task(self._debouncer.async_call())

    async def _on_interval(self, _now: datetime) -> None:
        if self._forecast_due():
            await self._async_refresh_forecast()
        self._learn()
        await self._async_evaluate()
        if self._saved_at is None or dt_util.utcnow() - self._saved_at > SAVE_EVERY:
            await self._async_save()

    async def _async_save(self) -> None:
        self._saved_at = dt_util.utcnow()
        await self._store.async_save({uid: m.as_dict() for uid, m in self.models.items()})
        await self._journal_store.async_save({uid: j.as_list() for uid, j in self.journals.items()})

    def _learn(self) -> None:
        """Feed a five-minute sample of every unit into its thermal model."""
        now = dt_util.utcnow()
        for unit_id, snapshot in self.snapshots.items():
            zones = [z for z in snapshot.zones if z.temperature is not None]
            if not zones:
                continue
            total = sum(z.opening for z in snapshot.zones if z.on and z.opening > 0) or 1
            sample = ThermalSample(
                temps={z.zone: z.temperature for z in zones},  # type: ignore[misc]
                outdoor=snapshot.outdoor_temp,
                running=snapshot.unit.power,
                mode=snapshot.unit.mode,
                shares={z.zone: (z.opening / total if z.on else 0.0) for z in snapshot.zones},
                night=in_night(snapshot),
            )
            previous = self._samples.get(unit_id)
            if previous is None or now - previous[0] >= SAMPLE_EVERY:
                if previous is not None:
                    minutes = (now - previous[0]).total_seconds() / 60
                    if minutes <= 20:
                        model = self.models.setdefault(unit_id, ThermalModel())
                        model.learn(previous[1], sample, minutes)
                        self._note_learned(unit_id, model)
                self._samples[unit_id] = (now, sample)

    def _note_learned(self, unit_id: str, model: ThermalModel) -> None:
        """Tell the journal what was learned about a room's own warmth or chill."""
        names = self._zone_names(unit_id)
        for zone, night, bias in model.news():
            name = names.get(zone, f"Zone {zone}")
            when = "at night" if night else "by day"
            hourly = abs(bias) * 60
            if bias > 0:
                headline = f"Learned: {name} keeps itself warm {when}"
                reason = (
                    f"{name} stays about {hourly:.1f} °C an hour warmer {when} than outside "
                    "explains (people, sun or devices). It will not be left to cool on its own "
                    "for long."
                )
            else:
                headline = f"Learned: {name} loses heat fast {when}"
                reason = (
                    f"{name} cools about {hourly:.1f} °C an hour faster {when} than outside "
                    "explains (a draught, or a window left open?). It will not be left to warm "
                    "up on its own for long."
                )
            self._note(unit_id, Kind.LEARN, headline, reason)

    # Listeners --------------------------------------------------------------------

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call ``listener`` whenever advice or inputs change."""
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    # Inputs -----------------------------------------------------------------------

    def _forecast_due(self) -> bool:
        if not self.entry.options.get(CONF_WEATHER):
            return False
        return self._forecast_at is None or dt_util.utcnow() - self._forecast_at > timedelta(
            minutes=FORECAST_REFRESH_MINUTES
        )

    async def _async_refresh_forecast(self) -> None:
        entity_id = self.entry.options.get(CONF_WEATHER)
        if not entity_id:
            self._forecast = ()
            return
        self._forecast_at = dt_util.utcnow()
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": entity_id, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
        except (HomeAssistantError, ValueError) as err:
            _LOGGER.debug("No hourly forecast from %s: %s", entity_id, err)
            self._forecast = ()
            return
        points = []
        for item in (response or {}).get(entity_id, {}).get("forecast", []):
            when = dt_util.parse_datetime(str(item.get("datetime", "")))
            if when is None:
                continue
            points.append(
                ForecastPoint(
                    time=when,
                    temperature=item.get("temperature"),
                    humidity=item.get("humidity"),
                    condition=item.get("condition"),
                )
            )
        self._forecast = tuple(points)

    def _outdoor(self) -> float | None:
        options = self.entry.options
        value = _float(self.hass.states.get(options.get(CONF_OUTDOOR_SENSOR, "")))
        if value is None and options.get(CONF_WEATHER):
            weather = self.hass.states.get(options[CONF_WEATHER])
            if weather is not None:
                raw = weather.attributes.get("temperature")
                value = float(raw) if isinstance(raw, int | float) else None
        return value

    def _presence(self) -> tuple[bool | None, float | None]:
        people = self.entry.options.get(CONF_PEOPLE) or []
        states = [s for s in (self.hass.states.get(p) for p in people) if s is not None]
        if not states:
            return None, None
        if any(s.state in HOME_STATES for s in states):
            return True, None
        left = max(s.last_changed for s in states)
        return False, (dt_util.utcnow() - left).total_seconds() / 60

    def _power(self, option: str) -> float | None:
        """Return a power sensor's reading in watts."""
        state = self.hass.states.get(self.entry.options.get(option, ""))
        value = _float(state)
        if value is None or state is None:
            return None
        return value * POWER_SCALE.get(state.attributes.get("unit_of_measurement"), 1.0)

    def _solar_export(self) -> float | None:
        value = self._power(CONF_SOLAR_SENSOR)
        if value is None:
            return None
        if self.entry.options.get(CONF_SOLAR_EXPORT_NEGATIVE, True):
            value = -value
        return max(0.0, value)

    def _battery(self) -> float | None:
        """Return battery power in watts, positive while charging."""
        value = self._power(CONF_BATTERY_POWER_SENSOR)
        if value is None:
            return None
        return -value if self.entry.options.get(CONF_BATTERY_CHARGING_NEGATIVE, True) else value

    def _zone_inputs(self, unit_id: str) -> tuple[ZoneInputs, ...]:
        state = self.coordinator.state(unit_id)
        if state is None:
            return ()
        now = dt_util.utcnow()
        damp = self._damp.setdefault(unit_id, DampTracker())
        zones = []
        for subentry in zone_subentries(self.coordinator):
            data = subentry.data
            if data.get(CONF_UNIT_ID) != unit_id:
                continue
            number = int(data[CONF_ZONE])
            zone_state = state.zones.get(number)
            if zone_state is None:
                continue
            occupied, vacant = None, None
            sensors = [
                s
                for s in (self.hass.states.get(e) for e in data.get(CONF_PRESENCE_SENSORS) or [])
                if s is not None and s.state not in UNAVAILABLE
            ]
            if sensors:
                if any(s.state == STATE_ON for s in sensors):
                    occupied = True
                else:
                    # Still sleepers and readers go quiet: only call a room empty after
                    # every sensor has been off for the hold time.
                    vacant = (now - max(s.last_changed for s in sensors)).total_seconds() / 60
                    hold = float(data.get(CONF_PRESENCE_HOLD, DEFAULT_PRESENCE_HOLD) or 0)
                    occupied = vacant < hold
                    if occupied:
                        vacant = None
            sleep_zone = bool(data.get(CONF_SLEEP_ZONE, False))
            if sleep_zone and self.autopilot.get(unit_id, {}).get(SLEEP_MODE):
                occupied, vacant = True, None
            options = self.entry.options
            window_open = self._open_long_enough(
                data.get(CONF_WINDOW_SENSORS),
                float(options.get(CONF_WINDOW_DELAY, DEFAULT_WINDOW_DELAY)),
                now,
            )
            door_open = self._open_long_enough(
                data.get(CONF_DOOR_SENSORS),
                float(options.get(CONF_DOOR_DELAY, DEFAULT_DOOR_DELAY)),
                now,
            )
            try:
                priority = Priority(data.get(CONF_PRIORITY, Priority.LIVING))
            except ValueError:
                priority = Priority.LIVING
            zones.append(
                ZoneInputs(
                    zone=number,
                    name=subentry.title,
                    on=zone_state.on,
                    opening=zone_state.opening,
                    temperature=_float(self.hass.states.get(data.get(CONF_TEMP_SENSOR, "")))
                    if data.get(CONF_TEMP_SENSOR)
                    else zone_state.temperature,
                    humidity=_float(self.hass.states.get(data.get(CONF_HUMIDITY_SENSOR, ""))),
                    occupied=occupied,
                    vacant_minutes=vacant,
                    window_open=window_open or door_open,
                    door_open=door_open and not window_open,
                    offset=float(data.get(CONF_OFFSET, 0) or 0),
                    priority=priority,
                    min_opening=int(data.get(CONF_MIN_OPENING, 0) or 0),
                    max_opening=int(data.get(CONF_MAX_OPENING, 100) or 100),
                    spill=bool(data.get(CONF_SPILL, False)),
                    guest=bool(data.get(CONF_GUEST, False)),
                    sleep=sleep_zone,
                    damp_minutes=damp.minutes(number),
                )
            )
        return tuple(sorted(zones, key=lambda z: z.zone))

    def _spill_zones(self, unit_id: str) -> set[int]:
        return {
            int(sub.data[CONF_ZONE])
            for sub in zone_subentries(self.coordinator)
            if sub.data.get(CONF_UNIT_ID) == unit_id and sub.data.get(CONF_SPILL)
        }

    def _open_long_enough(
        self, entity_ids: list[str] | None, minutes: float, now: datetime
    ) -> bool:
        """Return True when any sensor has been open for at least ``minutes``."""
        for entity_id in entity_ids or []:
            state = self.hass.states.get(entity_id)
            if state is None or state.state != STATE_ON:
                continue
            if (now - state.last_changed).total_seconds() / 60 >= minutes:
                return True
        return False

    def build_snapshot(self, unit_id: str) -> HouseSnapshot | None:
        """Gather every input for one unit."""
        state = self.coordinator.state(unit_id)
        if state is None:
            return None
        info = self.coordinator.units[unit_id]
        anyone_home, away_minutes = self._presence()
        options = self.entry.options
        return HouseSnapshot(
            now=dt_util.now(),
            unit_name=info.name,
            caps=info.capabilities,
            unit=state,
            zones=self._zone_inputs(unit_id),
            settings=settings_from_options(dict(self.entry.options)),
            outdoor_temp=self._outdoor(),
            forecast=self._forecast,
            anyone_home=anyone_home,
            away_minutes=away_minutes,
            solar_export_w=self._solar_export(),
            solar_w=(
                max(0.0, solar)
                if (solar := self._power(CONF_SOLAR_POWER_SENSOR)) is not None
                else None
            ),
            battery_w=self._battery(),
            battery_soc=_float(self.hass.states.get(options.get(CONF_BATTERY_SOC_SENSOR, ""))),
            guest_mode=self.guest_modes.get(unit_id, False),
            sleep_mode=bool(self.autopilot.get(unit_id, {}).get(SLEEP_MODE)),
            sleep_target=self.sleep_targets.get(unit_id, DEFAULT_SLEEP_TARGET),
        )

    # Advice -----------------------------------------------------------------------

    async def _async_evaluate(self) -> None:
        now = dt_util.utcnow()
        self._dismissed = {k: v for k, v in self._dismissed.items() if v > now}
        for unit_id in self.coordinator.units:
            snapshot = self.build_snapshot(unit_id)
            if snapshot is None:
                continue
            self.snapshots[unit_id] = snapshot
            self._damp.setdefault(unit_id, DampTracker()).observe(
                snapshot.now, snapshot.zones, snapshot.outdoor_temp, snapshot.unit_humidity
            )
            self.trends.setdefault(unit_id, TrendTracker()).observe(snapshot)
            self._resolve_energy(unit_id, snapshot)
            model = self.models.setdefault(unit_id, ThermalModel())
            try:
                self.plans[unit_id] = Plan(
                    blocks=precondition_blocks(snapshot, model), periods=periods(snapshot)
                )
            except Exception:
                # A bad input must not stop advice and autopilot; plan without it.
                _LOGGER.exception("Could not build the plan for %s", unit_id)
                self.plans[unit_id] = Plan()
            self.advice[unit_id] = [
                rec for rec in evaluate(snapshot, model) if (unit_id, rec.id) not in self._dismissed
            ]
        for unit_id in self.coordinator.units:
            await self._async_autopilot(unit_id)
        for listener in list(self._listeners):
            listener()
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))

    def _resolve_energy(self, unit_id: str, snapshot: HouseSnapshot) -> None:
        """Pick the energy mode for now, and note it when it changes."""
        before = self.energy.get(unit_id)
        mode, reason = resolve(
            snapshot,
            self.energy_settings.get(unit_id, EnergySetting.AUTO),
            self._energy_memory.setdefault(unit_id, EnergyMemory()),
        )
        self.energy[unit_id] = (mode, reason)
        if before is not None and before[0] is not mode:
            self._note(unit_id, Kind.ENERGY, f"Energy: {mode.value.capitalize()}", reason)

    async def _async_autopilot(self, unit_id: str) -> None:
        """Let the autopilot engine decide, within the guard rails."""
        enabled = self.autopilot.get(unit_id, {})
        snapshot = self.snapshots.get(unit_id)
        if not any(enabled.values()) or snapshot is None or not snapshot.unit.online:
            self.decisions.pop(unit_id, None)
            return
        now = dt_util.utcnow()
        last = self._last_run.get(unit_id)
        if last is not None and now - last < AUTOPILOT_MIN_GAP:
            return
        minutes = (now - last).total_seconds() / 60 if last else 1.0
        self._last_run[unit_id] = now
        guard = self._guard(unit_id)
        paused = {n for n in guard.zone_paused_until if guard.zone_paused(n, now)}
        plan = self.plans.get(unit_id) or Plan()
        memory = self._memory.setdefault(unit_id, AutopilotMemory())
        energy = self.energy.get(unit_id, (EnergyMode.MID, ""))[0]
        decision = decide(
            snapshot,
            Switches(
                presence=enabled.get(PRESENCE_ZONING, False),
                balancing=enabled.get(ROOM_BALANCING, False),
                weather=enabled.get(WEATHER_PRECONDITION, False),
                sleep=enabled.get(SLEEP_MODE, False),
                energy=energy,
            ),
            plan.blocks,
            self.models.setdefault(unit_id, ThermalModel()),
            memory,
            paused,
            minutes,
            self.trends.get(unit_id),
        )
        self.decisions[unit_id] = decision
        # Open windows and doors win over everything else for their zones.
        openings = openings_command(snapshot, self._openings.setdefault(unit_id, OpeningMemory()))
        command = decision.command.merge(openings)
        sent = await self._async_send_autopilot(
            unit_id, snapshot, guard, command, now, decision.reason
        )
        self.journal(unit_id).observe(
            snapshot.now,
            decision.activity.value if decision.activity else None,
            decision.headline,
            decision.reason,
            sent,
            {z.zone: z.name for z in snapshot.zones},
        )

    async def _async_send_autopilot(
        self,
        unit_id: str,
        snapshot: HouseSnapshot,
        guard: GuardState,
        command: Command,
        now: datetime,
        reason: str = "",
    ) -> Command | None:
        """Send what the guard allows; return it, or None when nothing went out."""
        allowed = filter_command(self.guard_config, guard, snapshot, command, now)
        if allowed is None:
            return None
        try:
            await self.coordinator.async_send(unit_id, allowed, source="autopilot")
        except HomeAssistantError as err:
            _LOGGER.warning("Autopilot could not send %s: %s", allowed, err)
            return None
        record(guard, allowed, now)
        memory = self._memory.setdefault(unit_id, AutopilotMemory())
        note_sent(memory, allowed, now, snapshot.unit.mode)
        self.last_action[unit_id] = {
            "time": now.isoformat(),
            "action": command_as_dict(allowed),
            "reason": reason,
        }
        return allowed

    async def async_set_autopilot(
        self, unit_id: str, behaviour: str, enabled: bool, restoring: bool = False
    ) -> None:
        """Turn an autopilot behaviour on or off.

        Switching one on hands the unit over: holds left by earlier manual
        changes end, so the Butler can act at once.
        """
        was = self.autopilot.get(unit_id, {}).get(behaviour, False)
        self.autopilot.setdefault(unit_id, {})[behaviour] = enabled
        if enabled and not restoring:
            guard = self._guard(unit_id)
            guard.unit_paused_until = guard.fan_paused_until = None
        if not restoring and enabled != was:
            label = BEHAVIOUR_NAMES.get(behaviour, behaviour)
            reason = ""
            if behaviour == SLEEP_MODE and enabled:
                target = self.sleep_targets.get(unit_id, DEFAULT_SLEEP_TARGET)
                reason = f"Holding the bedroom within half a degree of {target:g} °C."
            self._note(unit_id, Kind.SWITCH, f"{label} {'on' if enabled else 'off'}", reason)
        # What each room is aiming for has changed: decide afresh, and straight away,
        # keeping only what the lighter behaviours need to put things back.
        old = self._memory.pop(unit_id, None)
        if old is not None:
            self._memory[unit_id] = AutopilotMemory(
                presence=old.presence,
                health_started=old.health_started,
                mode_at=old.mode_at,
                mode_changed_at=old.mode_changed_at,
                last_mode=old.last_mode,
            )
        self._last_run.pop(unit_id, None)
        await self._async_evaluate()

    async def async_set_energy(
        self, unit_id: str, setting: EnergySetting, restoring: bool = False
    ) -> None:
        """Choose the energy mode, or Auto to follow the solar and battery sensors."""
        before = self.energy_settings.get(unit_id, EnergySetting.AUTO)
        self.energy_settings[unit_id] = setting
        if restoring or setting is before:
            return
        self._note(unit_id, Kind.SWITCH, f"Energy set to {setting.value.capitalize()}")
        if unit_id in self._memory:
            self._memory[unit_id].fresh = True
        self._last_run.pop(unit_id, None)
        await self._async_evaluate()

    def butler_state(self, unit_id: str) -> str:
        """Return what the Climate Butler is doing in one word, for its sensor."""
        if not self.autopilot_on(unit_id):
            return "off"
        if self._guard(unit_id).unit_paused(dt_util.utcnow()):
            return "paused"
        decision = self.decisions.get(unit_id)
        return decision.activity.value if decision and decision.activity else "rest"

    def energy_status(self, unit_id: str) -> dict[str, Any]:
        """Return the energy setting, the mode in use and why, and the power readings."""
        snapshot = self.snapshots.get(unit_id)
        mode, reason = self.energy.get(unit_id, (EnergyMode.MID, ""))
        return {
            "setting": self.energy_settings.get(unit_id, EnergySetting.AUTO).value,
            "mode": mode.value,
            "reason": reason,
            "export_w": snapshot.solar_export_w if snapshot else None,
            "solar_w": snapshot.solar_w if snapshot else None,
            "battery_w": snapshot.battery_w if snapshot else None,
            "battery_soc": snapshot.battery_soc if snapshot else None,
        }

    async def async_set_sleep_target(self, unit_id: str, value: float) -> None:
        """Set the temperature sleep mode holds the sleep zones at."""
        self.sleep_targets[unit_id] = value
        if unit_id in self._memory:
            self._memory[unit_id].fresh = True
        self._last_run.pop(unit_id, None)
        await self._async_evaluate()

    async def async_pause(self, unit_id: str | None, minutes: int) -> None:
        """Hold autopilot for every zone and the unit."""
        until = dt_util.utcnow() + timedelta(minutes=minutes)
        for uid in [unit_id] if unit_id else list(self.coordinator.units):
            guard = self._guard(uid)
            guard.unit_paused_until = until
            slots = self.coordinator.units[uid].capabilities.zone_slots
            for zone in range(1, slots + 1):
                guard.zone_paused_until[zone] = until
        await self._async_evaluate()

    async def async_resume(self, unit_id: str | None) -> None:
        """Clear manual holds so autopilot acts again."""
        for uid in [unit_id] if unit_id else list(self.coordinator.units):
            self._guard(uid).resume()
        await self._async_evaluate()

    def autopilot_status(self, unit_id: str) -> dict[str, Any]:
        """Return autopilot state for the card and attributes."""
        now = dt_util.utcnow()
        guard = self._guard(unit_id)
        return {
            "behaviours": {b: self.autopilot.get(unit_id, {}).get(b, False) for b in BEHAVIOURS},
            "paused_until": guard.unit_paused_until.isoformat()
            if guard.unit_paused(now) and guard.unit_paused_until
            else None,
            "zones_paused": {
                str(n): until.isoformat()
                for n, until in guard.zone_paused_until.items()
                if guard.zone_paused(n, now)
            },
            "fan_paused_until": guard.fan_paused_until.isoformat()
            if guard.fan_paused(now) and guard.fan_paused_until
            else None,
            "last_action": self.last_action.get(unit_id),
            # What autopilot is doing now and why, with each zone's role.
            "decision": decision.as_dict()
            if (decision := self.decisions.get(unit_id)) is not None
            else None,
        }

    async def async_set_guest_mode(self, unit_id: str, enabled: bool) -> None:
        """Turn guest mode on or off for a unit."""
        self.guest_modes[unit_id] = enabled
        await self._async_evaluate()

    async def async_refresh(self) -> None:
        """Evaluate straight away."""
        await self._async_evaluate()

    def find(
        self, recommendation_id: str, unit_id: str | None = None
    ) -> tuple[str, Recommendation]:
        """Return the unit and recommendation for an id."""
        for uid, recs in self.advice.items():
            if unit_id and uid != unit_id:
                continue
            for rec in recs:
                if rec.id == recommendation_id:
                    return uid, rec
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="recommendation_not_found",
            translation_placeholders={"id": recommendation_id},
        )

    async def async_apply(self, recommendation_id: str, unit_id: str | None = None) -> None:
        """Apply a recommendation's action."""
        uid, rec = self.find(recommendation_id, unit_id)
        if rec.action is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="recommendation_no_action",
                translation_placeholders={"id": recommendation_id},
            )
        await self.coordinator.async_send(uid, rec.action)
        # Hide it briefly while the unit catches up, so it does not flicker back.
        self._dismissed[(uid, rec.id)] = dt_util.utcnow() + timedelta(minutes=10)
        await self._async_evaluate()

    async def async_dismiss(
        self, recommendation_id: str, unit_id: str | None = None, minutes: int | None = None
    ) -> None:
        """Hide a recommendation for a while."""
        uid, rec = self.find(recommendation_id, unit_id)
        self._dismissed[(uid, rec.id)] = dt_util.utcnow() + timedelta(
            minutes=minutes or DEFAULT_DISMISS_MINUTES
        )
        await self._async_evaluate()

    # Card payload -----------------------------------------------------------------

    def as_dict(self, unit_id: str) -> dict[str, Any] | None:
        """Return the websocket snapshot for the card."""
        snapshot = self.snapshots.get(unit_id) or self.build_snapshot(unit_id)
        if snapshot is None:
            return None
        info = self.coordinator.units[unit_id]
        caps = info.capabilities
        unit = snapshot.unit
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)

        def entity_id(domain: str, unique_id: str) -> str | None:
            return ent_reg.async_get_entity_id(domain, DOMAIN, unique_id)

        def device_id(identifier: str) -> str | None:
            device = dev_reg.async_get_device(identifiers={(DOMAIN, identifier)})
            return device.id if device else None

        band = house_band(snapshot)
        decision = self.decisions.get(unit_id)
        health = decision.health if decision else assess(snapshot)
        view = decision.outlook if decision else outlook(snapshot)
        trend = self.trends.get(unit_id)
        zones = []
        for zone in snapshot.zones:
            zband = zone_band(snapshot, zone)
            zhealth = health.get(zone.zone)
            zones.append(
                {
                    "zone": zone.zone,
                    "name": zone.name,
                    "device_id": device_id(f"{unit_id}_zone{zone.zone}"),
                    "on": zone.on,
                    "opening": zone.opening,
                    "temperature": zone.temperature,
                    "humidity": zone.humidity,
                    "occupied": zone.occupied,
                    "window_open": zone.window_open,
                    "guest": zone.guest,
                    "sleep": zone.sleep,
                    "door_open": zone.door_open,
                    "paused": self._guard(unit_id).zone_paused(zone.zone, dt_util.utcnow()),
                    "band": {"low": zband.low, "high": zband.high},
                    "demand": zone_demand(snapshot, zone),
                    # How much this room counts in the house average (0 without a sensor).
                    "weight": round(zone_weight(snapshot, zone), 2) if zone.has_sensor else 0,
                    "priority": zone.priority.value,
                    # What autopilot is doing with the zone (serve, hold, shed...), if on.
                    "role": role.value
                    if decision and (role := decision.roles.get(zone.zone))
                    else None,
                    # What autopilot aims the room at now, and how fast it is moving (°C an hour).
                    "aim": decision.aims.get(zone.zone) if decision else None,
                    "rate": round(rate, 2)
                    if trend and (rate := trend.rate(zone.zone)) is not None
                    else None,
                    # The last two hours, every five minutes, oldest first.
                    "history": trend.series(zone.zone, snapshot.now, HISTORY_MINUTES, HISTORY_STEP)
                    if trend and zone.has_sensor
                    else [],
                    "health": zhealth.as_dict() if zhealth else None,
                    "entities": {
                        "damper": entity_id("cover", f"{unit_id}_zone{zone.zone}_damper"),
                        "power": entity_id("switch", f"{unit_id}_zone{zone.zone}_power"),
                    },
                }
            )
        return {
            "schema": WS_SCHEMA_VERSION,
            "entry_id": self.entry.entry_id,
            "unit": {
                "id": unit_id,
                "name": info.name,
                "device_id": device_id(unit_id),
                "climate": entity_id("climate", f"{unit_id}_climate"),
                "entities": {
                    "advice": entity_id("sensor", f"{unit_id}_advice"),
                    "guest_mode": entity_id("switch", f"{unit_id}_guest_mode"),
                    "sleep_target": entity_id("number", f"{unit_id}_sleep_target"),
                    "energy_mode": entity_id("select", f"{unit_id}_energy_mode"),
                    "butler": entity_id("sensor", f"{unit_id}_climate_butler"),
                    **{b: entity_id("switch", f"{unit_id}_{b}") for b in BEHAVIOURS},
                },
                # The unit's own features (Powerful, Eco, Health...) and the entity that
                # controls each: a switch, or a select for multi-choice features.
                "features_entities": {
                    f.value: entity_id("switch", f"{unit_id}_{f.value}")
                    or entity_id("select", f"{unit_id}_{f.value}")
                    for f in caps.features
                },
                "online": unit.online,
                "power": unit.power,
                "mode": unit.mode.value if unit.mode else None,
                "target": unit.target_temp,
                "current": unit.current_temp,
                "fan": unit.fan_speed,
                "features": {f.value: v for f, v in unit.features.items()},
                "caps": {
                    "modes": [m.value for m in caps.hvac_modes],
                    "fans": list(caps.fan_speeds),
                    "min": caps.min_temp,
                    "max": caps.max_temp,
                    "step": caps.temp_step,
                    "features": [f.value for f in caps.features],
                    "zone_step": caps.zone_opening_step,
                },
            },
            "zones": zones,
            "house": {
                "period": current_period(snapshot).value,
                "band": {"low": band.low, "high": band.high},
                "indoor": indoor_temperature(snapshot),
                # What the indoor figure is: the rooms' weighted average, the sleep
                # zones in bedtime, the guest zones in guest mode, or the unit itself.
                "indoor_basis": (
                    "return_air"
                    if not snapshot.sensed_zones
                    else "sleep"
                    if snapshot.sleep_mode and any(z.sleep for z in snapshot.sensed_zones)
                    else "guest"
                    if snapshot.guest_mode and any(z.guest for z in snapshot.sensed_zones)
                    else "rooms"
                ),
                "deciding_zones": [z.zone for z in deciding_zones(snapshot)],
                "outdoor": snapshot.outdoor_temp,
                "anyone_home": snapshot.anyone_home,
                "demand": round(house_demand(snapshot)),
                "solar_export_w": snapshot.solar_export_w,
                **view.as_dict(),
                "guest_mode": snapshot.guest_mode,
                "sleep_mode": snapshot.sleep_mode,
                "sleep_target": snapshot.sleep_target,
                "energy": self.energy_status(unit_id),
            },
            "forecast": [
                {"time": p.time.isoformat(), "temperature": p.temperature, "condition": p.condition}
                for p in snapshot.forecast[:24]
            ],
            "advice": [rec.as_dict() for rec in self.advice.get(unit_id, [])],
            "forecast_hours": len(snapshot.forecast),
            "autopilot": {
                **self.autopilot_status(unit_id),
                "state": self.butler_state(unit_id),
                # The latest entries of what the Butler did and why, newest first.
                "journal": [e.as_dict() for e in self.journal(unit_id).recent(JOURNAL_IN_FEED)],
            },
            "plan": {
                **(self.plans.get(unit_id) or Plan()).as_dict(),
                "model": {
                    str(n): {
                        "loss": round(m.loss, 5),
                        "gain": round(m.gain, 4),
                        "confident": m.confident,
                    }
                    for n, m in (self.models.get(unit_id) or ThermalModel()).zones.items()
                },
            },
            "updated": snapshot.now.isoformat(),
        }
