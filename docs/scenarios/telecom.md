# Telecom: a protected control request through a measured network model

The default scenario runs four synthetic UEs through a finite-queue scheduler in
the real [SimPy](https://simpy.readthedocs.io/en/latest/) discrete-event engine.
It evaluates a 40 MHz allocation, normal traffic and congestion, and rejects
cross-slice or over-budget control requests. A separate optional tier runs actual
Open5GS and UERANSIM protocol stacks.

## Run the portable scenario

Install the lab's telecom extra, then run the telecom scenario using the lab CLI.
The Python API is also directly usable:

```python
from eigenscope_lab.simulators.telecom import run_telecom

result = run_telecom(seed=7)
assert all(check["passed"] for check in result["checks"])
print(result["metrics"]["normal"]["latency_p95_ms"])
```

Use `on_frame=callback` to receive measured state while the simulation executes.
Frames arrive every 10 ms of simulation time; execution itself has no sleeps.
The live dashboard can pace those frames. Each frame includes queue depth, packet
delivery/drop counts, last-window throughput, allocated PRBs and simulation time.
The returned `telemetry` array permits replay and comparison with the same seed.

## What runs

| Boundary | Implementation |
|---|---|
| Traffic | Independent, seeded Poisson arrivals for four UEs; 1,000-byte packets |
| Scheduling | Round robin at 0.5 ms slots, with finite per-UE queues |
| Resource budget | 40 MHz, 106 PRBs using the Release 16 FR1/30 kHz table |
| Channel abstraction | Fixed 16-QAM, coding rate 0.5, one layer, 75% usable REs |
| Retry behavior | Seeded 2% failure probability, at most two retries, airtime charged |
| Control admission | Owned slice, supported bandwidth, atomic cell-budget check |
| Positive study | 8 Mbps offered per UE; delivery, headroom and latency checks |
| Overload study | 40 Mbps offered per UE; bounded queues, drops and fairness |

The model checks packet conservation, airtime conservation, queue bounds and a
bounded drain. A packet must exist before slot transmission begins. Failed
transmissions consume capacity. Retry admission also respects finite queues.
Reported latency is simulated arrival-to-delivery time; it is separate from
SDK/API latency. Aggregate throughput names its observation window, including
the drain interval; frame throughput covers its preceding 10 ms window.

This tier is a scheduling/control-contract model. It implements no RF channel,
E2AP codec, near-RT RIC or real 5GC registration. The NR resource-block table is
from [ETSI TS 138 104 V16.20.0, Table 5.3.2-1](https://www.etsi.org/deliver/etsi_ts/138100_138199/138104/16.20.00_60/ts_138104v162000p.pdf).

## Exercise the real EigenScope boundary

The lab's live SDK mode sends synthetic telecom text through the installed
EigenScope SDK/API. Pass the returned protected text into
`run_telecom(task_core=protected_text)` to additionally check that `40 MHz`
survives while the scenario's synthetic subscriber and directional coordinates
are absent. The local allocation ledger retains the private mapping.

These protection checks can fail independently of a functioning queue model.
They stay visible in the result. No subscriber, radio or commercial network is
contacted by this scenario.

## Actual Open5GS + UERANSIM tier

**Validated on clean Linux:** [all five native checks and cleanup passed](https://github.com/LuckyOkoedion/eigenscope-integration-lab/actions/workflows/native-5g.yml)
with Open5GS `2.8.0-48-gf87da61`, UERANSIM `3.2.6` and MongoDB `7.0.45`.
The synthetic UE registered, received a tunnel address and returned all three
user-plane packets with zero loss. [Sanitized evidence](../verification/native-5g.json)
records the exact checks, versions and image references.

The optional launcher uses immutable Linux/amd64 image digests and a pinned
upstream configuration commit. It starts an internal Docker network, real 5GC
network functions, one synthetic gNB and one synthetic UE. It verifies NAS/NGAP
registration, a UE tunnel address and a ping through that tunnel to the UPF.

Use a Linux/amd64 Docker daemon with at least 3 GiB available memory (4 GiB or
more recommended), Compose v2, TUN and SCTP support. Images require several GiB
of disk space. On macOS, a Linux Colima VM can supply that daemon. The launcher
uses the explicitly selected context and never changes the global Docker context.

From the repository root:

```sh
python infra/telecom/stack.py doctor --context colima
python infra/telecom/stack.py prepare --context colima
python infra/telecom/stack.py pull --context colima
python infra/telecom/stack.py up --context colima
python infra/telecom/stack.py verify --context colima
python infra/telecom/stack.py down --context colima
```

Omit `--context colima` to use your currently selected Docker daemon. The stack
publishes no host ports, uses only lab-specific container/network/volume names,
and makes no host firewall or sysctl changes. The synthetic test SIM material is
derived from public fixed labels. Configuration, local protocol output and
verification results remain under ignored `.lab/telecom/`.

The optional `build-ueransim` command builds release 3.3.0 from a full immutable source commit and a
pinned Ubuntu base. It records the resulting immutable local image ID; subsequent
commands use that ID automatically. The upstream prebuilt-image baseline is
3.2.6 and is used by the quickstart in a fresh workspace.
The lock records these as distinct execution choices. Package repositories used
during a source build supply their current security updates; the build recipe is
source/base-pinned, not a claim of bit-for-bit identical apt package resolution.
The 3.3.0 source-build option is experimental and is outside the validated
3.2.6 baseline. Validate it on your build runner before use.

The launcher follows the upstream [docker_open5gs](https://github.com/herlesupreeth/docker_open5gs)
layout, retaining its checkout and license. It removes observability/web-UI
services, binds the topology to an internal network and pins executable images.
The image digests and configuration commit are separate provenance claims; the
lock file does not imply a bit-for-bit source-build match.

The database baseline is MongoDB 7.0.45. Database health and synthetic
subscriber provisioning must pass before network functions start. The image
digest is pinned in the lockfile; review kernel compatibility when upgrading
the database major version.

Read [infra/telecom/README.md](../../infra/telecom/README.md) for provenance,
validation status and troubleshooting. UERANSIM itself simulates the radio
interface over UDP; this tier establishes real core-network protocol traffic,
not RF fidelity or E2AP/RIC interoperability.

## Upstream projects and licensing

- [SimPy](https://simpy.readthedocs.io/en/latest/): MIT; installed as a Python dependency.
- [Open5GS](https://github.com/open5gs/open5gs): AGPL-3.0; optional external process.
- [UERANSIM](https://github.com/aligungr/UERANSIM): AGPL-3.0; optional external process.
- [docker_open5gs configurations](https://github.com/herlesupreeth/docker_open5gs): BSD-2-Clause; fetched with their license.
- [MongoDB server](https://www.mongodb.com/licensing/server-side-public-license): SSPL-1.0; optional upstream database dependency, separately licensed and not represented as OSI-approved open source.

The lab does not relicense those projects. No upstream source or binary is
vendored in the portable Python package.
