"""Damper cover for each zone: open or closed, and how far open."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CyborgRuntimeData
from .core.model import Command, ZoneCommand
from .entity import CyborgZoneEntity, zone_subentries


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[CyborgRuntimeData],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a damper for every configured zone."""
    coordinator = entry.runtime_data.coordinator
    for subentry in zone_subentries(coordinator):
        async_add_entities(
            [ZoneDamper(coordinator, subentry)], config_subentry_id=subentry.subentry_id
        )


class ZoneDamper(CyborgZoneEntity, CoverEntity):
    """A zone's damper. Closed means the zone is off."""

    _attr_name = None
    _attr_device_class = CoverDeviceClass.DAMPER
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, coordinator, subentry) -> None:
        """Bind to the zone."""
        super().__init__(coordinator, subentry, "damper")

    @property
    def is_closed(self) -> bool | None:
        """Return True when the zone is off."""
        zone = self.zone_state
        return None if zone is None else not zone.on

    @property
    def current_cover_position(self) -> int | None:
        """Return the opening while on, 0 while off."""
        zone = self.zone_state
        if zone is None:
            return None
        return zone.opening if zone.on else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the opening the zone returns to when switched on."""
        zone = self.zone_state
        return {"opening": zone.opening} if zone else {}

    def _open_command(self) -> ZoneCommand:
        zone = self.zone_state
        if zone is not None and zone.opening > 0:
            return ZoneCommand(on=True)
        return ZoneCommand(on=True, opening=self.caps.zone_opening_max)

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Switch the zone on at its last opening."""
        await self.async_send(Command(zones={self.zone: self._open_command()}))

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Switch the zone off, keeping its opening for next time."""
        await self.async_send(Command(zones={self.zone: ZoneCommand(on=False)}))

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Set the opening. 0 switches the zone off."""
        position = int(kwargs[ATTR_POSITION])
        if position <= 0:
            command = ZoneCommand(on=False)
        else:
            opening = self.caps.round_opening(position)
            if opening <= 0:
                opening = max(1, self.caps.zone_opening_step)
            command = ZoneCommand(on=True, opening=opening)
        await self.async_send(Command(zones={self.zone: command}))
