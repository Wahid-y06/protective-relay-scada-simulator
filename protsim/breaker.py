"""Circuit breaker with operating times, lockout and a failure mode."""
from __future__ import annotations

from enum import Enum

_EPS = 1e-9


class BreakerState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"


class Breaker:
    """A breaker changes state ``open_time`` / ``close_time`` after a command.

    ``failed=True`` simulates a stuck breaker that ignores trip commands
    (used to exercise breaker-failure protection). ``locked_out`` blocks
    closing until an operator resets it.
    """

    def __init__(self, open_time: float = 0.05, close_time: float = 0.08,
                 closed: bool = True) -> None:
        self.open_time = open_time
        self.close_time = close_time
        self.state = BreakerState.CLOSED if closed else BreakerState.OPEN
        self.locked_out = False
        self.failed = False
        self.last_open_cause: str | None = None
        self._pending: tuple[BreakerState, str] | None = None
        self._remaining = 0.0
        self._failure_reported = False

    @property
    def closed(self) -> bool:
        return self.state is BreakerState.CLOSED

    @property
    def pending(self) -> BreakerState | None:
        return self._pending[0] if self._pending else None

    def trip(self, cause: str) -> str | None:
        if self.pending is BreakerState.CLOSED:
            self._pending = None
            return f"close cancelled by trip ({cause})"
        if self.state is BreakerState.OPEN or self.pending is BreakerState.OPEN:
            return None
        if self.failed:
            if self._failure_reported:
                return None
            self._failure_reported = True
            return f"trip command ({cause}) NOT EXECUTED - breaker stuck"
        self._pending, self._remaining = (BreakerState.OPEN, cause), self.open_time
        return f"open command ({cause})"

    def close(self, cause: str) -> str | None:
        if self.locked_out:
            return f"close command ({cause}) BLOCKED - lockout active"
        if self.state is BreakerState.CLOSED or self.pending is not None:
            return None
        self._pending, self._remaining = (BreakerState.CLOSED, cause), self.close_time
        return f"close command ({cause})"

    def update(self, dt: float) -> tuple[BreakerState, str] | None:
        """Advance time; return (new_state, cause) when an operation completes."""
        if self._pending is None:
            return None
        self._remaining -= dt
        if self._remaining > _EPS:
            return None
        target, cause = self._pending
        self._pending = None
        self.state = target
        if target is BreakerState.OPEN:
            self.last_open_cause = cause
        return target, cause
