# Provider transport lab

Run the official OpenAI Python client against a temporary local HTTP service. Exercise response parsing, streamed content, tool-call fragments, failures and cancellation without a model account.

```bash
uv sync --frozen --all-extras
uv run python -c 'from eigenscope_lab.simulators.provider import run_provider; import json; print(json.dumps(run_provider(), indent=2))'
```

The fixture listens on an operating-system-assigned `127.0.0.1` port and closes on context exit. HTTP authorization headers are neither inspected nor recorded. Model messages are held in memory for assertions; use synthetic data.

| Model | Behavior |
|---|---|
| `lab-echo` | Echo the final user text unchanged, including Unicode and EigenScope symbols. Supports ordinary and streamed Chat Completions. |
| `lab-stream` | Emit the final user text in three-code-point SSE fragments, a finish event and optional usage event. |
| `lab-tool` | Return a synthetic `get_build_config` tool call; the stream splits JSON arguments across deltas. |
| `lab-error` | Return HTTP 429 with a structured error and `Retry-After: 1`. |
| `lab-malformed` | Emit a malformed JSON SSE event. |
| `lab-truncated` | Close the stream after an incomplete response, with no successful finish. |
| `lab-slow` | Delay the response, allowing a bounded client-timeout test. |

```python
import httpx
from openai import OpenAI
from eigenscope_lab.simulators.provider import ProviderServer

with ProviderServer() as server:
    with OpenAI(
        api_key="local-fixture",
        base_url=server.base_url,
        max_retries=0,
        timeout=2,
        http_client=httpx.Client(trust_env=False),
    ) as client:
        reply = client.chat.completions.create(
            model="lab-echo",
            messages=[{"role": "user", "content": "protected task text"}],
        )
        assert reply.choices[0].message.content == "protected task text"
    assert server.requests[0]["messages"][0]["content"] == "protected task text"
```

Use that context with the real EigenScope SDK: decompose a synthetic prompt, send `task_core` to the local provider, rehydrate its response, and verify ticket cleanup. The provider captures the actual HTTP request, so a private-value assertion checks the downstream boundary rather than assuming masking worked.

The suite checks exact Unicode reconstruction, finish/usage events, split tool arguments, structured rate limits, malformed data, missing finish, timeout and observed upstream disconnection after stream close. Rejected requests are bounded to 64 KiB and are not retained. The HTTP service exposes only the documented loopback completion route.

With an `on_frame` callback, the positive stream emits one observation after each actual SDK-parsed SSE chunk: `chunk_index`, `elapsed_ms`, `delta_bytes`, `total_bytes`, `event_type`, observed `finish_reason`, and `stream_finished`. A preview contains at most 512 characters of this scenario's fixed synthetic response. Role, content, finish and usage events remain distinct; no chunk is announced before it is read. Later check frames retain a bounded stream summary so the dashboard can recover the observed timeline after reconnecting. The cancellation control reports a disconnect only after the local HTTP server detects it.

Usage values are deterministic UTF-8 byte counts used to exercise protocol metadata. They are not model tokenizer counts. Recorded wall time includes loopback transport, scripted fixture delays and any callback pacing; use the live lab's stage timings for EigenScope latency analysis. A completed local fixture validates transport behavior, not a hosted model's reasoning.

Sources: [OpenAI streaming event reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events), [official OpenAI Python client](https://github.com/openai/openai-python) (Apache-2.0), and Python's [HTTP server documentation](https://docs.python.org/3/library/http.server.html). The local provider implementation is included with the lab.
