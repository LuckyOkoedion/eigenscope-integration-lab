"""Loopback MQTT v3.1.1 Sparkplug B UNS & read-only CDF/PI historian contracts.

Implements genuine TCP MQTT v3.1.1 packet framing (CONNECT, CONNACK, SUBSCRIBE,
SUBACK, PUBLISH, PUBACK, DISCONNECT) over loopback 127.0.0.1 plus an isolated
HTTP historian contract for Cognite Data Fusion / AVEVA PI Web API. No external
broker, cloud tenant or plant SCADA server is contacted.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import re
import socket
import socketserver
import struct
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

ENGINEERING_UNITS = ("kPag", "degC", "MMSCFD", "RPM")
FORBIDDEN_OPCODES = (
    re.compile(r"\bWRITE_COIL\b", re.I),
    re.compile(r"\bWRITE_REGISTER\b", re.I),
    re.compile(r"\bBYPASS_SIS\b", re.I),
    re.compile(r"\bOPCUA_WRITE(?:_VALUE)?\b", re.I),
    re.compile(r"\bPLC_FORCE\b", re.I),
    re.compile(r"\bMODBUS_FC0?(?:5|6|15|16)\b", re.I),
    re.compile(r"\bSPARKPLUG_(?:NCMD|DCMD)\b", re.I),
)


class SparkplugContractRejected(ValueError):
    """Raised fail-closed when a Sparkplug B UNS message or historian call attempts a write."""


def encode_remaining_length(length: int) -> bytes:
    out = bytearray()
    while True:
        encoded = length % 128
        length //= 128
        if length > 0:
            encoded |= 128
        out.append(encoded)
        if length == 0:
            break
    return bytes(out)


def read_mqtt_packet(sock: socket.socket) -> tuple[int, int, bytes]:
    first = sock.recv(1)
    if not first:
        raise EOFError("MQTT peer closed connection")
    packet_type = first[0] >> 4
    flags = first[0] & 0x0F
    multiplier = 1
    remaining = 0
    for _ in range(4):
        nxt = sock.recv(1)
        if not nxt:
            raise EOFError("Truncated MQTT remaining length")
        byte = nxt[0]
        remaining += (byte & 127) * multiplier
        if (byte & 128) == 0:
            break
        multiplier *= 128
    buf = bytearray()
    while len(buf) < remaining:
        chunk = sock.recv(remaining - len(buf))
        if not chunk:
            raise EOFError("Truncated MQTT packet payload")
        buf.extend(chunk)
    return packet_type, flags, bytes(buf)


def build_mqtt_connect(client_id: str) -> bytes:
    client_bytes = client_id.encode("utf-8")
    var_header = struct.pack("!H4sBBH", 4, b"MQTT", 4, 0x02, 15)
    payload = struct.pack("!H", len(client_bytes)) + client_bytes
    body = var_header + payload
    return bytes([0x10]) + encode_remaining_length(len(body)) + body


def build_mqtt_subscribe(packet_id: int, topic_filter: str) -> bytes:
    topic_bytes = topic_filter.encode("utf-8")
    body = (
        struct.pack("!H", packet_id) + struct.pack("!H", len(topic_bytes)) + topic_bytes + b"\x01"
    )
    return bytes([0x82]) + encode_remaining_length(len(body)) + body


def build_mqtt_publish(topic: str, payload: bytes, *, packet_id: int = 1, qos: int = 1) -> bytes:
    topic_bytes = topic.encode("utf-8")
    var_header = struct.pack("!H", len(topic_bytes)) + topic_bytes
    if qos > 0:
        var_header += struct.pack("!H", packet_id)
    body = var_header + payload
    header_byte = 0x30 | ((qos & 0x03) << 1)
    return bytes([header_byte]) + encode_remaining_length(len(body)) + body


class _LoopbackMQTTBroker:
    """Bounded stdlib TCP MQTT v3.1.1 broker strictly on 127.0.0.1:0."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.05)
        self.port = int(self._sock.getsockname()[1])
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._subscribers: list[tuple[socket.socket, str]] = []
        self._clients: list[socket.socket] = []
        self._threads: list[threading.Thread] = []
        self.received_topics: list[str] = []
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)

    def __enter__(self) -> _LoopbackMQTTBroker:
        self._accept_thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            client.settimeout(2.0)
            with self._lock:
                self._clients.append(client)
            t = threading.Thread(target=self._serve_client, args=(client,), daemon=True)
            t.start()
            with self._lock:
                self._threads.append(t)

    @staticmethod
    def _topic_matches(filt: str, topic: str) -> bool:
        if filt == "#" or filt == topic:
            return True
        if filt.endswith("/#"):
            return topic.startswith(filt[:-1])
        return False

    def _serve_client(self, client: socket.socket) -> None:
        try:
            while not self._stop.is_set():
                try:
                    pkt_type, flags, body = read_mqtt_packet(client)
                except (TimeoutError, EOFError, OSError):
                    break
                if pkt_type == 1:  # CONNECT
                    client.sendall(b"\x20\x02\x00\x00")
                elif pkt_type == 8:  # SUBSCRIBE
                    pid = struct.unpack("!H", body[:2])[0]
                    tlen = struct.unpack("!H", body[2:4])[0]
                    topic_filter = body[4 : 4 + tlen].decode("utf-8")
                    with self._lock:
                        self._subscribers.append((client, topic_filter))
                    client.sendall(bytes([0x90, 0x03, pid >> 8, pid & 0xFF, 0x01]))
                elif pkt_type == 3:  # PUBLISH
                    qos = (flags >> 1) & 0x03
                    tlen = struct.unpack("!H", body[:2])[0]
                    topic = body[2 : 2 + tlen].decode("utf-8")
                    idx = 2 + tlen
                    if qos > 0:
                        pid = struct.unpack("!H", body[idx : idx + 2])[0]
                        idx += 2
                        client.sendall(bytes([0x40, 0x02, pid >> 8, pid & 0xFF]))
                    payload = body[idx:]
                    with self._lock:
                        self.received_topics.append(topic)
                        targets = [
                            s for s, filt in self._subscribers if self._topic_matches(filt, topic)
                        ]
                    outbound = build_mqtt_publish(topic, payload, packet_id=0, qos=0)
                    for target in targets:
                        try:
                            target.sendall(outbound)
                        except OSError:
                            pass
                elif pkt_type == 12:  # PINGREQ
                    client.sendall(b"\xd0\x00")
                elif pkt_type == 14:  # DISCONNECT
                    break
        finally:
            try:
                client.close()
            except OSError:
                pass

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        with self._lock:
            clients = list(self._clients)
            threads = list(self._threads)
        for c in clients:
            try:
                c.close()
            except OSError:
                pass
        self._accept_thread.join(timeout=2)
        for t in threads:
            t.join(timeout=2)


def validate_sparkplug_uns_ingress(topic: str, narrative: str) -> None:
    upper = topic.strip().upper()
    if "/NCMD/" in upper or "/DCMD/" in upper or upper.endswith(("/NCMD", "/DCMD")):
        raise SparkplugContractRejected(f"Sparkplug command topic rejected: {topic}")
    if not upper.startswith("SPBV1.0/") or "/DDATA/" not in upper:
        raise SparkplugContractRejected(f"Only read-only Sparkplug DDATA ingress admitted: {topic}")
    for pattern in FORBIDDEN_OPCODES:
        if pattern.search(narrative):
            raise SparkplugContractRejected("PLC or SIS write opcode rejected in UNS narrative")


def validate_read_only_proposal(proposal: dict[str, Any]) -> None:
    if (
        not isinstance(proposal, dict)
        or proposal.get("mode") != "READ_ONLY_PROPOSAL"
        or proposal.get("requires_human_operator_signoff") is not True
        or proposal.get("independent_sis_interlock_intact") is not True
        or proposal.get("command_opcodes") != []
    ):
        raise SparkplugContractRejected("Invalid READ_ONLY_PROPOSAL structure")
    summary = str(proposal.get("summary", ""))
    for unit in ENGINEERING_UNITS:
        if unit not in summary:
            raise SparkplugContractRejected(f"Missing engineering unit {unit}")
    for pattern in FORBIDDEN_OPCODES:
        if pattern.search(summary):
            raise SparkplugContractRejected("Proposal text contains forbidden opcode")


class _HistorianServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]


def run_sparkplug_uns_contract() -> dict[str, Any]:
    """Run loopback MQTT v3.1.1 Sparkplug B UNS & Cognite CDF / AVEVA PI contracts."""
    before = time.perf_counter()
    checks: list[dict[str, Any]] = []
    valid_topic = "spBv1.0/LabPlant/DDATA/IgnitionEdge01/Compressor01"
    payload_obj = {
        "schema_version": 1,
        "topic": valid_topic,
        "facility_tag": "[FACILITY_1]",
        "asset_tag": "[ASSET_1]",
        "metrics": [
            {"name": "SuctionPressure", "value": 1500.0, "unit": "kPag"},
            {"name": "DischargeTemp", "value": 26.85, "unit": "degC"},
            {"name": "GasFlow", "value": 42.5, "unit": "MMSCFD"},
            {"name": "ShaftSpeed", "value": 3600, "unit": "RPM"},
        ],
    }
    raw_payload = json.dumps(payload_obj, sort_keys=True, separators=(",", ":")).encode("utf-8")

    # 1. Real loopback TCP MQTT v3.1.1 Sparkplug B DDATA round-trip
    mqtt_roundtrip_ok = False
    broker_port = 0
    with _LoopbackMQTTBroker() as broker:
        broker_port = broker.port
        sub_sock = socket.create_connection(("127.0.0.1", broker_port), timeout=2.0)
        pub_sock = socket.create_connection(("127.0.0.1", broker_port), timeout=2.0)
        try:
            sub_sock.sendall(build_mqtt_connect("eigenscope-lab-sub"))
            assert read_mqtt_packet(sub_sock)[:2] == (2, 0)
            sub_sock.sendall(build_mqtt_subscribe(1, "spBv1.0/LabPlant/DDATA/#"))
            assert read_mqtt_packet(sub_sock)[0] == 9

            pub_sock.sendall(build_mqtt_connect("eigenscope-lab-pub"))
            assert read_mqtt_packet(pub_sock)[:2] == (2, 0)
            pub_sock.sendall(build_mqtt_publish(valid_topic, raw_payload, packet_id=10, qos=1))
            puback_type, _, puback_body = read_mqtt_packet(pub_sock)
            assert puback_type == 4 and struct.unpack("!H", puback_body)[0] == 10

            msg_type, _, msg_body = read_mqtt_packet(sub_sock)
            tlen = struct.unpack("!H", msg_body[:2])[0]
            recv_topic = msg_body[2 : 2 + tlen].decode("utf-8")
            recv_json = json.loads(msg_body[2 + tlen :].decode("utf-8"))
            mqtt_roundtrip_ok = (
                msg_type == 3 and recv_topic == valid_topic and recv_json == payload_obj
            )
            sub_sock.sendall(b"\xe0\x00")
            pub_sock.sendall(b"\xe0\x00")
        finally:
            sub_sock.close()
            pub_sock.close()

    checks.append({"name": "mqtt_v311_sparkplug_ddata_roundtrip", "passed": mqtt_roundtrip_ok})

    # 2. Reject Sparkplug NCMD/DCMD and PLC/SIS write opcodes
    cmd_rejected = True
    for bad_topic, bad_note in (
        ("spBv1.0/LabPlant/NCMD/IgnitionEdge01", "Inspect pressure kPag."),
        ("spBv1.0/LabPlant/DCMD/IgnitionEdge01/Compressor01", "Inspect flow MMSCFD."),
        (valid_topic, "Issue WRITE_COIL and BYPASS_SIS on controller."),
        (valid_topic, "Send MODBUS_FC06 and OPCUA_WRITE to valve."),
    ):
        try:
            validate_sparkplug_uns_ingress(bad_topic, bad_note)
        except SparkplugContractRejected:
            continue
        cmd_rejected = False
    checks.append({"name": "ncmd_dcmd_and_write_opcodes_blocked", "passed": cmd_rejected})

    # 3. Engineering units preserved in READ_ONLY_PROPOSAL
    units_and_proposal_ok = True
    try:
        validate_read_only_proposal(
            {
                "mode": "READ_ONLY_PROPOSAL",
                "requires_human_operator_signoff": True,
                "independent_sis_interlock_intact": True,
                "command_opcodes": [],
                "summary": "Check 1500 kPag suction, 26.85 degC discharge, 42.5 MMSCFD at 3600 RPM.",
            }
        )
    except SparkplugContractRejected:
        units_and_proposal_ok = False
    try:
        validate_read_only_proposal(
            {
                "mode": "READ_ONLY_PROPOSAL",
                "requires_human_operator_signoff": True,
                "independent_sis_interlock_intact": False,
                "command_opcodes": ["WRITE_COIL"],
                "summary": "1500 kPag",
            }
        )
        units_and_proposal_ok = False
    except SparkplugContractRejected:
        pass
    checks.append(
        {
            "name": "engineering_units_and_readonly_proposal_enforced",
            "passed": units_and_proposal_ok,
        }
    )

    # 4. Cognite CDF / AVEVA PI Web API read-only loopback contract
    pi_state = {"reads": 0, "blocked_writes": 0, "cdf_calls": 0}

    class HistorianHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:
            return

        def _reply(self, status: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            if urlsplit(self.path).path == "/piwebapi/streams/lab-train-c04/value":
                pi_state["reads"] += 1
                self._reply(200, {"Value": 1500.0, "UnitsAbbreviation": "kPag", "Good": True})
                return
            self._reply(404, {"error_code": "STREAM_NOT_FOUND"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path.startswith("/piwebapi/"):
                pi_state["blocked_writes"] += 1
                self._reply(405, {"error_code": "READ_ONLY_HISTORIAN_WRITE_BLOCKED"})
                return
            if path == "/cdf/functions/eigenscope-gate":
                length = int(self.headers.get("Content-Length", "0"))
                req_json = json.loads(self.rfile.read(length))
                if not isinstance(req_json, dict) or req_json.get("task_core") is None:
                    self._reply(400, {"error_code": "INVALID_CDF_PAYLOAD"})
                    return
                pi_state["cdf_calls"] += 1
                digest = "sha256:" + hashlib.sha256(req_json["task_core"].encode()).hexdigest()
                self._reply(
                    200,
                    {
                        "status": "CDF_FUNCTION_OK",
                        "audit_receipt": {
                            "core_proof_digest": digest,
                            "vault_ticket_scrubbed": True,
                        },
                    },
                )
                return
            self._reply(404, {"error_code": "NOT_FOUND"})

    historian = _HistorianServer(("127.0.0.1", 0), HistorianHandler)
    h_thread = threading.Thread(
        target=historian.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    h_thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        base = f"http://127.0.0.1:{historian.server_port}"
        with opener.open(f"{base}/piwebapi/streams/lab-train-c04/value", timeout=5.0) as r:
            pi_read_status = r.status
            pi_read = json.loads(r.read().decode("utf-8"))
        pi_write_req = urllib.request.Request(
            f"{base}/piwebapi/streams/lab-train-c04/value",
            data=json.dumps({"Value": 9999.0}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with opener.open(pi_write_req, timeout=5.0) as r:
                pi_write_status = r.status
        except urllib.error.HTTPError as err:
            pi_write_status = err.code

        cdf_req = urllib.request.Request(
            f"{base}/cdf/functions/eigenscope-gate",
            data=json.dumps({"task_core": "Review [FACILITY_1] 1500 kPag 26.85 degC."}).encode(
                "utf-8"
            ),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(cdf_req, timeout=5.0) as r:
            cdf_status = r.status
            cdf_body = json.loads(r.read().decode("utf-8"))

        checks.append(
            {
                "name": "historian_pi_cdf_readonly_contract_enforced",
                "passed": bool(
                    pi_read_status == 200
                    and pi_read.get("UnitsAbbreviation") == "kPag"
                    and pi_write_status == 405
                    and cdf_status == 200
                    and cdf_body.get("audit_receipt", {}).get("vault_ticket_scrubbed") is True
                    and pi_state == {"reads": 1, "blocked_writes": 1, "cdf_calls": 1}
                ),
            }
        )
    finally:
        historian.shutdown()
        historian.server_close()
        h_thread.join(timeout=2)

    return {
        "checks": checks,
        "metrics": {
            "mqtt_protocol": "MQTT 3.1.1 / Sparkplug B UNS",
            "broker_port_closed": True,
            "historian_reads": pi_state["reads"],
            "historian_blocked_writes": pi_state["blocked_writes"],
            "cdf_function_calls": pi_state["cdf_calls"],
            "contract_wall_ms": round((time.perf_counter() - before) * 1000, 3),
        },
    }
