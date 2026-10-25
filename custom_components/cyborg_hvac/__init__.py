"""Cyborg HVAC: ducted air conditioners with zone control."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .backends.hisense_connectlife import adapter as hisense_adapter
from .brands import get_brand
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN, PLATFORMS
from .coordinator import CyborgRuntimeData, HvacCoordinator
from .core.errors import AuthFailed, HvacError
from .entity import unit_device_info

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type CyborgConfigEntry = ConfigEntry[CyborgRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the ConnectLife sign-in so existing entries can refresh tokens."""
    hisense_adapter.async_get_implementation(hass)
    return True


def _reload_snapshot(entry: ConfigEntry) -> object:
    """Return what, when changed, needs a reload (not token refreshes)."""
    return (
        dict(entry.options),
        {sid: (sub.title, dict(sub.data)) for sid, sub in entry.subentries.items()},
    )


async def async_setup_entry(hass: HomeAssistant, entry: CyborgConfigEntry) -> bool:
    """Connect, discover units and start the coordinator."""
    backend = await get_brand(entry.data).create_backend(hass, entry)
    try:
        units = await backend.async_discover()
    except AuthFailed as err:
        await backend.async_close()
        raise ConfigEntryAuthFailed(str(err)) from err
    except HvacError as err:
        await backend.async_close()
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = HvacCoordinator(
        hass,
        entry,
        backend,
        units,
        int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await backend.async_close()
        raise

    device_registry = dr.async_get(hass)
    for unit in units:
        device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id, **unit_device_info(unit)
        )
        coordinator.device_ids[unit.unit_id] = device.id

    entry.runtime_data = CyborgRuntimeData(
        backend=backend,
        coordinator=coordinator,
        reload_snapshot=_reload_snapshot(entry),
    )
    try:
        if await backend.async_start_push(coordinator.async_on_push):
            _LOGGER.debug("Push updates active for %s", entry.title)
    except Exception:
        # Push is optional; polling keeps everything working without it.
        _LOGGER.exception("Push updates failed to start for %s; polling only", entry.title)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: CyborgConfigEntry) -> None:
    """Reload when zones or options change; ignore token refreshes."""
    if _reload_snapshot(entry) != entry.runtime_data.reload_snapshot:
        hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CyborgConfigEntry) -> bool:
    """Unload platforms and close the backend."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.coordinator.async_shutdown()
    return unloaded
