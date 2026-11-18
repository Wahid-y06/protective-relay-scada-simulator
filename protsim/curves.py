"""Inverse-time overcurrent characteristics (IEC 60255-151 and IEEE C37.112).

Operate time for a current of M times pickup:

    t = TMS * (A / (M^p - 1) + B)

IEC curves have B = 0. IEEE curves also define an electromechanical-style
*timed reset* below pickup:

    t_reset = TMS * tr / (1 - M^2)
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Curve:
    name: str
    a: float
    p: float
    b: float = 0.0
    tr: float | None = None      # reset constant; None = instantaneous reset only

    def operate_time(self, multiple: float, tms: float) -> float:
        """Seconds to trip at ``multiple`` x pickup (inf at or below pickup)."""
        if multiple <= 1.0:
            return math.inf
        return tms * (self.a / (multiple ** self.p - 1.0) + self.b)

    def reset_time(self, multiple: float, tms: float) -> float:
        """Seconds for a fully timed-out element to reset at ``multiple`` < 1."""
        if self.tr is None:
            raise ValueError(f"curve {self.name} has no timed reset characteristic")
        if multiple >= 1.0:
            return math.inf
        return tms * self.tr / (1.0 - multiple ** 2)


CURVES: dict[str, Curve] = {c.name: c for c in (
    Curve("IEC_SI", 0.14, 0.02),
    Curve("IEC_VI", 13.5, 1.0),
    Curve("IEC_EI", 80.0, 2.0),
    Curve("IEC_LTI", 120.0, 1.0),
    Curve("IEEE_MI", 0.0515, 0.02, 0.1140, tr=4.85),
    Curve("IEEE_VI", 19.61, 2.0, 0.4910, tr=21.6),
    Curve("IEEE_EI", 28.2, 2.0, 0.1217, tr=29.1),
)}


def get_curve(name: str) -> Curve:
    try:
        return CURVES[name]
    except KeyError:
        raise ValueError(f"unknown curve {name!r}; choose from {sorted(CURVES)}") from None
