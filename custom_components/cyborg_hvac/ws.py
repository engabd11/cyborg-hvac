"""Websocket API for the Cyborg HVAC card."""

from __future__ import annotations

from typing import Any

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
import voluptuous as vol

from .const import DOMAIN

SIGNAL_UPDATE = f"{DOMAIN}_comfort_update_{{}}"


def signal_update(entry_id: str) -> str:
    """Return the dispatcher signal sent when an entry's comfort data changes."""
    return SIGNAL_UPDATE.format(entry_id)


def _loaded_entries(hass: HomeAssistant) -> list:
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED and getattr(entry, "runtime_data", None)
    ]


def _resolve(
    hass: HomeAssistant, entry_id: str | None, unit_id: str | None
) -> tuple[str, str] | None:
    """Return (entry_id, unit_id), defaulting to the first loaded unit."""
    for entry in _loaded_entries(hass):
        if entry_id and entry.entry_id != entry_id:
            continue
        units = entry.runtime_data.coordinator.units
        if unit_id and unit_id in units:
            return entry.entry_id, unit_id
        if not unit_id and units:
            return entry.entry_id, next(iter(units))
    return None


def payload(hass: HomeAssistant, entry_id: str, unit_id: str) -> dict[str, Any] | None:
    """Return the card snapshot for a unit, if its entry is loaded."""
    entry = hass.config_entries.async_get_entry(entry_id)
    runtime = getattr(entry, "runtime_data", None) if entry else None
    comfort = getattr(runtime, "comfort", None)
    if comfort is None or entry is None or entry.state is not ConfigEntryState.LOADED:
        return None
    return comfort.as_dict(unit_id)


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the websocket commands once."""
    websocket_api.async_register_command(hass, ws_list)
    websocket_api.async_register_command(hass, ws_subscribe)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/list"})
@callback
def ws_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """List loaded entries and their units."""
    result = []
    for entry in _loaded_entries(hass):
        coordinator = entry.runtime_data.coordinator
        result.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "units": [
                    {
                        "unit_id": unit_id,
                        "name": info.name,
                        "device_id": coordinator.device_ids.get(unit_id),
                    }
                    for unit_id, info in coordinator.units.items()
                ],
            }
        )
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/subscribe",
        vol.Optional("entry_id"): str,
        vol.Optional("unit_id"): str,
    }
)
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Stream a unit's snapshot whenever it changes; survives entry reloads."""
    target = _resolve(hass, msg.get("entry_id"), msg.get("unit_id"))
    if target is None:
        connection.send_error(msg["id"], "not_found", "No Cyborg HVAC unit found")
        return
    entry_id, unit_id = target

    @callback
    def forward() -> None:
        data = payload(hass, entry_id, unit_id)
        if data is not None:
            connection.send_message(websocket_api.event_message(msg["id"], data))

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(
        hass, signal_update(entry_id), forward
    )
    connection.send_result(msg["id"])
    forward()
