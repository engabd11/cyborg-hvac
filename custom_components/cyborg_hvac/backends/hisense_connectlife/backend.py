"""Hisense ConnectLife backend."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
import logging
from typing import Any

import aiohttp

from ...core.backend import HvacBackend, PushCallback
from ...core.errors import CannotConnect, CommandRejected, HvacError
from ...core.model import FAN_SILENT, Command, Feature, UnitInfo, UnitState
from .client import ConnectLifeClient
from .mapping import (
    apply_properties,
    build_capabilities,
    build_properties,
    effective_fan_map,
    installed_zones_hint,
    parse_status,
)
from .profiles import SUPPORTED_TYPES, Profile, get_profile, parse_property_list
from .push import ConnectLifePush, PushMessage, TaskFactory

_LOGGER = logging.getLogger(__name__)

BRAND = "hisense_connectlife"
MANUFACTURER = "Hisense"


@dataclass(slots=True)
class _Unit:
    info: UnitInfo
    profile: Profile
    fan_map: dict[str, str]
    status: dict[str, str] = field(default_factory=dict)
    online: bool = True


def _is_online(device: dict[str, Any]) -> bool:
    # In the REST device list, offlineState 1 means the device is online.
    return str(device.get("offlineState", "1")) == "1"


class HisenseConnectLifeBackend(HvacBackend):
    """Units on one ConnectLife account."""

    def __init__(
        self,
        client: ConnectLifeClient,
        session: aiohttp.ClientSession,
        task_factory: TaskFactory | None = None,
    ) -> None:
        """Create the backend around an authenticated client."""
        self._client = client
        self._session = session
        self._task_factory = task_factory
        self._units: dict[str, _Unit] = {}
        self._push: ConnectLifePush | None = None
        self._push_callback: PushCallback | None = None

    @property
    def client(self) -> ConnectLifeClient:
        """Return the REST client."""
        return self._client

    @property
    def push_connected(self) -> bool:
        """Return True while the push channel is connected."""
        return bool(self._push and self._push.connected)

    async def async_discover(self) -> list[UnitInfo]:
        """Find supported units and work out their capabilities."""
        devices = await self._client.async_get_devices()
        units: list[UnitInfo] = []
        for device in devices:
            type_code = str(device.get("deviceTypeCode", ""))
            feature_code = str(device.get("deviceFeatureCode", ""))
            puid = device.get("puid")
            if type_code not in SUPPORTED_TYPES or not puid:
                continue
            profile = get_profile(type_code, feature_code)
            status = {k: str(v) for k, v in (device.get("statusList") or {}).items()}
            try:
                meta = parse_property_list(
                    await self._client.async_get_property_list(type_code, feature_code)
                )
            except CannotConnect as err:
                _LOGGER.warning("No property list for %s, using status keys: %s", puid, err)
                meta = {}
            try:
                static = await self._client.async_query_static_data(puid)
            except CannotConnect as err:
                _LOGGER.debug("No static data for %s: %s", puid, err)
                static = {}

            caps = build_capabilities(profile, meta, status, static)
            fan_map = effective_fan_map(profile, meta, static)
            info = UnitInfo(
                unit_id=str(puid),
                name=str(device.get("deviceNickName") or "Air conditioner"),
                brand=BRAND,
                manufacturer=MANUFACTURER,
                model=profile.model or "Air conditioner",
                model_id=f"{type_code}-{feature_code}",
                room=device.get("roomName") or None,
                capabilities=caps,
                installed_zones_hint=installed_zones_hint(status, caps.zone_slots),
            )
            self._units[info.unit_id] = _Unit(
                info=info,
                profile=profile,
                fan_map=fan_map,
                status=status,
                online=_is_online(device),
            )
            units.append(info)
        return units

    async def async_fetch(self) -> dict[str, UnitState]:
        """Poll every unit's state in one request."""
        devices = await self._client.async_get_devices()
        seen: set[str] = set()
        for device in devices:
            unit = self._units.get(str(device.get("puid")))
            if unit is None:
                continue
            seen.add(unit.info.unit_id)
            unit.status = {k: str(v) for k, v in (device.get("statusList") or {}).items()}
            unit.online = _is_online(device)
        for unit_id, unit in self._units.items():
            if unit_id not in seen:
                unit.online = False
        return {unit_id: self._state(unit) for unit_id, unit in self._units.items()}

    def _state(self, unit: _Unit) -> UnitState:
        return parse_status(
            unit.profile,
            unit.info.capabilities,
            unit.status,
            online=unit.online,
            fan_map=unit.fan_map,
        )

    async def async_send(self, unit_id: str, command: Command) -> None:
        """Write a command as one property update."""
        unit = self._units.get(unit_id)
        if unit is None:
            raise CannotConnect(f"Unknown unit {unit_id}")
        props = build_properties(unit.profile, unit.info.capabilities, command, unit.fan_map)
        if not props:
            return
        _LOGGER.debug("ConnectLife write %s: %s", unit_id, props)
        await self._client.async_set_properties(unit_id, props)
        unit.status = apply_properties(unit.status, props)

    def validate(self, info: UnitInfo, state: UnitState, command: Command) -> None:
        """Add ConnectLife rules to the generic feature checks."""
        super().validate(info, state, command)
        caps = info.capabilities
        if command.fan_speed is not None and command.fan_speed not in caps.fan_speeds:
            raise CommandRejected("fan_speed_unsupported", "Unsupported fan speed")
        quiet = caps.features.get(Feature.QUIET)
        if command.fan_speed == FAN_SILENT and quiet is not None:
            result = command.apply_to(state)
            if not quiet.accepts(result.power, result.mode):
                raise CommandRejected("feature_not_available", "Quiet is not available now")

    async def async_start_push(self, callback: PushCallback) -> bool:
        """Connect the push channel."""
        self._push_callback = callback
        self._push = ConnectLifePush(
            self._client, self._session, self._handle_push, self._task_factory
        )
        return await self._push.async_start()

    def _handle_push(self, message: PushMessage) -> None:
        unit = self._units.get(message.puid)
        if unit is None or self._push_callback is None:
            return
        if message.online is not None:
            unit.online = message.online
        if message.properties:
            unit.status = apply_properties(unit.status, message.properties)
        self._push_callback(unit.info.unit_id, self._state(unit))

    async def async_close(self) -> None:
        """Stop the push channel."""
        if self._push is not None:
            with contextlib.suppress(HvacError):
                await self._push.async_stop()
            self._push = None

    def raw_status(self, unit_id: str) -> dict[str, str]:
        """Return the last raw status, for diagnostics."""
        unit = self._units.get(unit_id)
        return dict(unit.status) if unit else {}
