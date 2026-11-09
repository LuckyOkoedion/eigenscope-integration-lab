# Healthcare: IV pharmacokinetic dosing and HL7 FHIR R4 boundary

Run a bounded two-compartment intravenous pharmacokinetic (PK) infusion model,
evaluate clinician-reviewed dosing orders, and verify independent pump clamping
during acute renal clearance impairment or stale laboratory data. SciPy advances
coupled compartment drug mass balances, NumPy verifies linear matrix-exponential
washout limits, and loopback HTTP exercises an HL7 FHIR R4 `Observation` and
`MedicationRequest` boundary.

This scenario uses synthetic parameters and contains no patient data, clinical
calibration or medical-device certification.

## Run it

```bash
uv sync --locked --extra healthcare
uv run --frozen eigenscope-lab run healthcare
uv run --frozen pytest tests/test_healthcare.py
```

## Pharmacokinetic model and units

Drug mass distributes between a central serum compartment ($V_1$) and a
peripheral tissue compartment ($V_2$) with linear renal elimination ($CL \cdot f_{\text{renal}}$)
and intercompartmental clearance ($Q$):

```text
d(A_central)/dt    = infusion_rate - CL_eff * (A_central / V1) - Q * (A_central / V1 - A_peripheral / V2)
d(A_peripheral)/dt = Q * (A_central / V1 - A_peripheral / V2)
```

`scipy.integrate.solve_ivp` integrates the coupled system with `DOP853`
(`rtol=1e-10`, `atol=1e-12`, maximum step `0.05` simulated hours). Terminal
events locate toxic serum concentration ceiling crossings and laboratory
freshness timeouts inside a reporting interval. Once latched, pump infusion is
held at `0 mg/h`.

| Parameter | Synthetic lab value |
| --- | --- |
| Central volume (`V1`) | 14.0 L |
| Peripheral volume (`V2`) | 28.0 L |
| Baseline renal clearance (`CL`) | 4.2 L/h |
| Intercompartmental clearance (`Q`) | 5.6 L/h |
| Initial steady-state serum concentration | 18.0 mg/L |
| Toxic serum ceiling | 30.0 mg/L |
| Lab freshness timeout | 1.5 simulated hours |
| Per-order titration step bound | 30.0 mg/h |
| Admitted pump rate range | `[0, 180]` mg/h |

## Five operating phases

The default run emits 112 frames covering 28 simulated hours:

| Phase | Duration | Expected result |
| --- | --- | --- |
| `baseline-infusion` | 4.0 h | Maintenance infusion balances baseline clearance at 18.0 mg/L |
| `reviewed-dose-titration` | 5.0 h | A single-use clinician-approved increase of 24.0 mg/h is applied |
| `rejected-orders` | 3.0 h | Extra override fields, ceiling-change operations, unlisted channels and unapproved orders are rejected |
| `renal-impairment-clamp` | 12.0 h | Clearance drops to 20% of baseline; true serum concentration trips the 30.0 mg/L clamp despite a published lab value frozen at 19.0 mg/L |
| `stale-lab-hold` | 4.0 h | A separate subject's lab timestamp freezes at 1.0 h; pump infusion holds at the 2.5 h freshness deadline |

## HL7 FHIR R4 loopback contract

`run_fhir_r4_contract()` starts an ephemeral `127.0.0.1:0` HTTP server speaking
`application/fhir+json`:

- `GET /fhir/r4/Observation/serum-drug-level` returns a LOINC-coded serum
  `Observation` with UCUM `mg/L` units, a patient reference and `effectiveDateTime`.
- `POST /fhir/r4/MedicationRequest` validates schema, channel allowlist, unit,
  validity window and single-use `X-Clinician-Approval` digest binding,
  rejecting unapproved, field-injected and replayed requests with `422`
  `OperationOutcome`.

Orders use standard `status`, `intent`, `subject`, medication coding,
`supportingInformation` for the pump device, `dosageInstruction.doseAndRate`
for the UCUM `mg/h` rate, and `timing.repeat.boundsPeriod` for the validity
window. The scenario maps simulation hours to a fixed UTC epoch, keeping
wire resources deterministic while preserving proper FHIR dateTime values.

Each resource is validated offline with `jsonschema` against the complete,
unmodified [HL7 FHIR R4 JSON schema](https://hl7.org/fhir/R4/downloads.html),
bundled with its [source and attribution](../../src/eigenscope_lab/assets/fhir/).
The local profile then checks the selected patient, pump, medication, units,
single dosage and bounded validity window. Approval consumption is atomic
across concurrent requests.

References: [HL7 MedicationRequest](https://hl7.org/fhir/R4/medicationrequest.html),
[Dosage](https://hl7.org/fhir/R4/dosage.html),
[Observation](https://hl7.org/fhir/R4/observation.html), and
[LOINC 20578-1](https://loinc.org/20578-1/).
