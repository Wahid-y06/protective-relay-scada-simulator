import pytest

from protsim.__main__ import main
from protsim.hmi import Hmi
from protsim.recloser import RecloserState
from protsim.scenarios import SCENARIOS, build_feeder, run_scenario


def times(system, source, text):
    return [e.t for e in system.events if e.source == source and text in e.message]


def test_load_flow_without_faults():
    s = build_feeder()
    s.run(0.1)
    assert s.last_currents == (270.0, 120.0)        # SUB carries both sections


def test_opening_upstream_breaker_deenergises_downstream():
    s = build_feeder()
    s.operator_open("SUB")
    s.run(0.2)
    assert s.last_currents == (0.0, 0.0)
    assert not s.energized(1)


def test_temporary_fault_cleared_by_downstream_recloser():
    s = run_scenario("temporary_fault")
    assert len(times(s, "REC1", "TRIP")) == 1
    assert times(s, "SUB", "TRIP") == []             # coordination: SUB never trips
    assert times(s, "REC1", "reclose shot 1")
    assert all(b.breaker.closed for b in s.bays)
    assert s.bay("REC1").recloser.state is RecloserState.READY


def test_downstream_trips_before_upstream_with_coordination_margin():
    s = build_feeder()
    sub, rec = s.bay("SUB").relay, s.bay("REC1").relay
    t_rec = rec.operate_time(2620.0)
    t_sub = sub.operate_time(2770.0)
    assert t_sub - t_rec >= 0.3                     # typical minimum CTI


def test_permanent_fault_locks_out_recloser_only():
    s = run_scenario("permanent_fault")
    assert len(times(s, "REC1", "TRIP")) == 3        # initial trip + 2 failed recloses
    assert times(s, "SUB", "TRIP") == []
    rec1 = s.bay("REC1")
    assert rec1.recloser.state is RecloserState.LOCKOUT
    assert not rec1.breaker.closed and rec1.breaker.locked_out
    assert s.bay("SUB").breaker.closed               # healthy section stays in service
    assert s.last_currents == (150.0, 0.0)


def test_close_in_fault_uses_instantaneous_element():
    s = run_scenario("close_in_fault")
    trips = [e for e in s.events if e.source == "SUB" and "TRIP" in e.message]
    assert trips and all(e.message.startswith("50 TRIP") for e in trips)
    assert trips[0].t == pytest.approx(1.0, abs=0.002)
    assert s.bay("SUB").recloser.state is RecloserState.LOCKOUT


def test_breaker_failure_transfer_trips_upstream_without_reclose():
    s = run_scenario("breaker_failure")
    rec_trip = times(s, "REC1", "51 TRIP")[0]
    bf = times(s, "REC1", "50BF")[0]
    assert bf - rec_trip == pytest.approx(0.15, abs=0.002)
    sub = s.bay("SUB")
    assert not sub.breaker.closed
    assert sub.recloser.state is RecloserState.LOCKOUT
    assert times(s, "SUB", "reclose") == []
    # 50BF clears much faster than waiting for the SUB 51 backup (~1.06 s)
    assert times(s, "SUB", "breaker OPEN")[0] - 1.0 < 0.5


def test_operator_must_reset_lockout_before_closing():
    s = run_scenario("permanent_fault")
    s.repair_faults()
    s.operator_close("REC1")
    assert any("BLOCKED" in e.message for e in s.events[-2:])
    s.operator_reset("REC1")
    s.operator_close("REC1")
    s.run(0.5)
    assert s.bay("REC1").breaker.closed
    assert s.last_currents == (270.0, 120.0)


def test_operator_close_onto_permanent_fault_locks_out_without_reclosing():
    s = run_scenario("permanent_fault")
    s.operator_reset("REC1")
    s.operator_close("REC1")                         # fault not repaired yet
    s.run(3.0)
    rec1 = s.bay("REC1")
    assert rec1.recloser.state is RecloserState.LOCKOUT
    assert not rec1.breaker.closed


def test_add_fault_validation():
    s = build_feeder()
    with pytest.raises(ValueError):
        s.add_fault(5, 1000.0)
    with pytest.raises(ValueError):
        s.add_fault("SUB", -1.0)
    with pytest.raises(KeyError):
        s.add_fault("NOPE", 1000.0)


def test_hmi_session():
    hmi = Hmi(build_feeder())
    assert "Commands" in hmi.execute("help")
    assert "SUB" in hmi.execute("status")
    assert "scheduled" in hmi.execute("fault rec1 2500 temp")
    out = hmi.execute("run 3")
    assert "51 TRIP" in out and "reclose shot 1" in out
    assert "already closed" in hmi.execute("close REC1")
    assert "open command" in hmi.execute("open SUB")
    assert "unknown command" in hmi.execute("bogus")
    assert hmi.execute("run -1").startswith("error")
    assert hmi.execute("fault rec1 abc").startswith("error")
    assert hmi.execute("open NOPE").startswith("error")
    hmi.execute("quit")
    assert hmi.done


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_cli_runs_every_scenario(name, tmp_path, capsys):
    png = tmp_path / "out.png"
    assert main(["--scenario", name, "--plot", str(png)]) == 0
    assert png.stat().st_size > 10_000
    assert "Sequence of events" in capsys.readouterr().out


def test_cli_tcc(tmp_path):
    png = tmp_path / "tcc.png"
    assert main(["--tcc", str(png)]) == 0
    assert png.stat().st_size > 10_000
