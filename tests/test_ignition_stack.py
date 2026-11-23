"""Unit controls for the optional Ignition 8.1 + Mosquitto launcher without Docker."""

from __future__ import annotations

import json
import os
import runpy
import socket
import stat
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def ignition_helpers():
    return runpy.run_path(str(Path(__file__).parents[1] / "infra" / "ignition" / "stack.py"))


def test_prepare_requires_explicit_eula_acceptance(ignition_helpers, tmp_path, monkeypatch):
    prepare = ignition_helpers["prepare"]
    monkeypatch.setitem(prepare.__globals__, "STATE", tmp_path)
    monkeypatch.setitem(prepare.__globals__, "COMPOSE", tmp_path / "compose.json")
    monkeypatch.setitem(prepare.__globals__, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setitem(prepare.__globals__, "PASS_FILE", tmp_path / ".admin-password")
    monkeypatch.setitem(prepare.__globals__, "MOSQUITTO_CONF", tmp_path / "mosquitto.conf")

    with pytest.raises(RuntimeError, match="--accept-ignition-eula"):
        prepare(None, accept_eula=False)

    res = prepare(None, accept_eula=True, allow_unsigned_modules=True)
    assert res["prepared"] is True
    assert res["loopback_ports"] == ["127.0.0.1:8088", "127.0.0.1:62541", "127.0.0.1:1883"]
    compose = json.loads((tmp_path / "compose.json").read_text())
    ports = compose["services"]["ignition"]["ports"] + compose["services"]["mosquitto"]["ports"]
    assert all(p.startswith("127.0.0.1:") for p in ports)
    assert "-Dignition.allowunsignedmodules=true" in compose["services"]["ignition"]["command"]

    pw = (tmp_path / ".admin-password").read_text().strip()
    assert pw and pw not in json.dumps(res) and pw not in (tmp_path / "compose.json").read_text()
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / ".admin-password").stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600


def test_subnet_conflict_detection(ignition_helpers):
    check = ignition_helpers["check_network_conflicts"]
    check([{"Name": "bridge", "IPAM": {"Config": [{"Subnet": "172.17.0.0/16"}]}}])
    check(
        [
            {
                "Name": "eigenscope-lab-ignition-isolated",
                "IPAM": {"Config": [{"Subnet": "10.232.0.0/24"}]},
            }
        ]
    )
    with pytest.raises(RuntimeError, match="conflicts"):
        check([{"Name": "other", "IPAM": {"Config": [{"Subnet": "10.232.0.0/24"}]}}])


def test_up_fails_closed_if_pinned_image_missing(ignition_helpers, tmp_path, monkeypatch):
    up = ignition_helpers["up"]
    scope = up.__globals__
    monkeypatch.setitem(scope, "STATE", tmp_path)
    monkeypatch.setitem(scope, "COMPOSE", tmp_path / "compose.json")
    monkeypatch.setitem(scope, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setitem(scope, "PASS_FILE", tmp_path / ".admin-password")
    monkeypatch.setitem(scope, "MOSQUITTO_CONF", tmp_path / "mosquitto.conf")
    monkeypatch.setitem(scope, "doctor", lambda _ctx: {"os": "linux", "architecture": "amd64"})
    monkeypatch.setitem(
        scope,
        "docker",
        lambda _ctx, *args, **_k: subprocess.CompletedProcess(
            args, 1 if args[:2] == ("image", "inspect") else 0, stdout="", stderr=""
        ),
    )
    with pytest.raises(RuntimeError, match="pull"):
        up(None, accept_eula=True)


def test_verify_reports_pass_when_gateway_opcua_and_mqtt_succeed(ignition_helpers, monkeypatch):
    verify = ignition_helpers["verify"]
    scope = verify.__globals__

    class DummyCM:
        def __init__(self, *, status=200, data=b'{"state":"RUNNING"}'):
            self.status = status
            self._data = data

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    class DummyOpener:
        def open(self, _url, timeout=3.0):
            return DummyCM()

    monkeypatch.setattr(scope["urllib"].request, "build_opener", lambda *_a: DummyOpener())
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: DummyCM())
    monkeypatch.setitem(scope, "_mqtt_roundtrip_check", lambda *_a: True)
    res = verify(timeout=5)
    assert res["status"] == "PASS"
    assert all(res["checks"].values())
