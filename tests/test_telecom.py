"""Capacity, causality, determinism and adversarial telecom contract checks."""

import runpy
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from eigenscope_lab.simulators.telecom import (
    PRBS_30KHZ,
    RanControl,
    authorize_control,
    run_telecom,
    simulate_ran,
)


def test_default_study_meets_declared_contract():
    result = run_telecom()
    assert all(check["passed"] for check in result["checks"]), result["checks"]
    assert result["engine_version"]


@pytest.mark.parametrize("seed", [0, 7, 42, 2**32 - 1])
def test_study_is_seeded_and_conserves_packets_and_airtime(seed):
    control = RanControl(duration_seconds=0.2)
    result = simulate_ran(control, seed=seed)
    assert result == simulate_ran(control, seed=seed)
    assert result["packet_conservation"] and result["airtime_conservation"]
    assert result["packets_still_queued"] == 0
    assert result["latency_p50_ms"] >= 0.5  # A new packet occupies a whole slot.
    assert result["latency_min_ms"] >= 0.5 - 1e-6
    assert result["transmitted_bits"] <= result["capacity_bits"]


def test_seed_changes_arrivals_but_does_not_break_invariants():
    a = simulate_ran(RanControl(duration_seconds=0.2), seed=10)
    b = simulate_ran(RanControl(duration_seconds=0.2), seed=11)
    assert a["packets_arrived"] != b["packets_arrived"]
    assert a["packet_conservation"] and b["packet_conservation"]


def test_overload_is_a_measured_failure_of_capacity_not_infinite_buffering():
    control = RanControl(offered_mbps_per_ue=40, queue_limit_packets=8, duration_seconds=0.2)
    result = simulate_ran(control)
    assert result["packets_dropped_queue"] > 0
    assert max(result["max_queue_depths"]) <= 8
    assert result["throughput_mbps"] <= result["nominal_air_capacity_mbps"]
    assert result["packet_conservation"]


def test_retransmissions_consume_airtime_and_obey_retry_bound():
    control = RanControl(retry_probability=0.5, max_retries=1, duration_seconds=0.2)
    result = simulate_ran(control)
    assert result["retry_attempts_scheduled"] > 0
    assert result["packets_dropped_retry"] > 0
    assert result["tx_attempts"] <= result["packets_arrived"] * 2
    assert result["transmitted_bits"] == result["tx_attempts"] * control.packet_bytes * 8
    assert result["packet_conservation"]


def test_no_channel_loss_has_no_retransmission_or_retry_drop():
    result = simulate_ran(RanControl(retry_probability=0, duration_seconds=0.2))
    assert result["retry_attempts_scheduled"] == result["packets_dropped_retry"] == 0


def test_policy_update_is_atomic_on_both_success_and_failure():
    ledger = {"1-000001": 20, "1-000002": 60}
    original = ledger.copy()
    accepted = authorize_control(RanControl(), allocations=ledger)
    assert accepted["1-000001"] == 40 and ledger == original
    with pytest.raises(ValueError, match="exceeds"):
        authorize_control(RanControl(bandwidth_mhz=60), allocations=ledger)
    assert ledger == original


def test_cross_slice_ownership_is_rejected_even_when_capacity_is_available():
    with pytest.raises(PermissionError):
        authorize_control(RanControl(slice_id="1-000002"), allocations={"1-000001": 5})


@pytest.mark.parametrize(
    "field,value",
    [
        ("bandwidth_mhz", True),
        ("bandwidth_mhz", 40.0),
        ("bandwidth_mhz", 41),
        ("bandwidth_mhz", float("nan")),
        ("offered_mbps_per_ue", float("inf")),
        ("offered_mbps_per_ue", 10**1000),
        ("offered_mbps_per_ue", True),
        ("queue_limit_packets", 0),
        ("queue_limit_packets", 1_000_000),
        ("ue_count", -1),
        ("duration_seconds", 0),
        ("retry_probability", 0.99),
        ("slice_id", "SUPI-001010000000001"),
        ("slice_id", "999-000001"),
        ("slice_id", {"sst": 1}),
        ("packet_bytes", 0),
    ],
)
def test_invalid_or_unbounded_control_is_rejected(field, value):
    with pytest.raises(ValueError):
        replace(RanControl(), **{field: value}).validate()


@pytest.mark.parametrize("seed", [-1, True, 2**32, 1.5])
def test_invalid_seed_is_rejected(seed):
    with pytest.raises(ValueError):
        simulate_ran(seed=seed)


def test_model_exposes_fragmentation_limit_explicitly():
    with pytest.raises(ValueError, match="fragmentation"):
        simulate_ran(RanControl(bandwidth_mhz=5))
    result = simulate_ran(RanControl(bandwidth_mhz=5, packet_bytes=64, offered_mbps_per_ue=0.1))
    assert result["packets_delivered"] > 0


def test_resource_block_table_is_the_declared_release16_30khz_subset():
    assert PRBS_30KHZ[40] == 106 and PRBS_30KHZ[100] == 273
    assert PRBS_30KHZ[5] == 11


def test_live_adapter_reports_protection_failure_without_hiding_local_results():
    result = run_telecom(task_core="Allocate 40MHz; SUPI-302-720-009481726 at 51.0447N")
    checks = {c["name"]: c["passed"] for c in result["checks"]}
    assert checks["protected_core_retains_40mhz"]
    assert not checks["protected_core_hides_synthetic_subscriber"]
    assert not checks["protected_core_hides_synthetic_coordinates"]
    assert checks["positive_real_packet_delivery"]


def test_protected_core_can_pass_the_adapter_contract():
    result = run_telecom(task_core="Allocate 40 MHz for ⟦PRIVATE_1⟧ at ⟦PRIVATE_2⟧")
    assert all(c["passed"] for c in result["checks"])


def test_actual_event_frames_are_ordered_and_match_returned_observations():
    observed = []
    result = run_telecom(on_frame=observed.append)
    assert observed == result["telemetry"]
    for phase in ("normal", "overload"):
        frames = [frame for frame in observed if frame["phase"] == phase]
        assert len(frames) > 100
        assert all(
            a["t_seconds"] < b["t_seconds"] for a, b in zip(frames, frames[1:], strict=False)
        )
        assert all(frame["allocated_prbs"] == 106 for frame in frames)
    assert any(frame["queue_packets"] > 0 for frame in observed)
    assert any(frame["dropped_packets"] > 0 for frame in observed)
    for frame in observed:
        assert frame["queue_limit_packets"] == 32
        assert all(depth <= frame["queue_limit_packets"] for depth in frame["per_ue_queue_packets"])
        assert frame["offered_mbps"] == (32 if frame["phase"] == "normal" else 160)
        assert frame["arrival_window_open"] is (frame["t_seconds"] < 1.0)
        assert sum(frame["per_ue_delivered"]) == frame["served_packets"]
        assert frame["latency_sample_count"] == frame["served_packets"]
        if frame["served_packets"]:
            assert frame["observed_latency_p95_ms"] >= 0.5 - 1e-6
            delivered = frame["per_ue_delivered"]
            expected = sum(delivered) ** 2 / (len(delivered) * sum(n * n for n in delivered))
            assert frame["jain_fairness"] == pytest.approx(expected, abs=1e-6)
        else:
            assert frame["observed_latency_p95_ms"] is None
            assert frame["jain_fairness"] is None


def test_callback_cancellation_halts_the_actual_simulation():
    class Cancelled(Exception):
        pass

    observed = []

    def cancel(frame):
        observed.append(frame)
        if len(observed) == 3:
            raise Cancelled

    with pytest.raises(Cancelled):
        run_telecom(on_frame=cancel)
    assert len(observed) == 3


@pytest.fixture(scope="module")
def native_helpers():
    return runpy.run_path(str(Path(__file__).parents[1] / "infra" / "telecom" / "stack.py"))


def test_native_preflight_accepts_real_nullable_docker_ipam_metadata(native_helpers):
    native_helpers["check_network_conflicts"](
        [
            {"Name": "host", "IPAM": {"Config": None}},
            {"Name": "none", "IPAM": None},
            {"Name": "bridge", "IPAM": {"Config": [{"Subnet": "172.17.0.0/16"}]}},
        ]
    )


@pytest.mark.parametrize("subnet", ["10.231.0.0/24", "10.231.0.0/16", "10.231.0.128/25"])
def test_native_preflight_rejects_overlapping_unrelated_networks(native_helpers, subnet):
    with pytest.raises(RuntimeError, match="conflicts"):
        native_helpers["check_network_conflicts"](
            [{"Name": "unrelated-network", "IPAM": {"Config": [{"Subnet": subnet}]}}]
        )


def test_native_preflight_allows_idempotent_owned_network_and_ipv6(native_helpers):
    native_helpers["check_network_conflicts"](
        [
            {
                "Name": "eigenscope-lab-5gc-isolated",
                "IPAM": {"Config": [{"Subnet": "10.231.0.0/24"}]},
            },
            {"Name": "ipv6-network", "IPAM": {"Config": [{"Subnet": "fd00::/64"}]}},
        ]
    )


@pytest.fixture
def native_up_spy(native_helpers, monkeypatch):
    function = native_helpers["up"]
    scope = function.__globals__
    events = []
    monkeypatch.setitem(scope, "doctor", lambda _context: {"kernel_version": "7.0.0-ci"})
    monkeypatch.setitem(scope, "prepare", lambda _context: {})
    monkeypatch.setitem(
        scope,
        "docker",
        lambda _context, *args, **kwargs: subprocess.CompletedProcess(
            args, 0, stdout="", stderr=""
        ),
    )

    def fake_compose(_context, *args, **kwargs):
        if "nr_gnb" in args:
            events.append(("radio_and_ue", args))
        elif "nrf" in args:
            events.append(("network_functions", args))
        else:
            events.append(("mongo", args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setitem(scope, "compose", fake_compose)
    monkeypatch.setitem(
        scope, "provision_subscriber", lambda _context: events.append(("provisioned", ()))
    )
    return function, scope, events


def test_native_dependent_services_wait_for_database_health_and_provisioning(native_up_spy):
    function, _, events = native_up_spy
    result = function(None)
    assert result["started"]
    assert [name for name, _ in events] == [
        "mongo",
        "provisioned",
        "network_functions",
        "radio_and_ue",
    ]
    assert "--wait" in events[0][1]
    assert "--pull" in events[0][1] and "never" in events[0][1]


def test_native_database_failure_never_starts_dependent_network_functions(
    native_up_spy, monkeypatch
):
    function, scope, events = native_up_spy

    def fail_provisioning(_context):
        raise RuntimeError("synthetic database unavailable")

    monkeypatch.setitem(scope, "provision_subscriber", fail_provisioning)
    with pytest.raises(RuntimeError, match="database unavailable"):
        function(None)
    assert [name for name, _ in events] == ["mongo"]


def test_native_verifier_executes_status_instead_of_dumping_nodes(
    native_helpers, monkeypatch, tmp_path
):
    function = native_helpers["verify"]
    scope = function.__globals__
    monkeypatch.setitem(scope, "STATE", tmp_path)

    def respond(_context, *args, **kwargs):
        if "./nr-cli" in args:
            assert args[args.index("./nr-cli") :] == (
                "./nr-cli",
                "imsi-001010000000001",
                "--exec",
                "status",
            )
            stdout = "cm-state: CM-CONNECTED\nrm-state: RM-REGISTERED\n"
        elif "address" in args:
            stdout = '[{"ifname":"uesimtun0","addr_info":[{"family":"inet","local":"10.251.0.2"}]}]'
        elif "ping" in args:
            stdout = "3 packets transmitted, 3 received, 0% packet loss\n"
        else:
            stdout = "fixture-native-version"
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setitem(scope, "compose", respond)
    result = function(None)
    assert result["status"] == "PASS" and all(result["checks"].values())
    assert result["ue_status_returncode"] == 0


def test_native_verifier_rejects_failed_cli_even_with_matching_status_text(
    native_helpers, monkeypatch, tmp_path
):
    function = native_helpers["verify"]
    scope = function.__globals__
    monkeypatch.setitem(scope, "STATE", tmp_path)
    ticks = iter([0, 0, 100])
    monkeypatch.setitem(
        scope, "time", SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _: None)
    )

    def respond(_context, *args, **kwargs):
        if "./nr-cli" in args:
            return subprocess.CompletedProcess(
                args, 1, stdout="rm-state: RM-REGISTERED\n", stderr="synthetic CLI failure"
            )
        return subprocess.CompletedProcess(
            args, 0, stdout="[]" if "address" in args else "0% packet loss\n", stderr=""
        )

    monkeypatch.setitem(scope, "compose", respond)
    result = function(None)
    assert result["status"] == "FAIL"
    assert not result["checks"]["ue_registered"]
    assert not result["checks"]["ue_status_command_succeeded"]
