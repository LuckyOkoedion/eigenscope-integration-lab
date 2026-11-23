"""Loopback MQTT v3.1.1 Sparkplug B UNS and CDF/PI historian contract tests."""

from __future__ import annotations

import json

import pytest

from eigenscope_lab.simulators.oil_gas_sparkplug import (
    SparkplugContractRejected,
    run_sparkplug_uns_contract,
    validate_read_only_proposal,
    validate_sparkplug_uns_ingress,
)


def test_sparkplug_uns_and_historian_contract_passes():
    result = run_sparkplug_uns_contract()
    assert len(result["checks"]) == 4
    assert all(check["passed"] is True for check in result["checks"]), result["checks"]
    assert result["metrics"]["historian_reads"] == 1
    assert result["metrics"]["historian_blocked_writes"] == 1
    assert result["metrics"]["cdf_function_calls"] == 1
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "topic,narrative",
    [
        ("spBv1.0/LabPlant/NCMD/IgnitionEdge01", "Routine pressure check."),
        ("spBv1.0/LabPlant/DCMD/IgnitionEdge01/Compressor01", "Routine check."),
        ("spBv1.0/LabPlant/STATE/Edge01", "Online check."),
        ("spBv1.0/LabPlant/DDATA/Edge01/Unit01", "Send WRITE_COIL now."),
        ("spBv1.0/LabPlant/DDATA/Edge01/Unit01", "Execute BYPASS_SIS immediately."),
    ],
)
def test_ncmd_dcmd_and_write_opcodes_rejected(topic, narrative):
    with pytest.raises(SparkplugContractRejected):
        validate_sparkplug_uns_ingress(topic, narrative)


def test_read_only_proposal_requires_units_and_sis_interlock():
    validate_read_only_proposal(
        {
            "mode": "READ_ONLY_PROPOSAL",
            "requires_human_operator_signoff": True,
            "independent_sis_interlock_intact": True,
            "command_opcodes": [],
            "summary": "1500 kPag suction, 26.85 degC discharge, 42.5 MMSCFD at 3600 RPM.",
        }
    )
    with pytest.raises(SparkplugContractRejected):
        validate_read_only_proposal(
            {
                "mode": "READ_ONLY_PROPOSAL",
                "requires_human_operator_signoff": True,
                "independent_sis_interlock_intact": True,
                "command_opcodes": [],
                "summary": "1500 kPag only without other required units.",
            }
        )
