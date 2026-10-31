"""Loopback OpenAI-compatible provider for reproducible integration tests.

The real OpenAI client exercises HTTP, JSON and SSE; model output is deterministic.
No request is forwarded to a model service. Captures remain in memory.
"""

from __future__ import annotations

import importlib.metadata
import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class ProviderServer:
    """Bounded, ephemeral loopback provider supporting Chat Completions.

    ``lab-echo`` echoes the final user content byte-for-byte after JSON decoding.
    ``lab-stream`` emits the same text in SSE chunks. Other model names select
    documented faults: ``lab-error``, ``lab-malformed``, ``lab-truncated``,
    ``lab-slow`` and ``lab-tool``. Authorization headers are never captured.
    """

    MODELS = frozenset(
        {
            "lab-echo",
            "lab-stream",
            "lab-error",
            "lab-malformed",
            "lab-truncated",
            "lab-slow",
            "lab-tool",
        }
    )

    def __init__(self, *, chunk_delay: float = 0.002, slow_delay: float = 0.15) -> None:
        if not 0 <= chunk_delay <= 0.1 or not 0 <= slow_delay <= 2:
            raise ValueError("Provider delays must be within documented bounds")
        self.chunk_delay = chunk_delay
        self.slow_delay = slow_delay
        self.requests: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._used = False

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("Start ProviderServer using a with block")
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def __enter__(self) -> ProviderServer:
        if self._used:
            raise RuntimeError("ProviderServer cannot be started twice")
        self._used = True
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                return

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(2)

            def _json(self, status: int, body: dict[str, Any]) -> None:
                data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                if status == 429:
                    self.send_header("Retry-After", "1")
                self.end_headers()
                self.wfile.write(data)
                self.close_connection = True

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/v1/chat/completions":
                    self._json(
                        404,
                        {
                            "error": {
                                "message": "Unknown local endpoint",
                                "type": "invalid_request_error",
                            }
                        },
                    )
                    return
                raw_length = self.headers.get("Content-Length", "")
                try:
                    length = int(raw_length)
                    if not 0 < length <= 65536:
                        raise ValueError
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError
                    model = body["model"]
                    messages = body["messages"]
                    if model not in owner.MODELS or not isinstance(messages, list):
                        raise ValueError
                    if not messages or not all(
                        isinstance(m, dict) and isinstance(m.get("content"), str) for m in messages
                    ):
                        raise ValueError
                    text = next(m["content"] for m in reversed(messages) if m.get("role") == "user")
                    if type(body.get("stream", False)) is not bool:
                        raise ValueError
                    if not isinstance(body.get("stream_options", {}), dict):
                        raise ValueError
                except (ValueError, KeyError, StopIteration, TypeError, UnicodeDecodeError):
                    self._json(
                        400,
                        {
                            "error": {
                                "message": "Expected a supported model and text user message",
                                "type": "invalid_request_error",
                            }
                        },
                    )
                    return
                with owner._lock:
                    sequence = len(owner.requests) + 1
                    owner.requests.append(
                        {"model": model, "messages": messages, "stream": body.get("stream", False)}
                    )
                    event = {
                        "sequence": sequence,
                        "model": model,
                        "chunks_sent": 0,
                        "finished": False,
                        "disconnected": False,
                    }
                    owner.events.append(event)
                try:
                    if model == "lab-error":
                        self._json(
                            429,
                            {
                                "error": {
                                    "message": "Synthetic rate limit",
                                    "type": "rate_limit_error",
                                    "code": "lab_rate_limit",
                                }
                            },
                        )
                        return
                    if model == "lab-slow" and owner._stop.wait(owner.slow_delay):
                        return
                    completion_id = f"chatcmpl-lab-{sequence}"
                    usage = {
                        "prompt_tokens": len(text.encode()),
                        "completion_tokens": len(text.encode()),
                        "total_tokens": 2 * len(text.encode()),
                    }
                    common = {"id": completion_id, "created": 0, "model": model}
                    if not body.get("stream", False):
                        message: dict[str, Any] = {"role": "assistant", "content": text}
                        finish_reason = "stop"
                        if model == "lab-tool":
                            message = {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_lab",
                                        "type": "function",
                                        "function": {
                                            "name": "get_build_config",
                                            "arguments": '{"target":"linux-x64"}',
                                        },
                                    }
                                ],
                            }
                            finish_reason = "tool_calls"
                        self._json(
                            200,
                            {
                                **common,
                                "object": "chat.completion",
                                "usage": usage,
                                "choices": [
                                    {"index": 0, "message": message, "finish_reason": finish_reason}
                                ],
                            },
                        )
                        event["finished"] = True
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True

                    def send(delta: dict[str, Any], finish: str | None = None) -> None:
                        payload = {
                            **common,
                            "object": "chat.completion.chunk",
                            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                        }
                        self.wfile.write(
                            ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode()
                        )
                        self.wfile.flush()
                        event["chunks_sent"] += 1

                    send({"role": "assistant", "content": ""})
                    if model == "lab-malformed":
                        self.wfile.write(b"data: {malformed-json\n\n")
                        self.wfile.flush()
                        return
                    if model == "lab-tool":
                        send(
                            {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_lab",
                                        "type": "function",
                                        "function": {
                                            "name": "get_build_config",
                                            "arguments": '{"target":',
                                        },
                                    }
                                ]
                            }
                        )
                        send(
                            {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": '"linux-x64"}'}}
                                ]
                            }
                        )
                        send({}, "tool_calls")
                    else:
                        for offset in range(0, len(text), 3):
                            if owner._stop.wait(owner.chunk_delay):
                                return
                            send({"content": text[offset : offset + 3]})
                            if model == "lab-truncated":
                                return
                        send({}, "stop")
                    if body.get("stream_options", {}).get("include_usage"):
                        payload = {
                            **common,
                            "object": "chat.completion.chunk",
                            "choices": [],
                            "usage": usage,
                        }
                        self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    event["finished"] = True
                except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                    event["disconnected"] = True

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self._stop.set()
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=3)
        self._server = None


def run_provider(
    seed: int = 7, on_frame: Callable[[dict[str, Any]], None] | None = None
) -> dict[str, Any]:
    """Exercise actual HTTP/SSE with the official OpenAI client, locally."""
    import httpx
    from openai import APITimeoutError, OpenAI, RateLimitError

    checks: list[dict[str, Any]] = []
    observed_stream: list[dict[str, Any]] = []
    disconnect_observed = False

    def record(check: dict[str, Any]) -> None:
        checks.append(check)
        if on_frame:
            on_frame(
                {
                    "suite": "provider",
                    "phase": check["name"],
                    "passed": check["passed"],
                    "completed_checks": len(checks),
                    "stream": {
                        "latest": observed_stream[-1] if observed_stream else None,
                        "timeline": [
                            {
                                key: sample[key]
                                for key in (
                                    "chunk_index",
                                    "elapsed_ms",
                                    "delta_bytes",
                                    "total_bytes",
                                    "event_type",
                                )
                            }
                            for sample in observed_stream[-128:]
                        ],
                        "disconnect_observed": disconnect_observed,
                    },
                }
            )

    text = "Synthetic ⟪private:1⟫ — 👩‍💻 café / build linux-x64"
    start = time.perf_counter()
    with (
        ProviderServer() as server,
        OpenAI(
            api_key="local-fixture",
            base_url=server.base_url,
            max_retries=0,
            timeout=2,
            http_client=httpx.Client(trust_env=False),
        ) as client,
    ):
        args = {"messages": [{"role": "user", "content": text}]}
        result = client.chat.completions.create(model="lab-echo", **args)
        record(
            {
                "name": "http_echo_preserves_unicode_and_symbols",
                "passed": result.choices[0].message.content == text,
            }
        )
        stream_started = time.perf_counter()
        chunks = []
        accumulated_text = ""
        observed_finish = None
        positive_stream = client.chat.completions.create(
            model="lab-stream", stream=True, stream_options={"include_usage": True}, **args
        )
        try:
            for chunk_index, chunk in enumerate(positive_stream, start=1):
                chunks.append(chunk)
                content = chunk.choices[0].delta.content or "" if chunk.choices else ""
                finish_reason = chunk.choices[0].finish_reason if chunk.choices else None
                accumulated_text += content
                if finish_reason is not None:
                    observed_finish = finish_reason
                event_type = (
                    "usage"
                    if not chunk.choices
                    else "finish"
                    if finish_reason is not None
                    else "content"
                    if content
                    else "role"
                )
                sample = {
                    "suite": "provider",
                    "phase": "streaming",
                    "chunk_index": chunk_index,
                    "elapsed_ms": round((time.perf_counter() - stream_started) * 1000, 3),
                    "delta_bytes": len(content.encode("utf-8")),
                    "total_bytes": len(accumulated_text.encode("utf-8")),
                    "finish_reason": finish_reason,
                    "stream_finished": observed_finish is not None,
                    "event_type": event_type,
                    "text_preview": accumulated_text[-512:],
                }
                observed_stream.append(sample)
                if on_frame:
                    on_frame(sample)
        finally:
            positive_stream.close()
        restored = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
        record({"name": "sse_reassembles_exact_text", "passed": restored == text})
        record(
            {
                "name": "sse_finish_and_usage",
                "passed": any(c.choices and c.choices[0].finish_reason == "stop" for c in chunks)
                and chunks[-1].usage is not None
                and isinstance(chunks[-1].usage.total_tokens, int),
            }
        )
        tool_chunks = list(client.chat.completions.create(model="lab-tool", stream=True, **args))
        arguments = "".join(
            t.function.arguments or ""
            for c in tool_chunks
            for choice in c.choices
            for t in choice.delta.tool_calls or []
            if t.function
        )
        record(
            {
                "name": "fragmented_tool_arguments",
                "passed": json.loads(arguments) == {"target": "linux-x64"},
            }
        )
        try:
            client.chat.completions.create(model="lab-error", **args)
            limited = False
        except RateLimitError as exc:
            limited = exc.status_code == 429 and exc.response.headers["retry-after"] == "1"
        record({"name": "rate_limit_exposes_retry_metadata", "passed": limited})
        try:
            list(client.chat.completions.create(model="lab-malformed", stream=True, **args))
            malformed_rejected = False
        except json.JSONDecodeError:
            malformed_rejected = True
        record({"name": "malformed_sse_not_accepted_as_success", "passed": malformed_rejected})
        partial = list(client.chat.completions.create(model="lab-truncated", stream=True, **args))
        record(
            {
                "name": "truncated_stream_has_no_success_finish",
                "passed": not any(c.choices and c.choices[0].finish_reason for c in partial),
            }
        )
        try:
            client.with_options(timeout=0.02).chat.completions.create(model="lab-slow", **args)
            timed_out = False
        except APITimeoutError:
            timed_out = True
        record({"name": "bounded_client_timeout", "passed": timed_out})
        stream = client.chat.completions.create(
            model="lab-stream", stream=True, messages=[{"role": "user", "content": "cancel " * 100}]
        )
        next(stream)
        stream.close()
        cancel_event = server.events[-1]
        deadline = time.monotonic() + 1
        while not cancel_event["disconnected"] and time.monotonic() < deadline:
            time.sleep(0.005)
        disconnect_observed = bool(cancel_event["disconnected"])
        if disconnect_observed and on_frame:
            on_frame(
                {
                    "suite": "provider",
                    "phase": "upstream_disconnect",
                    "observed": True,
                    "chunks_sent": cancel_event["chunks_sent"],
                    "finished": cancel_event["finished"],
                }
            )
        record(
            {
                "name": "stream_close_disconnects_upstream",
                "passed": cancel_event["disconnected"] and not cancel_event["finished"],
            }
        )
        request_count = len(server.requests)
    return {
        "suite": "provider",
        "engine": "OpenAI Python client + loopback HTTP/SSE",
        "engine_version": importlib.metadata.version("openai"),
        "seed": seed,
        "checks": checks,
        "metrics": {
            "http_requests": request_count,
            "observed_sse_chunks": len(observed_stream),
            "observed_sse_content_bytes": len(restored.encode("utf-8")),
            "observed_first_chunk_ms": observed_stream[0]["elapsed_ms"],
            "observed_finish_ms": next(
                sample["elapsed_ms"]
                for sample in observed_stream
                if sample["finish_reason"] is not None
            ),
            "upstream_disconnect_observed": disconnect_observed,
            "wall_ms": round((time.perf_counter() - start) * 1000, 3),
        },
        "scope": "Real local HTTP, SSE and client behavior; deterministic synthetic provider output, no hosted model call. Usage counts are UTF-8 byte fixtures, not tokenizer estimates.",
    }
