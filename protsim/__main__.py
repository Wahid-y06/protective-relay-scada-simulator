"""Command-line entry point.

    python -m protsim --list
    python -m protsim --scenario permanent_fault --plot permanent.png
    python -m protsim --tcc tcc.png
    python -m protsim --hmi
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .scenarios import SCENARIOS, build_feeder, run_scenario


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="protsim",
                                 description="Protective relay and SCADA simulator")
    ap.add_argument("--scenario", choices=sorted(SCENARIOS))
    ap.add_argument("--plot", type=Path, help="save the scenario timeline to this PNG")
    ap.add_argument("--tcc", type=Path, help="save the time-current curves to this PNG")
    ap.add_argument("--hmi", action="store_true", help="start the interactive SCADA console")
    ap.add_argument("--list", action="store_true", help="list scenarios")
    args = ap.parse_args(argv)

    if args.list:
        for s in SCENARIOS.values():
            print(f"{s.name:<17} {s.description}")
        return 0
    if args.hmi:
        from .hmi import repl
        repl(build_feeder())
        return 0
    if args.tcc:
        from .plot import plot_tcc
        plot_tcc(build_feeder(), args.tcc, fault_current=2500.0)
        print(f"TCC plot written to {args.tcc}")
    if args.scenario:
        scenario = SCENARIOS[args.scenario]
        system = run_scenario(args.scenario)
        print(f"Scenario: {scenario.name} - {scenario.description}\n")
        print("Sequence of events:")
        for event in system.events:
            print(event)
        from .hmi import render_status
        print("\nFinal state:\n" + render_status(system))
        if args.plot:
            from .plot import plot_timeline
            plot_timeline(system, f"{scenario.name}: {scenario.description}", args.plot)
            print(f"\nPlot written to {args.plot}")
    elif not args.tcc:
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
