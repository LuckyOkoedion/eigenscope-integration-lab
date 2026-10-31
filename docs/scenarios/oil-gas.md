# Oil and gas: a gas-compression operations copilot

Run a bounded methane discharge receiver, review a compressor-load proposal,
and observe independent shutdowns when an outlet is restricted or a pressure
source stops updating. CoolProp supplies real-fluid properties, SciPy advances
the inventory balance, and asyncua exercises an actual local OPC UA server and
client. No plant connection, production credentials or industrial hardware is
required.

## Run it

```bash
python -m pip install -e '.[oil-gas]'
python -c 'import json; from eigenscope_lab.simulators.oil_gas import run_oil_gas; print(json.dumps(run_oil_gas(), indent=2))'
python -m pytest tests/test_oil_gas.py tests/test_oil_gas_opcua.py
```

The optional dependencies are real engines, not passing substitutes. Missing
dependencies produce an installation error. The OPC UA contract opens only a
temporary loopback endpoint and closes its clients, server and background tasks
when it finishes. It does not connect to an external OPC UA endpoint.

## Process model and units

The receiver contains well-mixed, pure methane at a fixed 300 K. Its inventory
and pressure satisfy:

```text
d(mass)/dt = compressor_inflow - downstream_outflow
density = mass / volume
absolute_pressure = HEOS_methane(density, temperature)
```

`CoolProp.AbstractState("HEOS", "Methane")` supplies the pressure relationship.
Density and temperature are passed directly to its low-level interface. The
operating temperature is above methane's critical temperature, and the declared
pressure range remains in a homogeneous gas-like region. SciPy's `solve_ivp`
uses DOP853 with relative tolerance `1e-9`, absolute tolerance `1e-11`, and a
maximum step of 0.125 simulated seconds. Terminal events locate pressure and
source-age trips inside a reporting interval. Integration then continues with
compressor/inlet flow set to zero and the shutdown latch retained.

**Constant temperature assumes an external heat reservoir.** The model does not
solve an energy balance, calculate reservoir heat duty or predict compression
heating, cooling failure, hydrate formation or gas temperature excursions.

| Parameter | Synthetic lab value |
| --- | --- |
| Receiver volume | 2 m³ |
| Gas temperature | 300 K |
| Initial receiver pressure | 1,500,000 Pa absolute |
| Downstream process pressure | 800,000 Pa absolute |
| High-high trip threshold | 2,100,000 Pa absolute |
| Compressor mass-flow capacity | 0.65 kg/s at load fraction 1 |
| Normal outlet effective area | 0.00018 m² |
| Outlet discharge coefficient / heat-capacity ratio | 0.8 / 1.31 |
| Source freshness deadline | 1 simulated second |
| Telemetry reporting interval | 0.25 simulated seconds |

The compressor is an ideal commanded mass-flow source: capacity times load,
with instantaneous isolation on a trip. It has no compressor map, shaft-speed,
surge, recycle, pressure-ratio, power or actuator dynamics. Initial load balances
the outlet flow. The outlet uses the ideal-gas isentropic nozzle approximation,
with continuous subcritical and choked branches. The receiver uses real-fluid
properties; the nozzle closure does not. Flow goes to the stated downstream
process, never to an ambient vent, and reverse flow is excluded.

These are inspectable synthetic parameters, not a vessel rating, valve sizing,
operating envelope or relief-system design for a physical installation.

## Five operating phases

The default run emits 360 frames covering 90 simulated seconds:

| Phase | Duration | Expected result |
| --- | --- | --- |
| `stable-production` | 20 s | Balanced inflow/outflow holds receiver pressure |
| `reviewed-load-change` | 20 s | A locally reviewed increase of 0.06 load fraction is applied |
| `rejected-proposals` | 10 s | Extra safety fields, a trip-setpoint operation, an unknown asset and an unapproved load proposal are rejected |
| `outlet-restriction` | 30 s | Outlet opening falls to 0.12; true pressure triggers the high-high latch despite a fresh published pressure fixed at 900,000 Pa |
| `stale-sensor-stop` | 10 s | A separate receiver's source timestamp freezes at 2 s; inflow stops at its 3 s freshness deadline |

The final phase creates a separate initialized experiment so that source-age
shutdown can be tested independently of the existing pressure latch. It is not
an automatic restart or a remote reset of the first receiver. Neither the
structured action interface nor the task text can disable, raise or reset a
shutdown threshold. The simulated safety monitor reads process truth separately
from the published copilot pressure. A real installation would need independent
instrumentation, power, logic and final elements; separate Python logic does
not establish physical independence or a certified safety function.

## Guarded action boundary

An operations copilot may propose only a `set_compressor_load` action for
`lab-methane-compressor-01`. The strict object contains exactly:

```json
{
  "asset_id": "lab-methane-compressor-01",
  "operation": "set_compressor_load",
  "value": 0.62,
  "unit": "fraction",
  "issued_at_s": 20.0,
  "expires_at_s": 22.0
}
```

Values must be finite JSON numbers; booleans and numeric strings are rejected.
Load is restricted to `[0, 0.85]`, each action changes load by at most 0.10, and
accepted changes are additionally limited to 0.02 fraction per elapsed simulated
second. A timestamp must be current, nonfuture and unexpired; its validity
interval may not exceed five seconds. The sensor source must also be fresh.
An existing shutdown latch rejects restart proposals.

`LocalOperatorApprovals` is the trusted local harness that issues a synthetic
opaque approval handle for one exact action digest. `apply_action` consumes it
once after validating the command and process state. Changing the value,
asset, unit or timestamp invalidates the approval. A caller's `approved: true`
field is an unsupported field, not authorization. Handles stay in memory and
are cleared in `finally`; they are not included in reports or telemetry. This
demonstrates approval binding and replay prevention, not operator identity,
credential management or production authorization.

The optional `task_core` argument checks a documented set of explicit directives
such as disabling ESD, bypassing approval, raising a trip threshold or venting
methane. It reports a failed boundary check while the structured gate and
shutdown remain active. It is a deterministic regression oracle, not a learned
planner or general natural-language safety detector.

## Numerical and protocol verification

The report checks positive inventory, inventory conservation, stable production,
the approved load change, rejected proposals, a truth-based pressure trip,
zero inflow after a latch, stale-source shutdown and a strictly increasing
callback clock. Additional numerical controls compare:

- A low-pressure HEOS state against an independently specified methane ideal-gas
  constant. This is a limiting-case check, not validation across the entire EOS.
- A closed outlet with constant inflow against `m(t) = m(0) + inflow × t`.
- The same transient with maximum solver steps of 0.125 and 0.03125 s.

Cumulative inlet and outlet mass are integrated alongside inventory; their
residual is reported. The separate analytic control helps detect errors that
the integrated residual alone would miss.

After physics, `run_opcua_contract()` performs real loopback OPC UA operations.
Its checks appear with an `opcua_` prefix and its measurements under
`metrics.opcua`. It verifies the copilot's restricted role, authorized bounded
operator load writes, DataValue quality and UTC source freshness, and cleanup.
The process demonstration and the protocol contract are separate: telemetry
does not claim that each physics sample traverses OPC UA or that protocol
permissions alone implement an industrial shutdown system. See the protocol
module for the exact permitted nodes and methods. The actual loopback channel
uses OPC UA Basic256Sha256 **SignAndEncrypt**, separate ephemeral RSA-2048
application certificates, an explicit server-certificate pin and a server-side
client-certificate pin. Only encrypted session endpoints are offered. OPC UA
discovery can use an unencrypted channel; a raw low-level activation attempt
with otherwise valid fixture credentials is rejected without the pinned channel
certificate. Other negative controls try a wrong server pin and an untrusted
client certificate. Temporary
private keys are created with mode 0600 where POSIX modes apply and removed
when the contract ends, including on exceptions. These local application pins
do not represent a production PKI, OEM certification or plant deployment.

## Telemetry and timing

```python
from eigenscope_lab.simulators.oil_gas import run_oil_gas

result = run_oil_gas(
    seed=7,
    on_frame=lambda frame: print(frame["t"], frame["pressure_pa"], frame["stop_reason"]),
)
assert all(check["passed"] for check in result["checks"])
```

Frames contain a global `frame` index and strictly increasing `t`, plus
`phase`, `phase_time_s`, true `pressure_pa`, `published_pressure_pa`,
`hh_pressure_pa`, `downstream_pressure_pa`, `mass_kg`, `inflow_kg_s`,
`outflow_kg_s`, effective `compressor_load`, `requested_load`, `outlet_opening`, `sensor_age_s`,
`sensor_quality`, `safety_stop`, `stop_reason`, `trip_pressure_pa` and
`action_status`. Pressure is always absolute Pa; mass and flow use kg and kg/s.
The published pressure is a source sample at the beginning of its reporting
interval; the true pressure is the post-integration state. After a trip,
normal downstream outflow may lower pressure while the latch stays active.
`trip_pressure_pa` records pressure at the shutdown event itself.
Effective `compressor_load` becomes zero when shutdown isolates inflow;
`requested_load` retains the last accepted command for inspection.

`t` joins the phase demonstrations into a display clock. `phase_time_s` starts
at zero for each phase; receiver source-age calculations use that receiver's
continuous physics clock. `seed` is validated and recorded for a common runner
interface; this model currently has no stochastic disturbances.

The default run does not sleep. Optional `pacing_ms` adds 0–100 wall-clock
milliseconds per emitted frame for display pacing. Simulated seconds, display
pacing, OPC UA wall-clock freshness and EigenScope HTTP request timings are
different measurements. Neither solver speed nor paced animation is a claim
about physical plant or API latency. Callback exceptions propagate, and local
approval state is cleared on cancellation.

## Scope and upstream references

This release covers a small gas-compression control boundary, not a complete
production train, multiphase separator, flare/relief network or digital twin.
It does not model mixed natural-gas composition, liquids, corrosion, emissions,
linepack, mechanical failure or a certified SIS/ESD. Its assertions support
developer integration testing and do not replace process hazard analysis,
instrument validation or plant operating procedures.

- [CoolProp low-level AbstractState API](https://coolprop.org/coolprop/LowLevelAPI.html)
- [CoolProp methane EOS and fluid references](https://coolprop.org/fluid_properties/fluids/Methane.html)
- [SciPy solve_ivp tolerances and terminal events](https://docs.scipy.org/doc/scipy/reference/generated/scipy.integrate.solve_ivp.html)
- [asyncua server and access-control APIs](https://opcua-asyncio.readthedocs.io/en/latest/api/asyncua.server.html)
- [OPC UA DataValue quality and timestamps](https://reference.opcfoundation.org/Core/Part4/v105/docs/7.11)
- [NIST SP 800-82 Rev. 3, Guide to Operational Technology Security](https://csrc.nist.gov/pubs/sp/800/82/r3/final)

CoolProp is MIT-licensed, SciPy uses BSD-3-Clause, and asyncua uses
LGPL-3.0-or-later. They are installed dependencies under their upstream licenses.
The receiver model and boundary tests are original repository code.
