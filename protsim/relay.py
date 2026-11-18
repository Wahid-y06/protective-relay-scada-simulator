"""Overcurrent relay with instantaneous (50) and inverse-time (51) elements.

The 51 element uses an *integrating* timer, the digital equivalent of an
induction disk: each time step adds ``dt / t_operate(I)`` to a progress
value and the relay trips when progress reaches 1.0. This makes the trip
time correct even when the fault current changes during the fault.

Below pickup the progress either resets instantly or, for IEEE curves with
``reset="timed"``, winds back down over the curve's reset time - so repeated
short fault bursts can still add up to a trip, as with a real disk relay.
"""
from __future__ import annotations

from dataclasses import dataclass

from .curves import get_curve

_EPS = 1e-9


@dataclass(frozen=True)
class RelaySettings:
    pickup_a: float                     # 51 pickup current
    curve: str = "IEC_SI"
    tms: float = 0.1                    # time multiplier / time dial
    inst_pickup_a: float | None = None  # 50 pickup; None disables the element
    inst_delay_s: float = 0.0           # definite delay for the 50 element
    dropout_ratio: float = 0.95         # 51 drops out below ratio * pickup
    reset: str = "instant"              # "instant" or "timed"

    def __post_init__(self) -> None:
        if self.pickup_a <= 0 or self.tms <= 0:
            raise ValueError("pickup_a and tms must be positive")
        if not 0 < self.dropout_ratio <= 1:
            raise ValueError("dropout_ratio must be in (0, 1]")
        if self.reset not in ("instant", "timed"):
            raise ValueError("reset must be 'instant' or 'timed'")
        curve = get_curve(self.curve)
        if self.reset == "timed" and curve.tr is None:
            raise ValueError(f"curve {self.curve} does not support timed reset")
        if self.inst_pickup_a is not None and self.inst_pickup_a <= self.pickup_a:
            raise ValueError("inst_pickup_a must be above pickup_a")


class OvercurrentRelay:
    def __init__(self, settings: RelaySettings) -> None:
        self.settings = settings
        self.curve = get_curve(settings.curve)
        self.progress = 0.0           # 51 integrator, trips at 1.0
        self.picked_up = False
        self.trip = False             # trip output, sealed in until current goes away
        self.trip_element: str | None = None
        self._inst_timer = 0.0

    def operate_time(self, current: float) -> float:
        """Steady-state 51 operate time at a constant ``current``."""
        s = self.settings
        return self.curve.operate_time(current / s.pickup_a, s.tms)

    def update(self, dt: float, current: float) -> list[str]:
        """Advance by ``dt`` seconds at ``current`` amps; return event messages."""
        s = self.settings
        events: list[str] = []
        multiple = current / s.pickup_a

        if not self.picked_up and current >= s.pickup_a:
            self.picked_up = True
            events.append(f"51 pickup ({current:.0f} A)")
        elif self.picked_up and current < s.dropout_ratio * s.pickup_a:
            self.picked_up = False
            events.append("51 dropout")

        if self.picked_up:
            if multiple > 1.0:
                self.progress += dt / self.curve.operate_time(multiple, s.tms)
        elif s.reset == "timed":
            self.progress = max(0.0, self.progress - dt / self.curve.reset_time(multiple, s.tms))
        else:
            self.progress = 0.0

        inst_active = s.inst_pickup_a is not None and current >= s.inst_pickup_a
        self._inst_timer = self._inst_timer + dt if inst_active else 0.0

        if not self.trip:
            if inst_active and self._inst_timer >= s.inst_delay_s - _EPS:
                self._set_trip("50", current, events)
            elif self.progress >= 1.0 - _EPS:
                self._set_trip("51", current, events)
        elif current < s.dropout_ratio * s.pickup_a:
            self.trip = False
            self.trip_element = None
            self.progress = 0.0
            events.append("trip output reset")
        return events

    def _set_trip(self, element: str, current: float, events: list[str]) -> None:
        self.trip = True
        self.trip_element = element
        events.append(f"{element} TRIP ({current:.0f} A)")
