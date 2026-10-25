"""Base entities for units and zones."""

from __future__ import annotations

from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_AREA, CONF_UNIT_ID, CONF_ZONE, DOMAIN, SUBENTRY_ZONE
from .coordinator import HvacCoordinator
from .core.model import Capabilities, Command, UnitInfo, UnitState, ZoneState

# Home Assistant 2026.10 links child devices by device id; older versions by identifiers.
_VIA_DEVICE_ID = "via_device_id" in (DeviceInfo.__required_keys__ | DeviceInfo.__optional_keys__)


def unit_device_info(info: UnitInfo) -> DeviceInfo:
    """Return the device registry entry for a unit."""
    return DeviceInfo(
        identifiers={(DOMAIN, info.unit_id)},
        name=info.name,
        manufacturer=info.manufacturer,
        model=info.model,
        model_id=info.model_id,
        sw_version=info.sw_version,
    )


class CyborgEntity(CoordinatorEntity[HvacCoordinator]):
    """An entity that belongs to a unit."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HvacCoordinator, unit_id: str, key: str) -> None:
        """Bind to a unit."""
        super().__init__(coordinator)
        self._unit_id = unit_id
        self._attr_unique_id = f"{unit_id}_{key}"
        self._attr_device_info = unit_device_info(self.info)

    @property
    def info(self) -> UnitInfo:
        """Return the unit's static description."""
        return self.coordinator.units[self._unit_id]

    @property
    def caps(self) -> Capabilities:
        """Return the unit's capabilities."""
        return self.info.capabilities

    @property
    def unit_state(self) -> UnitState | None:
        """Return the unit's latest state."""
        return self.coordinator.state(self._unit_id)

    @property
    def available(self) -> bool:
        """Return True while the unit reports in."""
        state = self.unit_state
        return super().available and state is not None and state.online

    async def async_send(self, command: Command) -> None:
        """Send a command to this unit."""
        await self.coordinator.async_send(self._unit_id, command)


class CyborgZoneEntity(CyborgEntity):
    """An entity that belongs to one zone of a unit."""

    def __init__(self, coordinator: HvacCoordinator, subentry: ConfigSubentry, key: str) -> None:
        """Bind to a zone described by a config subentry."""
        unit_id = subentry.data[CONF_UNIT_ID]
        self._zone = int(subentry.data[CONF_ZONE])
        self._subentry = subentry
        super().__init__(coordinator, unit_id, f"zone{self._zone}_{key}")
        area = None
        if area_id := subentry.data.get(CONF_AREA):
            entry = ar.async_get(coordinator.hass).async_get_area(area_id)
            area = entry.name if entry else None
        device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{unit_id}_zone{self._zone}")},
            name=subentry.title,
            manufacturer=self.info.manufacturer,
            model="Zone",
            suggested_area=area,
        )
        parent_id = coordinator.device_ids.get(unit_id)
        if _VIA_DEVICE_ID and parent_id:
            device_info["via_device_id"] = parent_id
        else:
            device_info["via_device"] = (DOMAIN, unit_id)  # type: ignore[typeddict-unknown-key]
        self._attr_device_info = device_info

    @property
    def zone(self) -> int:
        """Return the zone number."""
        return self._zone

    @property
    def zone_state(self) -> ZoneState | None:
        """Return the zone's latest state."""
        state = self.unit_state
        return state.zones.get(self._zone) if state else None

    @property
    def available(self) -> bool:
        """Return True while the unit reports this zone."""
        return super().available and self.zone_state is not None


def zone_subentries(coordinator: HvacCoordinator) -> list[ConfigSubentry]:
    """Return zone subentries whose unit is loaded."""
    return [
        subentry
        for subentry in coordinator.config_entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_ZONE
        and subentry.data.get(CONF_UNIT_ID) in coordinator.units
    ]
