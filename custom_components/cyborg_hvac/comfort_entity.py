"""Entities driven by the comfort manager rather than the unit's own state."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_PRESENCE_SENSORS
from .entity import CyborgEntity, CyborgZoneEntity


class _ComfortMixin:
    """Refresh when the comfort manager publishes new advice."""

    @property
    def comfort(self):
        return self.coordinator.config_entry.runtime_data.comfort

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.comfort.async_add_listener(self.async_write_ha_state))


class AdviceSensor(_ComfortMixin, CyborgEntity, SensorEntity):
    """How many recommendations are open, with the full list as attributes."""

    _attr_translation_key = "advice"

    def __init__(self, coordinator, unit_id: str) -> None:
        """Bind to a unit."""
        super().__init__(coordinator, unit_id, "advice")

    @property
    def available(self) -> bool:
        """Advice stays available while the unit is offline, to say so."""
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> int:
        """Return the number of open recommendations."""
        return len(self.comfort.advice.get(self._unit_id, []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the top recommendation and the full list."""
        recs = self.comfort.advice.get(self._unit_id, [])
        return {
            "top": recs[0].title if recs else None,
            "recommendations": [rec.as_dict() for rec in recs],
        }


class GuestModeSwitch(_ComfortMixin, CyborgEntity, SwitchEntity, RestoreEntity):
    """Focus comfort on guest zones (living and dining), others second."""

    _attr_translation_key = "guest_mode"

    def __init__(self, coordinator, unit_id: str) -> None:
        """Bind to a unit."""
        super().__init__(coordinator, unit_id, "guest_mode")

    async def async_added_to_hass(self) -> None:
        """Restore the last state."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == "on":
            await self.comfort.async_set_guest_mode(self._unit_id, True)

    @property
    def available(self) -> bool:
        """Guest mode can be set even while the unit is offline."""
        return True

    @property
    def is_on(self) -> bool:
        """Return True while guest mode is on."""
        return self.comfort.guest_modes.get(self._unit_id, False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn guest mode on."""
        await self.comfort.async_set_guest_mode(self._unit_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn guest mode off."""
        await self.comfort.async_set_guest_mode(self._unit_id, False)


class ZoneOccupiedSensor(_ComfortMixin, CyborgZoneEntity, BinarySensorEntity):
    """Whether anyone is in the zone, merged from its presence sensors."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_translation_key = "zone_occupied"

    def __init__(self, coordinator, subentry) -> None:
        """Bind to the zone."""
        super().__init__(coordinator, subentry, "occupied")

    @property
    def available(self) -> bool:
        """Available while presence is known."""
        return self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        """Return the merged presence."""
        snapshot = self.comfort.snapshots.get(self._unit_id)
        if snapshot is None:
            return None
        zone = next((z for z in snapshot.zones if z.zone == self.zone), None)
        return zone.occupied if zone else None


def has_presence(subentry) -> bool:
    """Return True when a zone has presence sensors configured."""
    return bool(subentry.data.get(CONF_PRESENCE_SENSORS))
