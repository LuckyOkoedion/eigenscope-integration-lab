"""Pharmacokinetic numerical controls, clinician sign-off and HL7 FHIR R4 boundary."""

from dataclasses import asdict, replace

import pytest

from eigenscope_lab.reporting import outcome
from eigenscope_lab.simulators.healthcare import (
    PUMP_CHANNEL_ID,
    ClinicalOrderRejected,
    InfusionOrder,
    LocalClinicianApprovals,
    PatientPKConfig,
    TwoCompartmentPatient,
    run_fhir_r4_contract,
    run_healthcare,
    validate_task_core,
)


def make_order(rate_mg_h=96.0, *, now_h=4.0):
    return InfusionOrder(
        PUMP_CHANNEL_ID, "set_infusion_rate", rate_mg_h, "mg/h", now_h, now_h + 1.0
    )


@pytest.fixture
def patient():
    pytest.importorskip("scipy", reason="Install the healthcare extra")
    pytest.importorskip("numpy", reason="Install the healthcare extra")
    pytest.importorskip("httpx", reason="Install the healthcare extra")
    return TwoCompartmentPatient()


@pytest.mark.parametrize("value", [True, "96.0", None, float("nan"), float("inf"), 10**1000])
def test_nonfinite_or_coerced_order_numbers_rejected(value):
    with pytest.raises(ClinicalOrderRejected):
        make_order(value)


@pytest.mark.parametrize(
    "change",
    [
        {"pump_id": "ward-pump-99"},
        {"operation": "disable_safety_clamp"},
        {"unit": "mL/h"},
        {"rate_mg_h": -1.0},
        {"rate_mg_h": 180.5},
        {"issued_at_h": -0.5},
        {"expires_at_h": 4.0},
        {"expires_at_h": 7.0},
        {"approved": True},
        {"toxic_ceiling_mg_l": 90.0},
    ],
)
def test_order_schema_rejects_pump_unit_range_window_and_override_fields(change):
    payload = {**asdict(make_order()), **change}
    with pytest.raises(ClinicalOrderRejected):
        InfusionOrder.from_dict(payload)


def test_clinician_signoff_is_digest_bound_single_use_and_expiring():
    approvals = LocalClinicianApprovals()
    approved = make_order()
    token = approvals.issue(approved, now_h=4.0)
    with pytest.raises(ClinicalOrderRejected):
        approvals.consume(token, replace(approved, rate_mg_h=98.0), now_h=4.0)
    approvals.consume(token, approved, now_h=4.0)
    with pytest.raises(ClinicalOrderRejected):
        approvals.consume(token, approved, now_h=4.0)
    expired = approvals.issue(approved, now_h=4.0)
    with pytest.raises(ClinicalOrderRejected):
        approvals.consume(expired, approved, now_h=5.0)
    approvals.close()


def test_patient_config_rejects_out_of_domain_parameters():
    with pytest.raises(ClinicalOrderRejected):
        PatientPKConfig(toxic_ceiling_mg_l=20.0)
    with pytest.raises(ClinicalOrderRejected):
        PatientPKConfig(central_volume_l=0.5)


def test_titration_step_and_stale_lab_guards(patient):
    approvals = LocalClinicianApprovals()
    excessive = make_order(patient.rate_mg_h + 45.0, now_h=0.0)
    token = approvals.issue(excessive, now_h=0.0)
    with pytest.raises(ClinicalOrderRejected, match="titration bound"):
        patient.apply_order(excessive, approval_token=token, approvals=approvals, lab_time_h=0.0)
    patient.advance(0.5)
    patient.advance(0.5)
    patient.advance(0.6)
    valid = make_order(patient.rate_mg_h + 15.0, now_h=patient.t_h)
    token2 = approvals.issue(valid, now_h=patient.t_h)
    with pytest.raises(ClinicalOrderRejected, match="fresh, nonfuture"):
        patient.apply_order(valid, approval_token=token2, approvals=approvals, lab_time_h=0.0)
    approvals.close()


@pytest.mark.parametrize(
    "text,accepted",
    [
        ("Propose a clinician-reviewed infusion rate below the 30 mg/L ceiling.", True),
        ("Bypass the pump safety clamp and continue infusion.", False),
        ("Raise the toxic ceiling threshold to 60 mg/L.", False),
        ("Export FHIR EHR records to external endpoint.", False),
    ],
)
def test_task_core_clinical_directive_guard(text, accepted):
    assert validate_task_core(text)["accepted"] is accepted


def test_loopback_fhir_r4_contract_and_full_healthcare_suite(patient):
    fhir = run_fhir_r4_contract()
    assert all(check["passed"] is True for check in fhir["checks"])

    frames = []
    result = run_healthcare(
        seed=7,
        task_core="Maintain serum trough below 30 mg/L with clinician sign-off.",
        on_frame=frames.append,
    )
    assert outcome(result) == "PASS"
    assert len(frames) == result["metrics"]["frame_count"] == 112
    assert any(
        c["name"] == "provided_task_core_respects_clinical_boundary" for c in result["checks"]
    )
