"""Local dashboard transport, cancellation, replay bounds and input isolation."""

import json
import threading
import time
from contextlib import contextmanager

import pytest

from eigenscope_lab.web import LabSession, create_server


def fake_run(seed=7, on_frame=None):
    for index in range(4):
        if on_frame:
            on_frame({"suite": "robotics", "phase": "test", "t": index / 10, "x": index, "y": 0})
    return {
        "suite": "robotics",
        "checks": [{"name": "test", "passed": True}],
        "metrics": {"seed": seed},
        "telemetry": [1, 2, 3],
    }


@contextmanager
def local_server(session=None):
    server = create_server(port=0, session=session or LabSession({"robotics": fake_run}))
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def wait_finished(session):
    session.worker.join(timeout=3)
    assert not session.worker.is_alive()


def test_session_actual_frame_delivery_completion_and_reset():
    session = LabSession({"robotics": fake_run})
    session.start("robotics", 11, 0)
    wait_finished(session)
    state = session.snapshot()
    assert state["status"] == "completed"
    assert state["frames_received"] == 4
    assert state["frame"]["x"] == 3
    assert "telemetry" not in state["result"]
    assert len([event for event in session.events if event["kind"] == "frame"]) == 4
    assert all(event["id"] == event["data"]["event_id"] for event in session.events)
    session.reset()
    assert session.snapshot()["status"] == "idle"
    assert session.snapshot()["frame"] is None
    assert len(session.snapshot()["history"]) == 1
    session.close()


@pytest.mark.parametrize("host,container", [("127.0.0.1", False), ("0.0.0.0", True)])
def test_fixed_local_bind_does_not_resolve_a_hostname(monkeypatch, host, container):
    import socket

    def forbidden_reverse_lookup(*_args, **_kwargs):
        pytest.fail("A fixed local bind must not query reverse DNS")

    monkeypatch.setattr(socket, "getfqdn", forbidden_reverse_lookup)
    with create_server(host=host, port=0, allow_container=container) as server:
        assert server.server_name == "localhost"
        assert server.server_port > 0


def test_cancellation_and_concurrent_run_guard():
    def many_frames(seed=7, on_frame=None):
        for index in range(10000):
            on_frame({"index": index})
        return {"checks": []}

    session = LabSession({"robotics": many_frames})
    session.start("robotics", 7, 10)
    with pytest.raises(RuntimeError):
        session.start("robotics", 7, 0)
    with pytest.raises(RuntimeError):
        session.reset()
    session.cancel()
    wait_finished(session)
    assert session.snapshot()["status"] == "cancelled"
    assert session.snapshot()["frames_received"] < 10000
    session.close()


@pytest.mark.parametrize(
    "checks",
    [[], [{"name": "failed contract", "passed": False}], [{"name": "nonboolean", "passed": 1}]],
)
def test_incomplete_or_failed_checks_never_receive_success_status(checks):
    session = LabSession({"robotics": lambda seed=7: {"checks": checks}})
    session.start("robotics", 7, 0)
    wait_finished(session)
    state = session.snapshot()
    assert state["status"] == "failed"
    assert state["result"]["checks"] == checks
    assert "did not pass" in state["error"]
    session.close()


def test_event_and_history_memory_are_bounded():
    def many_frames(seed=7, on_frame=None):
        for index in range(600):
            on_frame({"index": index})
        return {"checks": []}

    session = LabSession({"robotics": many_frames})
    session.start("robotics", 7, 0)
    wait_finished(session)
    assert len(session.events) == 512
    assert session.snapshot()["frames_received"] == 600
    for index in range(22):
        session.start("robotics", index, 0)
        wait_finished(session)
    assert len(session.history) == 20
    session.close()


def test_ui_error_is_friendly_and_details_stay_in_terminal(caplog):
    def broken(seed=7):
        raise ValueError("INTERNAL_ONLY_DIAGNOSTIC")

    session = LabSession({"robotics": broken})
    session.start("robotics", 7, 0)
    wait_finished(session)
    assert session.snapshot()["status"] == "failed"
    assert "INTERNAL_ONLY" not in json.dumps(session.snapshot())
    assert "INTERNAL_ONLY_DIAGNOSTIC" in caplog.text
    session.close()


def test_loopback_routes_reject_invalid_cross_origin_and_credential_fields():
    httpx = pytest.importorskip("httpx")
    with local_server() as (_server, base), httpx.Client(trust_env=False, timeout=2) as client:
        assert client.get(base).status_code == 200
        assert client.get(base + "/app.js").status_code == 200
        assert client.get(base + "/style.css").status_code == 200
        assert client.get(base + "/../../etc/passwd").status_code == 404
        assert (
            client.get(base + "/api/state", headers={"Host": "attacker.invalid"}).status_code == 403
        )
        assert (
            client.post(
                base + "/api/run",
                json={"scenario": "robotics"},
                headers={"Origin": "https://attacker.invalid"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                base + "/api/run", json={"scenario": "robotics", "api_key": "synthetic"}
            ).status_code
            == 400
        )
        for invalid in (True, -1, 1.5, "7"):
            assert (
                client.post(
                    base + "/api/run", json={"scenario": "robotics", "seed": invalid}
                ).status_code
                == 400
            )
        assert (
            client.post(
                base + "/api/run", content=b"x" * 4097, headers={"Content-Type": "application/json"}
            ).status_code
            == 400
        )
        assert client.post(base + "/api/run", json=[]).status_code == 400
        assert client.post(base + "/api/run", json={"scenario": "shell"}).status_code == 400
        response = client.get(base + "/")
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    with pytest.raises(ValueError):
        create_server("0.0.0.0", 8765)


def test_sse_delivers_actual_frames_and_completed_state():
    httpx = pytest.importorskip("httpx")
    with local_server() as (_server, base), httpx.Client(trust_env=False, timeout=3) as client:
        with client.stream("GET", base + "/api/events?after=9999") as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"] == "text/event-stream"
            lines = stream.iter_lines()
            snapshot = []
            while len(snapshot) < 3:
                value = next(lines)
                if value:
                    snapshot.append(value)
            assert snapshot[0] == "id: 0"
            assert snapshot[1] == "event: reset"
            assert json.loads(snapshot[2][6:])["event_id"] == 0
            assert (
                client.post(
                    base + "/api/run", json={"scenario": "robotics", "pace_ms": 0}
                ).status_code
                == 200
            )
            frames = 0
            done = False
            deadline = time.monotonic() + 3
            kind = None
            event_id = None
            while not done and time.monotonic() < deadline:
                value = next(lines)
                if value.startswith("id: "):
                    event_id = int(value[4:])
                if value.startswith("event: "):
                    kind = value[7:]
                if value.startswith("data: "):
                    data = json.loads(value[6:])
                    assert data["event_id"] == event_id
                    frames += kind == "frame"
                    done = kind == "state" and data["status"] == "completed"
            assert done and frames == 4


def test_explicit_container_binding_keeps_loopback_host_and_origin_boundary():
    httpx = pytest.importorskip("httpx")
    with pytest.raises(ValueError):
        create_server("0.0.0.0", 0)
    with pytest.raises(ValueError):
        create_server("0.0.0.0", 0, allow_container="true")
    with pytest.raises(ValueError):
        create_server("192.0.2.1", 0, allow_container=True)
    server = create_server(
        "0.0.0.0", 0, allow_container=True, session=LabSession({"robotics": fake_run})
    )
    assert server.server_address[0] == "0.0.0.0"
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with httpx.Client(trust_env=False, timeout=2) as client:
            assert client.get(base).status_code == 200
            assert (
                client.get(base, headers={"Host": f"0.0.0.0:{server.server_port}"}).status_code
                == 403
            )
            assert (
                client.get(base, headers={"Host": f"192.0.2.1:{server.server_port}"}).status_code
                == 403
            )
            assert (
                client.post(
                    base + "/api/run",
                    json={"scenario": "robotics"},
                    headers={"Origin": f"http://0.0.0.0:{server.server_port}"},
                ).status_code
                == 403
            )
            assert (
                client.post(
                    base + "/api/run",
                    json={"scenario": "robotics", "pace_ms": 0},
                    headers={"Origin": base},
                ).status_code
                == 200
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
