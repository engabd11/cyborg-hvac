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
    CONF_DEMO_ZONES,
    CONF_SOURCE_ID,
    CONF_TOKEN,
    CONF_UNIT_ID,
    CONF_ZONE,
    DOMAIN,
    SUBENTRY_ZONE,
)
from .core.errors import AccountActionRequired, AuthFailed, HvacError
from .core.model import UnitInfo

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
            data = {CONF_UNIT_ID: unit_id, CONF_ZONE: int(zone)}
            if area := user_input.get(CONF_AREA):
                data[CONF_AREA] = area
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
                    vol.Optional(CONF_AREA): selector.AreaSelector(),
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
            data = dict(subentry.data)
            data.pop(CONF_AREA, None)
            if area := user_input.get(CONF_AREA):
                data[CONF_AREA] = area
            return self.async_update_and_abort(
                entry, subentry, title=user_input[CONF_NAME], data=data
            )
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=subentry.title): selector.TextSelector(),
                vol.Optional(CONF_AREA): selector.AreaSelector(),
            }
        )
        suggested = (
            {CONF_AREA: subentry.data.get(CONF_AREA)} if subentry.data.get(CONF_AREA) else {}
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
        )
