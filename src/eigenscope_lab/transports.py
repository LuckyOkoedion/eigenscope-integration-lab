"""Bounded real HTTP transport and in-memory observation of owned ticket handles."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

SDKTransport = Callable[[str, dict[str, str], bytes], tuple[int, bytes]]
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ResponseTooLargeError(ValueError):
    """The configured service exceeded the lab's bounded response size."""


class ResponseDeadlineError(TimeoutError):
    """The response exceeded the configured total transport deadline."""


class ObservedTransport:
    """Use the public SDK transport hook, preserving its original wire and guards.

    The optional delegate is an explicitly supplied transport, such as a test
    fixture. Otherwise this owns a real HTTPX client with redirect following and
    environment proxies disabled. Only this object's returned decomposition
    handles are retained; raw bodies and authentication are never exported.
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        owned_tickets: set[str],
        delegate: SDKTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.owned_tickets = owned_tickets
        self.delegate = delegate
        self._http: Any = None
        if delegate is None:
            import httpx

            self._http = httpx.Client(
                timeout=timeout_seconds, follow_redirects=False, trust_env=False
            )

    def __call__(self, path: str, headers: dict[str, str], wire: bytes) -> tuple[int, bytes]:
        deadline = time.monotonic() + self.timeout_seconds

        def remaining_seconds() -> float:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ResponseDeadlineError("Service response exceeded the configured deadline")
            return min(remaining, self.timeout_seconds)

        if self.delegate is not None:
            status, raw = self.delegate(path, headers, wire)
            remaining_seconds()
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ResponseTooLargeError("Service response exceeds the lab response limit")
        else:
            # Streaming enforces the ceiling even without a Content-Length.
            # The SDK's canonical bytes and authorization headers are unchanged.
            with self._http.stream(
                "POST",
                self.base_url + path,
                headers=headers,
                content=wire,
                timeout=remaining_seconds(),
            ) as response:
                status = response.status_code
                chunks = bytearray()
                # No fixed-size buffer: inspect each available decoded network
                # chunk so a slow trickle cannot keep resetting the read timer.
                for chunk in response.iter_bytes():
                    remaining_seconds()
                    if len(chunks) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ResponseTooLargeError(
                            "Service response exceeds the lab response limit"
                        )
                    chunks.extend(chunk)
                remaining_seconds()
                raw = bytes(chunks)
        if path == "/v1/decompose" and status == 200:
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                value = None
            if isinstance(value, dict):
                ticket = value.get("vault_ticket")
                if isinstance(ticket, str) and ticket.startswith("vlt_") and len(ticket) <= 512:
                    self.owned_tickets.add(ticket)
        # Identity, schema, policy, telemetry and custody validation remain with
        # the genuine SDK, which receives the unmodified response bytes.
        return status, raw

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
