"""SCADA-style operator console.

Each command returns text, so the console can be driven interactively or
from tests/scripts:

    SCADA> status
    SCADA> fault REC1 2500 temp
    SCADA> run 5
    SCADA> close REC1
"""
from __future__ import annotations

import shlex

from .recloser import RecloserState
from .system import FeederSystem

HELP = """\
Commands:
  status                         breaker/relay/recloser overview
  run SECONDS                    advance the simulation, print new events
  fault BAY AMPS [temp|perm]     apply a fault on the section fed by BAY
  repair                         repair all permanent faults
  open BAY | close BAY           operator breaker control
  reset BAY                      clear a lockout
  log [N]                        show the last N events (default 20)
  help | quit"""


def render_status(system: FeederSystem) -> str:
    currents = system.last_currents
    header = (f"{'BAY':<6} {'BREAKER':<9} {'SECTION':<9} {'CURRENT':>9}  "
              f"{'51 TIMER':>8}  {'RELAY':<10} {'79':<10}")
    lines = [f"t = {system.t:.3f} s", header, "-" * len(header)]
    for i, (bay, amps) in enumerate(zip(system.bays, currents)):
        state = bay.breaker.state.value
        if bay.breaker.pending is not None:
            state += "*"
        section = "LIVE" if system.energized(i) else "DEAD"
        relay = ("TRIP " + (bay.relay.trip_element or "")) if bay.relay.trip else (
            "PICKUP" if bay.relay.picked_up else "normal")
        reclose = bay.recloser.state.value if bay.recloser else "-"
        if bay.breaker.locked_out and reclose != RecloserState.LOCKOUT.value:
            reclose = "LOCKOUT"
        lines.append(f"{bay.name:<6} {state:<9} {section:<9} {amps:>7.0f} A  "
                     f"{bay.relay.progress:>7.0%}  {relay:<10} {reclose:<10}")
    active = [f for f in system.faults if f.active]
    lines.append("Active faults: " + (", ".join(
        f"{system.bays[f.location].name} {f.current_a:.0f} A ({f.kind})" for f in active)
        or "none"))
    return "\n".join(lines)


class Hmi:
    def __init__(self, system: FeederSystem) -> None:
        self.system = system
        self.done = False

    def execute(self, line: str) -> str:
        try:
            args = shlex.split(line)
        except ValueError as exc:
            return f"error: {exc}"
        if not args:
            return ""
        cmd, rest = args[0].lower(), args[1:]
        handler = getattr(self, f"_cmd_{cmd}", None)
        if handler is None:
            return f"unknown command {cmd!r} - type 'help'"
        try:
            return handler(rest)
        except (KeyError, ValueError) as exc:
            return f"error: {exc.args[0] if exc.args else exc}"

    def _cmd_help(self, _args: list[str]) -> str:
        return HELP

    def _cmd_quit(self, _args: list[str]) -> str:
        self.done = True
        return "bye"

    _cmd_exit = _cmd_quit

    def _cmd_status(self, _args: list[str]) -> str:
        return render_status(self.system)

    def _cmd_run(self, args: list[str]) -> str:
        if len(args) != 1:
            raise ValueError("usage: run SECONDS")
        seconds = float(args[0])
        if not 0 < seconds <= 600:
            raise ValueError("SECONDS must be between 0 and 600")
        events = self.system.run(seconds)
        body = "\n".join(str(e) for e in events) or "(no events)"
        return f"{body}\n\n{render_status(self.system)}"

    def _cmd_fault(self, args: list[str]) -> str:
        if len(args) not in (2, 3):
            raise ValueError("usage: fault BAY AMPS [temp|perm]")
        kind = args[2].lower() if len(args) == 3 else "perm"
        if kind not in ("temp", "perm"):
            raise ValueError("fault type must be 'temp' or 'perm'")
        bay = self.system.bay(args[0])
        self.system.add_fault(bay.name, float(args[1]), permanent=(kind == "perm"))
        return f"fault scheduled on section {bay.name} at t = {self.system.t:.3f} s - use 'run'"

    def _cmd_repair(self, _args: list[str]) -> str:
        n = self.system.repair_faults()
        return f"{n} permanent fault(s) repaired"

    def _operator(self, args: list[str], action) -> str:
        if len(args) != 1:
            raise ValueError("usage: open|close|reset BAY")
        start = len(self.system.events)
        action(args[0])
        return "\n".join(str(e) for e in self.system.events[start:])

    def _cmd_open(self, args: list[str]) -> str:
        return self._operator(args, self.system.operator_open)

    def _cmd_close(self, args: list[str]) -> str:
        return self._operator(args, self.system.operator_close)

    def _cmd_reset(self, args: list[str]) -> str:
        return self._operator(args, self.system.operator_reset)

    def _cmd_log(self, args: list[str]) -> str:
        n = int(args[0]) if args else 20
        return "\n".join(str(e) for e in self.system.events[-n:]) or "(no events)"


def repl(system: FeederSystem) -> None:
    hmi = Hmi(system)
    print("Protective relay SCADA console - type 'help'\n")
    print(render_status(system))
    while not hmi.done:
        try:
            line = input("\nSCADA> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        output = hmi.execute(line)
        if output:
            print(output)
