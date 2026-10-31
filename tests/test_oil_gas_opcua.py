"""Independent OPC UA policy, source-clock and actual-wire controls."""

from __future__ import annotations

import hashlib
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("asyncua")

from asyncua import ua
from asyncua.common.utils import Buffer
from asyncua.crypto.permission_rules import User, UserRole
from asyncua.ua.ua_binary import struct_to_binary

from eigenscope_lab.simulators import oil_gas_opcua
from eigenscope_lab.simulators.oil_gas_opcua import (
    OilGasPermissionRules,
    OilGasUserManager,
    SourceDataRejected,
    run_opcua_contract,
    validate_source_data_value,
)

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
SYNTHETIC_PEER = b"synthetic-unit-test-channel-peer"
SYNTHETIC_PIN = hashlib.sha256(SYNTHETIC_PEER).hexdigest()


def source_sample(value=60.0, *, stamp=NOW, status=ua.StatusCodes.Good):
    return ua.DataValue(
        ua.Variant(value),
        StatusCode=ua.StatusCode(status),
        SourceTimestamp=stamp,
        ServerTimestamp=NOW,
    )


def test_source_data_uses_quality_and_source_utc_not_server_or_simulation_clock():
    assert validate_source_data_value(source_sample(), now_utc=NOW) == 60.0
    with pytest.raises(SourceDataRejected, match="STALE_SOURCE_TIMESTAMP"):
        validate_source_data_value(source_sample(stamp=NOW - timedelta(seconds=5)), now_utc=NOW)
    with pytest.raises(SourceDataRejected, match="MISSING_OR_NON_UTC"):
        validate_source_data_value(source_sample(stamp=datetime(2026, 10, 30)), now_utc=NOW)
    with pytest.raises(SourceDataRejected, match="MISSING_OR_NON_UTC"):
        validate_source_data_value(source_sample(stamp=None), now_utc=NOW)
    with pytest.raises(SourceDataRejected, match="FUTURE_SOURCE_TIMESTAMP"):
        validate_source_data_value(source_sample(stamp=NOW + timedelta(seconds=1)), now_utc=NOW)


@pytest.mark.parametrize("status", [ua.StatusCodes.BadSensorFailure, ua.StatusCodes.Uncertain])
def test_non_good_quality_is_rejected(status):
    with pytest.raises(SourceDataRejected, match="BAD_SOURCE_QUALITY"):
        validate_source_data_value(source_sample(status=status), now_utc=NOW)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "60"])
def test_bad_measurement_values_are_rejected(value):
    with pytest.raises(SourceDataRejected, match="NONFINITE_OR_NONNUMERIC"):
        validate_source_data_value(source_sample(value), now_utc=NOW)


@pytest.mark.parametrize("username", ["Admin", "admin", "unknown", None])
def test_builtin_admin_or_unknown_identity_never_gets_a_fixture_role(username):
    manager = OilGasUserManager("synthetic-unit-test-only", SYNTHETIC_PIN)
    assert (
        manager.get_user(
            SimpleNamespace(allow_remote_admin=True), username, "synthetic-unit-test-only"
        )
        is None
    )


def test_explicit_principals_are_non_admin_and_password_must_match():
    manager = OilGasUserManager("synthetic-unit-test-only", SYNTHETIC_PIN)
    assert manager.get_user(None, "copilot", "wrong") is None
    for name in ("copilot", "operator"):
        user = manager.get_user(None, name, "synthetic-unit-test-only", SYNTHETIC_PEER)
        assert user.role is UserRole.User
        assert user.name == name


@pytest.mark.parametrize("peer", [None, b"", b"different-channel-peer", "not-a-certificate"])
def test_valid_password_without_pinned_channel_certificate_is_rejected(peer):
    manager = OilGasUserManager("synthetic-unit-test-only", SYNTHETIC_PIN)
    assert manager.get_user(None, "copilot", "synthetic-unit-test-only", peer) is None


def write_body(node_id, value=0.72):
    write = ua.WriteValue(
        NodeId=node_id,
        AttributeId=ua.AttributeIds.Value,
        Value=ua.DataValue(ua.Variant(value, ua.VariantType.Double)),
    )
    return Buffer(struct_to_binary(ua.WriteParameters(NodesToWrite=[write])))


def test_approval_is_bound_to_principal_target_and_one_value_and_consumed_once():
    target = ua.NodeId("LoadRequestFraction", 2)
    other = ua.NodeId("HighPressureTripLatched", 2)
    rules = OilGasPermissionRules(target)
    action = ua.NodeId(ua.ObjectIds.WriteRequest_Encoding_DefaultBinary)
    operator = User(UserRole.User, "operator")
    copilot = User(UserRole.User, "copilot")
    assert not rules.check_validity(operator, action, write_body(target))
    rules.approve_load(0.72)
    assert not rules.check_validity(copilot, action, write_body(target))
    assert not rules.check_validity(operator, action, write_body(other))
    assert not rules.check_validity(operator, action, write_body(target, 0.8))
    body = write_body(target)
    before = bytes(body)
    assert rules.check_validity(operator, action, body)
    assert bytes(body) == before  # Permission inspection must not consume the wire buffer.
    assert not rules.check_validity(operator, action, write_body(target))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1, True])
def test_approval_does_not_admit_invalid_load_requests(value):
    with pytest.raises(ValueError):
        OilGasPermissionRules(ua.NodeId("Load", 2)).approve_load(value)


def test_calls_and_malformed_writes_are_denied_by_default():
    rules = OilGasPermissionRules(ua.NodeId("Load", 2))
    user = User(UserRole.User, "operator")
    assert not rules.check_validity(
        user, ua.NodeId(ua.ObjectIds.CallRequest_Encoding_DefaultBinary), Buffer(b"")
    )
    assert not rules.check_validity(
        user, ua.NodeId(ua.ObjectIds.WriteRequest_Encoding_DefaultBinary), Buffer(b"broken")
    )


def test_approved_value_cannot_carry_forged_status_or_server_clock():
    target = ua.NodeId("Load", 2)
    rules = OilGasPermissionRules(target)
    rules.approve_load(0.72)
    operator = User(UserRole.User, "operator")
    action = ua.NodeId(ua.ObjectIds.WriteRequest_Encoding_DefaultBinary)
    for data in (
        ua.DataValue(ua.Variant(0.72), StatusCode=ua.StatusCode(ua.StatusCodes.BadSensorFailure)),
        ua.DataValue(ua.Variant(0.72), ServerTimestamp=NOW),
    ):
        write = ua.WriteValue(NodeId=target, AttributeId=ua.AttributeIds.Value, Value=data)
        wire = Buffer(struct_to_binary(ua.WriteParameters(NodesToWrite=[write])))
        assert not rules.check_validity(operator, action, wire)
    assert rules.check_validity(operator, action, write_body(target))


@pytest.mark.industry
def test_actual_loopback_wire_permissions_source_quality_and_cleanup():
    result = run_opcua_contract()
    failed = [c for c in result["checks"] if c["passed"] is not True]
    assert not failed, failed
    metrics = result["metrics"]
    assert metrics["engine_version"]
    assert metrics["copilot_write_status"] == "BadUserAccessDenied"
    assert metrics["copilot_call_status"] == "BadUserAccessDenied"
    assert metrics["accepted_load_writes"] == 1
    assert metrics["approved_load_readback"] == 0.72
    assert metrics["listener_closed"]
    assert metrics["port_reusable_after_stop"]
    assert metrics["owned_tasks_remaining"] == 0
    assert metrics["transport_encryption_tested"] is True
    assert metrics["security_policy"] == "Basic256Sha256 / SignAndEncrypt"
    assert metrics["rsa_key_bits"] == 2048
    assert metrics["plaintext_activation_status"] == "BadUserAccessDenied"
    assert metrics["temporary_key_material_removed"]
    if os.name == "posix":
        assert metrics["posix_private_key_mode_verified"]
    assert "password" not in str(result).lower()


def test_ephemeral_application_keys_are_restricted_and_distinct(tmp_path):
    material = oil_gas_opcua._certificate_material(tmp_path)
    assert material.server_sha256 != material.client_sha256
    assert material.server_cert.read_bytes().startswith(b"-----BEGIN CERTIFICATE-----")
    if os.name == "posix":
        for path in (material.server_key, material.client_key, material.foreign_key):
            assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_temporary_application_material_is_removed_after_contract_exception(monkeypatch):
    captured = []

    async def broken(material):
        captured.append(material.server_key.parent)
        raise RuntimeError("Synthetic test interruption")

    monkeypatch.setattr(oil_gas_opcua, "_run_contract", broken)
    with pytest.raises(RuntimeError, match="Synthetic test interruption"):
        run_opcua_contract()
    assert captured and all(not Path(path).exists() for path in captured)
