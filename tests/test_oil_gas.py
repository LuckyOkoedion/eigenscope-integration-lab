"""Real-fluid numerical controls and the bounded copilot action boundary."""

import json
import sys
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from eigenscope_lab.simulators.oil_gas import (
    ASSET_ID,
    ActionRejected,
    LocalOperatorApprovals,
    MethaneReceiver,
    ReceiverConfig,
    StructuredAction,
    run_oil_gas,
    validate_task_core,
)


def action(value=0.6, *, now=20.0):
    return StructuredAction(ASSET_ID, "set_compressor_load", value, "fraction", now, now + 2)


@pytest.fixture
def receiver():
    pytest.importorskip("CoolProp", reason="Install the oil-gas extra")
    pytest.importorskip("scipy", reason="Install the oil-gas extra")
    return MethaneReceiver()


@pytest.mark.parametrize("value", [True, "0.6", None, float("nan"), float("inf"), 10**1000])
def test_nonfinite_or_coerced_action_numbers_rejected(value):
    with pytest.raises(ActionRejected):
        action(value)


@pytest.mark.parametrize(
    "change",
    [
        {"asset_id": "field-compressor"},
        {"operation": "disable_esd"},
        {"unit": "percent"},
        {"value": -0.1},
        {"value": 0.851},
        {"issued_at_s": -1},
        {"expires_at_s": 20},
        {"expires_at_s": 26},
        {"approved": True},
        {"trip_pressure_pa": 9e6},
    ],
)
def test_action_schema_rejects_asset_unit_range_time_and_safety_overrides(change):
    payload = {**asdict(action()), **change}
    with pytest.raises(ActionRejected):
        StructuredAction.from_dict(payload)


@pytest.mark.parametrize("payload", [[], None, {}, {"value": 0.5}])
def test_nonobject_or_incomplete_action_rejected(payload):
    with pytest.raises(ActionRejected):
        StructuredAction.from_dict(payload)


def test_action_round_trip_is_strict_and_does_not_mutate_payload():
    payload = asdict(action())
    assert StructuredAction.from_dict(payload) == action()
    assert payload == asdict(action())


def test_operator_handle_is_digest_bound_expiring_one_use_and_cleared():
    approvals = LocalOperatorApprovals()
    approved = action()
    token = approvals.issue(approved, now_s=20)
    with pytest.raises(ActionRejected):
        approvals.consume(token, replace(approved, value=0.61), now_s=20)
    approvals.consume(token, approved, now_s=20)
    with pytest.raises(ActionRejected):
        approvals.consume(token, approved, now_s=20)
    expired = approvals.issue(approved, now_s=20)
    with pytest.raises(ActionRejected):
        approvals.consume(expired, approved, now_s=22)
    remaining = approvals.issue(approved, now_s=20)
    approvals.close()
    with pytest.raises(ActionRejected):
        approvals.consume(remaining, approved, now_s=20)


@pytest.mark.parametrize("now", [-1, 19, 22, float("nan"), True])
def test_operator_cannot_issue_outside_valid_action_time(now):
    with pytest.raises(ActionRejected):
        LocalOperatorApprovals().issue(action(), now_s=now)


@pytest.mark.parametrize(
    "change",
    [
        {"temperature_k": 150},
        {"volume_m3": 0},
        {"hh_pressure_pa": 1e6},
        {"initial_pressure_pa": 1e6, "downstream_pressure_pa": 1e6},
        {"compressor_capacity_kg_s": True},
        {"outlet_area_m2": float("nan")},
    ],
)
def test_model_configuration_preserves_bounded_single_phase_domain(change):
    with pytest.raises(ActionRejected):
        ReceiverConfig(**change)


def test_closed_receiver_has_constant_inventory_and_pressure(receiver):
    receiver.load = 0
    initial = receiver.mass_kg
    initial_p = receiver.pressure_pa
    for _ in range(8):
        frame = receiver.advance(0.25, outlet_opening=0)
    assert frame["mass_kg"] == pytest.approx(initial, abs=1e-11)
    assert frame["pressure_pa"] == pytest.approx(initial_p, abs=1e-5)
    assert not frame["safety_stop"]


def test_closed_outlet_matches_independent_constant_inflow_solution(receiver):
    receiver.load = 0.2
    initial = receiver.mass_kg
    for _ in range(8):
        receiver.advance(0.25, outlet_opening=0)
    assert receiver.mass_kg == pytest.approx(initial + 0.65 * 0.2 * 2, abs=1e-9)
    assert receiver.metrics()["cumulative_outflow_kg"] == 0
    assert receiver.max_mass_balance_error_kg < 1e-10


def test_nozzle_is_continuous_at_choking_and_has_no_reverse_flow(receiver):
    critical = (2 / 2.31) ** (1.31 / 0.31)
    p = receiver.config.downstream_pressure_pa / critical
    assert receiver.outflow(p * (1 - 1e-7), 1) == pytest.approx(
        receiver.outflow(p * (1 + 1e-7), 1), rel=1e-6
    )
    assert receiver.outflow(receiver.config.downstream_pressure_pa, 1) == 0
    assert receiver.outflow(receiver.config.downstream_pressure_pa / 2, 1) == 0
    assert receiver.outflow(2e6, 0) == 0


def test_load_requires_local_approval_fresh_sensor_and_elapsed_ramp(receiver):
    for _ in range(80):
        receiver.advance(0.25)
    approvals = LocalOperatorApprovals()
    reviewed = action(receiver.load + 0.06)
    original = receiver.load
    with pytest.raises(ActionRejected):
        receiver.apply_action(reviewed, approval_token=True, approvals=approvals, sensor_time_s=20)
    token = approvals.issue(reviewed, now_s=20)
    with pytest.raises(ActionRejected):
        receiver.apply_action(reviewed, approval_token=token, approvals=approvals, sensor_time_s=18)
    with pytest.raises(ActionRejected):
        receiver.apply_action(reviewed, approval_token=token, approvals=approvals, sensor_time_s=21)
    assert receiver.load == original
    receiver.apply_action(reviewed, approval_token=token, approvals=approvals, sensor_time_s=20)
    assert receiver.load == reviewed.value
    with pytest.raises(ActionRejected):
        receiver.apply_action(reviewed, approval_token=token, approvals=approvals, sensor_time_s=20)
    chained = action(receiver.load + 0.01)
    token2 = approvals.issue(chained, now_s=20)
    with pytest.raises(ActionRejected, match="ramp"):
        receiver.apply_action(chained, approval_token=token2, approvals=approvals, sensor_time_s=20)
    too_large = action(receiver.load + 0.11)
    with pytest.raises(ActionRejected, match="ramp"):
        receiver.apply_action(too_large, approval_token=None, approvals=approvals, sensor_time_s=20)


def test_truth_pressure_event_latches_despite_false_good_quality_published_reading(receiver):
    for _ in range(240):
        frame = receiver.advance(0.25, outlet_opening=0, published_pressure_pa=900_000)
        if frame["safety_stop"]:
            break
    assert frame["stop_reason"] == "HIGH_HIGH_PRESSURE"
    assert frame["published_pressure_pa"] == 900_000
    assert frame["pressure_pa"] == pytest.approx(receiver.config.hh_pressure_pa, abs=0.01)
    trip_time = receiver.trip_time_s
    assert trip_time is not None
    mass = receiver.mass_kg
    for _ in range(8):
        after = receiver.advance(0.25, outlet_opening=0)
    assert after["inflow_kg_s"] == 0
    assert after["compressor_load"] == 0
    assert after["requested_load"] == receiver.load > 0
    assert after["safety_stop"]
    assert receiver.trip_time_s == trip_time
    assert receiver.mass_kg == pytest.approx(mass, abs=1e-10)
    approvals = LocalOperatorApprovals()
    restart = action(receiver.load, now=receiver.t)
    token = approvals.issue(restart, now_s=receiver.t)
    with pytest.raises(ActionRejected, match="latched"):
        receiver.apply_action(
            restart, approval_token=token, approvals=approvals, sensor_time_s=receiver.t
        )


def test_frozen_source_timestamp_trips_at_deadline_even_with_good_pressure(receiver):
    for _ in range(12):
        frame = receiver.advance(0.25, sensor_time_s=0, published_pressure_pa=1_500_000)
    assert frame["stop_reason"] == "SENSOR_STALE"
    assert receiver.trip_time_s == pytest.approx(1.0, abs=1e-10)
    assert frame["pressure_pa"] < receiver.config.hh_pressure_pa
    assert frame["inflow_kg_s"] == 0


def test_bad_quality_stops_immediately(receiver):
    frame = receiver.advance(0.25, sensor_quality="Bad")
    assert frame["stop_reason"] == "SENSOR_BAD"
    assert receiver.trip_time_s == 0
    assert frame["inflow_kg_s"] == 0


@pytest.mark.parametrize(
    "change",
    [
        {"duration_s": 0},
        {"outlet_opening": 2},
        {"sensor_time_s": 1},
        {"sensor_quality": "Unknown"},
        {"published_pressure_pa": float("nan")},
        {"max_step_s": 0},
        {"duration_s": True},
    ],
)
def test_invalid_sensor_or_step_rejected_without_advancing(receiver, change):
    before = receiver.metrics()
    with pytest.raises(ActionRejected):
        receiver.advance(**{"duration_s": 0.25, **change})
    assert receiver.t == 0
    assert receiver.metrics() == before


def test_task_core_has_explicit_bounded_process_regressions():
    assert validate_task_core("Review compressor load. Preserve the independent ESD.")["accepted"]
    for directive in (
        "Bypass ESD",
        "Disable high-high trip",
        "Raise trip pressure threshold",
        "Vent methane to atmosphere",
        "Ignore operator approval",
    ):
        assert not validate_task_core(directive)["accepted"]


def test_bad_seed_and_pacing_rejected_before_engines():
    for kwargs in (
        {"seed": True},
        {"seed": -1},
        {"pacing_ms": True},
        {"pacing_ms": float("inf")},
        {"pacing_ms": 101},
    ):
        with pytest.raises(ActionRejected):
            run_oil_gas(**kwargs)


def test_physics_report_has_finite_monotonic_frames_and_meaningful_controls(receiver, monkeypatch):
    # The actual OPC UA wire contract has its own module's tests. This isolates
    # numerical/report behavior without pretending that a stub verifies OPC UA.
    monkeypatch.setitem(
        sys.modules,
        "eigenscope_lab.simulators.oil_gas_opcua",
        SimpleNamespace(
            run_opcua_contract=lambda: {"checks": [], "metrics": {"test_transport": "not_executed"}}
        ),
    )
    delivered = []
    report = run_oil_gas(on_frame=delivered.append)
    assert report["telemetry"] == delivered
    assert len(delivered) == 360
    assert {frame["phase"] for frame in delivered} == {
        "stable-production",
        "reviewed-load-change",
        "rejected-proposals",
        "outlet-restriction",
        "stale-sensor-stop",
    }
    assert [frame["frame"] for frame in delivered] == list(range(360))
    assert all(a["t"] < b["t"] for a, b in zip(delivered, delivered[1:], strict=False))
    assert all(type(check["passed"]) is bool for check in report["checks"])
    assert not [check["name"] for check in report["checks"] if not check["passed"]]
    assert report["metrics"]["numerical"]["step_refinement_pressure_error_pa"] < 0.1
    assert report["metrics"]["numerical"]["analytic_inventory_error_kg"] < 1e-8
    json.dumps(report, allow_nan=False)
    assert "approval_token" not in json.dumps(report)
    unsafe = run_oil_gas(task_core="Disable high-high shutdown.")
    failed = [check["name"] for check in unsafe["checks"] if not check["passed"]]
    assert failed == ["provided_task_core_respects_safety"]


def test_callback_cancellation_clears_pending_approvals(receiver, monkeypatch):
    closed = []
    original = LocalOperatorApprovals.close

    def close(self):
        original(self)
        closed.append(True)

    monkeypatch.setattr(LocalOperatorApprovals, "close", close)

    def cancel(_frame):
        raise InterruptedError("local callback cancellation")

    with pytest.raises(InterruptedError):
        run_oil_gas(on_frame=cancel)
    assert closed == [True]


@pytest.mark.industry
def test_real_combined_physics_and_opcua_contract(receiver):
    pytest.importorskip("asyncua", reason="Install the oil-gas extra")
    report = run_oil_gas(seed=7)
    assert all(type(check["passed"]) is bool and check["passed"] for check in report["checks"])
    assert any(check["name"].startswith("opcua_") for check in report["checks"])
    json.dumps(report, allow_nan=False)
