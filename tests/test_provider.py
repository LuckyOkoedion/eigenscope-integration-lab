"""Transport tests run against an actual ephemeral HTTP provider."""

import pytest

from eigenscope_lab.simulators.provider import ProviderServer, run_provider


def test_official_client_fault_and_stream_contracts():
    pytest.importorskip("openai")
    frames = []
    result = run_provider(on_frame=frames.append)
    assert result["metrics"]["http_requests"] == 8
    assert len(result["checks"]) == 9
    assert all(check["passed"] for check in result["checks"]), result["checks"]
    streaming = [frame for frame in frames if frame["phase"] == "streaming"]
    assert len(streaming) == result["metrics"]["observed_sse_chunks"] > 3
    assert [frame["chunk_index"] for frame in streaming] == list(range(1, len(streaming) + 1))
    assert [frame["elapsed_ms"] for frame in streaming] == sorted(
        frame["elapsed_ms"] for frame in streaming
    )
    assert streaming[0]["total_bytes"] == 0 and streaming[0]["event_type"] == "role"
    assert streaming[-1]["total_bytes"] == sum(frame["delta_bytes"] for frame in streaming)
    assert streaming[-1]["total_bytes"] == len(streaming[-1]["text_preview"].encode("utf-8"))
    assert all(len(frame["text_preview"]) <= 512 for frame in streaming)
    finishes = [frame for frame in streaming if frame["finish_reason"] is not None]
    assert len(finishes) == 1 and finishes[0]["finish_reason"] == "stop"
    assert streaming[-1]["event_type"] == "usage" and streaming[-1]["stream_finished"]
    assert frames.index(streaming[-1]) < next(
        i for i, frame in enumerate(frames) if frame["phase"] == "sse_reassembles_exact_text"
    )
    disconnects = [frame for frame in frames if frame["phase"] == "upstream_disconnect"]
    assert len(disconnects) == 1 and disconnects[0]["observed"] and not disconnects[0]["finished"]
    assert frames[-1]["stream"]["latest"] == streaming[-1]


def test_invalid_requests_are_bounded_and_not_captured():
    httpx = pytest.importorskip("httpx")
    with ProviderServer() as server, httpx.Client(trust_env=False, timeout=2) as client:
        for payload in (
            [],
            {},
            {"model": "unregistered", "messages": []},
            {"model": "lab-echo", "messages": [{"role": "assistant", "content": "no user"}]},
        ):
            response = client.post(server.base_url + "/chat/completions", json=payload)
            assert response.status_code == 400
        assert client.post(server.base_url + "/unknown", json={}).status_code == 404
        assert (
            client.post(server.base_url + "/chat/completions", content=b"x" * 65537).status_code
            == 400
        )
        assert server.requests == []


def test_provider_lifecycle_and_delay_validation():
    with pytest.raises(ValueError):
        ProviderServer(chunk_delay=-1)
    server = ProviderServer()
    with pytest.raises(RuntimeError):
        _ = server.base_url
    with server:
        assert server.base_url.startswith("http://127.0.0.1:")
        with pytest.raises(RuntimeError):
            server.__enter__()
    assert not server._thread.is_alive()
    with pytest.raises(RuntimeError):
        _ = server.base_url
