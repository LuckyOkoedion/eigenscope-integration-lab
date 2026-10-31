"""Real loopback OPC UA authorization and telemetry contracts for the gas lab.

The loopback endpoint requires Basic256Sha256 SignAndEncrypt with ephemeral
application certificates and explicit peer pins. This exercises an encrypted
binary channel, not a production PKI, historian, PLC or certified safety system.
Temporary private keys are removed when the fixture exits.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import secrets
import socket
import stat
import tempfile
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from typing import Any

import asyncua
from asyncua import Client, Server, ua
from asyncua.crypto.permission_rules import PermissionRuleset, User, UserRole
from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
from asyncua.server.user_managers import UserManager
from asyncua.ua.ua_binary import struct_from_binary
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

_SERVER_URI = "urn:eigenscope:integration-lab:oil-gas:server"
_CLIENT_URI = "urn:eigenscope:integration-lab:oil-gas:client"


@dataclass(frozen=True)
class _CertificateMaterial:
    server_cert: Path
    server_key: Path
    client_cert: Path
    client_key: Path
    foreign_cert: Path
    foreign_key: Path
    server_sha256: str
    client_sha256: str


def _application_pair(directory: Path, name: str, uri: str, purpose: Any) -> tuple[Path, Path, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"EigenScope {name} fixture")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.UniformResourceIdentifier(uri),
                    x509.DNSName("localhost"),
                    x509.DNSName(socket.gethostname()),
                    x509.IPAddress(ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=True,
                data_encipherment=True,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([purpose]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = directory / f"{name}.pem", directory / f"{name}-key.pem"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    return cert_file, key_file, certificate.fingerprint(hashes.SHA256()).hex()


def _certificate_material(directory: Path) -> _CertificateMaterial:
    server = _application_pair(directory, "server", _SERVER_URI, ExtendedKeyUsageOID.SERVER_AUTH)
    client = _application_pair(directory, "client", _CLIENT_URI, ExtendedKeyUsageOID.CLIENT_AUTH)
    foreign = _application_pair(
        directory, "untrusted-client", _CLIENT_URI, ExtendedKeyUsageOID.CLIENT_AUTH
    )
    return _CertificateMaterial(
        server[0], server[1], client[0], client[1], foreign[0], foreign[1], server[2], client[2]
    )


class SourceDataRejected(ValueError):
    """A source measurement cannot be used as current, good plant telemetry."""


def validate_source_data_value(
    sample: ua.DataValue, *, now_utc: datetime | None = None, max_age_seconds: float = 2.0
) -> float:
    """Validate wire quality and the source UTC clock, not the simulation clock."""
    if (
        type(max_age_seconds) not in (int, float)
        or not math.isfinite(max_age_seconds)
        or not 0.05 <= max_age_seconds <= 60.0
    ):
        raise ValueError("Maximum source age must be in [0.05, 60] seconds")
    now = now_utc or datetime.now(UTC)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("The comparison clock must be timezone-aware UTC")
    if (
        not isinstance(sample, ua.DataValue)
        or sample.StatusCode is None
        or not sample.StatusCode.is_good()
    ):
        raise SourceDataRejected("BAD_SOURCE_QUALITY")
    stamp = sample.SourceTimestamp
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise SourceDataRejected("MISSING_OR_NON_UTC_SOURCE_TIMESTAMP")
    age = (now - stamp).total_seconds()
    if age < -0.05:
        raise SourceDataRejected("FUTURE_SOURCE_TIMESTAMP")
    if age > max_age_seconds:
        raise SourceDataRejected("STALE_SOURCE_TIMESTAMP")
    value = sample.Value.Value if sample.Value is not None else None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise SourceDataRejected("NONFINITE_OR_NONNUMERIC_SOURCE_VALUE")
    return float(value)


class OilGasUserManager(UserManager):
    """Two fixture principals, neither of which can become a remote Admin."""

    def __init__(self, fixture_password: str, expected_client_sha256: str) -> None:
        self._password = fixture_password
        self._expected_client_sha256 = expected_client_sha256

    def get_user(
        self,
        iserver: Any,
        username: str | None = None,
        password: str | None = None,
        certificate: Any = None,
    ) -> User | None:
        if (
            username not in {"copilot", "operator"}
            or not isinstance(password, str)
            or not secrets.compare_digest(password, self._password)
            or not isinstance(certificate, bytes)
            or not secrets.compare_digest(
                hashlib.sha256(certificate).hexdigest(), self._expected_client_sha256
            )
        ):
            return None
        return User(role=UserRole.User, name=username)


class OilGasPermissionRules(PermissionRuleset):
    """Deny-by-default binary service policy with one approved write target.

    The approval is a one-shot in-memory fixture grant, separate from an LLM or
    the client request. Production change management is outside this fixture.
    """

    def __init__(self, load_node_id: ua.NodeId) -> None:
        self.load_node_id = load_node_id
        self._approved: tuple[float, float] | None = None
        self.denied_writes = 0
        self.denied_calls = 0
        self.accepted_writes = 0
        self._read_services = {
            ua.NodeId(value)
            for value in (
                ua.ObjectIds.ReadRequest_Encoding_DefaultBinary,
                ua.ObjectIds.BrowseRequest_Encoding_DefaultBinary,
                ua.ObjectIds.BrowseNextRequest_Encoding_DefaultBinary,
                ua.ObjectIds.TranslateBrowsePathsToNodeIdsRequest_Encoding_DefaultBinary,
                ua.ObjectIds.RegisterNodesRequest_Encoding_DefaultBinary,
                ua.ObjectIds.UnregisterNodesRequest_Encoding_DefaultBinary,
            )
        }

    def approve_load(self, value: float) -> None:
        if type(value) not in (int, float) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("Approved load must be a finite fraction in [0, 1]")
        self._approved = (float(value), time.monotonic() + 5.0)

    def check_validity(self, user: User, action_type: ua.NodeId, body: Any) -> bool:
        if (
            not isinstance(user, User)
            or user.role is not UserRole.User
            or user.name not in {"copilot", "operator"}
        ):
            return False
        if action_type in self._read_services:
            return True
        if action_type == ua.NodeId(ua.ObjectIds.CallRequest_Encoding_DefaultBinary):
            self.denied_calls += 1
            return False
        if action_type != ua.NodeId(ua.ObjectIds.WriteRequest_Encoding_DefaultBinary):
            return False
        allowed = False
        try:
            if user.name == "operator" and len(body) <= 4096:
                parameters = struct_from_binary(ua.WriteParameters, body.copy())
                if len(parameters.NodesToWrite) == 1:
                    write = parameters.NodesToWrite[0]
                    variant = write.Value.Value
                    value = variant.Value if variant is not None else None
                    allowed = (
                        write.NodeId == self.load_node_id
                        and write.AttributeId == ua.AttributeIds.Value
                        and not write.IndexRange
                        and write.Value.StatusCode is not None
                        and write.Value.StatusCode.value == ua.StatusCodes.Good
                        and write.Value.ServerTimestamp is None
                        and variant is not None
                        and variant.VariantType is ua.VariantType.Double
                        and not variant.is_array
                        and type(value) is float
                        and math.isfinite(value)
                        and 0.0 <= value <= 1.0
                        and self._approved is not None
                        and value == self._approved[0]
                        and time.monotonic() <= self._approved[1]
                    )
        except (AttributeError, TypeError, ValueError, ua.UaError):
            allowed = False
        if allowed:
            self._approved = None
            self.accepted_writes += 1
        else:
            self.denied_writes += 1
        return bool(allowed)


async def _status_of(operation: Awaitable[Any]) -> int:
    try:
        await operation
    except ua.UaStatusCodeError as exc:
        return exc.code
    return ua.StatusCodes.Good


def _check(name: str, passed: bool) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed)}


async def _run_contract(material: _CertificateMaterial) -> dict[str, Any]:
    baseline_tasks = set(asyncio.all_tasks())
    password = secrets.token_urlsafe(20)
    server = Server(user_manager=OilGasUserManager(password, material.client_sha256))
    await server.init()
    server.allow_remote_admin(False)
    await server.set_application_uri(_SERVER_URI)
    await server.load_certificate(material.server_cert)
    await server.load_private_key(material.server_key)

    async def validate_client_certificate(certificate: x509.Certificate, _description: Any) -> None:
        if certificate.fingerprint(hashes.SHA256()).hex() != material.client_sha256:
            raise ua.UaStatusCodeError(ua.StatusCodes.BadCertificateUntrusted)

    server.set_certificate_validator(validate_client_certificate)
    server.set_endpoint("opc.tcp://127.0.0.1:0/eigenscope/oil-gas/")
    server.set_server_name("EigenScope gas-compressor protocol fixture")
    server.set_identity_tokens([ua.UserNameIdentityToken])
    namespace = await server.register_namespace("urn:eigenscope:integration-lab:oil-gas")
    compressor = await server.nodes.objects.add_object(namespace, "SyntheticCompressor")
    load = await compressor.add_variable(namespace, "LoadRequestFraction", 0.6)
    pressure = await compressor.add_variable(namespace, "DischargePressureBarA", 60.0)
    trip = await compressor.add_variable(namespace, "HighPressureTripLatched", True)
    simulation_clock = await compressor.add_variable(namespace, "SimulationClockSeconds", 12.5)
    await load.set_writable()
    await pressure.set_writable(False)
    await trip.set_writable(False)
    await simulation_clock.set_writable(False)
    rules = OilGasPermissionRules(load.nodeid)
    server.set_security_policy(
        [ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt], permission_ruleset=rules
    )
    checks: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {
        "engine": "asyncua OPC UA binary client/server",
        "engine_version": asyncua.__version__,
        "security_policy": "Basic256Sha256 / SignAndEncrypt",
        "transport_encryption_tested": True,
        "certificate_trust": "ephemeral explicitly pinned application certificates",
        "server_certificate_sha256": material.server_sha256,
        "client_certificate_sha256": material.client_sha256,
        "rsa_key_bits": 2048,
        "remote_admin_enabled": False,
        "load_request_unit": "fraction",
        "pressure_unit": "bar absolute",
    }
    started = False
    port = 0
    try:
        await server.start()
        started = True
        port = server.bserver.port
        endpoint = f"opc.tcp://127.0.0.1:{port}/eigenscope/oil-gas/"

        async def client_for(
            name: str, *, wrong_server_pin: bool = False, foreign_client: bool = False
        ) -> Client:
            client = Client(endpoint, timeout=2.0, watchdog_intervall=0.5)
            client.session_timeout = 60_000
            client.application_uri = _CLIENT_URI
            client.set_user(name)
            client.set_password(password)
            await client.set_security(
                SecurityPolicyBasic256Sha256,
                material.foreign_cert if foreign_client else material.client_cert,
                material.foreign_key if foreign_client else material.client_key,
                server_certificate=str(
                    material.foreign_cert if wrong_server_pin else material.server_cert
                ),
                mode=ua.MessageSecurityMode.SignAndEncrypt,
            )
            return client

        async with await client_for("copilot") as copilot:
            checks.append(
                _check(
                    "actual_sign_and_encrypt_channel",
                    copilot.security_policy.Mode == ua.MessageSecurityMode.SignAndEncrypt
                    and isinstance(copilot.security_policy, SecurityPolicyBasic256Sha256),
                )
            )
            endpoints = await copilot.get_endpoints()
            checks.append(
                _check(
                    "server_offers_only_encrypted_session_endpoints",
                    bool(endpoints)
                    and all(
                        endpoint.SecurityMode == ua.MessageSecurityMode.SignAndEncrypt
                        for endpoint in endpoints
                    ),
                )
            )
            remote_pressure = copilot.get_node(pressure.nodeid)
            remote_load = copilot.get_node(load.nodeid)
            remote_trip = copilot.get_node(trip.nodeid)
            now = datetime.now(UTC)
            await pressure.write_value(
                ua.DataValue(
                    ua.Variant(60.0, ua.VariantType.Double),
                    SourceTimestamp=now - timedelta(milliseconds=100),
                    ServerTimestamp=now,
                )
            )
            before = time.perf_counter()
            sample = await remote_pressure.read_data_value(raise_on_bad_status=False)
            value = validate_source_data_value(sample)
            metrics["read_data_value_wall_ms"] = round((time.perf_counter() - before) * 1000.0, 3)
            metrics["source_timestamp_utc"] = sample.SourceTimestamp.isoformat()
            metrics["simulation_clock_seconds"] = await copilot.get_node(
                simulation_clock.nodeid
            ).read_value()
            checks.append(_check("good_utc_source_measurement_read_over_wire", value == 60.0))
            checks.append(
                _check(
                    "source_utc_clock_distinct_from_simulation_clock",
                    sample.SourceTimestamp.timestamp() > 1e9,
                )
            )

            denied_write = await _status_of(remote_load.write_value(0.85))
            unchanged = await remote_load.read_value()
            metrics["copilot_write_status"] = ua.StatusCode(denied_write).name
            checks.append(
                _check(
                    "copilot_write_denied_and_load_unchanged",
                    denied_write == ua.StatusCodes.BadUserAccessDenied and unchanged == 0.6,
                )
            )
            denied_call = await _status_of(
                copilot.get_node(compressor.nodeid).call_method(
                    ua.NodeId("ForbiddenResetTrip", namespace)
                )
            )
            metrics["copilot_call_status"] = ua.StatusCode(denied_call).name
            checks.append(
                _check(
                    "copilot_forged_call_denied", denied_call == ua.StatusCodes.BadUserAccessDenied
                )
            )
            unknown = await _status_of(
                copilot.get_node(ua.NodeId("UnknownSyntheticNode", namespace)).read_value()
            )
            checks.append(
                _check(
                    "unknown_node_returns_protocol_error",
                    unknown == ua.StatusCodes.BadNodeIdUnknown,
                )
            )

            for name, stamp, quality in (
                (
                    "stale_source_with_fresh_server_timestamp",
                    now - timedelta(seconds=10),
                    ua.StatusCodes.Good,
                ),
                ("bad_source_quality", datetime.now(UTC), ua.StatusCodes.BadSensorFailure),
                (
                    "future_source_timestamp",
                    datetime.now(UTC) + timedelta(seconds=5),
                    ua.StatusCodes.Good,
                ),
            ):
                await pressure.write_value(
                    ua.DataValue(
                        ua.Variant(60.0, ua.VariantType.Double),
                        StatusCode=ua.StatusCode(quality),
                        SourceTimestamp=stamp,
                        ServerTimestamp=datetime.now(UTC),
                    )
                )
                wire_sample = await remote_pressure.read_data_value(raise_on_bad_status=False)
                rejected = False
                try:
                    validate_source_data_value(wire_sample)
                except SourceDataRejected:
                    rejected = True
                checks.append(_check(name + "_rejected", rejected))

            async with await client_for("operator") as operator:
                operator_load = operator.get_node(load.nodeid)
                no_approval = await _status_of(operator_load.write_value(0.72))
                checks.append(
                    _check(
                        "operator_write_without_independent_approval_denied",
                        no_approval == ua.StatusCodes.BadUserAccessDenied,
                    )
                )
                rules.approve_load(0.72)
                denied_approved = await _status_of(remote_load.write_value(0.72))
                checks.append(
                    _check(
                        "approval_does_not_grant_copilot_write_access",
                        denied_approved == ua.StatusCodes.BadUserAccessDenied,
                    )
                )
                await operator_load.write_value(0.72)
                readback = await remote_load.read_value()
                metrics["approved_load_readback"] = readback
                checks.append(_check("approved_operator_load_write_and_readback", readback == 0.72))
                replay = await _status_of(operator_load.write_value(0.72))
                checks.append(
                    _check(
                        "consumed_approval_cannot_be_replayed",
                        replay == ua.StatusCodes.BadUserAccessDenied,
                    )
                )
                safety_write = await _status_of(operator.get_node(trip.nodeid).write_value(False))
                checks.append(
                    _check(
                        "safety_trip_read_only_even_for_operator",
                        safety_write == ua.StatusCodes.BadUserAccessDenied
                        and await remote_trip.read_value() is True,
                    )
                )
                pressure_write = await _status_of(
                    operator.get_node(pressure.nodeid).write_value(1.0)
                )
                checks.append(
                    _check(
                        "operator_cannot_write_measurement_nodes",
                        pressure_write == ua.StatusCodes.BadUserAccessDenied
                        and await operator.get_node(pressure.nodeid).read_value() == 60.0,
                    )
                )

        async def forged_admin_session() -> None:
            async with await client_for("Admin"):
                pass

        admin_status = await _status_of(forged_admin_session())
        checks.append(
            _check(
                "remote_admin_identity_rejected", admin_status == ua.StatusCodes.BadUserAccessDenied
            )
        )

        # Use lower-level requests: the normal client rejects the unadvertised
        # plaintext endpoint locally, which would not test server enforcement.
        # asyncua permits NoSecurity discovery; credentials alone must not turn
        # that channel into an authenticated process-data session.
        plain = Client(endpoint, timeout=2.0)
        try:
            await plain.connect_socket()
            await plain.send_hello()
            await plain.open_secure_channel()
            parameters = ua.CreateSessionParameters(
                ClientDescription=ua.ApplicationDescription(ApplicationUri=_CLIENT_URI),
                EndpointUrl=endpoint,
                RequestedSessionTimeout=60_000,
                ClientNonce=secrets.token_bytes(32),
            )
            await plain.uaclient.create_session(parameters)
            activation = ua.ActivateSessionParameters(
                UserIdentityToken=ua.UserNameIdentityToken(
                    PolicyId="username",
                    UserName="copilot",
                    Password=password.encode("utf-8"),
                )
            )
            plaintext_status = await _status_of(plain.uaclient.activate_session(activation))
            checks.append(
                _check(
                    "plaintext_channel_cannot_activate_with_valid_credentials",
                    plaintext_status == ua.StatusCodes.BadUserAccessDenied,
                )
            )
            metrics["plaintext_activation_status"] = ua.StatusCode(plaintext_status).name
        finally:
            await plain.disconnect()

        async def mismatched_peer(
            *, wrong_server_pin: bool = False, foreign_client: bool = False
        ) -> None:
            async with await client_for(
                "copilot", wrong_server_pin=wrong_server_pin, foreign_client=foreign_client
            ):
                pass

        wrong_pin_rejected = False
        try:
            await asyncio.wait_for(mismatched_peer(wrong_server_pin=True), timeout=3.0)
        except (ua.UaError, OSError, ConnectionError, TimeoutError) as exc:
            wrong_pin_rejected = True
            metrics["wrong_server_pin_exception"] = type(exc).__name__
        checks.append(_check("wrong_pinned_server_certificate_cannot_connect", wrong_pin_rejected))
        foreign_status = await _status_of(mismatched_peer(foreign_client=True))
        checks.append(
            _check(
                "untrusted_client_application_certificate_rejected",
                foreign_status == ua.StatusCodes.BadCertificateUntrusted,
            )
        )
        metrics["accepted_load_writes"] = rules.accepted_writes
        metrics["denied_write_requests"] = rules.denied_writes
        metrics["denied_call_requests"] = rules.denied_calls
    finally:
        if started:
            await asyncio.wait_for(server.stop(), timeout=3.0)
        password = ""

    # Verify resources after the real server/client contexts have exited.
    await asyncio.sleep(0)
    for _ in range(20):
        pending = [t for t in asyncio.all_tasks() - baseline_tasks if not t.done()]
        if not pending:
            break
        await asyncio.sleep(0.01)
    listener_closed = server.bserver._server is not None and not server.bserver._server.is_serving()
    port_reusable = False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
            port_reusable = True
        except OSError:
            pass
    metrics["listener_closed"] = listener_closed
    metrics["port_reusable_after_stop"] = port_reusable
    metrics["owned_tasks_remaining"] = len(pending)
    checks.append(
        _check(
            "server_stopped_port_reusable_no_owned_tasks",
            listener_closed and port_reusable and not pending,
        )
    )
    return {"checks": checks, "metrics": metrics}


def run_opcua_contract() -> dict[str, Any]:
    """Run bounded actual-wire checks synchronously; no production endpoint exists."""

    before = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="eigenscope-opcua-") as temporary:
        directory = Path(temporary)
        material = _certificate_material(directory)
        modes = [
            stat.S_IMODE(path.stat().st_mode)
            for path in (material.server_key, material.client_key, material.foreign_key)
        ]

        async def bounded() -> dict[str, Any]:
            return await asyncio.wait_for(_run_contract(material), timeout=25.0)

        result = asyncio.run(bounded())
        result["metrics"]["requested_private_key_file_mode"] = "0600"
        result["metrics"]["posix_private_key_mode_verified"] = (
            all(mode == 0o600 for mode in modes) if os.name == "posix" else None
        )
    removed = not directory.exists()
    result["metrics"]["temporary_key_material_removed"] = removed
    result["checks"].append(_check("temporary_application_keys_removed_from_disk", removed))
    result["metrics"]["protocol_contract_wall_ms"] = round((time.perf_counter() - before) * 1000, 3)
    return result
