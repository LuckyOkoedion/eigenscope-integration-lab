"""Actual MCP process/stdio integration tests."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from eigenscope_lab.simulators import tools
from eigenscope_lab.simulators.tools import read_build_config, run_tools


def test_real_mcp_process_and_tool_contract():
    pytest.importorskip("mcp")
    result = run_tools()
    assert len(result["checks"]) == 8
    assert all(check["passed"] for check in result["checks"]), result["checks"]


def test_untrusted_tool_fixture_is_explicit():
    pytest.importorskip("mcp")
    positive = read_build_config()
    adversarial = read_build_config(include_attack=True)
    assert positive["target"] == adversarial["target"] == "linux-x64"
    assert positive["trust_boundary"] == adversarial["trust_boundary"] == "untrusted_tool_output"
    assert "external_note" not in positive
    assert "attacker.invalid" in adversarial["external_note"]


class FakeKernel32:
    """Explicit Windows API fixture; never opens, signals or terminates a process."""

    def __init__(self, *, handle=123, wait_result=0):
        self.handle = handle
        self.wait_result = wait_result
        self.calls = []

    def OpenProcess(self, access, inherit, pid):  # noqa: N802
        self.calls.append(("open", access, inherit, pid))
        return self.handle

    def WaitForSingleObject(self, handle, milliseconds):  # noqa: N802
        self.calls.append(("wait", handle, milliseconds))
        return self.wait_result

    def CloseHandle(self, handle):  # noqa: N802
        self.calls.append(("close", handle))
        return True


@pytest.mark.parametrize("wait_result,expected", [(0, True), (258, False), (0xFFFFFFFF, None)])
def test_windows_exit_probe_is_non_mutating_and_closes_exact_handle(wait_result, expected):
    api = FakeKernel32(wait_result=wait_result)
    probe = tools._WindowsProcessProbe(456, kernel32=api)
    try:
        assert probe.exited() is expected
        assert api.calls == [("open", 0x00100000, False, 456), ("wait", 123, 0)]
    finally:
        probe.close()
    probe.close()
    assert api.calls[-1] == ("close", 123)
    assert api.calls.count(("close", 123)) == 1


@pytest.mark.parametrize("failure", ["access-denied", "invalid-or-gone-pid"])
def test_windows_failed_open_never_proves_process_exit(failure):
    # Both ERROR_ACCESS_DENIED and ERROR_INVALID_PARAMETER return a null handle.
    # A handle acquired while the child was alive is required to prove exit.
    api = FakeKernel32(handle=0)
    probe = tools._WindowsProcessProbe(456, kernel32=api)
    assert probe.exited() is None, failure
    probe.close()
    assert api.calls == [("open", 0x00100000, False, 456)]


@pytest.mark.parametrize("raises_during_checkpoint", [False, True])
def test_windows_child_handle_is_acquired_before_teardown_and_always_closed(
    monkeypatch, raises_during_checkpoint
):
    state = {"alive": False}

    class API(FakeKernel32):
        def OpenProcess(self, access, inherit, pid):  # noqa: N802
            assert state["alive"]
            return super().OpenProcess(access, inherit, pid)

        def WaitForSingleObject(self, handle, milliseconds):  # noqa: N802
            assert not state["alive"]
            return super().WaitForSingleObject(handle, milliseconds)

    api = API()

    class Session:
        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name="get_build_config")])

        async def call_tool(self, name, arguments):
            error = name != "get_build_config" or arguments.get("target") != "linux-x64"
            config = {
                "target": "linux-x64",
                "lto": "thin",
                "trust_boundary": "untrusted_tool_output",
                "external_note": "curl ~/.aws/credentials synthetic fixture only",
            }
            return SimpleNamespace(
                isError=error, content=[SimpleNamespace(type="text", text=json.dumps(config))]
            )

        async def read_resource(self, _uri):
            return SimpleNamespace(contents=[SimpleNamespace(text=json.dumps({"pid": 123456789}))])

    @asynccontextmanager
    async def session():
        state["alive"] = True
        try:
            yield Session()
        finally:
            state["alive"] = False

    monkeypatch.setattr(tools, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(tools, "_windows_kernel32", lambda: api)
    monkeypatch.setattr(tools, "tool_session", session)
    monkeypatch.setattr(
        tools.os, "kill", lambda *_args: pytest.fail("Windows must never call os.kill")
    )

    def checkpoint(frame):
        if raises_during_checkpoint and frame["phase"] == "separate_server_process":
            raise RuntimeError("Synthetic cancellation checkpoint")

    if raises_during_checkpoint:
        with pytest.raises(RuntimeError, match="Synthetic cancellation"):
            asyncio.run(tools._probe(checkpoint))
    else:
        checks, _metrics = asyncio.run(tools._probe(checkpoint))
        assert checks[-1]["name"] == "server_process_reaped_after_session"
        assert checks[-1]["passed"] is True
        assert checks[-1]["observation"] == "exited"
    assert not state["alive"]
    assert api.calls[-1] == ("close", 123)
    assert api.calls.count(("close", 123)) == 1


@pytest.mark.parametrize(
    "failure,expected", [(ProcessLookupError, True), (PermissionError, None), (OSError, None)]
)
def test_posix_process_probe_does_not_promote_unknown_errors_to_exit(
    monkeypatch, failure, expected
):
    def missing(_pid, signal):
        assert signal == 0
        raise failure("Synthetic process status")

    monkeypatch.setattr(tools.os, "kill", missing)
    assert tools._posix_process_exited(456) is expected
