#!/usr/bin/env python3
"""Optional isolated Inductive Automation Ignition 8.1 + Mosquitto MQTT lab launcher.

Requires an explicit --accept-ignition-eula confirmation on prepare/up because
Ignition is distributed under Inductive Automation's EULA. All published ports are
bound strictly to 127.0.0.1 (8088 UI, 62541 OPC UA, 1883 MQTT). Generated secrets
and state remain under gitignored .lab/ignition/ and are never printed to stdout.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import secrets
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".lab" / "ignition"
LOCK = json.loads(Path(__file__).with_name("upstream.lock.json").read_text())
PROJECT = "eigenscope-lab-ignition"
COMPOSE = STATE / "compose.json"
ENV_FILE = STATE / ".env"
PASS_FILE = STATE / ".admin-password"
MOSQUITTO_CONF = STATE / "mosquitto.conf"
SUBNET = "10.232.0.0/24"


def execute(
    command: list[str],
    *,
    timeout: int = 120,
    cwd: Path | None = None,
    capture: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, cwd=cwd, timeout=timeout, text=True, capture_output=capture, check=check
    )


def docker_prefix(context: str | None) -> list[str]:
    return ["docker", "--context", context] if context else ["docker"]


def docker(context: str | None, *arguments: str, **kwargs: Any) -> subprocess.CompletedProcess:
    return execute(docker_prefix(context) + list(arguments), **kwargs)


def compose(context: str | None, *arguments: str, **kwargs: Any) -> subprocess.CompletedProcess:
    return docker(
        context, "compose", "--project-name", PROJECT, "-f", str(COMPOSE), *arguments, **kwargs
    )


def doctor(context: str | None) -> dict[str, Any]:
    info = json.loads(docker(context, "info", "--format", "{{json .}}").stdout)
    result = {
        "docker_server": info["ServerVersion"],
        "os": info["OSType"],
        "architecture": info["Architecture"],
        "cpus": info["NCPU"],
        "memory_gib": round(info["MemTotal"] / 2**30, 2),
        "compose": docker(context, "compose", "version", "--short").stdout.strip(),
        "context": context or "current (unchanged)",
        "ignition_version": LOCK["versions"]["ignition"],
        "mosquitto_version": LOCK["versions"]["mosquitto"],
    }
    if info["OSType"] != "linux" or info["Architecture"] not in (
        "x86_64",
        "amd64",
        "arm64",
        "aarch64",
    ):
        raise RuntimeError(
            "Ignition/Mosquitto lab images require a Linux x86_64 or arm64 Docker daemon"
        )
    if info["MemTotal"] < 2 * 2**30:
        raise RuntimeError(
            "The Ignition stack requires at least 2GiB Docker memory; 4GiB+ recommended"
        )
    return result


def check_network_conflicts(networks: list[dict[str, Any]]) -> None:
    target = ipaddress.ip_network(SUBNET)
    for net in networks:
        if net.get("Name") == f"{PROJECT}-isolated":
            continue
        for config in (net.get("IPAM") or {}).get("Config") or []:
            subnet = config.get("Subnet")
            if subnet:
                net_obj = ipaddress.ip_network(subnet)
                if net_obj.version == 4 and net_obj.overlaps(target):
                    raise RuntimeError(
                        "Lab Ignition subnet 10.232.0.0/24 conflicts with an existing Docker network"
                    )


def _write_secret_0600(path: Path, content: str) -> None:
    if path.exists():
        path.unlink()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)


def prepare(
    context: str | None,
    *,
    accept_eula: bool = False,
    allow_unsigned_modules: bool = False,
) -> dict[str, Any]:
    if not accept_eula:
        raise RuntimeError(
            "Explicitly pass --accept-ignition-eula to acknowledge Inductive Automation's EULA before preparing the stack"
        )
    STATE.mkdir(parents=True, exist_ok=True)
    if not PASS_FILE.exists():
        _write_secret_0600(PASS_FILE, secrets.token_urlsafe(18) + "\n")
    admin_password = PASS_FILE.read_text(encoding="utf-8").strip()
    _write_secret_0600(
        ENV_FILE,
        f"ACCEPT_IGNITION_EULA=Y\nGATEWAY_ADMIN_USERNAME=admin\nGATEWAY_ADMIN_PASSWORD={admin_password}\n",
    )
    MOSQUITTO_CONF.write_text(
        "listener 1883\nallow_anonymous true\npersistence false\n", encoding="utf-8"
    )
    ignition_cmd = [
        "-n",
        "EigenScopeLabGateway",
        "-a",
        "localhost",
        "-h",
        "8088",
        "-s",
        "8043",
    ]
    if allow_unsigned_modules:
        ignition_cmd.extend(["--", "-Dignition.allowunsignedmodules=true"])

    document = {
        "name": PROJECT,
        "services": {
            "mosquitto": {
                "container_name": "egslab-mosquitto",
                "image": LOCK["images"]["mosquitto"],
                "pull_policy": "never",
                "labels": {"org.eigenscope.lab": "ignition"},
                "ports": ["127.0.0.1:1883:1883"],
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(MOSQUITTO_CONF),
                        "target": "/mosquitto/config/mosquitto.conf",
                        "read_only": True,
                    }
                ],
                "restart": "no",
                "mem_limit": "128m",
            },
            "ignition": {
                "container_name": "egslab-ignition",
                "image": LOCK["images"]["ignition"],
                "pull_policy": "never",
                "labels": {"org.eigenscope.lab": "ignition"},
                "ports": ["127.0.0.1:8088:8088", "127.0.0.1:62541:62541"],
                "env_file": [str(ENV_FILE)],
                "environment": {
                    "IGNITION_EDITION": "standard",
                    "TZ": "UTC",
                    "GATEWAY_MODULES_ENABLED": "opc-ua,perspective,tag-historian,modbus-driver-v2",
                },
                "command": ignition_cmd,
                "volumes": [
                    {
                        "type": "volume",
                        "source": "ignitiondata",
                        "target": "/usr/local/bin/ignition/data",
                    }
                ],
                "restart": "no",
                "mem_limit": "2048m",
                "depends_on": ["mosquitto"],
            },
        },
        "networks": {
            "default": {
                "name": f"{PROJECT}-isolated",
                "driver": "bridge",
                "ipam": {"config": [{"subnet": SUBNET}]},
            }
        },
        "volumes": {"ignitiondata": {"name": f"{PROJECT}-data"}},
    }
    COMPOSE.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return {
        "prepared": True,
        "project": PROJECT,
        "images": LOCK["images"],
        "loopback_ports": ["127.0.0.1:8088", "127.0.0.1:62541", "127.0.0.1:1883"],
        "allow_unsigned_modules": bool(allow_unsigned_modules),
        "password_file": str(PASS_FILE),
        "context": context or "current (unchanged)",
    }


def up(
    context: str | None,
    *,
    accept_eula: bool = False,
    allow_unsigned_modules: bool = False,
) -> dict[str, Any]:
    prerequisites = doctor(context)
    prepare(context, accept_eula=accept_eula, allow_unsigned_modules=allow_unsigned_modules)
    for image in LOCK["images"].values():
        ready = docker(context, "image", "inspect", image, check=False)
        if ready.returncode != 0:
            raise RuntimeError(
                "Required Ignition/Mosquitto image is not present locally; run 'pull' first. No containers were started."
            )
    ids = docker(context, "network", "ls", "--format", "{{.ID}}").stdout.split()
    if ids:
        networks = json.loads(docker(context, "network", "inspect", *ids).stdout)
        check_network_conflicts(networks)
    compose(context, "up", "-d", "--pull", "never", timeout=180, capture=False)
    return {
        "started": True,
        "project": PROJECT,
        "prerequisites": prerequisites,
        "gateway_url": "http://127.0.0.1:8088",
        "opcua_endpoint": "opc.tcp://127.0.0.1:62541",
        "mqtt_broker": "127.0.0.1:1883",
    }


def _mqtt_roundtrip_check(host: str = "127.0.0.1", port: int = 1883) -> bool:
    def enc_len(n: int) -> bytes:
        out = bytearray()
        while True:
            b = n % 128
            n //= 128
            if n > 0:
                b |= 128
            out.append(b)
            if not n:
                return bytes(out)

    topic = "spBv1.0/LabPlant/DDATA/IgnitionEdge01/Verify01"
    payload = b'{"metric":"SuctionPressure","value":1500.0,"unit":"kPag"}'
    cid = b"eigenscope-ignition-verify"
    conn_body = struct.pack("!H4sBBH", 4, b"MQTT", 4, 0x02, 10) + struct.pack("!H", len(cid)) + cid
    conn_pkt = b"\x10" + enc_len(len(conn_body)) + conn_body
    t_bytes = topic.encode("utf-8")
    sub_body = struct.pack("!HH", 1, len(t_bytes)) + t_bytes + b"\x00"
    sub_pkt = b"\x82" + enc_len(len(sub_body)) + sub_body
    pub_body = struct.pack("!H", len(t_bytes)) + t_bytes + payload
    pub_pkt = b"\x30" + enc_len(len(pub_body)) + pub_body

    with socket.create_connection((host, port), timeout=4.0) as sock:
        sock.sendall(conn_pkt)
        if sock.recv(4)[:2] != b"\x20\x02":
            return False
        sock.sendall(sub_pkt)
        if not sock.recv(5).startswith(b"\x90"):
            return False
        sock.sendall(pub_pkt)
        received = sock.recv(256)
        sock.sendall(b"\xe0\x00")
        return topic.encode("utf-8") in received and payload in received


def verify(timeout: int = 120) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    gateway_running = False
    status_payload: dict[str, Any] = {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        try:
            with opener.open("http://127.0.0.1:8088/StatusPing", timeout=3.0) as resp:
                if resp.status == 200:
                    status_payload = json.loads(resp.read().decode("utf-8"))
                    if status_payload.get("state") == "RUNNING":
                        gateway_running = True
                        break
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(2.0)

    opcua_tcp_open = False
    try:
        with socket.create_connection(("127.0.0.1", 62541), timeout=3.0):
            opcua_tcp_open = True
    except OSError:
        opcua_tcp_open = False

    mqtt_ok = False
    try:
        mqtt_ok = _mqtt_roundtrip_check("127.0.0.1", 1883)
    except OSError:
        mqtt_ok = False

    checks = {
        "ignition_gateway_running": gateway_running,
        "ignition_opcua_port_listening": opcua_tcp_open,
        "mosquitto_sparkplug_mqtt_roundtrip": mqtt_ok,
    }
    return {
        "tier": "actual-ignition-mosquitto",
        "project": PROJECT,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "gateway_status": status_payload,
        "images": LOCK["images"],
        "scope": "Isolated Ignition 8.1 trial gateway + Mosquitto MQTT broker bound strictly to host 127.0.0.1.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["doctor", "prepare", "pull", "up", "verify", "down"])
    parser.add_argument("--context", help="Explicit Docker context, e.g. colima")
    parser.add_argument(
        "--accept-ignition-eula",
        action="store_true",
        help="Explicitly accept Inductive Automation's EULA for the Ignition container",
    )
    parser.add_argument(
        "--allow-unsigned-modules",
        action="store_true",
        help="Enable -Dignition.allowunsignedmodules=true for local dev .modl testing",
    )
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            result = doctor(args.context)
        elif args.command == "prepare":
            result = prepare(
                args.context,
                accept_eula=args.accept_ignition_eula,
                allow_unsigned_modules=args.allow_unsigned_modules,
            )
        elif args.command == "pull":
            for image in LOCK["images"].values():
                docker(args.context, "pull", image, timeout=900, capture=False)
            result = {"pulled": LOCK["images"]}
        elif args.command == "up":
            result = up(
                args.context,
                accept_eula=args.accept_ignition_eula,
                allow_unsigned_modules=args.allow_unsigned_modules,
            )
        elif args.command == "verify":
            result = verify()
        else:
            if not COMPOSE.exists():
                raise RuntimeError("No lab Ignition compose file exists; nothing was removed")
            compose(
                args.context, "down", "--volumes", "--remove-orphans", timeout=120, capture=False
            )
            result = {"removed_only_project": PROJECT}
        print(json.dumps(result, indent=2))
        return 1 if result.get("status") == "FAIL" else 0
    except (subprocess.SubprocessError, RuntimeError, OSError, ValueError) as exc:
        print(
            json.dumps({"status": "ERROR", "operation": args.command, "message": str(exc)}),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
