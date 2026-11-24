"""Cooperative operator cancellation shared by startup, data requests and waits.

Allowed: carry an irreversible stop event and wake waiting read-only work.
Forbidden: decide trades, alter risk, interrupt portfolio transactions or clear a stop.
"""

from __future__ import annotations

from math import isfinite
from threading import Event
from time import monotonic

# Brief waits also let the main Python thread dispatch Windows console signals.
_CANCEL_POLL_SEC = 0.1


class OperationCancelled(Exception):
    """The operator requested a graceful stop, not a source or transport failure."""


class CancellationToken:
    """Own a one-way stop event; callers check it at safe operation boundaries."""

    def __init__(self) -> None:
        """Create an initially running token without global signal handlers."""
        self._event = Event()

    def request_stop(self) -> None:
        """Request a permanent stop and promptly wake any cancellable waits."""
        self._event.set()

    def is_cancelled(self) -> bool:
        """Return whether an operator stop has been requested."""
        return self._event.is_set()

    def check(self) -> None:
        """Raise the dedicated cancellation signal if a stop has been requested."""
        if self.is_cancelled():
            raise OperationCancelled("operator requested stop")

    def sleep(self, seconds: float) -> None:
        """Wait in brief chunks so Windows SIGINT can dispatch, or raise on stop."""
        self.check()
        if not isfinite(seconds) or seconds < 0:
            raise ValueError("wait duration must be finite and non-negative")
        deadline = monotonic() + seconds
        while True:
            self.check()
            remaining = deadline - monotonic()
            if remaining <= 0:
                return
            self._event.wait(min(remaining, _CANCEL_POLL_SEC))
