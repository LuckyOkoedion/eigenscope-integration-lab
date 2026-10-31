"""Local live simulator dashboard with bounded state and server-sent events."""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import socketserver
import threading
import time
from collections import deque
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

SCENARIOS = ("robotics", "telecom", "provider", "tools", "coding")
ASSET_ROOT = Path(__file__).parent / "assets" / "web"
LOGGER = logging.getLogger(__name__)


class RunCancelled(Exception):
    """Internal checkpoint signal; simulation contexts retain their cleanup."""


def _runner_for(scenario: str) -> Callable[..., dict[str, Any]]:
    # Import only the selected extra, so a coding-only installation stays useful.
    module = importlib.import_module(f"eigenscope_lab.simulators.{scenario}")
    return getattr(module, f"run_{scenario}")


class LabSession:
    """One active simulation; bounded replay and history, containing no credentials."""

    def __init__(self, runners: dict[str, Callable[..., dict[str, Any]]] | None = None) -> None:
        self._runners = runners
        self.condition = threading.Condition(threading.RLock())
        self.events: deque[dict[str, Any]] = deque(maxlen=512)
        self.history: deque[dict[str, Any]] = deque(maxlen=20)
        self.sequence = 0
        self.run_number = 0
        self.stop = threading.Event()
        self.closed = threading.Event()
        self.worker: threading.Thread | None = None
        self.state: dict[str, Any] = {
            "status": "idle",
            "scenario": None,
            "run_id": 0,
            "frame": None,
            "result": None,
            "frames_received": 0,
        }

    def snapshot(self) -> dict[str, Any]:
        with self.condition:
            return {**self.state, "history": list(self.history), "event_id": self.sequence}

    def _event(self, kind: str, data: dict[str, Any]) -> None:
        self.sequence += 1
        self.events.append({"id": self.sequence, "kind": kind, "data": data})
        self.condition.notify_all()

    def start(self, scenario: str, seed: int, pace_ms: int) -> None:
        if scenario not in SCENARIOS:
            raise ValueError("Choose a supported scenario")
        if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
            raise ValueError("Seed must be an integer from 0 to 4294967295")
        if type(pace_ms) is not int or not 0 <= pace_ms <= 100:
            raise ValueError("Frame pacing must be an integer from 0 to 100 ms")
        with self.condition:
            if self.closed.is_set():
                raise RuntimeError("The lab server is closing")
            if self.worker is not None and self.worker.is_alive():
                raise RuntimeError("A simulation is already running")
            self.stop.clear()
            self.run_number += 1
            self.state = {
                "status": "running",
                "scenario": scenario,
                "run_id": self.run_number,
                "seed": seed,
                "pace_ms": pace_ms,
                "frame": None,
                "result": None,
                "frames_received": 0,
                "started_unix": time.time(),
            }
            self._event("state", self.snapshot())
            self.worker = threading.Thread(
                target=self._run, args=(scenario, seed, pace_ms), daemon=True
            )
            self.worker.start()

    def _run(self, scenario: str, seed: int, pace_ms: int) -> None:
        start = time.perf_counter()

        def on_frame(frame: dict[str, Any]) -> None:
            if self.stop.is_set() or self.closed.is_set():
                raise RunCancelled
            # Copy finite JSON only; worker objects are never retained in a frame.
            payload = json.loads(json.dumps(frame, allow_nan=False))
            with self.condition:
                self.state["frame"] = payload
                self.state["frames_received"] += 1
                self._event(
                    "frame",
                    {"run_id": self.state["run_id"], "scenario": scenario, "frame": payload},
                )
            if self.stop.wait(pace_ms / 1000):
                raise RunCancelled

        result = None
        error = None
        try:
            runner = self._runners[scenario] if self._runners else _runner_for(scenario)
            arguments: dict[str, Any] = {"seed": seed}
            if "on_frame" in inspect.signature(runner).parameters:
                arguments["on_frame"] = on_frame
            result = runner(**arguments)
            checks = result.get("checks", [])
            passed = bool(checks) and all(check.get("passed") is True for check in checks)
            status = "cancelled" if self.stop.is_set() else "completed" if passed else "failed"
            if status == "failed":
                error = "Some scenario checks did not pass. Review the contract checks and download the run report for details."
        except RunCancelled:
            status = "cancelled"
        except Exception:
            status = "cancelled" if self.stop.is_set() else "failed"
            if status == "failed":
                error = "This scenario could not finish. Check the terminal diagnostics and installed scenario dependencies, then try again."
                LOGGER.exception("Local scenario %s failed", scenario)
        with self.condition:
            if result is not None:
                result = {key: value for key, value in result.items() if key != "telemetry"}
            self.state.update(
                status=status,
                result=result,
                error=error,
                wall_ms=round((time.perf_counter() - start) * 1000, 3),
            )
            self.history.appendleft(
                {
                    "run_id": self.state["run_id"],
                    "scenario": scenario,
                    "status": status,
                    "seed": seed,
                    "wall_ms": self.state["wall_ms"],
                }
            )
            self._event("state", self.snapshot())

    def cancel(self) -> None:
        with self.condition:
            if self.state["status"] in {"running", "cancelling"}:
                self.stop.set()
                self.state["status"] = "cancelling"
                self._event("state", self.snapshot())

    def reset(self) -> None:
        with self.condition:
            if self.worker and self.worker.is_alive():
                raise RuntimeError("Stop the active simulation before resetting the view")
            self.state = {
                "status": "idle",
                "scenario": None,
                "run_id": self.run_number,
                "frame": None,
                "result": None,
                "frames_received": 0,
            }
            self._event("state", self.snapshot())

    def close(self) -> None:
        self.closed.set()
        self.stop.set()
        with self.condition:
            self.condition.notify_all()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=3)


class LabHTTPServer(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # Fixed numeric loopback/container binds need no reverse-DNS lookup.
        # HTTPServer's getfqdn() can otherwise block on macOS mDNS resolution.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]

    daemon_threads = True

    def __init__(self, host: str, port: int, session: LabSession) -> None:
        self.session = session
        self.stream_slots = threading.BoundedSemaphore(4)
        super().__init__((host, port), LabHandler)

    def server_close(self) -> None:
        self.session.close()
        super().server_close()


class LabHandler(BaseHTTPRequestHandler):
    """Fixed local routes only; no file browsing, live API or command execution."""

    server: LabHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: Any) -> None:
        return

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def _trusted_request(self, *, mutate: bool = False) -> bool:
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        origin = self.headers.get("Origin")
        valid = host in allowed and (not mutate or origin is None or origin == f"http://{host}")
        if not valid:
            self._json(403, {"error": "Open the lab using its local browser address."})
        return valid

    def _send(self, status: int, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'",
        )
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(content)

    def _json(self, status: int, value: dict[str, Any]) -> None:
        self._send(
            status, json.dumps(value, allow_nan=False).encode(), "application/json; charset=utf-8"
        )

    def do_GET(self) -> None:  # noqa: N802
        if not self._trusted_request():
            return
        route = urlsplit(self.path)
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
        }
        if route.path in assets:
            name, content_type = assets[route.path]
            self._send(200, (ASSET_ROOT / name).read_bytes(), content_type)
        elif route.path == "/api/state":
            self._json(200, self.server.session.snapshot())
        elif route.path == "/api/events":
            self._stream(route.query)
        else:
            self._json(404, {"error": "That lab page does not exist."})

    def _stream(self, query: str) -> None:
        try:
            cursor = int(parse_qs(query).get("after", [self.headers.get("Last-Event-ID", "0")])[0])
            if cursor < 0:
                raise ValueError
        except ValueError:
            self._json(400, {"error": "Invalid event cursor."})
            return
        if not self.server.stream_slots.acquire(blocking=False):
            self._json(429, {"error": "Close an extra lab tab and reconnect."})
            return
        session = self.server.session
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            # A fresh server resets sequence numbers; an old browser cursor must
            # not suppress future updates after a reconnect.
            cursor = min(cursor, session.sequence)
            self.wfile.write(
                (
                    f"id: {session.sequence}\nevent: snapshot\ndata: "
                    + json.dumps(session.snapshot(), allow_nan=False)
                    + "\n\n"
                ).encode()
            )
            self.wfile.flush()
            while not session.closed.is_set():
                with session.condition:
                    session.condition.wait(timeout=1)
                    replay = [event for event in session.events if event["id"] > cursor]
                    if session.events and cursor < session.events[0]["id"] - 1:
                        replay = [
                            {"id": session.sequence, "kind": "snapshot", "data": session.snapshot()}
                        ]
                for event in replay:
                    payload = f"id: {event['id']}\nevent: {event['kind']}\ndata: {json.dumps(event['data'], allow_nan=False)}\n\n"
                    self.wfile.write(payload.encode())
                    cursor = event["id"]
                if not replay:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass
        finally:
            self.server.stream_slots.release()

    def do_POST(self) -> None:  # noqa: N802
        if not self._trusted_request(mutate=True):
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if (
                not 0 < length <= 4096
                or self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json"
            ):
                raise ValueError
            body = json.loads(
                self.rfile.read(length),
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError, TimeoutError):
            self._json(400, {"error": "Send a small JSON object using the lab controls."})
            return
        try:
            if self.path == "/api/run":
                if set(body) - {"scenario", "seed", "pace_ms"}:
                    raise ValueError("Use only the scenario, seed and frame-pacing controls")
                self.server.session.start(
                    body.get("scenario"), body.get("seed", 7), body.get("pace_ms", 15)
                )
            elif self.path == "/api/cancel" and not body:
                self.server.session.cancel()
            elif self.path == "/api/reset" and not body:
                self.server.session.reset()
            else:
                self._json(404, {"error": "That lab action does not exist."})
                return
            self._json(200, self.server.session.snapshot())
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except RuntimeError as exc:
            self._json(409, {"error": str(exc)})


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    allow_container: bool = False,
    session: LabSession | None = None,
) -> LabHTTPServer:
    if type(allow_container) is not bool:
        raise ValueError("Container binding must be explicitly enabled with a boolean")
    allowed_hosts = {"127.0.0.1", "localhost"}
    if allow_container:
        allowed_hosts.add("0.0.0.0")
    if host not in allowed_hosts:
        raise ValueError("Use loopback, or explicitly enable container binding for 0.0.0.0")
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("Port must be an integer from 0 to 65535")
    bind_host = "0.0.0.0" if host == "0.0.0.0" else "127.0.0.1"
    return LabHTTPServer(bind_host, port, session or LabSession())


def serve(host: str = "127.0.0.1", port: int = 8765, *, allow_container: bool = False) -> None:
    """Serve the local dashboard until interrupted; no browser is opened silently."""
    with create_server(host, port, allow_container=allow_container) as server:
        print(f"EigenScope Integration Lab: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            print("\nStopping the local lab.", flush=True)
