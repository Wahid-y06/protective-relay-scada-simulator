"""Radial feeder: bays in series, each with a breaker, relay and recloser.

    Source ── [bay 0] ── section 0 ── [bay 1] ── section 1 ── ...

Bay *i* carries the load of every section from *i* downstream, as long as
all breakers from the source up to that section are closed. A fault on
section *k* is seen by every bay 0..k, which is why protection must be
*coordinated*: the bay closest to the fault should trip first and the
upstream bays act as time-delayed backup.

Breaker-failure protection (50BF): if a bay's relay is tripping and current
is still flowing after ``bf_time``, the breaker is assumed stuck. The next
breaker upstream is tripped directly and its recloser is locked out (you
must not reclose onto a fault behind a failed breaker).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .breaker import Breaker, BreakerState
from .recloser import AutoRecloser, RecloserState
from .relay import OvercurrentRelay

_EPS = 1e-9
PROTECTION_CAUSES = ("50", "51")


@dataclass
class Bay:
    name: str
    breaker: Breaker
    relay: OvercurrentRelay
    recloser: AutoRecloser | None
    load_a: float                      # load of the section this bay feeds
    bf_time: float = 0.15
    _bf_timer: float = field(default=0.0, repr=False)
    _bf_operated: bool = field(default=False, repr=False)


@dataclass
class Fault:
    location: int                      # index of the faulted section
    current_a: float
    permanent: bool
    start: float
    deion_time: float = 0.2            # temporary fault clears after this long de-energised
    active: bool = False
    cleared: bool = False
    _dead_for: float = 0.0

    @property
    def kind(self) -> str:
        return "permanent" if self.permanent else "temporary"


@dataclass(frozen=True)
class Event:
    t: float
    source: str
    message: str

    def __str__(self) -> str:
        return f"{self.t:9.3f} s  {self.source:<6} {self.message}"


@dataclass(frozen=True)
class Snapshot:
    t: float
    currents: tuple[float, ...]
    closed: tuple[bool, ...]


class FeederSystem:
    def __init__(self, bays: list[Bay], dt: float = 0.001,
                 sample_interval: float = 0.01) -> None:
        if not bays:
            raise ValueError("feeder needs at least one bay")
        names = [b.name for b in bays]
        if len(set(names)) != len(names):
            raise ValueError("bay names must be unique")
        self.bays = bays
        self.dt = dt
        self.faults: list[Fault] = []
        self.events: list[Event] = []
        self.history: list[Snapshot] = []
        self._k = 0
        self._sample_every = max(1, round(sample_interval / dt))
        self._last_currents = tuple(0.0 for _ in bays)

    # ------------------------------------------------------------------ state
    @property
    def t(self) -> float:
        return self._k * self.dt

    def bay(self, name: str) -> Bay:
        for b in self.bays:
            if b.name.lower() == name.lower():
                return b
        raise KeyError(f"no bay named {name!r} (have: {', '.join(b.name for b in self.bays)})")

    def index(self, name: str) -> int:
        return self.bays.index(self.bay(name))

    def energized(self, section: int) -> bool:
        return all(b.breaker.closed for b in self.bays[:section + 1])

    def currents(self) -> tuple[float, ...]:
        n = len(self.bays)
        live = [self.energized(j) for j in range(n)]
        out = []
        for i in range(n):
            amps = sum(self.bays[j].load_a for j in range(i, n) if live[j])
            amps += sum(f.current_a for f in self.faults
                        if f.active and f.location >= i and live[f.location])
            out.append(amps)
        return tuple(out)

    @property
    def last_currents(self) -> tuple[float, ...]:
        return self._last_currents

    def log(self, source: str, message: str) -> None:
        self.events.append(Event(self.t, source, message))

    # ------------------------------------------------------------- operator
    def add_fault(self, section: int | str, current_a: float, permanent: bool = True,
                  at: float | None = None, deion_time: float = 0.2) -> Fault:
        idx = self.index(section) if isinstance(section, str) else section
        if not 0 <= idx < len(self.bays):
            raise ValueError(f"section index {idx} out of range")
        if current_a <= 0:
            raise ValueError("fault current must be positive")
        fault = Fault(idx, current_a, permanent, self.t if at is None else at, deion_time)
        self.faults.append(fault)
        return fault

    def repair_faults(self) -> int:
        repaired = 0
        for f in self.faults:
            if f.active and f.permanent:
                f.active, f.cleared = False, True
                repaired += 1
                self.log("CREW", f"permanent fault on section {self.bays[f.location].name} repaired")
        return repaired

    def operator_open(self, name: str) -> None:
        bay = self.bay(name)
        self.log(bay.name, "operator OPEN")
        self._log_opt(bay.name, bay.breaker.trip("operator")
                      or "no action - breaker already open or opening")
        if bay.recloser:
            bay.recloser.on_manual_open()

    def operator_close(self, name: str) -> None:
        bay = self.bay(name)
        self.log(bay.name, "operator CLOSE")
        msg = bay.breaker.close("operator")
        self._log_opt(bay.name, msg or "no action - breaker already closed or closing")
        if msg and "BLOCKED" not in msg and bay.recloser:
            bay.recloser.on_manual_close()

    def operator_reset(self, name: str) -> None:
        bay = self.bay(name)
        bay.breaker.locked_out = False
        bay._bf_operated = False
        if bay.recloser:
            bay.recloser.reset()
        self.log(bay.name, "operator RESET - lockout cleared")

    # ------------------------------------------------------------ simulation
    def run(self, seconds: float) -> list[Event]:
        start = len(self.events)
        for _ in range(int(round(seconds / self.dt))):
            self.step()
        return self.events[start:]

    def step(self) -> None:
        dt = self.dt
        self._apply_faults()

        currents = self.currents()
        self._last_currents = currents
        if self._k % self._sample_every == 0:
            self.history.append(Snapshot(self.t, currents,
                                         tuple(b.breaker.closed for b in self.bays)))

        for i, (bay, amps) in enumerate(zip(self.bays, currents)):
            for msg in bay.relay.update(dt, amps):
                self.log(bay.name, msg)
            if bay.relay.trip:
                self._log_opt(bay.name, bay.breaker.trip(bay.relay.trip_element or "51"))
            self._breaker_failure(i, bay, amps)

        for bay in self.bays:
            done = bay.breaker.update(dt)
            if done is None:
                continue
            state, cause = done
            self.log(bay.name, f"breaker {state.value} ({cause})")
            if state is BreakerState.OPEN and cause in PROTECTION_CAUSES and bay.recloser:
                for msg in bay.recloser.on_protection_trip():
                    self.log(bay.name, msg)
                if bay.recloser.state is RecloserState.LOCKOUT:
                    bay.breaker.locked_out = True

        for bay in self.bays:
            if bay.recloser is None:
                continue
            msgs, close = bay.recloser.update(dt)
            for msg in msgs:
                self.log(bay.name, msg)
            if close:
                self._log_opt(bay.name, bay.breaker.close("79 reclose"))

        self._extinguish_temporary_faults()
        self._k += 1

    # ------------------------------------------------------------- internals
    def _apply_faults(self) -> None:
        for f in self.faults:
            if not f.active and not f.cleared and self.t >= f.start - _EPS:
                f.active = True
                self.log("FAULT", f"{f.kind} fault on section {self.bays[f.location].name}, "
                                  f"{f.current_a:.0f} A")

    def _extinguish_temporary_faults(self) -> None:
        for f in self.faults:
            if not f.active or f.permanent:
                continue
            if self.energized(f.location):
                f._dead_for = 0.0
                continue
            f._dead_for += self.dt
            if f._dead_for >= f.deion_time - _EPS:
                f.active, f.cleared = False, True
                self.log("FAULT", f"temporary fault on section {self.bays[f.location].name} "
                                  f"extinguished (de-energised {f.deion_time:.2f} s)")

    def _breaker_failure(self, i: int, bay: Bay, amps: float) -> None:
        current_flowing = amps >= bay.relay.settings.dropout_ratio * bay.relay.settings.pickup_a
        if not (bay.relay.trip and current_flowing) or bay._bf_operated:
            bay._bf_timer = 0.0
            return
        bay._bf_timer += self.dt
        if bay._bf_timer < bay.bf_time - _EPS or i == 0:
            return
        bay._bf_operated = True
        upstream = self.bays[i - 1]
        self.log(bay.name, f"50BF breaker failure - transfer trip to {upstream.name}")
        self._log_opt(upstream.name, upstream.breaker.trip("50BF"))
        upstream.breaker.locked_out = True
        if upstream.recloser:
            for msg in upstream.recloser.force_lockout(f"50BF from {bay.name}"):
                self.log(upstream.name, msg)

    def _log_opt(self, source: str, message: str | None) -> None:
        if message:
            self.log(source, message)
