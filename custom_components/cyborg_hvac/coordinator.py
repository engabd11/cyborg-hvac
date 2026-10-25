"""Coordinator: polls and pushes state, and sends commands in batches."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    COALESCE_SECONDS,
    CONFIRM_REFRESH_SECONDS,
    DOMAIN,
    OVERLAY_SECONDS,
)
from .core.backend import HvacBackend
from .core.errors import (
    AccountActionRequired,
    AuthFailed,
    CommandRejected,
    HvacError,
)
from .core.model import Command, UnitInfo, UnitState

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _Pending:
    command: Command
    future: asyncio.Future[None]
    cancel: CALLBACK_TYPE | None = None


@dataclass(slots=True)
class _Overlay:
    command: Command
    expires: float


@dataclass(slots=True)
class CyborgRuntimeData:
    """Everything a loaded entry holds."""

    backend: HvacBackend
    coordinator: HvacCoordinator
    reload_snapshot: object = None
    comfort: Any = None
    extras: dict = field(default_factory=dict)


class HvacCoordinator(DataUpdateCoordinator[dict[str, UnitState]]):
    """Keeps unit state fresh and turns entity actions into backend commands."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        backend: HvacBackend,
        units: list[UnitInfo],
        scan_interval: int,
    ) -> None:
        """Create the coordinator for one config entry."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.backend = backend
        self.units: dict[str, UnitInfo] = {unit.unit_id: unit for unit in units}
        # Device registry ids of the units, so zone devices can link to them.
        self.device_ids: dict[str, str] = {}
        self._pending: dict[str, _Pending] = {}
        self._overlays: dict[str, _Overlay] = {}
        self._confirm_cancel: CALLBACK_TYPE | None = None

    async def _async_update_data(self) -> dict[str, UnitState]:
        try:
            states = await self.backend.async_fetch()
        except AuthFailed as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except AccountActionRequired as err:
            self._raise_account_issue(err)
            raise UpdateFailed(str(err)) from err
        except HvacError as err:
            raise UpdateFailed(str(err)) from err
        ir.async_delete_issue(self.hass, DOMAIN, f"account_{self.config_entry.entry_id}")
        return {unit_id: self._with_overlay(unit_id, s) for unit_id, s in states.items()}

    def _raise_account_issue(self, err: AccountActionRequired) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"account_{self.config_entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=f"account_{err.reason}",
        )

    def _with_overlay(self, unit_id: str, state: UnitState) -> UnitState:
        """Keep showing commanded values until the cloud catches up."""
        overlay = self._overlays.get(unit_id)
        if overlay is None:
            return state
        if time.monotonic() > overlay.expires or overlay.command.satisfied_by(state):
            del self._overlays[unit_id]
            return state
        return overlay.command.apply_to(state)

    @callback
    def async_on_push(self, unit_id: str, state: UnitState) -> None:
        """Take a pushed state from the backend."""
        if self.data is None or unit_id not in self.units:
            return
        self.async_set_updated_data({**self.data, unit_id: self._with_overlay(unit_id, state)})

    def state(self, unit_id: str) -> UnitState | None:
        """Return the latest state of a unit."""
        return self.data.get(unit_id) if self.data else None

    async def async_send(self, unit_id: str, command: Command) -> None:
        """Validate, show straight away, then send within a short batch window."""
        if command.is_empty:
            return
        info = self.units[unit_id]
        state = self.state(unit_id)
        if state is None or not state.online:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="unit_offline")
        pending = self._pending.get(unit_id)
        combined = pending.command.merge(command) if pending else command
        try:
            # The shown state already includes earlier parts of this batch.
            self.backend.validate(info, state, combined)
        except CommandRejected as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key=err.reason
            ) from err

        self.async_set_updated_data({**self.data, unit_id: command.apply_to(state)})

        if pending is None:
            pending = _Pending(command=combined, future=self.hass.loop.create_future())
            self._pending[unit_id] = pending

            @callback
            def _flush_later(_now: object) -> None:
                self.config_entry.async_create_background_task(
                    self.hass, self._async_flush(unit_id), f"{DOMAIN} send {unit_id}"
                )

            pending.cancel = async_call_later(self.hass, COALESCE_SECONDS, _flush_later)
        else:
            pending.command = combined
        await asyncio.shield(pending.future)

    async def _async_flush(self, unit_id: str) -> None:
        pending = self._pending.pop(unit_id, None)
        if pending is None:
            return
        try:
            await self.backend.async_send(unit_id, pending.command)
        except AuthFailed as err:
            self.config_entry.async_start_reauth(self.hass)
            self._fail(pending, "auth_failed", err)
        except AccountActionRequired as err:
            self._raise_account_issue(err)
            self._fail(pending, f"account_{err.reason}", err)
        except CommandRejected as err:
            self._fail(pending, err.reason, err)
        except HvacError as err:
            self._fail(pending, "command_failed", err)
        else:
            self._overlays[unit_id] = _Overlay(
                pending.command,
                time.monotonic() + OVERLAY_SECONDS,
            )
            if not pending.future.done():
                pending.future.set_result(None)
            self._schedule_confirm()

    def _fail(self, pending: _Pending, key: str, err: Exception) -> None:
        _LOGGER.warning("Command failed: %s", err)
        if not pending.future.done():
            pending.future.set_exception(
                HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key=key,
                    translation_placeholders={"error": str(err)},
                )
            )
        # Fetch the real state so the optimistic change is undone.
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _schedule_confirm(self) -> None:
        if self._confirm_cancel is not None:
            self._confirm_cancel()

        @callback
        def _confirm(_now: object) -> None:
            self._confirm_cancel = None
            self.hass.async_create_task(self.async_request_refresh())

        self._confirm_cancel = async_call_later(self.hass, CONFIRM_REFRESH_SECONDS, _confirm)

    async def async_shutdown(self) -> None:
        """Cancel timers and close the backend."""
        if self._confirm_cancel is not None:
            self._confirm_cancel()
            self._confirm_cancel = None
        for pending in self._pending.values():
            if pending.cancel is not None:
                pending.cancel()
            if not pending.future.done():
                pending.future.cancel()
        self._pending.clear()
        await super().async_shutdown()
        await self.backend.async_close()
