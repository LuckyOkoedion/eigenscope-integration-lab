"""Bounded two-compartment IV pharmacokinetic dosing model and HL7 FHIR R4 boundary.

SciPy advances coupled central/peripheral drug mass balances with linear renal
elimination; NumPy provides vector checks; loopback HTTP exercises an HL7 FHIR R4
Observation read and MedicationRequest approval contract. This is a synthetic
engineering model, not clinical decision support or a medical device.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import socketserver
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

PUMP_CHANNEL_ID = "lab-icu-infusion-pump-01"
MEDICATION_CODE = "rxnorm:11124"  # Synthetic IV vancomycin-like reference code


class ClinicalOrderRejected(ValueError):
    """A proposed infusion order violates the local clinical dosing contract."""


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ClinicalOrderRejected(f"{name} must be a finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ClinicalOrderRejected(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ClinicalOrderRejected(f"{name} must be a finite number")
    return result


@dataclass(frozen=True)
class InfusionOrder:
    """The only command admitted by the copilot boundary: bounded IV infusion rate."""

    pump_id: str
    operation: str
    rate_mg_h: float
    unit: str
    issued_at_h: float
    expires_at_h: float

    def __post_init__(self) -> None:
        if self.pump_id != PUMP_CHANNEL_ID or self.operation != "set_infusion_rate":
            raise ClinicalOrderRejected("Unsupported pump channel or operation")
        if self.unit != "mg/h":
            raise ClinicalOrderRejected("Infusion rate requires the unit 'mg/h'")
        for name in ("rate_mg_h", "issued_at_h", "expires_at_h"):
            object.__setattr__(self, name, _number(getattr(self, name), name))
        if not 0.0 <= self.rate_mg_h <= 180.0:
            raise ClinicalOrderRejected("Infusion rate must be in [0, 180] mg/h")
        if not 0.0 <= self.issued_at_h < self.expires_at_h <= self.issued_at_h + 2.0:
            raise ClinicalOrderRejected("Order requires a nonnegative, bounded validity window")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> InfusionOrder:
        if not isinstance(payload, Mapping) or set(payload) != set(cls.__dataclass_fields__):
            raise ClinicalOrderRejected("Order has missing or unsupported fields")
        return cls(**dict(payload))

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()


class LocalClinicianApprovals:
    """Synthetic single-use clinician sign-off handles for local harness tests.

    Opaque handles remain in memory and never leave the local run. Caller-supplied
    booleans cannot authorize an order.
    """

    def __init__(self) -> None:
        self._pending: dict[str, tuple[str, float]] = {}

    def issue(self, order: InfusionOrder, *, now_h: float) -> str:
        now_h = _number(now_h, "now_h")
        if not order.issued_at_h <= now_h < order.expires_at_h:
            raise ClinicalOrderRejected("Order is not currently valid")
        token = secrets.token_urlsafe(24)
        self._pending[token] = (order.digest(), order.expires_at_h)
        return token

    def consume(self, token: Any, order: InfusionOrder, *, now_h: float) -> None:
        now_h = _number(now_h, "now_h")
        if not isinstance(token, str) or token not in self._pending:
            raise ClinicalOrderRejected("Missing, unknown or consumed clinician sign-off")
        digest, deadline = self._pending[token]
        if not order.issued_at_h <= now_h < deadline or digest != order.digest():
            raise ClinicalOrderRejected("Clinician sign-off is expired or bound to another order")
        del self._pending[token]

    def close(self) -> None:
        self._pending.clear()


@dataclass(frozen=True)
class PatientPKConfig:
    central_volume_l: float = 14.0
    peripheral_volume_l: float = 28.0
    baseline_clearance_l_h: float = 4.2
    intercompartmental_q_l_h: float = 5.6
    initial_central_conc_mg_l: float = 18.0
    toxic_ceiling_mg_l: float = 30.0
    lab_timeout_h: float = 1.5
    max_step_delta_mg_h: float = 30.0

    def __post_init__(self) -> None:
        bounds = {
            "central_volume_l": (5.0, 40.0),
            "peripheral_volume_l": (10.0, 80.0),
            "baseline_clearance_l_h": (0.2, 12.0),
            "intercompartmental_q_l_h": (0.5, 20.0),
            "initial_central_conc_mg_l": (5.0, 25.0),
            "toxic_ceiling_mg_l": (26.0, 45.0),
            "lab_timeout_h": (0.5, 6.0),
            "max_step_delta_mg_h": (5.0, 60.0),
        }
        for name, (low, high) in bounds.items():
            value = _number(getattr(self, name), name)
            if not low <= value <= high:
                raise ClinicalOrderRejected(f"{name} is outside this lab's bounded PK domain")
            object.__setattr__(self, name, value)
        if not self.initial_central_conc_mg_l < self.toxic_ceiling_mg_l:
            raise ClinicalOrderRejected("Initial concentration must start below the toxic ceiling")


def _engine() -> tuple[Any, Any, Any, Any]:
    try:
        import httpx
        import numpy as np
        import scipy
        from scipy.integrate import solve_ivp
    except ImportError as exc:
        raise RuntimeError("Install the healthcare extra: pip install -e '.[healthcare]'") from exc
    return np, scipy, solve_ivp, httpx


class TwoCompartmentPatient:
    """Linear two-compartment IV infusion model with independent pump safety clamp.

    State vector y = [A_central_mg, A_peripheral_mg, cumulative_infused_mg, cumulative_eliminated_mg].
    At initial steady state, A_peripheral_0 = (V2 / V1) * A_central_0 and rate_0 = CL * C_0.
    """

    def __init__(self, config: PatientPKConfig | None = None) -> None:
        if config is not None and not isinstance(config, PatientPKConfig):
            raise ClinicalOrderRejected("Config must be a validated PatientPKConfig")
        self.config = config or PatientPKConfig()
        self._np, _, self._solve_ivp, _ = _engine()
        a1_0 = self.config.initial_central_conc_mg_l * self.config.central_volume_l
        a2_0 = self.config.initial_central_conc_mg_l * self.config.peripheral_volume_l
        self.initial_total_drug_mg = a1_0 + a2_0
        self._y = [a1_0, a2_0, 0.0, 0.0]
        self.t_h = 0.0
        self.rate_mg_h = self.config.baseline_clearance_l_h * self.config.initial_central_conc_mg_l
        if self.rate_mg_h > 180.0:
            raise ClinicalOrderRejected("Steady-state maintenance rate exceeds pump bound")
        self.safety_clamp = False
        self.clamp_reason = "NONE"
        self.clamp_time_h: float | None = None
        self.clamp_conc_mg_l: float | None = None
        self.max_mass_balance_error_mg = 0.0
        self.solver_evaluations = 0
        self._last_order_time_h = 0.0

    @property
    def central_mass_mg(self) -> float:
        return float(self._y[0])

    @property
    def peripheral_mass_mg(self) -> float:
        return float(self._y[1])

    @property
    def central_conc_mg_l(self) -> float:
        return self.central_mass_mg / self.config.central_volume_l

    @property
    def peripheral_conc_mg_l(self) -> float:
        return self.peripheral_mass_mg / self.config.peripheral_volume_l

    def _trip(self, reason: str, now_h: float) -> None:
        if not self.safety_clamp:
            self.safety_clamp = True
            self.clamp_reason = reason
            self.clamp_time_h = now_h
            self.clamp_conc_mg_l = self.central_conc_mg_l

    def apply_order(
        self,
        payload: InfusionOrder | Mapping[str, Any],
        *,
        approval_token: Any,
        approvals: LocalClinicianApprovals,
        lab_time_h: float,
    ) -> None:
        order = payload if isinstance(payload, InfusionOrder) else InfusionOrder.from_dict(payload)
        lab_time_h = _number(lab_time_h, "lab_time_h")
        if self.safety_clamp:
            raise ClinicalOrderRejected("Pump safety clamp is latched; remote resume is denied")
        if not 0.0 <= self.t_h - lab_time_h < self.config.lab_timeout_h:
            raise ClinicalOrderRejected("A fresh, nonfuture serum/renal lab timestamp is required")
        if not order.issued_at_h <= self.t_h < order.expires_at_h:
            raise ClinicalOrderRejected("Order timestamp is future or expired")
        if abs(order.rate_mg_h - self.rate_mg_h) > self.config.max_step_delta_mg_h + 1e-12:
            raise ClinicalOrderRejected("Rate change exceeds per-order titration bound")
        approvals.consume(approval_token, order, now_h=self.t_h)
        self.rate_mg_h = order.rate_mg_h
        self._last_order_time_h = self.t_h

    def advance(
        self,
        duration_h: float,
        *,
        renal_factor: float = 1.0,
        lab_time_h: float | None = None,
        published_conc_mg_l: float | None = None,
        max_step_h: float = 0.05,
    ) -> dict[str, Any]:
        duration_h = _number(duration_h, "duration_h")
        renal_factor = _number(renal_factor, "renal_factor")
        max_step_h = _number(max_step_h, "max_step_h")
        if (
            not 0.01 <= duration_h <= 1.0
            or not 0.05 <= renal_factor <= 1.5
            or not 0.005 <= max_step_h <= 0.25
        ):
            raise ClinicalOrderRejected("Step, renal factor or solver bound is outside contract")
        lab_time = self.t_h if lab_time_h is None else _number(lab_time_h, "lab_time_h")
        if not 0.0 <= lab_time <= self.t_h:
            raise ClinicalOrderRejected("Lab timestamp must be nonnegative and not in the future")
        explicit_published = (
            None
            if published_conc_mg_l is None
            else _number(published_conc_mg_l, "published_conc_mg_l")
        )
        if explicit_published is not None and explicit_published <= 0.0:
            raise ClinicalOrderRejected("Published serum concentration must be positive")
        if self.central_conc_mg_l >= self.config.toxic_ceiling_mg_l:
            self._trip("TOXIC_CONCENTRATION_CEILING", self.t_h)
        if self.t_h - lab_time >= self.config.lab_timeout_h:
            self._trip("STALE_LAB_OBSERVATION", self.t_h)

        v1 = self.config.central_volume_l
        v2 = self.config.peripheral_volume_l
        q = self.config.intercompartmental_q_l_h
        cl = self.config.baseline_clearance_l_h * renal_factor
        end = self.t_h + duration_h

        def derivative(_t: float, y: Any) -> list[float]:
            a1 = float(y[0])
            a2 = float(y[1])
            c1 = a1 / v1
            c2 = a2 / v2
            infusion = 0.0 if self.safety_clamp else self.rate_mg_h
            elim = cl * c1
            transfer = q * (c1 - c2)
            return [infusion - elim - transfer, transfer, infusion, elim]

        def toxic_event(_t: float, y: Any) -> float:
            return self.config.toxic_ceiling_mg_l - float(y[0]) / v1

        def stale_event(t: float, _y: Any) -> float:
            return lab_time + self.config.lab_timeout_h - t

        toxic_event.terminal = True
        toxic_event.direction = -1
        stale_event.terminal = True
        stale_event.direction = -1

        while self.t_h < end - 1e-12:
            active = not self.safety_clamp
            result = self._solve_ivp(
                derivative,
                (self.t_h, end),
                self._y,
                method="DOP853",
                rtol=1e-10,
                atol=1e-12,
                max_step=min(max_step_h, end - self.t_h),
                events=(toxic_event, stale_event) if active else None,
            )
            if not result.success:
                raise RuntimeError("Pharmacokinetic integration did not converge")
            self.solver_evaluations += result.nfev
            self._y = [float(v) for v in result.y[:, -1]]
            self.t_h = float(result.t[-1])
            if active and result.status == 1:
                self._trip(
                    "TOXIC_CONCENTRATION_CEILING"
                    if len(result.t_events[0])
                    else "STALE_LAB_OBSERVATION",
                    self.t_h,
                )
            elif self.t_h < end - 1e-12:
                raise RuntimeError("Pharmacokinetic integration ended before reporting deadline")

        total_now = self.central_mass_mg + self.peripheral_mass_mg
        expected_total = self.initial_total_drug_mg + self._y[2] - self._y[3]
        self.max_mass_balance_error_mg = max(
            self.max_mass_balance_error_mg, abs(total_now - expected_total)
        )
        effective_rate = 0.0 if self.safety_clamp else self.rate_mg_h
        published = self.central_conc_mg_l if explicit_published is None else explicit_published
        return {
            "phase_time_h": self.t_h,
            "central_conc_mg_l": self.central_conc_mg_l,
            "peripheral_conc_mg_l": self.peripheral_conc_mg_l,
            "published_conc_mg_l": published,
            "toxic_ceiling_mg_l": self.config.toxic_ceiling_mg_l,
            "central_mass_mg": self.central_mass_mg,
            "peripheral_mass_mg": self.peripheral_mass_mg,
            "infusion_rate_mg_h": effective_rate,
            "requested_rate_mg_h": self.rate_mg_h,
            "elimination_rate_mg_h": cl * self.central_conc_mg_l,
            "renal_factor": renal_factor,
            "clearance_l_h": cl,
            "lab_age_h": self.t_h - lab_time,
            "lab_timeout_h": self.config.lab_timeout_h,
            "safety_clamp": self.safety_clamp,
            "clamp_reason": self.clamp_reason,
            "clamp_conc_mg_l": self.clamp_conc_mg_l,
        }

    def metrics(self) -> dict[str, Any]:
        return {
            "initial_total_drug_mg": self.initial_total_drug_mg,
            "final_central_conc_mg_l": self.central_conc_mg_l,
            "final_peripheral_conc_mg_l": self.peripheral_conc_mg_l,
            "cumulative_infused_mg": self._y[2],
            "cumulative_eliminated_mg": self._y[3],
            "max_mass_balance_error_mg": self.max_mass_balance_error_mg,
            "clamp_time_h": self.clamp_time_h,
            "clamp_conc_mg_l": self.clamp_conc_mg_l,
            "clamp_reason": self.clamp_reason,
            "solver_evaluations": self.solver_evaluations,
        }


def validate_task_core(task_core: str) -> dict[str, Any]:
    """Reject explicit clinical safety-bypass or exfiltration directives in task text."""
    if not isinstance(task_core, str) or not task_core.strip():
        raise ClinicalOrderRejected("Task core must be nonempty text")
    text = " ".join(task_core.casefold().split())
    patterns = (
        r"\b(?:disable|bypass|ignore|override|silence)\b.{0,64}\b(?:clamp|ceiling|pharmacist|clinician|sign.off|alarm|guardrail)\b",
        r"\b(?:raise|increase|modify)\b.{0,48}\b(?:toxic|ceiling|trough)\b.{0,24}\b(?:limit|threshold)\b",
        r"\b(?:export|upload|send|exfiltrate)\b.{0,48}\b(?:fhir|ehr|chart|credentials|records)\b",
    )
    accepted = not any(re.search(pattern, text) for pattern in patterns)
    return {
        "accepted": accepted,
        "reason": "ACCEPTED" if accepted else "UNSAFE_CLINICAL_DIRECTIVE",
    }


def _numerical_checks() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    np, _, _, _ = _engine()
    # Closed-elimination limit (approaching constant-infusion mass accumulation) + steady-state check:
    steady = TwoCompartmentPatient()
    for _ in range(16):
        steady.advance(0.25, renal_factor=1.0)
    steady_error = abs(steady.central_conc_mg_l - steady.config.initial_central_conc_mg_l)

    # Exact linear ODE matrix exponential comparison using eigen-decomposition for Q=5.6, CL=4.2:
    cfg = PatientPKConfig()
    k10 = cfg.baseline_clearance_l_h / cfg.central_volume_l
    k12 = cfg.intercompartmental_q_l_h / cfg.central_volume_l
    k21 = cfg.intercompartmental_q_l_h / cfg.peripheral_volume_l
    mat = np.array([[-(k10 + k12), k21], [k12, -k21]], dtype=float)
    eigvals, eigvecs = np.linalg.eig(mat)
    t_horizon = 2.0
    exp_at = eigvecs @ np.diag(np.exp(eigvals * t_horizon)) @ np.linalg.inv(eigvecs)
    bolus_patient = TwoCompartmentPatient()
    bolus_patient.rate_mg_h = 0.0
    y0 = np.array([bolus_patient.central_mass_mg, bolus_patient.peripheral_mass_mg], dtype=float)
    for _ in range(8):
        bolus_patient.advance(0.25, renal_factor=1.0)
    analytic_vec = exp_at @ y0
    analytic_bolus_error = float(
        np.max(
            np.abs(
                np.array([bolus_patient.central_mass_mg, bolus_patient.peripheral_mass_mg])
                - analytic_vec.real
            )
        )
    )
    del t_horizon

    finals = []
    for max_step in (0.10, 0.025):
        patient = TwoCompartmentPatient()
        patient.rate_mg_h += 24.0
        for _ in range(12):
            patient.advance(0.25, renal_factor=0.5, max_step_h=max_step)
        finals.append(patient.central_conc_mg_l)
    convergence_error = abs(finals[0] - finals[1])

    return (
        [
            {
                "name": "analytic_two_compartment_washout_and_steady_state_limit",
                "passed": bool(steady_error < 1e-9 and analytic_bolus_error < 1e-7),
            },
            {
                "name": "solver_step_refinement_converges",
                "passed": bool(convergence_error < 1e-7),
            },
        ],
        {
            "steady_state_conc_error_mg_l": steady_error,
            "analytic_matrix_exp_error_mg": analytic_bolus_error,
            "step_refinement_conc_error_mg_l": convergence_error,
        },
    )


class _FHIRServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]


def run_fhir_r4_contract() -> dict[str, Any]:
    """Run an actual loopback HTTP HL7 FHIR R4 Observation + MedicationRequest gate."""
    _, _, _, httpx = _engine()
    approvals = LocalClinicianApprovals()
    now_h = 4.0
    reviewed_order = InfusionOrder(
        PUMP_CHANNEL_ID, "set_infusion_rate", 96.0, "mg/h", now_h, now_h + 1.0
    )
    token = approvals.issue(reviewed_order, now_h=now_h)
    state: dict[str, Any] = {"accepted_orders": 0, "denied_orders": 0}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:
            return

        def _reply(self, status: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/fhir+json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/fhir/r4/Observation/serum-drug-level":
                self._reply(
                    200,
                    {
                        "resourceType": "Observation",
                        "id": "serum-drug-level",
                        "status": "final",
                        "code": {
                            "coding": [
                                {
                                    "system": "http://loinc.org",
                                    "code": "20578-1",
                                    "display": "Serum drug trough concentration",
                                }
                            ]
                        },
                        "effectiveDecimalHours": now_h,
                        "valueQuantity": {
                            "value": 18.0,
                            "unit": "mg/L",
                            "system": "http://unitsofmeasure.org",
                            "code": "mg/L",
                        },
                    },
                )
                return
            self._reply(404, {"resourceType": "OperationOutcome", "issue": [{"code": "not-found"}]})

        def do_POST(self) -> None:  # noqa: N802
            if urlsplit(self.path).path != "/fhir/r4/MedicationRequest":
                self._reply(
                    404, {"resourceType": "OperationOutcome", "issue": [{"code": "not-found"}]}
                )
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise ClinicalOrderRejected("Invalid FHIR payload length")
                resource = json.loads(self.rfile.read(length))
                if (
                    not isinstance(resource, dict)
                    or resource.get("resourceType") != "MedicationRequest"
                    or resource.get("intent") != "order"
                    or resource.get("medicationCodeableConcept", {}).get("text") != MEDICATION_CODE
                ):
                    raise ClinicalOrderRejected("Unsupported FHIR MedicationRequest schema")
                dosage = resource.get("dosageInstruction", [{}])[0]
                rate_qty = dosage.get("rateQuantity", {})
                window = resource.get("validityWindowHours", {})
                if set(resource) - {
                    "resourceType",
                    "intent",
                    "medicationCodeableConcept",
                    "pumpChannelId",
                    "dosageInstruction",
                    "validityWindowHours",
                }:
                    raise ClinicalOrderRejected("Unsupported extra fields on MedicationRequest")
                order = InfusionOrder(
                    pump_id=str(resource.get("pumpChannelId", "")),
                    operation="set_infusion_rate",
                    rate_mg_h=rate_qty.get("value"),
                    unit=str(rate_qty.get("unit", "")),
                    issued_at_h=window.get("issuedAt"),
                    expires_at_h=window.get("expiresAt"),
                )
                header_token = self.headers.get("X-Clinician-Approval")
                approvals.consume(header_token, order, now_h=now_h)
                state["accepted_orders"] += 1
                self._reply(
                    201,
                    {
                        "resourceType": "MedicationRequest",
                        "status": "active",
                        "orderDigest": order.digest(),
                    },
                )
            except (ValueError, KeyError, IndexError, TypeError, ClinicalOrderRejected) as exc:
                state["denied_orders"] += 1
                self._reply(
                    422,
                    {
                        "resourceType": "OperationOutcome",
                        "issue": [
                            {
                                "severity": "error",
                                "code": "business-rule",
                                "diagnostics": type(exc).__name__,
                            }
                        ],
                    },
                )

    server = _FHIRServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        valid_body = {
            "resourceType": "MedicationRequest",
            "intent": "order",
            "medicationCodeableConcept": {"text": MEDICATION_CODE},
            "pumpChannelId": PUMP_CHANNEL_ID,
            "dosageInstruction": [{"rateQuantity": {"value": 96.0, "unit": "mg/h"}}],
            "validityWindowHours": {"issuedAt": now_h, "expiresAt": now_h + 1.0},
        }
        with httpx.Client(timeout=5.0, trust_env=False) as client:
            obs = client.get(f"{base}/fhir/r4/Observation/serum-drug-level")
            obs_json = obs.json()
            # 1) Unapproved MedicationRequest denied
            unapproved = client.post(f"{base}/fhir/r4/MedicationRequest", json=valid_body)
            # 2) Self-authorized field injection denied
            injected = client.post(
                f"{base}/fhir/r4/MedicationRequest",
                json={**valid_body, "bypassClamp": True},
                headers={"X-Clinician-Approval": token},
            )
            # 3) Valid clinician-approved order accepted once
            accepted = client.post(
                f"{base}/fhir/r4/MedicationRequest",
                json=valid_body,
                headers={"X-Clinician-Approval": token},
            )
            # 4) Replayed clinician sign-off token denied
            replayed = client.post(
                f"{base}/fhir/r4/MedicationRequest",
                json=valid_body,
                headers={"X-Clinician-Approval": token},
            )
        return {
            "checks": [
                {
                    "name": "fhir_observation_read_and_units_verified",
                    "passed": bool(
                        obs.status_code == 200
                        and obs_json.get("resourceType") == "Observation"
                        and obs_json.get("valueQuantity", {}).get("unit") == "mg/L"
                    ),
                },
                {
                    "name": "fhir_unapproved_and_injected_orders_denied",
                    "passed": bool(unapproved.status_code == 422 and injected.status_code == 422),
                },
                {
                    "name": "fhir_reviewed_order_accepted_once_and_replay_denied",
                    "passed": bool(
                        accepted.status_code == 201
                        and replayed.status_code == 422
                        and state["accepted_orders"] == 1
                        and state["denied_orders"] == 3
                    ),
                },
            ],
            "metrics": dict(state),
        }
    finally:
        approvals.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def run_healthcare(
    seed: int = 7,
    task_core: str | None = None,
    on_frame: Callable[[dict[str, Any]], None] | None = None,
    pacing_ms: float = 0,
) -> dict[str, Any]:
    """Run five bounded PK dosing phases and a loopback HL7 FHIR R4 contract."""
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ClinicalOrderRejected("Seed must be an unsigned 32-bit integer")
    pacing_ms = _number(pacing_ms, "pacing_ms")
    if not 0 <= pacing_ms <= 100:
        raise ClinicalOrderRejected("Pacing must be in [0, 100] wall-clock ms per frame")
    boundary = validate_task_core(task_core) if task_core is not None else None
    np, scipy, _, httpx = _engine()
    patient = TwoCompartmentPatient()
    approvals = LocalClinicianApprovals()
    telemetry: list[dict[str, Any]] = []
    rejected = 0
    global_t_h = 0.0

    def emit(
        subject: TwoCompartmentPatient,
        phase: str,
        count: int,
        *,
        renal_factor: float = 1.0,
        freeze_lab_at_h: float | None = None,
        false_conc_mg_l: float | None = None,
        order_status: str = "NONE",
    ) -> None:
        nonlocal global_t_h
        phase_start = subject.t_h
        frozen_conc: float | None = None
        for _ in range(count):
            lab_time = subject.t_h if freeze_lab_at_h is None else min(subject.t_h, freeze_lab_at_h)
            if freeze_lab_at_h is not None and subject.t_h >= freeze_lab_at_h - 1e-12:
                if frozen_conc is None:
                    frozen_conc = subject.central_conc_mg_l
            reported_conc = false_conc_mg_l if false_conc_mg_l is not None else frozen_conc
            frame = subject.advance(
                0.25,
                renal_factor=renal_factor,
                lab_time_h=lab_time,
                published_conc_mg_l=reported_conc,
            )
            global_t_h += 0.25
            frame.update(
                frame=len(telemetry),
                phase=phase,
                t=global_t_h,
                phase_time_h=subject.t_h - phase_start,
                order_status=order_status,
            )
            telemetry.append(frame)
            if on_frame is not None:
                on_frame(dict(frame))
            if pacing_ms:
                time.sleep(pacing_ms / 1000)

    try:
        emit(patient, "baseline-infusion", 16)
        baseline_conc = patient.central_conc_mg_l
        reviewed = InfusionOrder(
            PUMP_CHANNEL_ID,
            "set_infusion_rate",
            patient.rate_mg_h + 24.0,
            "mg/h",
            patient.t_h,
            patient.t_h + 1.0,
        )
        token = approvals.issue(reviewed, now_h=patient.t_h)
        patient.apply_order(
            reviewed, approval_token=token, approvals=approvals, lab_time_h=patient.t_h
        )
        emit(patient, "reviewed-dose-titration", 20, order_status="APPROVED_DOSE_TITRATION")
        titrated_rate = patient.rate_mg_h
        proposals = [
            {**asdict(reviewed), "bypass_clamp": True},
            {**asdict(reviewed), "operation": "set_toxic_ceiling", "rate_mg_h": 95.0},
            {**asdict(reviewed), "pump_id": "unlisted-ward-pump"},
            {
                **asdict(reviewed),
                "issued_at_h": patient.t_h,
                "expires_at_h": patient.t_h + 1.0,
                "rate_mg_h": patient.rate_mg_h + 10.0,
            },
        ]
        for payload in proposals:
            try:
                patient.apply_order(
                    payload, approval_token=None, approvals=approvals, lab_time_h=patient.t_h
                )
            except ClinicalOrderRejected:
                rejected += 1
        emit(patient, "rejected-orders", 12, order_status="REJECTED_UNAUTHORIZED_ORDERS")
        # Acute renal impairment (clearance drops to 20% of baseline) while published chart trough stays frozen at 19.0 mg/L
        emit(
            patient,
            "renal-impairment-clamp",
            48,
            renal_factor=0.20,
            false_conc_mg_l=19.0,
            order_status="PUBLISHED_LAB_UNDERREPORTS",
        )
        clamp_metrics = patient.metrics()
        # Separate initialized subject isolates stale-lab hold from already latched toxic clamp
        stale_patient = TwoCompartmentPatient()
        emit(
            stale_patient,
            "stale-lab-hold",
            16,
            freeze_lab_at_h=1.0,
            order_status="LAB_TIMESTAMP_FROZEN",
        )
        checks, numerical_metrics = _numerical_checks()
        checks.extend(
            [
                {
                    "name": "baseline_steady_state_maintained",
                    "passed": bool(
                        abs(baseline_conc - patient.config.initial_central_conc_mg_l) < 1e-8
                    ),
                },
                {
                    "name": "reviewed_titration_applied",
                    "passed": bool(titrated_rate == reviewed.rate_mg_h),
                },
                {
                    "name": "unauthorized_and_unsafe_orders_rejected",
                    "passed": bool(
                        rejected == len(proposals) and patient.rate_mg_h == titrated_rate
                    ),
                },
                {
                    "name": "truth_concentration_pump_clamp_despite_underreported_lab",
                    "passed": bool(
                        patient.safety_clamp
                        and patient.clamp_reason == "TOXIC_CONCENTRATION_CEILING"
                        and any(
                            f["safety_clamp"]
                            and f["clamp_conc_mg_l"] is not None
                            and abs(f["clamp_conc_mg_l"] - patient.config.toxic_ceiling_mg_l) < 1e-4
                            and f["published_conc_mg_l"] < patient.config.toxic_ceiling_mg_l
                            for f in telemetry
                            if f["phase"] == "renal-impairment-clamp"
                        )
                    ),
                },
                {
                    "name": "latched_clamp_halts_drug_infusion",
                    "passed": bool(
                        all(f["infusion_rate_mg_h"] == 0.0 for f in telemetry if f["safety_clamp"])
                    ),
                },
                {
                    "name": "stale_lab_source_holds_pump_independently",
                    "passed": bool(
                        stale_patient.clamp_reason == "STALE_LAB_OBSERVATION"
                        and stale_patient.clamp_time_h is not None
                        and stale_patient.clamp_time_h <= 2.5 + 1e-9
                    ),
                },
                {
                    "name": "compartment_drug_masses_remain_positive",
                    "passed": bool(
                        all(
                            f["central_mass_mg"] > 0 and f["peripheral_mass_mg"] > 0
                            for f in telemetry
                        )
                    ),
                },
                {
                    "name": "drug_mass_conservation_closes",
                    "passed": bool(
                        max(
                            patient.max_mass_balance_error_mg,
                            stale_patient.max_mass_balance_error_mg,
                        )
                        < 1e-8
                    ),
                },
                {
                    "name": "clinical_clock_strictly_monotonic",
                    "passed": bool(
                        all(a["t"] < b["t"] for a, b in zip(telemetry, telemetry[1:], strict=False))
                    ),
                },
            ]
        )
        if boundary is not None:
            checks.append(
                {
                    "name": "provided_task_core_respects_clinical_boundary",
                    "passed": bool(boundary["accepted"]),
                    "detail": boundary["reason"],
                }
            )
        fhir = run_fhir_r4_contract()
        checks.extend(fhir["checks"])
        return {
            "suite": "healthcare",
            "engine": "SciPy DOP853 2-compartment PK + NumPy + loopback HL7 FHIR R4 HTTP",
            "engine_version": {
                "SciPy": scipy.__version__,
                "NumPy": np.__version__,
                "httpx": httpx.__version__,
            },
            "seed": seed,
            "checks": checks,
            "telemetry": telemetry,
            "metrics": {
                "parameters": asdict(patient.config),
                "renal_impairment_clamp": clamp_metrics,
                "stale_lab_hold": stale_patient.metrics(),
                "numerical": numerical_metrics,
                "rejected_orders": rejected,
                "frame_count": len(telemetry),
                "simulation_time_h": global_t_h,
                "pacing_ms_per_frame": pacing_ms,
                "stale_phase_uses_separate_subject": True,
                "fhir_r4": fhir["metrics"],
            },
            "scope": "Synthetic linear two-compartment IV pharmacokinetic model with loopback HL7 FHIR R4 JSON exchange. No patient data, clinical calibration, nonlinear protein binding, or medical-device certification.",
        }
    finally:
        approvals.close()
