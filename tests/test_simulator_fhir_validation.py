"""FHIR R4 standard shapes, bounded dosing profile and concurrent approval controls."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from eigenscope_lab.simulators.fhir_r4 import validate_fhir_r4_resource
from eigenscope_lab.simulators.healthcare import (
    PUMP_CHANNEL_ID,
    ClinicalOrderRejected,
    InfusionOrder,
    LocalClinicianApprovals,
    infusion_order_from_fhir,
    medication_request_for_order,
)


@pytest.fixture
def order():
    pytest.importorskip("jsonschema")
    return InfusionOrder(PUMP_CHANNEL_ID, "set_infusion_rate", 96.0, "mg/h", 4.0, 5.0)


def test_medication_resource_uses_real_r4_fields_and_preserves_the_bound_order(order):
    resource = medication_request_for_order(order)
    validate_fhir_r4_resource(resource)
    assert resource["status"] == "active"
    assert resource["subject"]["reference"] == "Patient/lab-patient-01"
    dosage = resource["dosageInstruction"][0]
    assert dosage["doseAndRate"][0]["rateQuantity"] == {
        "value": 96.0,
        "unit": "mg/h",
        "system": "http://unitsofmeasure.org",
        "code": "mg/h",
    }
    assert dosage["timing"]["repeat"]["boundsPeriod"] == {
        "start": "2026-01-01T04:00:00Z",
        "end": "2026-01-01T05:00:00Z",
    }
    assert infusion_order_from_fhir(resource) == order


@pytest.mark.parametrize(
    "extra", ["pumpChannelId", "validityWindowHours", "approved", "orderDigest"]
)
def test_official_schema_rejects_nonstandard_top_level_fields(order, extra):
    resource = medication_request_for_order(order)
    resource[extra] = "injected"
    with pytest.raises(ValueError):
        validate_fhir_r4_resource(resource)


def test_official_schema_rejects_nonstandard_observation_timestamp():
    pytest.importorskip("jsonschema")
    resource = {
        "resourceType": "Observation",
        "status": "final",
        "code": {"text": "Serum concentration"},
        "effectiveDecimalHours": 4.0,
    }
    with pytest.raises(ValueError):
        validate_fhir_r4_resource(resource)
    resource.pop("effectiveDecimalHours")
    resource["effectiveDateTime"] = "2026-01-01T04:00:00Z"
    validate_fhir_r4_resource(resource)


@pytest.mark.parametrize(
    "field", ["status", "subject", "dosageInstruction", "supportingInformation"]
)
def test_local_profile_requires_patient_status_pump_and_single_dose(order, field):
    resource = medication_request_for_order(order)
    resource.pop(field)
    with pytest.raises(ValueError):
        infusion_order_from_fhir(resource)


@pytest.mark.parametrize(
    "change",
    [
        "other_patient",
        "other_pump",
        "wrong_unit",
        "wrong_system",
        "multiple_rates",
        "future_window",
        "missing_timezone",
    ],
)
def test_standard_but_unauthorized_or_ambiguous_order_is_rejected(order, change):
    resource = deepcopy(medication_request_for_order(order))
    dosage = resource["dosageInstruction"][0]
    if change == "other_patient":
        resource["subject"]["reference"] = "Patient/other-patient"
    elif change == "other_pump":
        resource["supportingInformation"][0]["reference"] = "Device/other-pump"
    elif change == "wrong_unit":
        dosage["doseAndRate"][0]["rateQuantity"]["unit"] = "mL/h"
    elif change == "wrong_system":
        dosage["doseAndRate"][0]["rateQuantity"]["system"] = "https://example.invalid/units"
    elif change == "multiple_rates":
        dosage["doseAndRate"].append(deepcopy(dosage["doseAndRate"][0]))
    elif change == "future_window":
        dosage["timing"]["repeat"]["boundsPeriod"]["end"] = "2026-01-02T05:00:00Z"
    else:
        dosage["timing"]["repeat"]["boundsPeriod"]["start"] = "2026-01-01T04:00:00"
    with pytest.raises(ValueError):
        infusion_order_from_fhir(resource)


def test_clinician_approval_can_be_consumed_only_once_across_concurrent_requests(order):
    approvals = LocalClinicianApprovals()
    token = approvals.issue(order, now_h=4.0)

    def consume(_):
        try:
            approvals.consume(token, order, now_h=4.0)
            return True
        except ClinicalOrderRejected:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(consume, range(24))) == 1
    approvals.close()
