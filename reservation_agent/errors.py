"""Typed errors so tools can 'fail loudly' with messages a human can read.

Tools raise these; the agent layer catches them and surfaces ``.message`` to the
guest (and, on the LLM path, back to the model as the tool result). Splitting
*rule* failures from *not-found* failures lets the conversation respond
appropriately to each.
"""
from __future__ import annotations


class ReservationError(Exception):
    """Base class for all domain errors. Carries a guest-readable message."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ValidationError(ReservationError):
    """A business rule (BR-*) was violated or input was malformed."""


class NotFoundError(ReservationError):
    """No reservation matches the given confirmation code."""


class UnavailableError(ReservationError):
    """The requested slot cannot seat the party (BR-7)."""
