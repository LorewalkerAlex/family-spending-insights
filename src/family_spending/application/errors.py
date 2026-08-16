from __future__ import annotations


class ApplicationError(RuntimeError):
    """Base error for canonical Application use cases."""


class ApplicationNotFoundError(ApplicationError):
    """Raised when a requested current entity does not exist."""


class ApplicationValidationError(ApplicationError):
    """Raised when input cannot satisfy the canonical domain contract."""


class ApplicationConflictError(ApplicationError):
    """Raised when valid input cannot be applied unambiguously to current state."""


class ApplicationStateError(ApplicationError):
    """Raised when durable state cannot satisfy one Application use case."""
