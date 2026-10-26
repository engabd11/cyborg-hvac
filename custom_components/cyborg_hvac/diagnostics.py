"""Diagnostics: units, capabilities and raw state, with identifiers redacted."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import CyborgConfigEntry
from .const import CONF_ACCOUNT_ID, CONF_SOURCE_ID, CONF_TOKEN

TO_REDACT = {
    CONF_TOKEN,
    CONF_SOURCE_ID,
    CONF_ACCOUNT_ID,
    "unit_id",
    "puid",
    "deviceId",
    "wifiId",
    "access_token",
    "refresh_token",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CyborgConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data.coordinator
    comfort = entry.runtime_data.comfort
    units = []
    for index, (unit_id, info) in enumerate(coordinator.units.items(), start=1):
        state = coordinator.state(unit_id)
        trend = comfort.trends.get(unit_id) if comfort else None
        units.append(
            {
                "unit": index,
                "info": async_redact_data(asdict(info), TO_REDACT),
                "state": async_redact_data(
                    {k: v for k, v in asdict(state).items() if k != "raw"}, TO_REDACT
                )
                if state
                else None,
                "raw_status": async_redact_data(dict(state.raw), TO_REDACT) if state else None,
                # What autopilot is doing and why, to explain its choices in a bug report.
                "autopilot": comfort.autopilot_status(unit_id) if comfort else None,
                "energy": comfort.energy_status(unit_id) if comfort else None,
                # How fast each room is moving now, in °C an hour.
                "trend": {str(n): trend.rate(n) for n in trend.rooms} if trend else {},
                # What was learned about each room: loss, own warmth by day and night.
                "model": comfort.models[unit_id].as_dict()
                if comfort and unit_id in comfort.models
                else None,
                "journal": [e.as_dict() for e in comfort.journal(unit_id).recent(40)]
                if comfort
                else [],
            }
        )
    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
            "zones": [
                {"title": sub.title, "zone": sub.data.get("zone")}
                for sub in entry.subentries.values()
            ],
        },
        "push_connected": getattr(entry.runtime_data.backend, "push_connected", None),
        "last_update_success": coordinator.last_update_success,
        "units": units,
    }
