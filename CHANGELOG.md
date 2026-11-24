# Changelog

## 0.3.2 — 2026-11-23

- Upgrade Python, TypeScript, Wasm, CLI, MCP, and IDE SDK pins and documentation to official **0.2.18** releases (`eigenscope-sdk==0.2.18`, `@eigenscope/sdk@0.2.18`, `@eigenscope/orthocore-wasm@0.2.18`, `@eigenscope/mcp-server@0.2.18`, `@eigenscope/init@0.2.18`, `@eigenscope/pi-firewall@0.2.18`) across Python (`uv.lock`) and TypeScript (`package-lock.json`) lockfiles.

## 0.3.1 — 2026-11-23

- Fix `infra/ignition/stack.py` to forward `capture_output=capture` (instead of `capture=capture`) to `subprocess.run`, and add unit regression test `tests/test_ignition_stack.py::test_execute_forwards_capture_output` (verified end-to-end against live Ignition `8.1.48` Trial + Mosquitto `2.0.20`).

## 0.3.0 — 2026-11-23

- Upgrade Python, TypeScript and Wasm SDK pins to official **0.2.17** releases (`eigenscope-sdk==0.2.17`, `@eigenscope/sdk@0.2.17`, `@eigenscope/orthocore-wasm@0.2.17`) across Python and TypeScript lockfiles.
- Add the **Finance & data** scenario (`eigenscope-lab run finance`, `live --case finance`, dashboard UI, `docs/scenarios/finance.md`, and `finance` live case in `typescript/examples/live.ts`): real in-memory SQLite SQL scalar UDF execution (`eigenscope_protect_row`) over loopback External Access HTTP, exact `Decimal` double-entry conservation, single-use dual-control compliance approvals, and content-free audit receipts.
- Plug in loopback protocol contracts for all new global distribution clients without extra dependencies:
  - **Oil & Gas:** real loopback TCP MQTT v3.1.1 broker & Sparkplug B UNS (`DDATA`, `NCMD`/`DCMD` rejection, `WRITE_COIL`/`BYPASS_SIS` blocking, engineering units preservation) + AVEVA PI Web API / Cognite Data Fusion read-only historian contract (`oil_gas_sparkplug.py`).
  - **Optional Ignition 8.1 + Mosquitto Docker Stack (`infra/ignition/`):** isolated, loopback-bound launcher (`doctor`, `prepare`, `pull`, `up`, `verify`, `down`) with explicit `--accept-ignition-eula` confirmation and `--allow-unsigned-modules` support for local `.modl` testing.
  - **Healthcare:** SMART on FHIR App Launch v2 OAuth2 PKCE `S256` & `DocumentReference` resource checks validated against the bundled complete HL7 FHIR R4 JSON schema.
  - **Legal:** loopback matter-scoped DMS isolation & write-back rejection (`403` / `405`) with content-free counsel receipts.
  - **Telecom:** loopback O-RAN Non-RT RIC R1 rApp & GSMA CAMARA QoD filter contract blocking radio control mutations and raw `IMSI`/`SUPI` identifiers.
  - **Robotics:** loopback ROS 2 LifecycleNode & NVIDIA Isaac ROS / NIM pre-reasoning guardrail enforcing `SAFE_HOLD` on `/v1/health` `503` or gate `422`.
  - **MCP Tools & ITSM:** loopback ServiceNow Scoped ITSM work-note read-only gate rejecting mutating fields.
- Add a per-scenario **Production distribution clients** panel to the local web dashboard and add direct downloadable kit links (`.zip`), PyPI/npm package references, and EigenScope guide links across `README.md` and every `docs/scenarios/*.md`.

## 0.2.0 — 2026-11-06

Add the Healthcare scenario (two-compartment IV pharmacokinetic dosing, clinician sign-off and loopback HL7 FHIR R4 gate) and the Multimodal scenario (in-memory PCM16 WAV, CRC-32 PNG and RGBA OCVC container validation, masking and SHA-256 round-trip checks). Add live HTTP multimodal contract runners and upgrade Python and TypeScript SDK pins to 0.2.6.

## 0.1.2 — 2026-10-30

Improve the getting-started journey and public developer guides. Publish refreshed source archives and release checksums.

## 0.1.1 — 2026-10-30

Pin the official Python SDK to a hash-verified snapshot with license text, provenance and checksums for reproducible installation.

## 0.1.0 — 2026-10-30

Initial integration-lab release: MuJoCo robotics, SimPy telecom, MCP tool exchange, OpenAI-compatible streaming provider, isolated Git coding workflows, live Python/TypeScript SDK scenarios and telemetry visualization. Includes deterministic adversarial controls, machine-readable results, CI and optional isolated 5G stack setup.

See [verification](docs/verification.md) for executed coverage, product failures and runtime prerequisites.
