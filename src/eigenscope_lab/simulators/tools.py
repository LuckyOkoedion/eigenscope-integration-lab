"""Actual MCP subprocess transport and untrusted tool-result fixtures."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from typing import Any


def _load_mcp_sdk() -> tuple[Any, Any, Any]:
    """Load the official parent SDK before starting a child-session I/O deadline."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    return ClientSession, StdioServerParameters, stdio_client


@asynccontextmanager
async def tool_session() -> AsyncIterator[Any]:
    """Launch the source-owned server with real JSON-RPC over stdio.

    Only an explicit import path is added to the SDK's restricted child
    environment. No API credentials are passed. The SDK owns process teardown.
    """
    ClientSession, StdioServerParameters, stdio_client = _load_mcp_sdk()

    environment = {"PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    with tempfile.TemporaryFile(mode="w+") as errors:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "eigenscope_lab.simulators.mcp_server"],
            env=environment,
        )
        async with stdio_client(parameters, errlog=errors) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def result_is_error(result: Any) -> bool:
    """Use the installed SDK's explicit wire-result spelling."""
    return bool(result.isError if hasattr(result, "isError") else result.is_error)


def result_text(result: Any) -> str:
    return "\n".join(block.text for block in result.content if getattr(block, "type", "") == "text")


async def async_read_build_config(*, include_attack: bool = False) -> dict[str, Any]:
    """Fetch a configuration from the actual local MCP server."""
    _load_mcp_sdk()
    async with asyncio.timeout(45), tool_session() as session:
        result = await session.call_tool(
            "get_build_config", {"target": "linux-x64", "include_attack": include_attack}
        )
        if result_is_error(result):
            raise RuntimeError("Synthetic build tool failed")
        return json.loads(result_text(result))


def read_build_config(*, include_attack: bool = False) -> dict[str, Any]:
    """Synchronous bridge for a CLI; async hosts should use the async variant."""
    return asyncio.run(async_read_build_config(include_attack=include_attack))


def _windows_kernel32() -> Any:
    """Load only the non-mutating Windows process synchronization APIs."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


class _WindowsProcessProbe:
    """Retain the actual child's handle across teardown to avoid PID reuse.

    A missing handle or unexpected wait result is inconclusive. It never proves
    successful cleanup. No signal or process termination is used on Windows.
    """

    def __init__(self, pid: int, *, kernel32: Any = None) -> None:
        self._kernel32 = kernel32 if kernel32 is not None else _windows_kernel32()
        self._handle = self._kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only

    def exited(self) -> bool | None:
        if not self._handle:
            return None
        result = self._kernel32.WaitForSingleObject(self._handle, 0)
        if result == 0:  # WAIT_OBJECT_0: this specific process has terminated.
            return True
        if result == 258:  # WAIT_TIMEOUT: this process is still running.
            return False
        return None

    def close(self) -> None:
        if self._handle:
            try:
                self._kernel32.CloseHandle(self._handle)
            finally:
                self._handle = None


def _posix_process_exited(pid: int) -> bool | None:
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True
    except OSError:
        return None


async def _probe(
    on_frame: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import_started = time.perf_counter()
    _load_mcp_sdk()
    parent_import_ms = (time.perf_counter() - import_started) * 1000
    checks: list[dict[str, Any]] = []

    def record(check: dict[str, Any]) -> None:
        checks.append(check)
        if on_frame:
            on_frame(
                {
                    "suite": "tools",
                    "phase": check["name"],
                    "passed": check["passed"],
                    "completed_checks": len(checks),
                }
            )

    process_id = None
    session_started = time.perf_counter()
    session_ready = session_started
    operations_finished = session_started
    with ExitStack() as cleanup:
        windows_probe = None
        # This includes child process bootstrap. A cold, read-only two-CPU VM
        # measured ~17s just to initialize the maintained MCP SDK/server.
        async with asyncio.timeout(45), tool_session() as session:
            session_ready = time.perf_counter()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            record(
                {
                    "name": "stdio_handshake_and_tool_discovery",
                    "passed": names == ["get_build_config"],
                }
            )
            positive = await session.call_tool("get_build_config", {"target": "linux-x64"})
            config = json.loads(result_text(positive))
            record(
                {
                    "name": "real_tool_roundtrip",
                    "passed": not result_is_error(positive)
                    and config["target"] == "linux-x64"
                    and config["lto"] == "thin",
                }
            )
            attack = await session.call_tool(
                "get_build_config", {"target": "linux-x64", "include_attack": True}
            )
            adversarial = json.loads(result_text(attack))
            record(
                {
                    "name": "untrusted_injection_fixture_reaches_boundary",
                    "passed": not result_is_error(attack)
                    and adversarial["trust_boundary"] == "untrusted_tool_output"
                    and "curl ~/.aws/credentials" in adversarial["external_note"],
                }
            )
            invalid = await session.call_tool("get_build_config", {"target": "../../etc/passwd"})
            record(
                {"name": "schema_rejects_unregistered_target", "passed": result_is_error(invalid)}
            )
            missing = await session.call_tool("get_build_config", {})
            record(
                {
                    "name": "schema_rejects_missing_required_argument",
                    "passed": result_is_error(missing),
                }
            )
            unknown = await session.call_tool("execute_shell", {"command": "echo prohibited"})
            record({"name": "unknown_execution_tool_rejected", "passed": result_is_error(unknown)})
            identity = await session.read_resource("lab://identity")
            process_id = json.loads(identity.contents[0].text)["pid"]
            distinct = type(process_id) is int and process_id > 0 and process_id != os.getpid()
            if distinct and sys.platform == "win32":
                windows_probe = _WindowsProcessProbe(process_id)
                cleanup.callback(windows_probe.close)
            record({"name": "separate_server_process", "passed": distinct})
            operations_finished = time.perf_counter()
        if type(process_id) is int and process_id > 0 and process_id != os.getpid():
            exited = (
                windows_probe.exited()
                if windows_probe is not None
                else _posix_process_exited(process_id)
            )
            record(
                {
                    "name": "server_process_reaped_after_session",
                    "passed": exited is True,
                    "observation": "inconclusive"
                    if exited is None
                    else "exited"
                    if exited
                    else "running",
                }
            )
    return checks, {
        "tool_calls": 5,
        "transport": "stdio JSON-RPC",
        "server_process_distinct": process_id != os.getpid(),
        "parent_sdk_import_wall_ms": round(parent_import_ms, 3),
        "session_io_deadline_seconds": 45,
        "session_bootstrap_wall_ms": round((session_ready - session_started) * 1000, 3),
        "protocol_operations_wall_ms": round((operations_finished - session_ready) * 1000, 3),
    }


def run_tools(
    seed: int = 7, on_frame: Callable[[dict[str, Any]], None] | None = None
) -> dict[str, Any]:
    """Discover/call an official SDK MCP server through a real subprocess."""
    start = time.perf_counter()
    checks, metrics = asyncio.run(_probe(on_frame))
    metrics["wall_ms"] = round((time.perf_counter() - start) * 1000, 3)
    return {
        "suite": "tools",
        "engine": "Official MCP SDK subprocess/stdio",
        "engine_version": importlib.metadata.version("mcp"),
        "seed": seed,
        "checks": checks,
        "metrics": metrics,
        "scope": "Actual MCP handshake, discovery, schema validation, tool calls and subprocess cleanup. Injected text remains deliberately untrusted fixture data; no file-read, shell or network-exfiltration tool exists.",
    }
