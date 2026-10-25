"""Config flow: sign in, pick zones, and manage zones as subentries."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import secrets
from typing import Any

from aiohttp import ClientError
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.exceptions import OAuth2TokenRequestError
from homeassistant.helpers import config_entry_oauth2_flow, selector
import voluptuous as vol

from .backends.hisense_connectlife import adapter as hisense
from .backends.hisense_connectlife.client import new_source_id
from .brands import BRANDS
from .const import (
    BRAND_DEMO,
    BRAND_HISENSE,
    CONF_ACCOUNT_ID,
    CONF_AREA,
    CONF_BRAND,
    CONF_COOL_AWAY,
    CONF_COOL_DAY,
    CONF_COOL_NIGHT,
    CONF_DEMO_ZONES,
    CONF_DOOR_DELAY,
    CONF_DOOR_SENSORS,
    CONF_GUEST,
    CONF_HEAT_AWAY,
    CONF_HEAT_DAY,
    CONF_HEAT_NIGHT,
    CONF_HUMIDITY_MAX,
    CONF_HUMIDITY_SENSOR,
    CONF_MAX_OPENING,
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
    CONF_SCAN_INTERVAL,
    CONF_SLEEP_ZONE,
    CONF_SOLAR_EXPORT_NEGATIVE,
    CONF_SOLAR_SENSOR,
    CONF_SOURCE_ID,
    CONF_SPILL,
    CONF_TEMP_SENSOR,
    CONF_TOKEN,
    CONF_UNIT_ID,
    CONF_WEATHER,
    CONF_WINDOW_DELAY,
    CONF_WINDOW_SENSORS,
    CONF_ZONE,
    DEFAULT_DOOR_DELAY,
    DEFAULT_PRESENCE_HOLD,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WINDOW_DELAY,
    DOMAIN,
    SUBENTRY_ZONE,
)
from .core.errors import AccountActionRequired, AuthFailed, HvacError
from .core.model import UnitInfo
from .logic.snapshot import ComfortSettings, Priority

_LOGGER = logging.getLogger(__name__)

CONF_REDIRECT_URL = "redirect_url"
CONF_ZONE_COUNT = "zone_count"
CONF_NAME = "name"
CONF_SLOT = "slot"


def zone_subentry(unit_id: str, zone: int, name: str) -> ConfigSubentryData:
    """Return the subentry data for one zone."""
    return ConfigSubentryData(
        subentry_type=SUBENTRY_ZONE,
        title=name,
        unique_id=f"{unit_id}_zone{zone}",
        data={CONF_UNIT_ID: unit_id, CONF_ZONE: zone},
    )


class CyborgHvacConfigFlow(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Set up a connection to a brand's cloud or controller."""

    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        """Start with an empty plan."""
        super().__init__()
        self._data: dict[str, Any] = {}
        self._units: list[UnitInfo] = []
        self._zone_units: list[UnitInfo] = []
        self._zone_counts: dict[str, int] = {}
        self._subentries: list[ConfigSubentryData] = []
        self._paste_state: str | None = None

    @property
    def logger(self) -> logging.Logger:
        """Return the flow logger."""
        return _LOGGER

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Smart comfort inputs and targets."""
        return CyborgHvacOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Zones are managed as subentries."""
        return {SUBENTRY_ZONE: ZoneSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a brand."""
        return self.async_show_menu(step_id="user", menu_options=[BRAND_HISENSE, BRAND_DEMO])

    # Hisense ConnectLife ------------------------------------------------------

    async def async_step_hisense_connectlife(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how to sign in to ConnectLife."""
        self._data = {CONF_BRAND: BRAND_HISENSE}
        return self.async_show_menu(
            step_id="hisense_connectlife",
            menu_options=["hisense_browser", "hisense_paste"],
        )

    async def async_step_hisense_browser(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in through the browser; ConnectLife redirects back to Home Assistant."""
        self._data.setdefault(CONF_BRAND, BRAND_HISENSE)
        self.flow_impl = hisense.async_get_implementation(self.hass)
        return await self.async_step_auth()

    async def async_step_hisense_paste(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in, then paste the address the browser ends on.

        Works whatever address Home Assistant has, because the redirect page
        does not need to load.
        """
        self._data.setdefault(CONF_BRAND, BRAND_HISENSE)
        implementation = hisense.async_get_implementation(self.hass)
        if self._paste_state is None:
            self._paste_state = secrets.token_urlsafe(16)
        errors: dict[str, str] = {}
        if user_input is not None:
            code = hisense.extract_code(user_input[CONF_REDIRECT_URL], self._paste_state)
            if code is None:
                errors["base"] = "invalid_redirect_url"
            else:
                try:
                    token = await implementation.async_exchange_code(code)
                except (OAuth2TokenRequestError, ClientError, KeyError, ValueError) as err:
                    _LOGGER.warning("ConnectLife sign-in failed: %s", err)
                    errors["base"] = "oauth_failed"
                else:
                    self.flow_impl = implementation
                    return await self.async_oauth_create_entry(
                        {"auth_implementation": implementation.domain, CONF_TOKEN: token}
                    )
        return self.async_show_form(
            step_id="hisense_paste",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REDIRECT_URL): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
                    )
                }
            ),
            description_placeholders={
                "url": implementation.paste_authorize_url(self._paste_state),
                "redirect": implementation.redirect_uri,
            },
            errors=errors,
        )

    async def async_oauth_create_entry(self, data: dict) -> ConfigFlowResult:
        """Signed in: store the token, then discover units (or finish reauth)."""
        self._data.update(data)
        self._data.setdefault(CONF_BRAND, BRAND_HISENSE)
        if self.source == SOURCE_REAUTH:
            # The entry's update listener only reloads for zone or option
            # changes, so reload explicitly with the new token.
            entry = self._get_reauth_entry()
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_TOKEN: data[CONF_TOKEN]}
            )
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_abort(reason="reauth_successful")
        self._data.setdefault(CONF_SOURCE_ID, new_source_id())
        return await self.async_step_discover()

    # Demo ---------------------------------------------------------------------

    async def async_step_demo(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Set up a simulated ducted unit, for trying the integration without hardware."""
        if user_input is not None:
            self._data = {CONF_BRAND: BRAND_DEMO, CONF_DEMO_ZONES: int(user_input[CONF_DEMO_ZONES])}
            return await self.async_step_discover()
        return self.async_show_form(
            step_id="demo",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEMO_ZONES, default=5): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=8, mode=selector.NumberSelectorMode.BOX
                        )
                    )
                }
            ),
        )

    # Shared -------------------------------------------------------------------

    async def async_step_discover(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Find the units on the account."""
        brand = BRANDS[self._data[CONF_BRAND]]
        backend = await brand.create_flow_backend(self.hass, self._data)
        try:
            units = await backend.async_discover()
        except AuthFailed:
            return self.async_abort(reason="invalid_auth")
        except AccountActionRequired as err:
            return self.async_abort(reason=f"account_{err.reason}")
        except HvacError as err:
            _LOGGER.warning("Could not list units: %s", err)
            return self.async_abort(reason="cannot_connect")
        finally:
            await backend.async_close()
        if not units:
            return self.async_abort(reason="no_units")

        if self._data[CONF_BRAND] == BRAND_DEMO:
            account_id = "demo"
        else:
            account_id = hisense.account_id_from_token(
                self._data[CONF_TOKEN], [u.unit_id for u in units]
            )
        self._data[CONF_ACCOUNT_ID] = account_id
        await self.async_set_unique_id(f"{self._data[CONF_BRAND]}_{account_id}")
        self._abort_if_unique_id_configured()

        self._units = units
        self._zone_units = [u for u in units if u.capabilities.zone_slots]
        if not self._zone_units:
            return self._async_finish()
        return await self.async_step_zones()

    async def async_step_zones(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask how many zones a unit has."""
        unit = self._zone_units[0]
        slots = unit.capabilities.zone_slots
        if user_input is not None:
            self._zone_counts[unit.unit_id] = int(user_input[CONF_ZONE_COUNT])
            if self._zone_counts[unit.unit_id] == 0:
                return await self._async_next_unit()
            return await self.async_step_zone_names()
        hint = unit.installed_zones_hint
        default = max(hint) if hint else slots
        return self.async_show_form(
            step_id="zones",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ZONE_COUNT, default=default): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=slots, mode=selector.NumberSelectorMode.BOX
                        )
                    )
                }
            ),
            description_placeholders={
                "unit": unit.name,
                "slots": str(slots),
                "detected": ", ".join(str(n) for n in hint) or "none",
            },
        )

    async def async_step_zone_names(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name each zone, as in the vendor app."""
        unit = self._zone_units[0]
        count = self._zone_counts[unit.unit_id]
        if user_input is not None:
            for zone in range(1, count + 1):
                name = str(user_input.get(f"zone_{zone}") or f"Zone {zone}").strip()
                self._subentries.append(zone_subentry(unit.unit_id, zone, name))
            return await self._async_next_unit()
        return self.async_show_form(
            step_id="zone_names",
            data_schema=vol.Schema(
                {
                    vol.Required(f"zone_{zone}", default=f"Zone {zone}"): selector.TextSelector()
                    for zone in range(1, count + 1)
                }
            ),
            description_placeholders={"unit": unit.name},
        )

    async def _async_next_unit(self) -> ConfigFlowResult:
        self._zone_units.pop(0)
        if self._zone_units:
            return await self.async_step_zones()
        return self._async_finish()

    @callback
    def _async_finish(self) -> ConfigFlowResult:
        title = self._units[0].name if len(self._units) == 1 else "ConnectLife"
        if self._data[CONF_BRAND] == BRAND_DEMO:
            title = "Cyborg HVAC demo"
        return self.async_create_entry(title=title, data=self._data, subentries=self._subentries)

    # Reauth -------------------------------------------------------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauth after the token stopped working."""
        self._data = {CONF_BRAND: entry_data.get(CONF_BRAND, BRAND_HISENSE)}
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to sign in again."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_hisense_connectlife()


ZONE_SETTINGS = (
    CONF_AREA,
    CONF_TEMP_SENSOR,
    CONF_HUMIDITY_SENSOR,
    CONF_PRESENCE_SENSORS,
    CONF_PRESENCE_HOLD,
    CONF_WINDOW_SENSORS,
    CONF_DOOR_SENSORS,
    CONF_OFFSET,
    CONF_PRIORITY,
    CONF_GUEST,
    CONF_SLEEP_ZONE,
    CONF_MIN_OPENING,
    CONF_MAX_OPENING,
    CONF_SPILL,
)


def _entity(domain: str, device_class: str | list[str] | None = None, multiple: bool = False):
    config = selector.EntitySelectorConfig(domain=domain, multiple=multiple)
    if device_class:
        config = selector.EntitySelectorConfig(
            domain=domain, device_class=device_class, multiple=multiple
        )
    return selector.EntitySelector(config)


def _percent() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=100, step=5, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX
        )
    )


def zone_settings_schema() -> dict:
    """Return the optional zone fields: room sensors and zoning preferences."""
    return {
        vol.Optional(CONF_AREA): selector.AreaSelector(),
        vol.Optional(CONF_TEMP_SENSOR): _entity("sensor", "temperature"),
        vol.Optional(CONF_HUMIDITY_SENSOR): _entity("sensor", "humidity"),
        vol.Optional(CONF_PRESENCE_SENSORS): _entity(
            "binary_sensor", ["occupancy", "presence", "motion"], multiple=True
        ),
        vol.Optional(CONF_PRESENCE_HOLD, default=DEFAULT_PRESENCE_HOLD): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=60,
                step=1,
                unit_of_measurement="min",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(CONF_WINDOW_SENSORS): _entity(
            "binary_sensor", ["window", "opening"], multiple=True
        ),
        vol.Optional(CONF_DOOR_SENSORS): _entity(
            "binary_sensor", ["door", "garage_door", "opening"], multiple=True
        ),
        vol.Optional(CONF_OFFSET, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=-3,
                max=3,
                step=0.5,
                unit_of_measurement="°C",
                mode=selector.NumberSelectorMode.SLIDER,
            )
        ),
        vol.Optional(CONF_PRIORITY, default=Priority.LIVING.value): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[p.value for p in Priority], translation_key="priority"
            )
        ),
        vol.Optional(CONF_GUEST, default=False): selector.BooleanSelector(),
        vol.Optional(CONF_SLEEP_ZONE, default=False): selector.BooleanSelector(),
        vol.Optional(CONF_MIN_OPENING, default=0): _percent(),
        vol.Optional(CONF_MAX_OPENING, default=100): _percent(),
        vol.Optional(CONF_SPILL, default=False): selector.BooleanSelector(),
    }


def zone_settings(user_input: dict) -> dict:
    """Keep only the zone settings that were filled in."""
    data = {}
    for key in ZONE_SETTINGS:
        value = user_input.get(key)
        if value in (None, "", []):
            continue
        if key in (CONF_MIN_OPENING, CONF_MAX_OPENING, CONF_PRESENCE_HOLD):
            value = int(value)
        data[key] = value
    return data


class ZoneSubentryFlow(ConfigSubentryFlow):
    """Add or edit a zone."""

    def _unit_options(self) -> list[selector.SelectOptionDict]:
        entry = self._get_entry()
        runtime = getattr(entry, "runtime_data", None)
        if runtime is None:
            return []
        taken = {
            (sub.data.get(CONF_UNIT_ID), int(sub.data.get(CONF_ZONE, 0)))
            for sub in entry.subentries.values()
            if sub.subentry_type == SUBENTRY_ZONE
        }
        options = []
        for unit in runtime.coordinator.units.values():
            for zone in range(1, unit.capabilities.zone_slots + 1):
                if (unit.unit_id, zone) not in taken:
                    options.append(
                        selector.SelectOptionDict(
                            value=f"{unit.unit_id}|{zone}", label=f"{unit.name}: zone {zone}"
                        )
                    )
        return options

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a zone in a free slot."""
        options = self._unit_options()
        if not options:
            return self.async_abort(reason="no_free_slots")
        if user_input is not None:
            unit_id, _, zone = user_input[CONF_SLOT].rpartition("|")
            data = {CONF_UNIT_ID: unit_id, CONF_ZONE: int(zone), **zone_settings(user_input)}
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data=data,
                unique_id=f"{unit_id}_zone{zone}",
            )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SLOT): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    ),
                    vol.Required(CONF_NAME): selector.TextSelector(),
                    **zone_settings_schema(),
                }
            ),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Rename a zone or change its area."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            data = {
                CONF_UNIT_ID: subentry.data[CONF_UNIT_ID],
                CONF_ZONE: subentry.data[CONF_ZONE],
                **zone_settings(user_input),
            }
            return self.async_update_and_abort(
                entry, subentry, title=user_input[CONF_NAME], data=data
            )
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=subentry.title): selector.TextSelector(),
                **zone_settings_schema(),
            }
        )
        suggested = {k: v for k, v in subentry.data.items() if k in ZONE_SETTINGS}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
        )


def _temp(default: float) -> vol.Any:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=10,
            max=32,
            step=0.5,
            unit_of_measurement="°C",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _minutes(maximum: int) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0,
            max=maximum,
            step=1,
            unit_of_measurement="min",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


class CyborgHvacOptionsFlow(OptionsFlow):
    """House inputs for smart comfort, comfort targets, and polling."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick what to change."""
        return self.async_show_menu(step_id="init", menu_options=["inputs", "comfort"])

    def _save(self, user_input: dict[str, Any], keys: tuple[str, ...]) -> ConfigFlowResult:
        options = {k: v for k, v in self.config_entry.options.items() if k not in keys}
        options.update({k: v for k, v in user_input.items() if v not in (None, "", [])})
        return self.async_create_entry(data=options)

    async def async_step_inputs(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Outdoor, weather, people and solar inputs."""
        keys = (
            CONF_OUTDOOR_SENSOR,
            CONF_WEATHER,
            CONF_PEOPLE,
            CONF_SOLAR_SENSOR,
            CONF_SOLAR_EXPORT_NEGATIVE,
            CONF_WINDOW_DELAY,
            CONF_DOOR_DELAY,
            CONF_MIN_ZONES,
            CONF_MIN_TOTAL,
            CONF_SCAN_INTERVAL,
        )
        if user_input is not None:
            return self._save(user_input, keys)
        schema = vol.Schema(
            {
                vol.Optional(CONF_OUTDOOR_SENSOR): _entity("sensor", "temperature"),
                vol.Optional(CONF_WEATHER): _entity("weather"),
                vol.Optional(CONF_PEOPLE): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["person", "device_tracker", "binary_sensor"], multiple=True
                    )
                ),
                vol.Optional(CONF_SOLAR_SENSOR): _entity("sensor", "power"),
                vol.Optional(CONF_SOLAR_EXPORT_NEGATIVE, default=True): selector.BooleanSelector(),
                vol.Optional(CONF_WINDOW_DELAY, default=DEFAULT_WINDOW_DELAY): _minutes(30),
                vol.Optional(CONF_DOOR_DELAY, default=DEFAULT_DOOR_DELAY): _minutes(60),
                vol.Optional(CONF_MIN_ZONES, default=2): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=4, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(CONF_MIN_TOTAL, default=150): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=50,
                        max=400,
                        step=10,
                        unit_of_measurement="%",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): (
                    selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=15,
                            max=300,
                            step=5,
                            unit_of_measurement="s",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="inputs",
            data_schema=self.add_suggested_values_to_schema(schema, self.config_entry.options),
        )

    async def async_step_comfort(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Comfort targets for day, night and away."""
        base = ComfortSettings()
        defaults = {
            CONF_HEAT_DAY: base.heat_day,
            CONF_HEAT_NIGHT: base.heat_night,
            CONF_HEAT_AWAY: base.heat_away,
            CONF_COOL_DAY: base.cool_day,
            CONF_COOL_NIGHT: base.cool_night,
            CONF_COOL_AWAY: base.cool_away,
        }
        keys = (*defaults, CONF_HUMIDITY_MAX, CONF_NIGHT_START, CONF_NIGHT_END)
        errors: dict[str, str] = {}
        if user_input is not None:
            for period in ("day", "night", "away"):
                if float(user_input[f"heat_{period}"]) >= float(user_input[f"cool_{period}"]):
                    errors["base"] = "band_order"
            if not errors:
                return self._save(user_input, keys)
        fields: dict = {
            vol.Required(key, default=value): _temp(value) for key, value in defaults.items()
        }
        fields[vol.Required(CONF_HUMIDITY_MAX, default=base.humidity_max)] = (
            selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=40,
                    max=90,
                    step=1,
                    unit_of_measurement="%",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
        )
        fields[vol.Required(CONF_NIGHT_START, default=base.night_start.isoformat())] = (
            selector.TimeSelector()
        )
        fields[vol.Required(CONF_NIGHT_END, default=base.night_end.isoformat())] = (
            selector.TimeSelector()
        )
        return self.async_show_form(
            step_id="comfort",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(fields), user_input or self.config_entry.options
            ),
            errors=errors,
        )
