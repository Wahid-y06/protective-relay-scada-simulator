import pytest

from protsim.breaker import Breaker, BreakerState
from protsim.recloser import AutoRecloser, RecloserState, RecloseSettings

DT = 0.001


def run_until_done(breaker, limit=1.0):
    t = 0.0
    while t < limit:
        t += DT
        done = breaker.update(DT)
        if done:
            return t, done
    return None


def test_breaker_opens_after_operating_time():
    b = Breaker(open_time=0.05)
    assert b.trip("51") == "open command (51)"
    t, (state, cause) = run_until_done(b)
    assert t == pytest.approx(0.05, abs=DT)
    assert state is BreakerState.OPEN and cause == "51"
    assert b.last_open_cause == "51"


def test_repeated_trip_commands_are_ignored():
    b = Breaker()
    b.trip("51")
    assert b.trip("51") is None


def test_stuck_breaker_ignores_trip_and_reports_once():
    b = Breaker()
    b.failed = True
    assert "NOT EXECUTED" in b.trip("51")
    assert b.trip("51") is None
    assert run_until_done(b) is None
    assert b.closed


def test_lockout_blocks_close():
    b = Breaker(closed=False)
    b.locked_out = True
    assert "BLOCKED" in b.close("79 reclose")
    assert run_until_done(b) is None


def test_trip_cancels_pending_close():
    b = Breaker(closed=False, close_time=0.08)
    b.close("operator")
    assert "cancelled" in b.trip("51")
    assert run_until_done(b) is None
    assert not b.closed


def advance(rec, seconds):
    events, closes = [], 0
    for _ in range(round(seconds / DT)):
        ev, close = rec.update(DT)
        events += ev
        closes += close
    return events, closes


def test_recloser_full_sequence_to_lockout():
    rec = AutoRecloser(RecloseSettings(dead_times=(0.5, 2.0), reclaim_time=10.0))
    rec.on_protection_trip()
    assert rec.state is RecloserState.DEAD_TIME
    _, closes = advance(rec, 0.5)
    assert closes == 1 and rec.state is RecloserState.RECLAIM

    rec.on_protection_trip()                       # fault still there
    _, closes = advance(rec, 1.9)
    assert closes == 0                             # second dead time is 2.0 s
    _, closes = advance(rec, 0.1)
    assert closes == 1 and rec.shots_used == 2

    events = rec.on_protection_trip()
    assert rec.state is RecloserState.LOCKOUT
    assert "LOCKOUT" in events[0]
    assert advance(rec, 30.0) == ([], 0)


def test_successful_reclose_resets_after_reclaim_time():
    rec = AutoRecloser(RecloseSettings(dead_times=(0.5,), reclaim_time=3.0))
    rec.on_protection_trip()
    advance(rec, 0.5)
    events, _ = advance(rec, 3.0)
    assert rec.state is RecloserState.READY and rec.shots_used == 0
    assert any("reclaim" in e for e in events)
    rec.on_protection_trip()                       # a new fault gets all shots again
    assert rec.state is RecloserState.DEAD_TIME


def test_manual_close_onto_fault_locks_out_immediately():
    rec = AutoRecloser(RecloseSettings())
    rec.on_manual_close()
    rec.on_protection_trip()
    assert rec.state is RecloserState.LOCKOUT


def test_manual_open_does_not_reclose():
    rec = AutoRecloser(RecloseSettings())
    rec.on_manual_open()
    assert rec.state is RecloserState.READY
    assert advance(rec, 5.0) == ([], 0)


def test_invalid_reclose_settings():
    with pytest.raises(ValueError):
        RecloseSettings(dead_times=(0.0,))
