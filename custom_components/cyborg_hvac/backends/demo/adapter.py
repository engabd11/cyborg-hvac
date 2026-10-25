"""Home Assistant glue for the demo backend."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from ...const import CONF_DEMO_SPEED, CONF_DEMO_ZONES
from .backend import DemoBackend


async def async_create_backend(hass: HomeAssistant, entry: ConfigEntry) -> DemoBackend:
    """Build the simulation for a config entry."""
    return DemoBackend(
        zone_count=int(entry.data.get(CONF_DEMO_ZONES, 5)),
        speed=float(entry.data.get(CONF_DEMO_SPEED, 1.0)),
    )


async def async_create_flow_backend(hass: HomeAssistant, data: dict[str, Any]) -> DemoBackend:
    """Build a throwaway simulation for the config flow."""
    return DemoBackend(zone_count=int(data.get(CONF_DEMO_ZONES, 5)))
