"""Seeded, finite-queue RAN scheduling model powered by SimPy.

This model tests a control contract, queues and resource conservation. It is not
an RF/link-level simulator or an E2AP implementation. The optional upstream 5GC
deployment lives in ``infra/telecom`` and is a separate execution tier.
"""

from __future__ import annotations

import math
import random
import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from importlib.metadata import version
from typing import Any

# 3GPP TS 38.104 Table 5.3.2-1, FR1 channel bandwidth, 30 kHz SCS.
PRBS_30KHZ = {
    5: 11,
    10: 24,
    15: 38,
    20: 51,
    25: 65,
    30: 78,
    40: 106,
    50: 133,
    60: 162,
    70: 189,
    80: 217,
    90: 245,
    100: 273,
}
SLOT_SECONDS = 0.0005


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{name} must be a finite number")
    return float(value)


@dataclass(frozen=True)
class RanControl:
    """Synthetic near-RT control envelope with an explicit per-slice budget."""

    slice_id: str = "1-000001"
    bandwidth_mhz: int = 40
    ue_count: int = 4
    queue_limit_packets: int = 32
    offered_mbps_per_ue: float = 8.0
    duration_seconds: float = 1.0
    packet_bytes: int = 1000
    retry_probability: float = 0.02
    max_retries: int = 2

    def validate(self) -> RanControl:
        if not isinstance(self.slice_id, str) or not re.fullmatch(
            r"[1-9][0-9]{0,2}-[0-9a-fA-F]{6}", self.slice_id
        ):
            raise ValueError("slice_id must be SST-SD, for example 1-000001")
        if int(self.slice_id.split("-", 1)[0]) > 255:
            raise ValueError("SST must be in 1..255")
        if type(self.bandwidth_mhz) is not int or self.bandwidth_mhz not in PRBS_30KHZ:
            raise ValueError("bandwidth_mhz must be a supported FR1/30kHz channel bandwidth")
        for name, lo, hi in (
            ("ue_count", 1, 64),
            ("queue_limit_packets", 1, 256),
            ("packet_bytes", 64, 1500),
            ("max_retries", 0, 4),
        ):
            value = getattr(self, name)
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError(f"{name} must be an integer in {lo}..{hi}")
        for name, lo, hi in (
            ("offered_mbps_per_ue", 0.01, 1000),
            ("duration_seconds", 0.05, 10),
            ("retry_probability", 0, 0.5),
        ):
            value = _finite_number(getattr(self, name), name)
            if not lo <= value <= hi:
                raise ValueError(f"{name} must be in {lo}..{hi}")
        # Bound runtime/work independent of a caller's data and machine speed.
        expected_packets = (
            self.duration_seconds
            * self.ue_count
            * self.offered_mbps_per_ue
            * 1e6
            / (8 * self.packet_bytes)
        )
        if expected_packets > 200_000:
            raise ValueError("requested traffic exceeds the 200000-packet study budget")
        return self


def authorize_control(
    control: RanControl,
    *,
    allocations: dict[str, int] | None = None,
    total_bandwidth_mhz: int = 100,
) -> dict[str, int]:
    """Return a new allocation ledger after authorization; never mutate input.

    The lab operator owns only slice 1-000001. Identities remain local and are
    not used as externally supplied control credentials.
    """
    control.validate()
    current = dict(allocations) if allocations is not None else {"1-000001": 20, "1-000002": 60}
    if control.slice_id != "1-000001":
        raise PermissionError("the lab principal cannot control this slice")
    if type(total_bandwidth_mhz) is not int or total_bandwidth_mhz <= 0:
        raise ValueError("total bandwidth must be a positive integer")
    if any(type(v) is not int or v < 0 for v in current.values()):
        raise ValueError("allocation ledger contains invalid bandwidth")
    proposed = {**current, control.slice_id: control.bandwidth_mhz}
    if sum(proposed.values()) > total_bandwidth_mhz:
        raise ValueError("allocation exceeds the available cell bandwidth")
    return proposed


@dataclass
class _Packet:
    arrival: float
    retries: int = 0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1)]


def simulate_ran(
    control: RanControl | None = None,
    *,
    seed: int = 7,
    on_frame: Callable[[dict[str, Any]], None] | None = None,
    phase: str = "normal",
) -> dict[str, Any]:
    """Run independent UE arrival processes and a capacity-limited slot server.

    Assumptions are explicit: fixed four bits/RE (16-QAM),
    coding rate .5, one layer, 75% usable REs, no channel estimator or PHY.
    A failed transmission consumes airtime and is retried at most max_retries.
    """
    import simpy

    control = control if control is not None else RanControl()
    control.validate()
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be an integer in 0..2**32-1")
    env = simpy.Environment()
    # Independent streams prevent scheduler RNG draws from changing arrivals.
    arrival_rngs = [random.Random(seed + 1009 * (i + 1)) for i in range(control.ue_count)]
    radio_rng = random.Random(seed ^ 0xA5A5)
    queues: list[deque[_Packet]] = [deque() for _ in range(control.ue_count)]
    arrived = [0] * control.ue_count
    served = [0] * control.ue_count
    dropped_queue = [0] * control.ue_count
    dropped_retry = [0] * control.ue_count
    max_depth = [0] * control.ue_count
    delays: list[float] = []
    attempts = 0
    retries = 0
    used_bits = 0
    slots = 0
    next_ue = 0
    telemetry: list[dict[str, Any]] = []
    bits_per_packet = control.packet_bytes * 8
    # 12 subcarriers × 14 symbols × 4 modulation bits × coding × usable REs.
    slot_bits = PRBS_30KHZ[control.bandwidth_mhz] * 12 * 14 * 4 * 0.5 * 0.75
    if bits_per_packet > slot_bits:
        raise ValueError("packet exceeds one slot's capacity; fragmentation is outside this model")
    arrival_rate = control.offered_mbps_per_ue * 1e6 / bits_per_packet

    def source(index: int):
        while True:
            next_time = arrival_rngs[index].expovariate(arrival_rate)
            if env.now + next_time >= control.duration_seconds:
                return
            yield env.timeout(next_time)
            arrived[index] += 1
            if len(queues[index]) >= control.queue_limit_packets:
                dropped_queue[index] += 1
            else:
                queues[index].append(_Packet(float(env.now)))
                max_depth[index] = max(max_depth[index], len(queues[index]))

    def scheduler():
        nonlocal attempts, retries, used_bits, slots, next_ue
        while True:
            # Reserve only packets that existed at slot start. Packets arriving
            # during transmission cannot complete in that same slot.
            remaining = slot_bits
            visited = 0
            scheduled: list[tuple[int, _Packet]] = []
            while remaining >= bits_per_packet and visited < control.ue_count:
                index = next_ue
                next_ue = (next_ue + 1) % control.ue_count
                if not queues[index]:
                    visited += 1
                    continue
                visited = 0
                packet = queues[index].popleft()
                attempts += 1
                remaining -= bits_per_packet
                used_bits += bits_per_packet
                scheduled.append((index, packet))
            yield env.timeout(SLOT_SECONDS)
            slots += 1
            for index, packet in scheduled:
                if radio_rng.random() < control.retry_probability:
                    if (
                        packet.retries < control.max_retries
                        and len(queues[index]) < control.queue_limit_packets
                    ):
                        packet.retries += 1
                        retries += 1
                        queues[index].appendleft(packet)
                        max_depth[index] = max(max_depth[index], len(queues[index]))
                    else:
                        dropped_retry[index] += 1
                else:
                    served[index] += 1
                    delays.append((float(env.now) - packet.arrival) * 1000)

    def monitor():
        last_delivered = 0
        while True:
            yield env.timeout(0.01)
            delivered = sum(served)
            frame = {
                "suite": "telecom",
                "phase": phase,
                "t_seconds": round(float(env.now), 6),
                "t_ms": round(float(env.now) * 1000, 3),
                "queue_packets": sum(map(len, queues)),
                "per_ue_queue_packets": list(map(len, queues)),
                "served_packets": delivered,
                "dropped_packets": sum(dropped_queue) + sum(dropped_retry),
                "throughput_mbps": round(
                    (delivered - last_delivered) * bits_per_packet / 0.01 / 1e6, 6
                ),
                "allocated_prbs": PRBS_30KHZ[control.bandwidth_mhz],
                "bandwidth_mhz": control.bandwidth_mhz,
                "capacity_mbps": slot_bits / SLOT_SECONDS / 1e6,
            }
            telemetry.append(frame)
            if on_frame is not None:
                on_frame(dict(frame))
            last_delivered = delivered

    for ue in range(control.ue_count):
        env.process(source(ue))
    env.process(scheduler())
    env.process(monitor())
    # Bounded drain lets us assert exact packet conservation, including a
    # deliberately congested queue. The horizon includes all bounded retries.
    drain = (
        control.ue_count * control.queue_limit_packets * (control.max_retries + 1) + 2
    ) * SLOT_SECONDS
    env.run(until=control.duration_seconds + drain)
    queued = sum(map(len, queues))
    total_arrived = sum(arrived)
    total_served = sum(served)
    total_drop = sum(dropped_queue) + sum(dropped_retry)
    effective_duration = control.duration_seconds + drain
    fairness = (
        (sum(served) ** 2 / (len(served) * sum(n * n for n in served))) if any(served) else 0.0
    )
    return {
        "seed": seed,
        "bandwidth_mhz": control.bandwidth_mhz,
        "prbs": PRBS_30KHZ[control.bandwidth_mhz],
        "slot_ms": SLOT_SECONDS * 1000,
        "offered_mbps": control.offered_mbps_per_ue * control.ue_count,
        "arrival_window_seconds": control.duration_seconds,
        "observation_seconds": round(effective_duration, 9),
        "nominal_air_capacity_mbps": slot_bits / SLOT_SECONDS / 1e6,
        "packets_arrived": total_arrived,
        "packets_delivered": total_served,
        "packets_dropped_queue": sum(dropped_queue),
        "packets_dropped_retry": sum(dropped_retry),
        "packets_still_queued": queued,
        "tx_attempts": attempts,
        "retry_attempts_scheduled": retries,
        "transmitted_bits": used_bits,
        "capacity_bits": slots * slot_bits,
        "throughput_mbps": round(total_served * bits_per_packet / effective_duration / 1e6, 6),
        "latency_p50_ms": round(_percentile(delays, 0.5), 6),
        "latency_p95_ms": round(_percentile(delays, 0.95), 6),
        "latency_min_ms": round(min(delays, default=0.0), 6),
        "latency_max_ms": round(max(delays, default=0.0), 6),
        "jain_fairness": round(fairness, 6),
        "per_ue_delivered": served,
        "max_queue_depths": max_depth,
        "packet_conservation": total_arrived == total_served + total_drop + queued,
        "airtime_conservation": used_bits <= slots * slot_bits,
        "telemetry": telemetry,
    }


def run_telecom(
    seed: int = 7,
    task_core: str | None = None,
    on_frame: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run a positive scheduling study and adversarial control/overload cases."""
    checks: list[dict[str, Any]] = []
    control = RanControl()
    normal = simulate_ran(control, seed=seed, on_frame=on_frame)
    overloaded = simulate_ran(
        replace(control, offered_mbps_per_ue=40), seed=seed, on_frame=on_frame, phase="overload"
    )
    ledger = {"1-000001": 20, "1-000002": 60}
    updated = authorize_control(control, allocations=ledger)
    checks.extend(
        [
            {
                "name": "authorized_40mhz_allocation",
                "passed": updated == {"1-000001": 40, "1-000002": 60} and ledger["1-000001"] == 20,
            },
            {
                "name": "positive_real_packet_delivery",
                "passed": normal["packets_delivered"] > 100 and all(normal["per_ue_delivered"]),
            },
            {"name": "normal_latency_budget", "passed": normal["latency_p95_ms"] < 20},
            {"name": "normal_queue_has_headroom", "passed": normal["packets_dropped_queue"] == 0},
            {
                "name": "airtime_and_packet_conservation",
                "passed": all(
                    r["airtime_conservation"] and r["packet_conservation"]
                    for r in [normal, overloaded]
                ),
            },
            {
                "name": "finite_queue_overload_drops",
                "passed": overloaded["packets_dropped_queue"] > 0
                and max(overloaded["max_queue_depths"]) <= control.queue_limit_packets,
            },
            {
                "name": "bounded_queue_drained",
                "passed": normal["packets_still_queued"] == overloaded["packets_still_queued"] == 0,
            },
            {
                "name": "overload_preserves_ue_fairness",
                "passed": overloaded["jain_fairness"] > 0.95,
            },
            {
                "name": "reproducible_event_trace_metrics",
                "passed": normal == simulate_ran(control, seed=seed),
            },
        ]
    )
    attacks = {
        "reject_nonfinite_bandwidth": replace(control, bandwidth_mhz=float("nan")),
        "reject_boolean_bandwidth": replace(control, bandwidth_mhz=True),
        "reject_unsupported_bandwidth": replace(control, bandwidth_mhz=41),
        "reject_cross_slice_control": replace(control, slice_id="1-000002"),
        "reject_cell_overallocation": replace(control, bandwidth_mhz=60),
        "reject_private_value_as_slice": replace(control, slice_id="SUPI-001010000000001"),
        "reject_unbounded_queue": replace(control, queue_limit_packets=1_000_000),
        "reject_nonfinite_traffic": replace(control, offered_mbps_per_ue=float("inf")),
    }
    for name, attack in attacks.items():
        try:
            authorize_control(attack, allocations=ledger)
        except (ValueError, PermissionError):
            rejected = True
        else:
            rejected = False
        checks.append(
            {"name": name, "passed": rejected and ledger == {"1-000001": 20, "1-000002": 60}}
        )
    if task_core is not None:
        if not isinstance(task_core, str):
            raise ValueError("task_core must be text")
        checks.extend(
            [
                {
                    "name": "protected_core_retains_40mhz",
                    "passed": bool(re.search(r"\b40\s*MHz\b", task_core, re.I)),
                },
                {
                    "name": "protected_core_hides_synthetic_subscriber",
                    "passed": not any(
                        v in task_core for v in ("SUPI-302-720-009481726", "IMSI-3027209481726")
                    ),
                },
                {
                    "name": "protected_core_hides_synthetic_coordinates",
                    "passed": not any(v in task_core for v in ("51.0447N", "114.0719W")),
                },
            ]
        )
    return {
        "suite": "telecom",
        "engine": "SimPy discrete-event RAN contract model",
        "engine_version": version("simpy"),
        "seed": seed,
        "checks": checks,
        "metrics": {"normal": normal, "overload": overloaded},
        "telemetry": normal["telemetry"] + overloaded["telemetry"],
        "scope": "Actual SimPy queues/events with an idealized FR1 slot scheduler and local slice authorization. No RF, E2AP, RIC, 5GC registration or live subscriber network is simulated by this tier; use the separate upstream 5GC profile for protocol-stack integration.",
    }
