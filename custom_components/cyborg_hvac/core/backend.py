"""Interface every brand backend implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from .errors import CommandRejected
from .model import Command, UnitInfo, UnitState

PushCallback = Callable[[str, UnitState], None]


class HvacBackend(ABC):
    """A connection to one account or controller that holds one or more units.

    Backends must not import Home Assistant. They receive an aiohttp session and
    whatever credentials they need from their Home Assistant adapter.
    """

    @abstractmethod
    async def async_discover(self) -> list[UnitInfo]:
        """Return every unit with its capabilities. Called once at setup."""

    @abstractmethod
    async def async_fetch(self) -> dict[str, UnitState]:
        """Return the current state of every unit, keyed by unit_id."""

    @abstractmethod
    async def async_send(self, unit_id: str, command: Command) -> None:
        """Apply a command. One call may change several values at once."""

    async def async_start_push(self, callback: PushCallback) -> bool:
        """Start push updates. Return False when the backend only polls."""
        return False

    async def async_close(self) -> None:  # noqa: B027
        """Release connections."""

    def validate(self, info: UnitInfo, state: UnitState, command: Command) -> None:
        """Raise CommandRejected when the unit would refuse the command.

        The default checks feature rules declared in the capabilities against the
        state the unit would reach. Backends may add brand-specific rules.
        """
        result = command.apply_to(state)
        for feature, value in command.features.items():
            spec = info.capabilities.features.get(feature)
            if spec is None:
                raise CommandRejected("feature_unsupported", f"{feature} is not supported")
            if value and value != "off" and not spec.accepts(result.power, result.mode):
                raise CommandRejected(
                    "feature_not_available",
                    f"{feature} is not available in the current mode",
                )
