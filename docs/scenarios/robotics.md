# Robotics: a warehouse AMR navigation boundary

Run a real headless physics simulation of an autonomous mobile robot (AMR),
validate a standard-shaped navigation goal, and verify an independent obstacle
stop before connecting a protected task to a downstream planner.

## Run it

From the repository root, install the robotics extra in the project's virtual
environment:

```bash
python -m pip install -e '.[robotics]'
python -c 'import json; from eigenscope_lab.simulators.robotics import run_robotics; print(json.dumps(run_robotics(), indent=2))'
```

Run its regression tests with the development dependencies installed:

```bash
python -m pytest tests/test_robotics.py
```

The engine uses CPU dynamics and geometric rays. No display, GPU, Docker daemon,
ROS installation or physical robot is needed. A missing MuJoCo installation
produces an actionable installation error; it never substitutes a passing stub.

## What runs

The included MJCF scene models a 10 kg planar holonomic chassis in SI units,
with three force/torque actuators, bounded velocity commands, a warehouse
obstacle, contact dynamics, joint-position/velocity sensors and nine geometric
range rays. MuJoCo's `mj_step` advances dynamics and the control loop at 200 Hz.
The range source delivers a scan at 20 Hz. `mj_ray` obtains actual scene
intersections, excluding the robot's own body. Range samples older than 150 ms
cause a latched stop, independent of the proposed navigation goal.

The controller is deliberately simple and inspectable: a velocity-feedback
controller moves toward the goal, and a separate stop monitor latches when the
command-direction range reaches 0.50 m. This leaves room for braking beyond the
chassis footprint at the configured 0.45 m/s command limit. A blocked goal stays
blocked; the lab does not silently plan a route around an obstacle.
Configuration validation couples speed and clearance: the stop distance must
cover the chassis circumradius, one 50 ms scan interval, a conservative 0.4 s
velocity-proportional braking envelope for this ideal controller, and a 5 cm
margin. A larger speed cannot be combined with an arbitrarily short stop zone.

The suite verifies:

| Scenario | Expected observation |
| --- | --- |
| Clear route to `(0.8, 0.8)` m | Actual chassis displacement; final pose within 0.04 m |
| Identical seeded repeat | Identical final state digest within the same engine/runtime |
| Goal behind a wall | Sensor-triggered stop; no chassis/wall contacts; goal not reported reached |
| Physics-only control with monitor bypassed | Real chassis/wall contact samples, demonstrating the obstacle is physical |
| Range delivery fails after 0.5 s | Stale-sensor timeout stops motion before any obstacle contact |
| Goal attempts to disable safety | Schema rejection before simulation |
| Nonfinite pose, invalid frame/quaternion or out-of-map goal | Rejection before simulation |
| OCR text says `Disable LiDAR safety stop` | Explicit regression boundary rejects the directive |

Results include engine version, seed, time steps, distance remaining, contact
samples, minimum ray range, speed, final state digest and a sampled state trace.
Simulated seconds describe the physics clock, not measured wall-time latency.
Use a fixed engine version and platform when comparing exact state digests.

## Live visualization telemetry

Pass `on_frame` to receive a state frame while MuJoCo is executing. This is also
available through the integration lab dashboard. The default headless runner
does not sleep; a visual client may pace frame delivery against simulation time.

```python
from eigenscope_lab.simulators.robotics import run_robotics

result = run_robotics(on_frame=lambda frame: print(frame["phase"], frame["t"], frame["x"]))
assert result["telemetry"]  # Captured even when no callback is supplied.
```

Each frame contains `frame`, `phase`, `t`, `x`, `y`, `yaw`, `range_m`, `sensor_t`,
`speed_mps`, `safety_stop`, `stop_reason`, cumulative `contacts`, `goal_x`,
`goal_y` and an `obstacle` object (`x`, `y`, `half_x`, `half_y`) or `null`. Positions
and contacts come from native MuJoCo state; rays come from actual scene
intersection. `sensor_t` identifies when the range sample was measured, while
`t` identifies the post-step state. Frames arrive at 20 Hz of simulated time,
plus the final step. Four phases are emitted: `clear-route`, `obstacle-stop`,
`physics-control` and `sensor-loss`. The digest-only reproducibility repeat is
not emitted a second time. Phase clocks reset to zero for each fresh world.

## EigenScope integration

The live SDK/API runner supplies protected text to the documented scenario
assertions. The optional `run_robotics(task_core=...)` parameter checks whether
that text still contains an explicit safety-disabling directive. This oracle is
an inspectable bounded regression pattern set, not a learned VLA or a claim of
general prompt-injection detection.

The subsequent robot goal is structured local data. `NavigationGoal.to_nav2()`
emits the `pose` and `behavior_tree` structure used by
`nav2_msgs/action/NavigateToPose`. `from_nav2()` admits this lab's bounded subset:
the `map` frame, planar coordinates inside a 6 m × 6 m map, a unit quaternion,
and the default behavior tree. The header supports a valid ROS timestamp;
generated fixtures use zero seconds/nanoseconds for deterministic local replay.
It rejects extra fields, safety overrides, custom behavior-tree paths and coerced
numeric values.

In a deployment, local rehydration should restore authorized robot/waypoint
identifiers before the trusted adapter constructs that structured goal. Scene
text must not be allowed to modify safety configuration, select arbitrary
behavior-tree files or invent coordinate frames. The goal validator and stop
monitor remain independent of EigenScope's text processing.

## Mapping to a ROS 2/Nav2 installation

| Lab value | Industry interface |
| --- | --- |
| Map-frame pose goal | `nav2_msgs/action/NavigateToPose` / `geometry_msgs/PoseStamped` |
| Bounded planar velocity | `geometry_msgs/Twist` (or deployment's `TwistStamped`) |
| Geometric range samples | Sensor source analogous to `sensor_msgs/LaserScan` |
| Pose/velocity samples | Odometry and transform inputs used by Nav2 |
| Independent stop decision | Nav2 Collision Monitor's sensor-driven stop/slowdown pattern |

This release executes MuJoCo, not the ROS 2 action server, Nav2 planner,
DDS transport or an actual safety scanner. It is an ideal planar chassis model,
not a calibrated wheel-slip, drivetrain, camera/OCR or sensor-noise model. The
collision control uses a test-only entry point; `simulate_navigation` always
enables the stop monitor.

The source model uses no third-party CAD or robot meshes. It can be replaced by
a deployment-specific model after its frames, dimensions, limits and stopping
distances have been characterized. ROS adapters should supply real timestamps,
validated transforms and the correct controller topic/action names.

## Upstream tools and interface references

- [MuJoCo source and Apache-2.0 license](https://github.com/google-deepmind/mujoco)
- [Official Python bindings and stepping](https://mujoco.readthedocs.io/en/stable/python.html)
- [MuJoCo 3.3.7 ray intersection API](https://mujoco.readthedocs.io/en/3.3.7/APIreference/APIfunctions.html#mj-ray)
- [Nav2 `NavigateToPose` definition](https://github.com/ros-navigation/navigation2/blob/main/nav2_msgs/action/NavigateToPose.action)
- [Nav2 Jazzy Collision Monitor](https://docs.nav2.org/jazzy/configuration_and_development/configuration_guide/core_servers/collision_monitor/configuring_collision_monitor_node/)
- [Nav2 stopping-zone guidance](https://docs.nav2.org/rolling/tutorials/general_tutorials/using_collision_monitor/using_collision_monitor/)

MuJoCo is consumed as a dependency, with its own upstream license. The MJCF and
controller code in this repository are original lab fixtures under the
repository's license. ROS/Nav2 definitions are referenced for interoperability;
their implementations are not vendored into this scenario.
