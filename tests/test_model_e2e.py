"""Exercise lab orchestration through real GPT-2/Llama inference and native hooks.

The tiny models are random initializations. The local scaling hook and cache
receipts below are explicit test fixtures, not an EigenScope connector, signed
profile, four-channel calibration or evidence about a hosted deployment.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import time
from pathlib import Path
from typing import Any

import pytest

from eigenscope_lab.model_engine import make_smoke_engine
from eigenscope_lab.model_integration import run_model_matrix


def _require_model_dependencies() -> None:
    if not importlib.util.find_spec("torch") or not importlib.util.find_spec("transformers"):
        pytest.skip("Install the optional model-inference environment to execute real models")
    pytest.importorskip("torch")
    pytest.importorskip("transformers")


class NativeHookFixture:
    """Test-only embedding scaling, with counters driven by actual forward calls."""

    def __init__(
        self,
        model: Any,
        identity: dict[str, Any],
        cache_dir: Path,
        refresh: bool,
        protected_scale: float,
    ) -> None:
        import torch

        self._torch = torch
        self._mode = "identity"
        self._protected_scale = protected_scale
        self.closed = False
        self.cache_dir = cache_dir
        detected = {
            "model_type": model.config.model_type,
            "model_revision": identity["model_revision"],
            "hidden_size": model.config.hidden_size,
        }
        assert detected == identity
        digest = hashlib.sha256(json.dumps(detected, sort_keys=True).encode()).hexdigest()
        marker = cache_dir / "native-hook-test-cache.json"
        if refresh:
            assert not marker.exists()
            marker.write_text(json.dumps({"fixture_digest": digest}), encoding="utf-8")
        else:
            assert json.loads(marker.read_text(encoding="utf-8")) == {"fixture_digest": digest}
        self.receipt = {
            "schema_version": 1,
            **detected,
            "profile_digest": digest,
            "signature_verified": True,  # Fixture receipt; no production signature assertion.
            "events": ["detected", "cache_miss" if refresh else "cache_hit", "verified", "loaded"],
        }
        self._counters: dict[str, Any] = {
            "hook_calls": 0,
            "projected_calls": 0,
            "changed_calls": 0,
            "provision_count": 1 if refresh else 0,
            "projection_ms": [],
        }
        # Both architectures expose a tensor-valued embedding module. Hooking it
        # avoids assuming identical block-output structures across architectures.
        self._handle = model.get_input_embeddings().register_forward_hook(self._project)

    def _project(self, _module: Any, _inputs: Any, output: Any) -> Any:
        started = time.perf_counter()
        scale = self._protected_scale if self._mode == "protected" else 1.0
        projected = output * scale
        changed = not self._torch.equal(output, projected)
        self._counters["hook_calls"] += 1
        self._counters["projected_calls"] += 1
        self._counters["changed_calls"] += int(changed)
        self._counters["projection_ms"].append((time.perf_counter() - started) * 1000)
        return projected

    def set_mode(self, mode: str) -> None:
        assert mode in {"identity", "protected"}
        self._mode = mode

    def snapshot(self) -> dict[str, Any]:
        return {**self._counters, "projection_ms": list(self._counters["projection_ms"])}

    def close(self) -> None:
        if not self.closed:
            self._handle.remove()
            self.closed = True


def _execute_native_hooks(protected_scale: float):
    _require_model_dependencies()
    engines = []
    sessions = []
    identities: dict[int, dict[str, Any]] = {}
    cases = [
        {
            "id": architecture,
            "reference": f"/generated-smoke/{architecture}",
            "prompt": "Synthetic sequence: one, two,",
            "max_new_tokens": 3,
        }
        for architecture in ("gpt2", "llama")
    ]

    def engine_factory(case):
        engine = make_smoke_engine(case["id"])
        engines.append(engine)
        identities[id(engine.model)] = dict(engine.identity)
        return engine

    def connector(model, *, cache_dir, refresh):
        session = NativeHookFixture(
            model,
            identities[id(model)],
            cache_dir,
            refresh,
            protected_scale,
        )
        sessions.append(session)
        return session

    result = run_model_matrix(
        cases,
        connector=connector,
        engine_factory=engine_factory,
        repeat=1,
        warmup=0,
    )
    return result, engines, sessions


def test_real_two_architecture_inference_exercises_lab_setup_hooks_and_cleanup():
    result, engines, sessions = _execute_native_hooks(protected_scale=0.9)
    assert result["summary"] == {"PASS": 4, "FAIL": 0, "BLOCKED": 0, "ERROR": 0}
    assert len(sessions) == 4
    for row in result["suites"]:
        assert row["checks"] and all(check["passed"] is True for check in row["checks"])
        if row["suite"].endswith(":integration"):
            assert row["protected_inference"]["generated_tokens"] == 3
            assert row["identity_inference"]["generated_tokens"] == 3
            assert row["protected_inference"]["ttft_ms"]["n"] == 1
            assert row["projection_ms"]["n"] == 3
            assert row["setup_ms"]["cold"] > 0 and row["setup_ms"]["cached"] > 0
    assert all(session.closed for session in sessions)
    assert all(engine.model is None and engine.hook_count() == 0 for engine in engines)
    assert all(not session.cache_dir.exists() for session in sessions)
    payload = json.dumps(result)
    assert "token_ids" not in payload
    assert "Synthetic sequence" not in payload
    assert "native-hook-test-cache" not in payload
    assert "/generated-smoke/" not in payload


def test_real_noop_protected_hook_is_a_failed_integration_contract():
    result, engines, sessions = _execute_native_hooks(protected_scale=1.0)
    rows = [row for row in result["suites"] if row["suite"].endswith(":integration")]
    assert len(rows) == 2
    assert all(row["status"] == "FAIL" for row in rows)
    assert all(
        any(
            check["name"].startswith("protected_sample_")
            and check["name"].endswith("_activations_changed")
            and check["passed"] is False
            for check in row["checks"]
        )
        for row in rows
    )
    assert all(session.closed for session in sessions)
    assert all(engine.model is None and engine.hook_count() == 0 for engine in engines)
