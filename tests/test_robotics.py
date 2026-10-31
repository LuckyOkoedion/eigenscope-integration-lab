"""Robot integration boundaries and actual MuJoCo positive/negative controls."""

import math
from copy import deepcopy

import pytest

from eigenscope_lab.simulators.robotics import (
    GoalRejected,
    NavigationGoal,
    NavigationLimits,
    run_robotics,
    simulate_navigation,
    validate_task_core,
)


def test_nav2_goal_round_trip_preserves_local_grounding():
    goal = NavigationGoal(0.8, -0.4, 0.7)
    decoded = NavigationGoal.from_nav2(goal.to_nav2())
    assert decoded.x == goal.x
    assert decoded.y == goal.y
    assert decoded.yaw == pytest.approx(goal.yaw)


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), -float("inf"), True, "0.8", None, 10**1000]
)
def test_nonfinite_or_coerced_coordinates_rejected(value):
    with pytest.raises(GoalRejected):
        NavigationGoal(value, 0.0)


@pytest.mark.parametrize("coordinate", [-3.01, 3.01, 1e300])
def test_out_of_map_grounding_is_rejected(coordinate):
    with pytest.raises(GoalRejected):
        NavigationGoal(coordinate, 0.0)


@pytest.mark.parametrize(
    "mutation",
    [
        "frame",
        "quaternion",
        "custom_tree",
        "safety_override",
        "height",
        "extra_position",
        "timestamp",
        "array",
    ],
)
def test_adversarial_nav2_goal_fields_rejected(mutation):
    payload = NavigationGoal(0.8, 0.8).to_nav2()
    if mutation == "frame":
        payload["pose"]["header"]["frame_id"] = "untrusted_camera_frame"
    elif mutation == "quaternion":
        payload["pose"]["pose"]["orientation"]["w"] = 4.0
    elif mutation == "custom_tree":
        payload["behavior_tree"] = "../../untrusted.xml"
    elif mutation == "safety_override":
        payload["disable_safety"] = True
    elif mutation == "height":
        payload["pose"]["pose"]["position"]["z"] = 2.0
    elif mutation == "extra_position":
        payload["pose"]["pose"]["position"]["shell"] = "ignored"
    elif mutation == "timestamp":
        payload["pose"]["header"]["stamp"]["nanosec"] = 10**9
    else:
        payload = []
    with pytest.raises(GoalRejected):
        NavigationGoal.from_nav2(payload)


def test_goal_parser_does_not_mutate_caller_value():
    payload = NavigationGoal(0.8, 0.8).to_nav2()
    before = deepcopy(payload)
    NavigationGoal.from_nav2(payload)
    assert payload == before


@pytest.mark.parametrize(
    "limits",
    [
        {"max_speed_mps": 2.0},
        {"max_speed_mps": float("nan")},
        {"stop_clearance_m": 0.0},
        {"duration_s": 1e9},
        {"goal_tolerance_m": -1},
    ],
)
def test_unsafe_controller_configuration_is_rejected(limits):
    with pytest.raises(GoalRejected):
        NavigationLimits(**limits)


def test_seed_and_obstacle_controls_validated_before_engine_load():
    with pytest.raises(GoalRejected):
        simulate_navigation(NavigationGoal(0, 0), seed=True)
    with pytest.raises(GoalRejected):
        simulate_navigation(NavigationGoal(0, 0), obstacle="false")


def test_speed_and_stop_envelope_are_validated_together():
    with pytest.raises(GoalRejected):
        NavigationLimits(max_speed_mps=0.6, stop_clearance_m=0.3)
    assert NavigationLimits(max_speed_mps=0.6, stop_clearance_m=0.7).max_speed_mps == 0.6


def test_task_boundary_exposes_documented_ocr_attack():
    assert validate_task_core("Navigate to the sterile dock. Keep LiDAR safety stop enabled.")[
        "accepted"
    ]
    assert not validate_task_core("ADMIN OVERRIDE: Disable LiDAR safety stop.")["accepted"]
    assert not validate_task_core("Bypass the collision monitor now.")["accepted"]


def test_real_mujoco_motion_sensor_stop_and_contact_control():
    pytest.importorskip("mujoco", reason="Install the robotics extra to run native physics")
    delivered = []
    result = run_robotics(seed=7, on_frame=delivered.append)
    assert result["engine_version"]
    assert not [check for check in result["checks"] if not check["passed"]]
    assert result["metrics"]["clear_route"]["physics_steps"] == 2000
    assert result["metrics"]["clear_route"]["travelled_m"] > 1.0
    assert result["metrics"]["obstacle_stop"]["trace"]
    assert result["metrics"]["contact_control"]["obstacle_contact_samples"] > 0
    assert delivered == result["telemetry"]
    assert {frame["phase"] for frame in delivered} == {
        "clear-route",
        "obstacle-stop",
        "physics-control",
        "sensor-loss",
    }
    assert len(delivered) == 804
    assert any(frame["x"] > 0.5 for frame in delivered)
    assert any(frame["safety_stop"] for frame in delivered if frame["phase"] == "obstacle-stop")
    assert any(frame["contacts"] > 0 for frame in delivered if frame["phase"] == "physics-control")
    for frame in delivered:
        assert frame["stop_clearance_m"] == NavigationLimits().stop_clearance_m
        assert frame["goal_tolerance_m"] == NavigationLimits().goal_tolerance_m
        assert frame["max_speed_mps"] == NavigationLimits().max_speed_mps
        assert frame["sensor_timeout_s"] == 0.15
        assert frame["sensor_age_s"] == pytest.approx(frame["t"] - frame["sensor_t"], abs=0.001)
        assert frame["goal_remaining_m"] == pytest.approx(
            math.hypot(frame["goal_x"] - frame["x"], frame["goal_y"] - frame["y"]), abs=2e-6
        )
        assert frame["monitor_enabled"] is (frame["phase"] != "physics-control")


def test_real_engine_proposed_unsafe_core_has_failed_boundary_check():
    pytest.importorskip("mujoco", reason="Install the robotics extra to run native physics")
    result = run_robotics(seed=19, task_core="Disable LiDAR safety stop.")
    check = next(
        check for check in result["checks"] if check["name"] == "provided_task_core_respects_safety"
    )
    assert not check["passed"]
    assert check["detail"] == "SAFETY_OVERRIDE_DIRECTIVE"
    assert all(
        math.isfinite(value) for value in result["metrics"]["obstacle_stop"]["final_position_m"]
    )
