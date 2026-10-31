"""Public SDK transport controls using synthetic responses and loopback HTTP only."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from eigenscope_lab import live, transports
from eigenscope_lab.fixtures import ContractCase
from eigenscope_lab.reporting import outcome
from eigenscope_lab.transports import (
    MAX_RESPONSE_BYTES,
    ObservedTransport,
    ResponseDeadlineError,
    ResponseTooLargeError,
)


@pytest.mark.parametrize("invalid", ["schema", "identity"])
def test_ticket_is_observed_before_sdk_envelope_rejection_and_owned_cleanup(invalid):
    sdk = pytest.importorskip("eigenscope_sdk")
    calls = []

    def fixture(path, headers, wire):
        body = json.loads(wire)
        calls.append((path, body))
        assert headers["Authorization"] == "Bearer synthetic-local-only"
        if path == "/v1/decompose":
            response = {
                "schema_version": 2 if invalid == "schema" else 1,
                "request_id": "invalid-response-identity"
                if invalid == "identity"
                else body["request_id"],
                "vault_ticket": "vlt_synthetic_pre_envelope",
            }
        else:
            assert path == "/v1/vault/scrub"
            response = {
                "schema_version": 1,
                "request_id": body["request_id"],
                "vault_ticket": body["vault_ticket"],
                "vault_ticket_scrubbed": True,
                "status": "VAULT_TICKET_SCRUBBED",
            }
        return 200, json.dumps(response).encode()

    client = live.make_observed_client(
        sdk.EigenScopeClient,
        base_url="https://sdk.example.invalid",
        bearer_token="synthetic-local-only",
        transport=fixture,
    )
    client.owned_tickets.add("vlt_unrelated_prior_synthetic")
    try:
        row = live._sdk_case(
            ContractCase("fixture", "coding", "Fixed synthetic input", (), ("safe",)),
            client=client,
            provider=SimpleNamespace(),
            provider_server=SimpleNamespace(),
            seed=7,
        )
        assert outcome(row) == "FAIL"
        assert [path for path, _ in calls] == ["/v1/decompose", "/v1/vault/scrub"]
        assert calls[-1][1]["vault_ticket"] == "vlt_synthetic_pre_envelope"
        assert client.owned_tickets == {"vlt_unrelated_prior_synthetic"}
        assert next(check for check in row["checks"] if check["name"] == "owned_ticket_cleanup")[
            "passed"
        ]
        assert "vlt_" not in json.dumps(row)
        assert not client._tickets
    finally:
        client.close()


@contextmanager
def local_server(respond):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(
                {"path": self.path, "authorization": self.headers.get("Authorization"), "body": raw}
            )
            try:
                respond(self)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def send_bytes(handler, raw, status=200, headers=None):
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(raw)


def test_cross_origin_redirect_does_not_forward_authorization():
    with local_server(lambda h: send_bytes(h, b"{}")) as (destination, destination_requests):
        with local_server(
            lambda h: send_bytes(h, b"{}", 302, {"Location": destination + "/other"})
        ) as (origin, requests):
            transport = ObservedTransport(base_url=origin, timeout_seconds=1, owned_tickets=set())
            try:
                status, raw = transport(
                    "/v1/decompose", {"Authorization": "Bearer synthetic-local-only"}, b"{}"
                )
                assert status == 302
                assert raw == b"{}"
                assert len(requests) == 1
                assert destination_requests == []
            finally:
                transport.close()


def test_real_http_preserves_sdk_wire_and_observes_response_before_validation(monkeypatch):
    # An unusable environment proxy must not divert this loopback boundary.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    raw_response = b'{"schema_version":2,"vault_ticket":"vlt_http_synthetic"}'
    with local_server(lambda h: send_bytes(h, raw_response)) as (origin, requests):
        owned = set()
        transport = ObservedTransport(base_url=origin, timeout_seconds=1, owned_tickets=owned)
        try:
            wire = b'{"published":"synthetic"}'
            assert transport(
                "/v1/decompose", {"Authorization": "Bearer synthetic-local-only"}, wire
            ) == (200, raw_response)
            assert requests == [
                {
                    "path": "/v1/decompose",
                    "authorization": "Bearer synthetic-local-only",
                    "body": wire,
                }
            ]
            assert owned == {"vlt_http_synthetic"}
        finally:
            transport.close()
        assert transport._http.is_closed


def test_response_limit_is_enforced_for_actual_stream_and_fixture_delegate():
    raw_response = b"x" * (MAX_RESPONSE_BYTES + 1)
    with local_server(lambda h: send_bytes(h, raw_response)) as (origin, _requests):
        transport = ObservedTransport(base_url=origin, timeout_seconds=1, owned_tickets=set())
        try:
            with pytest.raises(ResponseTooLargeError):
                transport("/v1/decompose", {}, b"{}")
            assert transport.owned_tickets == set()
        finally:
            transport.close()
    delegate = ObservedTransport(
        base_url="https://fixture.example.invalid",
        timeout_seconds=1,
        owned_tickets=set(),
        delegate=lambda *_args: (200, raw_response),
    )
    with pytest.raises(ResponseTooLargeError):
        delegate("/v1/decompose", {}, b"{}")
    delegate.close()


def test_timeout_covers_reading_incomplete_response_body():
    httpx = pytest.importorskip("httpx")
    finished = threading.Event()

    def partial(handler):
        handler.send_response(200)
        handler.send_header("Content-Length", "100")
        handler.end_headers()
        handler.wfile.write(b"{")
        handler.wfile.flush()
        finished.wait(timeout=1)

    with local_server(partial) as (origin, _requests):
        transport = ObservedTransport(base_url=origin, timeout_seconds=0.15, owned_tickets=set())
        try:
            with pytest.raises(httpx.ReadTimeout):
                transport("/v1/decompose", {}, b"{}")
            assert transport.owned_tickets == set()
        finally:
            finished.set()
            transport.close()


def test_wrapped_httpx_read_timeout_is_blocked_without_exporting_private_details():
    sdk = pytest.importorskip("eigenscope_sdk")
    httpx = pytest.importorskip("httpx")

    def unavailable(*_args):
        raise httpx.ReadTimeout("SYNTHETIC_PRIVATE_TIMEOUT_DETAIL")

    client = live.make_observed_client(
        sdk.EigenScopeClient,
        base_url="https://fixture.example.invalid",
        bearer_token="synthetic-local-only",
        transport=unavailable,
    )
    try:
        row = live._sdk_case(
            ContractCase("fixture", "coding", "Fixed synthetic input", (), ("safe",)),
            client=client,
            provider=SimpleNamespace(),
            provider_server=SimpleNamespace(),
            seed=7,
        )
        assert outcome(row) == "BLOCKED"
        assert row["checks"][-1]["exception"] == "EigenScopeAuthorityUnavailableError"
        assert "SYNTHETIC_PRIVATE_TIMEOUT_DETAIL" not in json.dumps(row)
        assert client.owned_tickets == set()
    finally:
        client.close()


def test_slow_trickle_uses_total_elapsed_deadline_and_closes_stream(monkeypatch):
    # Model a network response whose tiny chunks each arrive before the socket's
    # read timeout, while their cumulative duration exceeds the request budget.
    # The fake monotonic clock makes this deterministic without real sleeps.
    clock = [100.0]
    received = []
    closed = []
    monkeypatch.setattr(transports.time, "monotonic", lambda: clock[0])

    class Response:
        status_code = 200

        def iter_bytes(self):
            for index in range(100):
                clock[0] += 0.04
                received.append(index)
                yield b"x"

    @contextmanager
    def stream(*args, **kwargs):
        assert args == ("POST", "https://fixture.example.invalid/v1/decompose")
        assert 0 < kwargs["timeout"] <= 0.15
        clock[0] += 0.05  # Connection and response-header time is included.
        try:
            yield Response()
        finally:
            closed.append(True)

    transport = ObservedTransport(
        base_url="https://fixture.example.invalid",
        timeout_seconds=0.15,
        owned_tickets=set(),
        delegate=lambda *_args: (200, b"{}"),
    )
    transport.delegate = None
    transport._http = SimpleNamespace(stream=stream, close=lambda: None)
    try:
        with pytest.raises(ResponseDeadlineError, match="configured deadline") as failure:
            transport("/v1/decompose", {}, b"{}")
        assert isinstance(failure.value, TimeoutError)
        assert len(received) == 3
        assert closed == [True]
        assert transport.owned_tickets == set()
    finally:
        transport.close()


@pytest.mark.parametrize("path,status", [("/v1/rehydrate", 200), ("/v1/decompose", 400)])
def test_only_successful_own_decomposition_can_add_an_owned_handle(path, status):
    transport = ObservedTransport(
        base_url="https://fixture.example.invalid",
        timeout_seconds=1,
        owned_tickets=set(),
        delegate=lambda *_args: (status, b'{"vault_ticket":"vlt_unrelated_synthetic"}'),
    )
    try:
        transport(path, {}, b"{}")
        assert transport.owned_tickets == set()
    finally:
        transport.close()
