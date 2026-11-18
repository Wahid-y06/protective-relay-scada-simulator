"""The default two-bay feeder and the scripted fault scenarios."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .breaker import Breaker
from .recloser import AutoRecloser, RecloseSettings
from .relay import OvercurrentRelay, RelaySettings
from .system import Bay, FeederSystem

# Substation feeder breaker: slower, higher pickup -> backup for the line recloser.
SUB_RELAY = RelaySettings(pickup_a=400.0, curve="IEC_SI", tms=0.30, inst_pickup_a=6000.0)
SUB_RECLOSE = RecloseSettings(dead_times=(1.0, 5.0), reclaim_time=15.0)

# Mid-line recloser: faster, lower pickup -> clears faults on its section first.
REC_RELAY = RelaySettings(pickup_a=200.0, curve="IEC_SI", tms=0.10, inst_pickup_a=3000.0)
REC_RECLOSE = RecloseSettings(dead_times=(0.5, 2.0), reclaim_time=10.0)


def build_feeder(dt: float = 0.001) -> FeederSystem:
    return FeederSystem([
        Bay("SUB", Breaker(open_time=0.05, close_time=0.08), OvercurrentRelay(SUB_RELAY),
            AutoRecloser(SUB_RECLOSE), load_a=150.0),
        Bay("REC1", Breaker(open_time=0.04, close_time=0.06), OvercurrentRelay(REC_RELAY),
            AutoRecloser(REC_RECLOSE), load_a=120.0),
    ], dt=dt)


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    duration: float
    setup: Callable[[FeederSystem], None]


def _temporary(s: FeederSystem) -> None:
    s.add_fault("REC1", 2500.0, permanent=False, at=1.0)


def _permanent(s: FeederSystem) -> None:
    s.add_fault("REC1", 2500.0, permanent=True, at=1.0)


def _close_in(s: FeederSystem) -> None:
    s.add_fault("SUB", 6500.0, permanent=True, at=1.0)


def _breaker_failure(s: FeederSystem) -> None:
    s.bay("REC1").breaker.failed = True
    s.add_fault("REC1", 2500.0, permanent=True, at=1.0)


SCENARIOS: dict[str, Scenario] = {s.name: s for s in (
    Scenario("temporary_fault", "Temporary 2.5 kA fault beyond REC1: trip, successful reclose",
             15.0, _temporary),
    Scenario("permanent_fault", "Permanent 2.5 kA fault beyond REC1: two failed recloses, lockout",
             15.0, _permanent),
    Scenario("close_in_fault", "Permanent 6.5 kA fault near the substation: SUB instantaneous trip",
             12.0, _close_in),
    Scenario("breaker_failure", "REC1 breaker stuck closed: 50BF transfer-trips SUB, no reclose",
             5.0, _breaker_failure),
)}


def run_scenario(name: str, dt: float = 0.001) -> FeederSystem:
    scenario = SCENARIOS[name]
    system = build_feeder(dt)
    scenario.setup(system)
    system.run(scenario.duration)
    return system
