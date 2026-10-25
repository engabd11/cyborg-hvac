"""Errors raised by backends. The Home Assistant layer maps them to its own."""

from __future__ import annotations


class HvacError(Exception):
    """Base error for all backend failures."""


class AuthFailed(HvacError):
    """Credentials or tokens were rejected; the user must sign in again."""


class CannotConnect(HvacError):
    """The service or device could not be reached, or replied with an error."""


class RateLimited(CannotConnect):
    """The service asked us to slow down."""


class AccountActionRequired(HvacError):
    """The account needs attention in the vendor app (new terms, lockout)."""

    def __init__(self, reason: str, message: str = "") -> None:
        """Store a translation-friendly reason key."""
        super().__init__(message or reason)
        self.reason = reason


class CommandRejected(HvacError):
    """The unit refused a command in its current state."""

    def __init__(self, reason: str, message: str = "") -> None:
        """Store a translation-friendly reason key."""
        super().__init__(message or reason)
        self.reason = reason
