"""The Climate Butler's journal: what it did, what it decided to wait for, and what it learned.

Every command sent is an entry with the changes it made as short chips. A new
plan without a command (starting to watch a room, resting) is an entry too,
but the same plan is not written again within THINK_REPEAT. Holds, energy
mode switches and learned findings are added as they happen.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from ..core.model import Command, HvacMode

KEEP = timedelta(days=7)
MAX_ENTRIES = 500
THINK_REPEAT = timedelta(minutes=30)

_MODE_WORDS = {
    HvacMode.HEAT: "Heat",
    HvacMode.COOL: "Cool",
    HvacMode.DRY: "Dry",
    HvacMode.FAN_ONLY: "Fan only",
    HvacMode.AUTO: "Auto",
}


class Kind(StrEnum):
    """What a journal entry records."""

    ACT = "act"  # a command was sent
    THINK = "think"  # a new plan, nothing to send yet
    ENERGY = "energy"  # the energy mode changed
    HOLD = "hold"  # a person changed something: left alone for a while
    LEARN = "learn"  # something learned about a room
    SWITCH = "switch"  # a behaviour was switched on or off


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One line of history."""

    time: datetime
    kind: Kind
    headline: str
    reason: str = ""
    activity: str | None = None
    changes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly form for the card and storage."""
        return {
            "time": self.time.isoformat(),
            "kind": self.kind.value,
            "headline": self.headline,
            "reason": self.reason,
            "activity": self.activity,
            "changes": list(self.changes),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> JournalEntry | None:
        """Rebuild from storage, or None for an entry that cannot be read."""
        try:
            return cls(
                time=datetime.fromisoformat(str(data["time"])),
                kind=Kind(data["kind"]),
                headline=str(data.get("headline", "")),
                reason=str(data.get("reason", "")),
                activity=data.get("activity"),
                changes=tuple(str(c) for c in data.get("changes") or ()),
            )
        except (KeyError, TypeError, ValueError):
            return None


def describe(command: Command, names: Mapping[int, str]) -> tuple[str, ...]:
    """Return a command as short chips: "Cool", "22.5°", "Fan low", "Bedroom 100%"."""
    chips: list[str] = []
    if command.power is False:
        chips.append("Off")
    elif command.mode is not None:
        chips.append(_MODE_WORDS.get(command.mode, command.mode.value))
    elif command.power:
        chips.append("On")
    if command.target_temp is not None:
        chips.append(f"{command.target_temp:g}°")
    if command.fan_speed is not None:
        chips.append(f"Fan {command.fan_speed.replace('_', ' ')}")
    for number, change in command.zones.items():
        name = names.get(number, f"Zone {number}")
        if change.on is False:
            chips.append(f"{name} closed")
        elif change.opening is not None:
            chips.append(f"{name} {change.opening}%")
        elif change.on:
            chips.append(f"{name} open")
    return tuple(chips)


@dataclass(slots=True)
class Journal:
    """Recent history for one unit, newest last."""

    entries: deque[JournalEntry] = field(default_factory=lambda: deque(maxlen=MAX_ENTRIES))
    # When each plan was last written, so the same one is not repeated straight away.
    _thought: dict[tuple[str | None, str], datetime] = field(default_factory=dict)
    _last: tuple[str | None, str] | None = None

    def add(self, entry: JournalEntry) -> None:
        """Add an entry and drop what is too old."""
        self.entries.append(entry)
        while self.entries and entry.time - self.entries[0].time > KEEP:
            self.entries.popleft()

    def observe(
        self,
        now: datetime,
        activity: str | None,
        headline: str,
        reason: str,
        sent: Command | None,
        names: Mapping[int, str],
    ) -> JournalEntry | None:
        """Note one decision: an entry when something was sent or the plan changed."""
        key = (activity, headline)
        if sent is not None and not sent.is_empty:
            entry = JournalEntry(now, Kind.ACT, headline, reason, activity, describe(sent, names))
        elif key != self._last and headline:
            last = self._thought.get(key)
            self._last = key
            if last is not None and now - last < THINK_REPEAT:
                return None
            entry = JournalEntry(now, Kind.THINK, headline, reason, activity)
        else:
            return None
        self._last = key
        self._thought[key] = now
        self._thought = {k: t for k, t in self._thought.items() if now - t < THINK_REPEAT}
        self.add(entry)
        return entry

    def recent(self, limit: int = 15, before: datetime | None = None) -> list[JournalEntry]:
        """Return up to ``limit`` entries, newest first, older than ``before`` if given."""
        out: list[JournalEntry] = []
        for entry in reversed(self.entries):
            if before is not None and entry.time >= before:
                continue
            out.append(entry)
            if len(out) >= limit:
                break
        return out

    def as_list(self) -> list[dict[str, Any]]:
        """Return a storable form."""
        return [e.as_dict() for e in self.entries]

    @classmethod
    def from_list(cls, data: Any) -> Journal:
        """Rebuild from storage."""
        journal = cls()
        for item in data if isinstance(data, list) else []:
            if isinstance(item, Mapping) and (entry := JournalEntry.from_dict(item)) is not None:
                journal.entries.append(entry)
        return journal
