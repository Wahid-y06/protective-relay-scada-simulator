import math

import pytest

from protsim.curves import CURVES, get_curve
from protsim.relay import OvercurrentRelay, RelaySettings

DT = 0.001


def time_to_trip(relay, current, limit=60.0, dt=DT):
    t = 0.0
    while t < limit:
        relay.update(dt, current)
        t += dt
        if relay.trip:
            return t
    return math.inf


# --- curves -----------------------------------------------------------------

@pytest.mark.parametrize("name, multiple, tms, expected", [
    ("IEC_SI", 10.0, 1.0, 2.971),    # textbook value for standard inverse at 10x
    ("IEC_VI", 10.0, 1.0, 1.500),
    ("IEC_EI", 10.0, 1.0, 0.808),
    ("IEEE_MI", 10.0, 1.0, 1.207),
    ("IEEE_EI", 5.0, 0.5, 0.648),
])
def test_curve_operate_times(name, multiple, tms, expected):
    assert CURVES[name].operate_time(multiple, tms) == pytest.approx(expected, abs=2e-3)


def test_no_operation_at_or_below_pickup():
    for curve in CURVES.values():
        assert curve.operate_time(1.0, 1.0) == math.inf
        assert curve.operate_time(0.5, 1.0) == math.inf


def test_ieee_reset_time():
    assert CURVES["IEEE_VI"].reset_time(0.0, 1.0) == pytest.approx(21.6)
    assert CURVES["IEEE_VI"].reset_time(0.5, 1.0) == pytest.approx(28.8)
    with pytest.raises(ValueError):
        CURVES["IEC_SI"].reset_time(0.5, 1.0)


def test_unknown_curve_rejected():
    with pytest.raises(ValueError, match="unknown curve"):
        get_curve("IEC_XX")


# --- relay ------------------------------------------------------------------

def test_51_trip_time_matches_curve():
    s = RelaySettings(pickup_a=100.0, curve="IEC_SI", tms=0.2)
    relay = OvercurrentRelay(s)
    expected = CURVES["IEC_SI"].operate_time(5.0, 0.2)
    assert time_to_trip(relay, 500.0) == pytest.approx(expected, abs=2 * DT)
    assert relay.trip_element == "51"


def test_no_trip_below_pickup():
    relay = OvercurrentRelay(RelaySettings(pickup_a=100.0))
    assert time_to_trip(relay, 99.0, limit=5.0) == math.inf
    assert not relay.picked_up


def test_50_trips_instantly_and_with_definite_delay():
    fast = OvercurrentRelay(RelaySettings(pickup_a=100.0, inst_pickup_a=1000.0))
    assert time_to_trip(fast, 1500.0) == pytest.approx(DT)
    assert fast.trip_element == "50"
    delayed = OvercurrentRelay(RelaySettings(pickup_a=100.0, inst_pickup_a=1000.0,
                                             inst_delay_s=0.1))
    assert time_to_trip(delayed, 1500.0) == pytest.approx(0.1, abs=2 * DT)


def test_dropout_ratio_gives_pickup_hysteresis():
    relay = OvercurrentRelay(RelaySettings(pickup_a=100.0, dropout_ratio=0.95))
    relay.update(DT, 101.0)
    assert relay.picked_up
    relay.update(DT, 96.0)            # between dropout (95 A) and pickup
    assert relay.picked_up
    relay.update(DT, 94.0)
    assert not relay.picked_up


def test_integrating_timer_handles_changing_current():
    """Half the time at 5x, then the rest at 10x: trips when progress hits 1."""
    s = RelaySettings(pickup_a=100.0, curve="IEC_VI", tms=1.0)
    relay = OvercurrentRelay(s)
    t5, t10 = relay.operate_time(500.0), relay.operate_time(1000.0)   # 3.375, 1.5
    for _ in range(round(t5 / 2 / DT)):
        relay.update(DT, 500.0)
    assert relay.progress == pytest.approx(0.5, abs=1e-3)
    remaining = time_to_trip(relay, 1000.0)
    assert remaining == pytest.approx(t10 / 2, abs=2 * DT)


def test_instant_reset_forgets_interrupted_fault():
    relay = OvercurrentRelay(RelaySettings(pickup_a=100.0, curve="IEEE_VI", tms=1.0))
    for _ in range(300):
        relay.update(DT, 500.0)
    relay.update(DT, 0.0)
    assert relay.progress == 0.0


def test_timed_reset_accumulates_repeated_bursts():
    """Disk emulation: short bursts separated by brief gaps still add up to a
    trip with timed reset, but never trip with instantaneous reset."""
    def bursts(reset):
        relay = OvercurrentRelay(RelaySettings(pickup_a=100.0, curve="IEEE_VI",
                                               tms=1.0, reset=reset))
        burst_time = 0.6 * relay.operate_time(500.0)
        for _ in range(4):
            for _ in range(round(burst_time / DT)):
                relay.update(DT, 500.0)
                if relay.trip:
                    return True
            for _ in range(round(0.2 / DT)):
                relay.update(DT, 0.0)
        return False

    assert bursts("timed") is True
    assert bursts("instant") is False


def test_trip_output_seals_in_until_current_removed():
    relay = OvercurrentRelay(RelaySettings(pickup_a=100.0, inst_pickup_a=1000.0))
    time_to_trip(relay, 2000.0)
    relay.update(DT, 500.0)           # fault reduced but not gone
    assert relay.trip
    events = relay.update(DT, 0.0)
    assert not relay.trip and "trip output reset" in events


@pytest.mark.parametrize("kwargs", [
    {"pickup_a": 0},
    {"pickup_a": 100, "tms": 0},
    {"pickup_a": 100, "dropout_ratio": 1.2},
    {"pickup_a": 100, "reset": "slow"},
    {"pickup_a": 100, "curve": "IEC_SI", "reset": "timed"},
    {"pickup_a": 100, "inst_pickup_a": 50},
])
def test_invalid_settings_rejected(kwargs):
    with pytest.raises(ValueError):
        RelaySettings(**kwargs)
