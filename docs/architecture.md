# Architecture and fidelity

```mermaid
flowchart LR
  F[Published synthetic case] --> S[Actual EigenScope SDK]
  S --> E[Configured EigenScope service]
  E --> C[Protected task core]
  C --> P[External provider / industry adapter]
  P --> R[Actual SDK rehydration]
  R --> V[Independent contract checks]
  V --> J[Sanitized JSON report]
  M[MuJoCo / SimPy / MCP / Git / HTTP] --> T[Actual execution telemetry]
  T --> D[Local live dashboard]
```

## Two execution paths

`run` executes local external-system scenarios without calling EigenScope. These verify the industry engine, protocol boundary, control validation and failure controls. They do not measure EigenScope's separation effectiveness.

`live` invokes the official SDK against the configured actual service. A real OpenAI client sends the resulting protected core to the local deterministic provider; the official SDK restores the response and consumes the mapping. Independent expectations inspect privacy, retained task terms, untrusted directives and owned-ticket cleanup. Robotics/telecom adapters also exercise the returned core. Separate public workbench cases compare the actual workbench answer with independently stated arithmetic results.

## Fidelity table

| Boundary | Executed here | Additional deployment needed |
|---|---|---|
| Robotics | Actual MuJoCo state integration, ray sensing, contacts, controller constraints and loopback ROS 2 / NVIDIA NIM `SAFE_HOLD` gate | A robot's calibrated dynamics, ROS2 DDS runtime and physical safety validation |
| Oil & Gas | CoolProp methane EOS, SciPy receiver dynamics, OPC UA SignAndEncrypt, loopback MQTT v3.1.1 Sparkplug B UNS & PI/CDF HTTP | Calibrated site/equipment model, Ignition Gateway/broker deployment (`infra/ignition/`) and certified SIS/ESD |
| Healthcare | SciPy two-compartment IV PK mass balance, clinician sign-off, HL7 FHIR R4 HTTP and SMART on FHIR v2 PKCE / `DocumentReference` | Validated clinical PK/PD models, EHR authentication and medical-device governance |
| Finance & data | In-memory SQLite SQL UDF row batching (`eigenscope_protect_row`), Decimal double-entry balance and loopback External Access dual-control HTTP | Production Snowflake/Databricks connectivity, bank payment controls and enterprise settlement rails |
| Legal & deal rooms | Structured M&A terms extraction, read-only authorization and loopback matter-scoped DMS isolation | Live iManage/NetDocuments/VDR authentication and legal sign-off workflows |
| Multimodal | Lossless PCM16 WAV, CRC-32 PNG and RGBA OCVC codecs, Parseval/STFT checks and SHA-256 restoration | Hosted or sidecar `/v1/decompose/{audio,image,video}` and `/v1/rehydrate/media` runtime (`live --case multimodal`) |
| Telecom fast scenario | Actual SimPy queue/resource simulation plus loopback O-RAN Non-RT RIC R1 rApp & GSMA CAMARA HTTP gate | Radio propagation/RF, live RIC deployment and production scheduling |
| Telecom heavy profile | Pinned Open5GS core + UERANSIM RAN/UE when launched and verified | Suitable Linux container host, virtual-network capabilities; no physical RF |
| Provider | Actual OpenAI HTTP/SSE client and deterministic local endpoint | Actual model behavior, provider-network latency and commercial credentials |
| Tools | Actual official MCP stdio handshake/calls and ServiceNow Scoped ITSM work-note HTTP gate | A deployment's real tools, authorization and host configuration |
| Coding | Actual Git worktree and executable checks in temporary directories | A deployment's repository, CI permissions and IDE/proxy integration |

The lab never substitutes its own privacy engine for EigenScope. Protocol-wire fixtures in unit tests are explicitly synthetic and exercise client validation only.

## Event and report design

Engines emit measured state while running. The dashboard streams bounded events over SSE and keeps a limited run history. Optional pacing makes fast simulations readable while showing simulation and wall clocks separately. It is a diagnostic view, not a physical control surface.

The CLI emits versioned JSON with separate PASS/FAIL/BLOCKED/ERROR counts. Missing prerequisites cannot become passes. Runtime exceptions omit raw exception text to avoid accidentally publishing credentials. Reports use atomic file replacement.

## Dependency policy

`uv.lock` and the Node lockfile pin artifact integrity. Intel macOS uses MuJoCo 3.3.x, which supplies a compatible wheel; other platforms resolve the compatible current line recorded in the lockfile. Current cryptography may require Rust and OpenSSL to build on Intel macOS; do not substitute a vulnerable release to avoid compilation. Review platform constraints during dependency updates and run the security audit.
