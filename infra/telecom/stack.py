#!/usr/bin/env python3
"""Own-project-only Open5GS/UERANSIM launcher using immutable upstream images.

No host firewall/sysctl changes, public ports, production systems or RF devices.
All generated state is under .lab/telecom; source configuration licenses remain
in the pinned upstream checkout. Docker's active context is never changed.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".lab" / "telecom"
UPSTREAM = STATE / "upstream"
LOCK = json.loads(Path(__file__).with_name("upstream.lock.json").read_text())
PROJECT = "eigenscope-lab-5gc"
COMPOSE = STATE / "compose.json"
IMSI = "001010000000001"
# Publicly reproducible test SIM material, used only inside the isolated lab.
TEST_KI = hashlib.sha256(b"eigenscope-public-lab-test-sim-ki").hexdigest()[:32]
TEST_OPC = hashlib.sha256(b"eigenscope-public-lab-test-sim-opc").hexdigest()[:32]
CORE_SERVICES = [
    "mongo",
    "nrf",
    "scp",
    "ausf",
    "udr",
    "udm",
    "smf",
    "upf",
    "amf",
    "pcf",
    "bsf",
    "nssf",
]


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
        "kernel_version": info.get("KernelVersion", "unknown"),
        "locked_mongodb_version": LOCK["database_baseline"]["version"],
        "cpus": info["NCPU"],
        "memory_gib": round(info["MemTotal"] / 2**30, 2),
        "compose": docker(context, "compose", "version", "--short").stdout.strip(),
        "context": context or "current (unchanged)",
    }
    if info["OSType"] != "linux" or info["Architecture"] not in ("x86_64", "amd64"):
        raise RuntimeError(
            "Pinned 5GC images require a Linux/amd64 Docker daemon; use the portable SimPy tier on other platforms"
        )
    if info["MemTotal"] < 3 * 2**30:
        raise RuntimeError(
            "The 5GC tier requires at least 3GiB Docker memory; 4GiB+ is recommended"
        )
    return result


def selected_images() -> dict[str, str]:
    images = dict(LOCK["images"])
    built = STATE / "ueransim-source-build.json"
    if built.exists():
        metadata = json.loads(built.read_text())
        if metadata["source_commit"] != LOCK["ueransim_source_build"]["commit"]:
            raise RuntimeError("Local UERANSIM source build does not match the lock; rebuild it")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", metadata["image_id"]):
            raise RuntimeError("Local UERANSIM build has no immutable Docker image ID")
        images["ueransim"] = metadata["image_id"]
    return images


def build_ueransim(context: str | None) -> dict[str, Any]:
    doctor(context)
    STATE.mkdir(parents=True, exist_ok=True)
    recipe = Path(__file__).with_name("Dockerfile.ueransim")
    tag = "eigenscope-lab-ueransim:3.3.0-6bf5a1a"
    docker(
        context,
        "build",
        "--platform",
        LOCK["platform"],
        "--file",
        str(recipe),
        "--tag",
        tag,
        str(recipe.parent),
        timeout=1800,
        capture=False,
    )
    image_id = docker(context, "image", "inspect", tag, "--format", "{{.Id}}").stdout.strip()
    metadata = {
        "source_commit": LOCK["ueransim_source_build"]["commit"],
        "image_id": image_id,
        "version": LOCK["ueransim_source_build"]["version"],
        "base_image": LOCK["ueransim_source_build"]["base_image"],
    }
    (STATE / "ueransim-source-build.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def prepare(context: str | None) -> dict[str, Any]:
    STATE.mkdir(parents=True, exist_ok=True)
    if not UPSTREAM.exists():
        execute(["git", "clone", "--no-checkout", LOCK["repository"], str(UPSTREAM)], timeout=180)
    if (
        execute(["git", "remote", "get-url", "origin"], cwd=UPSTREAM).stdout.strip()
        != LOCK["repository"]
    ):
        raise RuntimeError("Existing checkout origin does not match the locked upstream")
    execute(["git", "checkout", "--detach", LOCK["commit"]], cwd=UPSTREAM)
    actual = execute(["git", "rev-parse", "HEAD"], cwd=UPSTREAM).stdout.strip()
    if actual != LOCK["commit"]:
        raise RuntimeError("Pinned configuration commit mismatch")
    # All values are synthetic. Keep the data plane inside this Docker network.
    env = (UPSTREAM / ".env").read_text()
    env = env.replace("172.22.0.", "10.231.0.")
    changes = {
        "DOCKER_HOST_IP": "127.0.0.1",
        "UE1_IMSI": IMSI,
        "UE1_KI": TEST_KI,
        "UE1_OP": TEST_OPC,
        "UE_IPV4_INTERNET": "10.251.0.0/24",
        "UE_IPV4_IMS": "10.252.0.0/24",
        "SMF_DNS1": "10.231.0.8",
        "SMF_DNS2": "10.231.0.8",
        "MAX_NUM_UE": "16",
    }
    for name, value in changes.items():
        env = re.sub(rf"^{name}=.*$", f"{name}={value}", env, flags=re.M)
    (UPSTREAM / ".env").write_text(env)
    # OPC is explicitly provisioned in the synthetic subscriber database.
    ue_config = UPSTREAM / "ueransim" / "ueransim-ue.yaml"
    ue_config.write_text(ue_config.read_text().replace("opType: 'OP'", "opType: 'OPC'"))
    documents = []
    for filename in ("sa-deploy.yaml", "nr-gnb.yaml", "nr-ue.yaml"):
        rendered = docker(
            context,
            "compose",
            "--env-file",
            str(UPSTREAM / ".env"),
            "-f",
            str(UPSTREAM / filename),
            "config",
            "--format",
            "json",
        )
        documents.append(json.loads(rendered.stdout))
    images = selected_images()
    services = {}
    for document in documents:
        services.update(document["services"])
    keep = CORE_SERVICES + ["nr_gnb", "nr_ue"]
    selected = {name: services[name] for name in keep}
    for name, service in selected.items():
        service["container_name"] = f"egslab-{name.replace('_', '-')}"
        service["labels"] = {"org.eigenscope.lab": "telecom"}
        service.pop("build", None)
        service.pop("ports", None)
        if service["image"] == "docker_open5gs":
            service["image"] = images["open5gs"]
        elif service["image"] == "docker_ueransim":
            service["image"] = images["ueransim"]
            if service["image"].startswith("sha256:"):
                service["pull_policy"] = "never"
        elif name == "mongo":
            service["image"] = images["mongo"]
            service["command"] = ["mongod", "--bind_ip_all", "--wiredTigerCacheSizeGB", "0.25"]
            service["healthcheck"] = {
                "test": [
                    "CMD",
                    "mongosh",
                    "--quiet",
                    "--eval",
                    "quit(db.runCommand({ping:1}).ok ? 0 : 2)",
                ],
                "interval": "2s",
                "timeout": "4s",
                "start_period": "5s",
                "retries": 25,
            }
        service["platform"] = LOCK["platform"]
        # Only the lab-scoped named volume can be removed by `down --volumes`.
        for mount in service.get("volumes", []):
            if isinstance(mount, dict) and mount.get("type") == "volume":
                mount["source"] = "mongodbdata"
        service["restart"] = "no"
        service["mem_limit"] = "512m" if name == "mongo" else "256m"
    network = documents[0]["networks"]["default"]
    network.pop("external", None)
    network["name"] = f"{PROJECT}-isolated"
    network["internal"] = True
    document = {
        "name": PROJECT,
        "services": selected,
        "networks": {"default": network},
        "volumes": {"mongodbdata": {"name": f"{PROJECT}-mongodbdata"}},
    }
    COMPOSE.write_text(json.dumps(document, indent=2) + "\n")
    metadata = {
        "configuration_commit": actual,
        "images": images,
        "synthetic_imsi": IMSI,
        "network": "10.231.0.0/24",
        "published_ports": [],
        "upstream_config_changes": [
            "isolated internal network",
            "test-only subscriber/OPC",
            "lab names/volume",
            "pinned images",
            "remove dashboard/webUI",
            "bounded memory",
        ],
    }
    (STATE / "prepared.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def provision_subscriber(context: str | None) -> None:
    ambr = {"uplink": {"value": 1, "unit": 3}, "downlink": {"value": 1, "unit": 3}}
    document = {
        "schema_version": 1,
        "imsi": IMSI,
        "msisdn": [],
        "imeisv": [],
        "mme_host": [],
        "mm_realm": [],
        "purge_flag": [],
        "security": {"k": TEST_KI, "op": None, "opc": TEST_OPC, "amf": "8000"},
        "ambr": ambr,
        "slice": [
            {
                "sst": 1,
                "default_indicator": True,
                "session": [
                    {
                        "name": "internet",
                        "type": 1,
                        "pcc_rule": [],
                        "ambr": ambr,
                        "qos": {
                            "index": 9,
                            "arp": {
                                "priority_level": 8,
                                "pre_emption_capability": 1,
                                "pre_emption_vulnerability": 2,
                            },
                        },
                    }
                ],
            }
        ],
        "access_restriction_data": 32,
        "subscriber_status": 0,
        "operator_determined_barring": 0,
        "network_access_mode": 0,
        "subscribed_rau_tau_timer": 12,
        "__v": 0,
    }

    def extended_json(value: Any) -> Any:
        if type(value) is int:
            return {"$numberInt": str(value)}
        if isinstance(value, dict):
            return {key: extended_json(item) for key, item in value.items()}
        if isinstance(value, list):
            return [extended_json(item) for item in value]
        return value

    expression = (
        "db=db.getSiblingDB('open5gs');db.subscribers.replaceOne({imsi:"
        + json.dumps(IMSI)
        + "},"
        + "EJSON.parse("
        + json.dumps(json.dumps(extended_json(document)))
        + ")"
        + ",{upsert:true});"
    )
    deadline = time.monotonic() + 45
    last_failure = "database has not accepted the synthetic subscriber"
    while True:
        try:
            result = compose(
                context,
                "exec",
                "-T",
                "mongo",
                "mongosh",
                "--quiet",
                "--eval",
                expression,
                check=False,
                timeout=8,
            )
        except subprocess.TimeoutExpired:
            last_failure = "database provisioning command timed out"
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Synthetic subscriber database did not become ready: " + last_failure
                ) from None
            continue
        if result.returncode == 0:
            return
        last_failure = (result.stderr or result.stdout or "database provisioning failed").strip()
        for test_value in (TEST_KI, TEST_OPC):
            last_failure = last_failure.replace(test_value, "[TEST_SIM_MATERIAL]")
        last_failure = last_failure[:400]
        if time.monotonic() > deadline:
            raise RuntimeError(
                "Synthetic subscriber database did not become ready: " + last_failure
            )
        time.sleep(1)


def check_network_conflicts(networks: list[dict[str, Any]]) -> None:
    for net in networks:
        if net["Name"] == f"{PROJECT}-isolated":
            continue
        for config in (net.get("IPAM") or {}).get("Config") or []:
            subnet = config.get("Subnet")
            if (
                subnet
                and ipaddress.ip_network(subnet).version == 4
                and ipaddress.ip_network(subnet).overlaps(ipaddress.ip_network("10.231.0.0/24"))
            ):
                raise RuntimeError(
                    "Lab subnet conflicts with an existing Docker network; no containers were started"
                )


def up(context: str | None) -> dict[str, Any]:
    prerequisites = doctor(context)
    prepare(context)
    for image in selected_images().values():
        ready = docker(context, "image", "inspect", image, check=False)
        if ready.returncode:
            raise RuntimeError(
                "A required immutable image is not ready. Complete the pull command (or build-ueransim for a source image) before up. No containers were started."
            )
    # Verify no unrelated Docker network overlaps our fixed synthetic subnet.
    ids = docker(context, "network", "ls", "--format", "{{.ID}}").stdout.split()
    if ids:
        networks = json.loads(docker(context, "network", "inspect", *ids).stdout)
        check_network_conflicts(networks)
    # Prove database readiness before starting dependent network functions.
    compose(
        context,
        "up",
        "-d",
        "--pull",
        "never",
        "--wait",
        "--wait-timeout",
        "60",
        "mongo",
        timeout=90,
        capture=False,
    )
    provision_subscriber(context)
    compose(context, "up", "-d", "--pull", "never", *CORE_SERVICES, timeout=300, capture=False)
    compose(context, "up", "-d", "--pull", "never", "nr_gnb", "nr_ue", timeout=120, capture=False)
    return {"started": True, "prerequisites": prerequisites, "project": PROJECT}


def verify(context: str | None, timeout: int = 90) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    cli = ""
    ip_info: list[Any] = []
    while time.monotonic() < deadline:
        status = compose(
            context,
            "exec",
            "-T",
            "nr_ue",
            "./nr-cli",
            f"imsi-{IMSI}",
            "--exec",
            "status",
            check=False,
        )
        cli = status.stdout
        addresses = compose(
            context, "exec", "-T", "nr_ue", "ip", "-j", "address", "show", check=False
        )
        try:
            ip_info = json.loads(addresses.stdout)
        except json.JSONDecodeError:
            ip_info = []
        tunnel = any(
            row.get("ifname", "").startswith("uesimtun") and row.get("addr_info") for row in ip_info
        )
        registered = status.returncode == 0 and bool(
            re.search(r"^rm-state:\s*RM-REGISTERED\s*$", cli, re.M)
        )
        if registered and tunnel:
            break
        time.sleep(2)
    ping = compose(
        context,
        "exec",
        "-T",
        "nr_ue",
        "ping",
        "-I",
        "uesimtun0",
        "-c",
        "3",
        "-W",
        "2",
        "10.251.0.1",
        check=False,
    )
    ueransim_version = compose(context, "exec", "-T", "nr_ue", "./nr-ue", "--version", check=False)
    open5gs_version = compose(
        context, "exec", "-T", "amf", "/open5gs/install/bin/open5gs-amfd", "-v", check=False
    )
    mongo_version = compose(
        context, "exec", "-T", "mongo", "mongod", "--version", check=False, timeout=8
    )
    checks = {
        "ue_status_command_succeeded": status.returncode == 0,
        "ue_registered": registered,
        "ue_tunnel_address_assigned": any(
            row.get("ifname", "").startswith("uesimtun") and row.get("addr_info") for row in ip_info
        ),
        "user_plane_ping_via_tunnel": ping.returncode == 0,
        "user_plane_zero_packet_loss": bool(
            re.search(r"(?:^|[ ,])0% packet loss", ping.stdout, re.M)
        ),
    }
    result = {
        "tier": "actual-open5gs-ueransim",
        "project": PROJECT,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "images": selected_images(),
        "configuration_commit": LOCK["commit"],
        "versions": {
            "ueransim": (ueransim_version.stdout + ueransim_version.stderr).strip(),
            "open5gs": (open5gs_version.stdout + open5gs_version.stderr).strip(),
            "mongodb": (mongo_version.stdout + mongo_version.stderr).strip(),
        },
        "ue_status": cli,
        "ue_status_returncode": status.returncode,
        "ue_status_stderr": status.stderr,
        "tunnel_addresses": [
            row for row in ip_info if row.get("ifname", "").startswith("uesimtun")
        ],
        "ping": ping.stdout + ping.stderr,
        "scope": "Actual NGAP/NAS registration and GTP-U tunnel traffic in an internal Docker network. UERANSIM uses a simulated radio interface; no RF, E2AP/RIC or commercial subscriber is exercised.",
    }
    (STATE / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["doctor", "build-ueransim", "prepare", "pull", "up", "verify", "down"]
    )
    parser.add_argument(
        "--context", help="Explicit Docker context, e.g. colima; does not change the active context"
    )
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            result = doctor(args.context)
        elif args.command == "build-ueransim":
            result = build_ueransim(args.context)
        elif args.command == "prepare":
            result = prepare(args.context)
        elif args.command == "pull":
            for image in selected_images().values():
                if not image.startswith("sha256:"):
                    docker(args.context, "pull", image, timeout=900, capture=False)
            result = {"pulled_or_locally_built": selected_images()}
        elif args.command == "up":
            result = up(args.context)
        elif args.command == "verify":
            result = verify(args.context)
        else:
            if not COMPOSE.exists():
                raise RuntimeError("No lab compose file exists; nothing was removed")
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
