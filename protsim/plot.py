"""Plots: event timeline of a scenario, and time-current coordination curves."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .system import FeederSystem  # noqa: E402

COLORS = ("#1f77b4", "#d62728", "#2ca02c", "#9467bd")


def plot_timeline(system: FeederSystem, title: str, path: str | Path) -> None:
    t = [s.t for s in system.history]
    n = len(system.bays)
    fig, (ax_i, ax_b) = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                                     gridspec_kw={"height_ratios": [3, 1.3]})
    fig.suptitle(title, fontsize=12)

    for i, bay in enumerate(system.bays):
        ax_i.plot(t, [s.currents[i] for s in system.history], color=COLORS[i % 4],
                  lw=1.3, label=f"{bay.name} current")
        ax_i.axhline(bay.relay.settings.pickup_a, color=COLORS[i % 4], ls=":", lw=0.9,
                     label=f"{bay.name} 51 pickup")
    for e in system.events:
        if "TRIP" in e.message or "50BF" in e.message:
            ax_i.axvline(e.t, color="#888", lw=0.6, ls="--")
    ax_i.set_ylabel("Current (A)")
    ax_i.legend(loc="upper right", fontsize=8)
    ax_i.grid(alpha=0.3)

    for i, bay in enumerate(system.bays):
        closed = [1 if s.closed[i] else 0 for s in system.history]
        ax_b.step(t, [n - 1 - i + 0.8 * c for c in closed], where="post",
                  color=COLORS[i % 4], lw=1.5)
    ax_b.set_yticks([n - 1 - i + 0.4 for i in range(n)])
    ax_b.set_yticklabels([f"{b.name}\nopen / closed" for b in system.bays], fontsize=8)
    ax_b.set_xlabel("Time (s)")
    ax_b.grid(alpha=0.3, axis="x")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def plot_tcc(system: FeederSystem, path: str | Path,
             fault_current: float | None = None) -> None:
    """Log-log time-current curves of every relay (51 + 50 elements)."""
    fig, ax = plt.subplots(figsize=(8, 6.5))
    currents = np.logspace(2, 4.2, 400)
    for i, bay in enumerate(system.bays):
        s, relay = bay.relay.settings, bay.relay
        times = [relay.operate_time(c) for c in currents]
        if s.inst_pickup_a is not None:
            times = [min(tt, max(s.inst_delay_s, 0.02)) if c >= s.inst_pickup_a else tt
                     for c, tt in zip(currents, times)]
        ax.loglog(currents, times, color=COLORS[i % 4], lw=1.8,
                  label=f"{bay.name}: {s.curve}, pickup {s.pickup_a:.0f} A, TMS {s.tms}"
                        + (f", 50 @ {s.inst_pickup_a:.0f} A" if s.inst_pickup_a else ""))
    if fault_current:
        # Each relay sees the fault current plus the load downstream of it.
        t_down = system.bays[-1].relay.operate_time(fault_current + system.bays[-1].load_a)
        t_up = system.bays[0].relay.operate_time(
            fault_current + sum(b.load_a for b in system.bays))
        ax.axvline(fault_current, color="#555", ls="--", lw=0.9)
        ax.annotate(f"{fault_current:.0f} A fault\nCTI = {t_up - t_down:.2f} s",
                    xy=(fault_current, t_down), xytext=(fault_current * 0.3, t_down * 0.2),
                    fontsize=9, arrowprops={"arrowstyle": "->", "color": "#555"})
    ax.set_xlim(100, 10 ** 4.2)
    ax.set_ylim(0.01, 100)
    ax.set_xlabel("Current (A)")
    ax.set_ylabel("Operate time (s)")
    ax.set_title("Time-current coordination")
    ax.grid(which="both", alpha=0.3)
    ax.legend(fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
