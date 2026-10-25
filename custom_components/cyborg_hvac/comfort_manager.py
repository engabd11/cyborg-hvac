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
from homeassistant.util import dt as dt_util

from .const import (
    ADVICE_INTERVAL_SECONDS,
    CONF_COOL_AWAY,
    CONF_COOL_DAY,
    CONF_COOL_NIGHT,
    CONF_GUEST,
    CONF_HEAT_AWAY,
    CONF_HEAT_DAY,
    CONF_HEAT_NIGHT,
    CONF_HUMIDITY_MAX,
    CONF_HUMIDITY_SENSOR,
    CONF_MAX_OPENING,
    CONF_MIN_OPENING,
    CONF_NIGHT_END,
    CONF_NIGHT_START,
    CONF_OFFSET,
    CONF_OUTDOOR_SENSOR,
    CONF_PEOPLE,
    CONF_PRESENCE_SENSORS,
    CONF_PRIORITY,
    CONF_SOLAR_EXPORT_NEGATIVE,
    CONF_SOLAR_SENSOR,
    CONF_SPILL,
    CONF_TEMP_SENSOR,
    CONF_UNIT_ID,
    CONF_WEATHER,
    CONF_WINDOW_SENSORS,
    CONF_ZONE,
    DEFAULT_DISMISS_MINUTES,
    DOMAIN,
    FORECAST_REFRESH_MINUTES,
    WS_SCHEMA_VERSION,
)
from .entity import zone_subentries
from .logic.advisor import Recommendation, evaluate
from .logic.comfort import (
    current_period,
    house_band,
    house_demand,
    indoor_temperature,
    zone_band,
    zone_demand,
)
from .logic.snapshot import (
    ComfortSettings,
    ForecastPoint,
    HouseSnapshot,
    Priority,
    ZoneInputs,
)
from .ws import signal_update

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import HvacCoordinator

_LOGGER = logging.getLogger(__name__)

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
        self._unsubs.append(self.coordinator.async_add_listener(self._schedule))
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._on_interval, timedelta(seconds=ADVICE_INTERVAL_SECONDS)
            )
        )
        await self._async_refresh_forecast()
        await self._async_evaluate()

    async def async_stop(self) -> None:
        """Stop tracking."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._debouncer.async_cancel()

    def _watched_entities(self) -> list[str]:
        options = self.entry.options
        ids: list[str] = []
        for key in (CONF_OUTDOOR_SENSOR, CONF_SOLAR_SENSOR, CONF_WEATHER):
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
        return sorted(set(ids))

    @callback
    def _on_input_change(self, _event: Event) -> None:
        self._schedule()

    @callback
    def _schedule(self) -> None:
        self.hass.async_create_task(self._debouncer.async_call())

    async def _on_interval(self, _now: datetime) -> None:
        if self._forecast_due():
            await self._async_refresh_forecast()
        await self._async_evaluate()

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

    def _solar_export(self) -> float | None:
        state = self.hass.states.get(self.entry.options.get(CONF_SOLAR_SENSOR, ""))
        value = _float(state)
        if value is None or state is None:
            return None
        value *= POWER_SCALE.get(state.attributes.get("unit_of_measurement"), 1.0)
        if self.entry.options.get(CONF_SOLAR_EXPORT_NEGATIVE, True):
            value = -value
        return max(0.0, value)

    def _zone_inputs(self, unit_id: str) -> tuple[ZoneInputs, ...]:
        state = self.coordinator.state(unit_id)
        if state is None:
            return ()
        now = dt_util.utcnow()
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
                occupied = any(s.state == STATE_ON for s in sensors)
                if not occupied:
                    vacant = (now - max(s.last_changed for s in sensors)).total_seconds() / 60
            windows = [self.hass.states.get(e) for e in data.get(CONF_WINDOW_SENSORS) or []]
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
                    window_open=any(w is not None and w.state == STATE_ON for w in windows),
                    offset=float(data.get(CONF_OFFSET, 0) or 0),
                    priority=priority,
                    min_opening=int(data.get(CONF_MIN_OPENING, 0) or 0),
                    max_opening=int(data.get(CONF_MAX_OPENING, 100) or 100),
                    spill=bool(data.get(CONF_SPILL, False)),
                    guest=bool(data.get(CONF_GUEST, False)),
                )
            )
        return tuple(sorted(zones, key=lambda z: z.zone))

    def build_snapshot(self, unit_id: str) -> HouseSnapshot | None:
        """Gather every input for one unit."""
        state = self.coordinator.state(unit_id)
        if state is None:
            return None
        info = self.coordinator.units[unit_id]
        anyone_home, away_minutes = self._presence()
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
            guest_mode=self.guest_modes.get(unit_id, False),
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
            self.advice[unit_id] = [
                rec for rec in evaluate(snapshot) if (unit_id, rec.id) not in self._dismissed
            ]
        for listener in list(self._listeners):
            listener()
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))

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
        zones = []
        for zone in snapshot.zones:
            zband = zone_band(snapshot, zone)
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
                    "band": {"low": zband.low, "high": zband.high},
                    "demand": zone_demand(snapshot, zone),
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
                "outdoor": snapshot.outdoor_temp,
                "anyone_home": snapshot.anyone_home,
                "demand": round(house_demand(snapshot)),
                "solar_export_w": snapshot.solar_export_w,
                "guest_mode": snapshot.guest_mode,
            },
            "forecast": [
                {"time": p.time.isoformat(), "temperature": p.temperature, "condition": p.condition}
                for p in snapshot.forecast[:24]
            ],
            "advice": [rec.as_dict() for rec in self.advice.get(unit_id, [])],
            "autopilot": {},
            "plan": [],
            "updated": snapshot.now.isoformat(),
        }
