"""Services: apply or dismiss a recommendation."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import DOMAIN

ATTR_RECOMMENDATION = "recommendation"
ATTR_UNIT_ID = "unit_id"
ATTR_ENTRY_ID = "entry_id"
ATTR_MINUTES = "minutes"

SERVICE_APPLY = "apply_recommendation"
SERVICE_DISMISS = "dismiss_recommendation"
SERVICE_PAUSE = "pause_autopilot"
SERVICE_RESUME = "resume_autopilot"

_BASE = {
    vol.Required(ATTR_RECOMMENDATION): cv.string,
    vol.Optional(ATTR_UNIT_ID): cv.string,
    vol.Optional(ATTR_ENTRY_ID): cv.string,
}


def _managers(hass: HomeAssistant, entry_id: str | None) -> list:
    managers = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry_id and entry.entry_id != entry_id:
            continue
        if entry.state is ConfigEntryState.LOADED:
            comfort = getattr(entry.runtime_data, "comfort", None)
            if comfort is not None:
                managers.append(comfort)
    return managers


def _owner(hass: HomeAssistant, call: ServiceCall):
    rec_id = call.data[ATTR_RECOMMENDATION]
    unit_id = call.data.get(ATTR_UNIT_ID)
    for manager in _managers(hass, call.data.get(ATTR_ENTRY_ID)):
        for uid, recs in manager.advice.items():
            if (unit_id is None or uid == unit_id) and any(r.id == rec_id for r in recs):
                return manager, uid
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="recommendation_not_found",
        translation_placeholders={"id": rec_id},
    )


def async_register(hass: HomeAssistant) -> None:
    """Register the services once."""

    async def apply(call: ServiceCall) -> None:
        manager, unit_id = _owner(hass, call)
        await manager.async_apply(call.data[ATTR_RECOMMENDATION], unit_id)

    async def dismiss(call: ServiceCall) -> None:
        manager, unit_id = _owner(hass, call)
        await manager.async_dismiss(
            call.data[ATTR_RECOMMENDATION], unit_id, call.data.get(ATTR_MINUTES)
        )

    hass.services.async_register(DOMAIN, SERVICE_APPLY, apply, schema=vol.Schema(_BASE))
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISMISS,
        dismiss,
        schema=vol.Schema(
            {**_BASE, vol.Optional(ATTR_MINUTES): vol.All(vol.Coerce(int), vol.Range(1, 10080))}
        ),
    )

    unit_schema = {vol.Optional(ATTR_UNIT_ID): cv.string, vol.Optional(ATTR_ENTRY_ID): cv.string}

    async def pause(call: ServiceCall) -> None:
        for manager in _managers(hass, call.data.get(ATTR_ENTRY_ID)):
            await manager.async_pause(call.data.get(ATTR_UNIT_ID), call.data[ATTR_MINUTES])

    async def resume(call: ServiceCall) -> None:
        for manager in _managers(hass, call.data.get(ATTR_ENTRY_ID)):
            await manager.async_resume(call.data.get(ATTR_UNIT_ID))

    hass.services.async_register(
        DOMAIN,
        SERVICE_PAUSE,
        pause,
        schema=vol.Schema(
            {
                **unit_schema,
                vol.Optional(ATTR_MINUTES, default=120): vol.All(
                    vol.Coerce(int), vol.Range(1, 1440)
                ),
            }
        ),
    )
    hass.services.async_register(DOMAIN, SERVICE_RESUME, resume, schema=vol.Schema(unit_schema))
