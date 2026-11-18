"""Auto-reclose (ANSI 79) state machine.

Most overhead-line faults are temporary (a branch or an arc that clears once
the line is de-energised). After a protection trip the recloser waits a
*dead time* and closes the breaker again. If the fault is still there the
protection trips again and the next shot is tried; when all shots are used
the recloser goes to LOCKOUT and the breaker stays open until an operator
resets it. If the breaker stays closed for the *reclaim time*, the reclose
was successful and the shot counter resets.

    READY --trip--> DEAD_TIME --dead time--> (close) RECLAIM --reclaim--> READY
                                                        |
                                                trip, shots left -> DEAD_TIME
                                                trip, no shots   -> LOCKOUT
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

_EPS = 1e-9


class RecloserState(str, Enum):
    READY = "READY"
    DEAD_TIME = "DEAD_TIME"
    RECLAIM = "RECLAIM"
    LOCKOUT = "LOCKOUT"


@dataclass(frozen=True)
class RecloseSettings:
    dead_times: tuple[float, ...] = (0.5, 2.0)   # one entry per reclose shot
    reclaim_time: float = 10.0

    def __post_init__(self) -> None:
        if any(t <= 0 for t in self.dead_times) or self.reclaim_time <= 0:
            raise ValueError("dead times and reclaim time must be positive")

    @property
    def shots(self) -> int:
        return len(self.dead_times)


class AutoRecloser:
    def __init__(self, settings: RecloseSettings) -> None:
        self.settings = settings
        self.state = RecloserState.READY
        self.shots_used = 0
        self._timer = 0.0

    def on_protection_trip(self) -> list[str]:
        """Called when protection has opened the breaker."""
        if self.state is RecloserState.LOCKOUT:
            return []
        if self.state is RecloserState.READY:
            self.shots_used = 0
        if self.shots_used < self.settings.shots:
            self.state = RecloserState.DEAD_TIME
            self._timer = self.settings.dead_times[self.shots_used]
            return [f"79 shot {self.shots_used + 1} dead time {self._timer:.2f} s started"]
        self.state = RecloserState.LOCKOUT
        return [f"79 LOCKOUT after {self.shots_used} reclose attempt(s)"]

    def on_manual_open(self) -> None:
        """Operator opened the breaker: never auto-reclose after that."""
        if self.state is not RecloserState.LOCKOUT:
            self.state = RecloserState.READY
            self.shots_used = 0

    def on_manual_close(self) -> None:
        """Operator closed the breaker: a trip during reclaim locks out
        straight away (no reclosing onto a fault the operator closed into)."""
        self.state = RecloserState.RECLAIM
        self.shots_used = self.settings.shots
        self._timer = self.settings.reclaim_time

    def force_lockout(self, reason: str) -> list[str]:
        self.state = RecloserState.LOCKOUT
        return [f"79 LOCKOUT ({reason})"]

    def reset(self) -> None:
        self.state = RecloserState.READY
        self.shots_used = 0
        self._timer = 0.0

    def update(self, dt: float) -> tuple[list[str], bool]:
        """Advance time; return (events, close_request)."""
        if self.state is RecloserState.DEAD_TIME:
            self._timer -= dt
            if self._timer <= _EPS:
                self.shots_used += 1
                self.state = RecloserState.RECLAIM
                self._timer = self.settings.reclaim_time
                return [f"79 reclose shot {self.shots_used}"], True
        elif self.state is RecloserState.RECLAIM:
            self._timer -= dt
            if self._timer <= _EPS:
                self.state = RecloserState.READY
                self.shots_used = 0
                return ["79 reclaim time complete - reset to READY"], False
        return [], False
