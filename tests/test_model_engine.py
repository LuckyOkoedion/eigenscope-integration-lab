"""Optional real-model execution and checkpoint-boundary checks; no network calls."""

import importlib.util
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from eigenscope_lab.model_engine import (
    ModelPrerequisiteError,
    TransformersEngine,
    _local_revision,
    _validate_device,
    checkpoint_fingerprint,
    make_smoke_engine,
)


def test_optional_model_dependencies_are_not_imported_by_the_module():
    code = (
        "import sys; import eigenscope_lab.model_engine; "
        "assert 'torch' not in sys.modules; assert 'transformers' not in sys.modules"
    )
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    completed = subprocess.run(
        [sys.executable, "-c", code],
        env=environment,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0


@pytest.mark.parametrize("revision", [None, "main", "v1", "a" * 39, "g" * 40])
def test_remote_model_requires_an_immutable_revision_before_importing_torch(revision):
    with pytest.raises(ValueError, match="immutable"):
        TransformersEngine("example/model", revision)


@pytest.mark.parametrize(
    "reference",
    ["https://user:secret@example.com/model", "", "a/b/c", "bad model"],
)
def test_invalid_references_are_rejected_without_loading_dependencies(reference):
    with pytest.raises(ValueError):
        TransformersEngine(reference, "a" * 40)


def test_missing_local_checkpoint_is_a_prerequisite_failure_before_importing_torch():
    with pytest.raises(ModelPrerequisiteError, match="unavailable"):
        TransformersEngine("../missing-checkpoint", None)


def test_local_fingerprint_depends_on_actual_weight_bytes(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"model_type": "fixture"}))
    weights = tmp_path / "model.safetensors"
    weights.write_bytes(b"synthetic-checkpoint-fixture-one")
    first = _local_revision(tmp_path)
    assert first.startswith("sha256:") and len(first) == 71
    assert first == _local_revision(tmp_path)
    assert checkpoint_fingerprint(tmp_path) == first
    weights.write_bytes(b"synthetic-checkpoint-fixture-two")
    assert first != _local_revision(tmp_path)


def test_local_checkpoint_requires_safe_weights(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "pytorch_model.bin").write_bytes(b"unused")
    with pytest.raises(ModelPrerequisiteError, match="safetensors"):
        _local_revision(tmp_path)


@pytest.mark.parametrize("revision", ["a" * 40, "main", "sha256:bad", True])
def test_local_expected_revision_requires_a_full_sha256_before_loading(tmp_path, revision):
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "model.safetensors").write_bytes(b"synthetic-weights")
    with pytest.raises(ValueError, match="SHA-256"):
        TransformersEngine(str(tmp_path), revision)


@pytest.mark.parametrize("prefix", ["", "sha256:"])
def test_local_expected_revision_must_match_actual_weight_bytes(tmp_path, prefix):
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "model.safetensors").write_bytes(b"synthetic-weights")
    with pytest.raises(ValueError, match="expected revision"):
        TransformersEngine(str(tmp_path), prefix + "a" * 64)


@pytest.mark.parametrize("failure", ["device", "identity"])
def test_failed_model_preparation_releases_partial_model(monkeypatch, failure):
    from eigenscope_lab import model_engine

    config = SimpleNamespace(model_type="fixture", hidden_size=32, _commit_hash="a" * 40)

    def move(_device):
        if failure == "device":
            raise RuntimeError("SYNTHETIC_DEVICE_FAILURE")

    model = SimpleNamespace(config=config, to=move, eval=lambda: None)
    if failure == "identity":
        model.config.hidden_size = None
    torch = SimpleNamespace(device=lambda _name: SimpleNamespace(type="cpu"))
    transformers = SimpleNamespace(
        AutoTokenizer=SimpleNamespace(from_pretrained=lambda *_args, **_kwargs: object()),
        AutoModelForCausalLM=SimpleNamespace(from_pretrained=lambda *_args, **_kwargs: model),
    )
    monkeypatch.setattr(model_engine, "_dependencies", lambda: (torch, transformers))
    engine = TransformersEngine.__new__(TransformersEngine)
    with pytest.raises((ValueError, RuntimeError)):
        engine.__init__("example/model", "a" * 40)
    assert engine.model is None
    assert engine._tokenizer is None


def test_loader_disables_remote_code_pickle_weights_and_unrequested_downloads(monkeypatch):
    from eigenscope_lab import model_engine

    calls = []
    config = SimpleNamespace(model_type="fixture", hidden_size=32, _commit_hash="a" * 40)
    model = SimpleNamespace(config=config, to=lambda _device: None, eval=lambda: None)

    def tokenizer_load(source, **options):
        calls.append(("tokenizer", source, options))
        return object()

    def model_load(source, **options):
        calls.append(("model", source, options))
        return model

    torch = SimpleNamespace(device=lambda _name: SimpleNamespace(type="cpu"))
    transformers = SimpleNamespace(
        AutoTokenizer=SimpleNamespace(from_pretrained=tokenizer_load),
        AutoModelForCausalLM=SimpleNamespace(from_pretrained=model_load),
    )
    monkeypatch.setattr(model_engine, "_dependencies", lambda: (torch, transformers))
    engine = TransformersEngine("example/model", "a" * 40)
    try:
        assert calls == [
            (
                "tokenizer",
                "example/model",
                {
                    "local_files_only": True,
                    "trust_remote_code": False,
                    "revision": "a" * 40,
                },
            ),
            (
                "model",
                "example/model",
                {
                    "use_safetensors": True,
                    "local_files_only": True,
                    "trust_remote_code": False,
                    "revision": "a" * 40,
                },
            ),
        ]
        assert engine.identity == {
            "model_type": "fixture",
            "model_revision": "a" * 40,
            "hidden_size": 32,
        }
    finally:
        engine.close()


def test_unavailable_cuda_is_rejected_before_querying_current_device():
    def current_device():
        pytest.fail("A missing CUDA runtime must not be initialized")

    torch = SimpleNamespace(
        device=lambda _name: SimpleNamespace(type="cuda", index=None),
        cuda=SimpleNamespace(is_available=lambda: False, current_device=current_device),
    )
    with pytest.raises(ModelPrerequisiteError, match="unavailable"):
        _validate_device(torch, "cuda")


def test_uncached_checkpoint_load_is_a_sanitized_prerequisite_failure(monkeypatch):
    from eigenscope_lab import model_engine

    def unavailable(*_args, **_kwargs):
        raise OSError("SYNTHETIC_PRIVATE_CACHE_PATH")

    torch = SimpleNamespace(device=lambda _name: SimpleNamespace(type="cpu"))
    transformers = SimpleNamespace(
        AutoTokenizer=SimpleNamespace(from_pretrained=unavailable),
    )
    monkeypatch.setattr(model_engine, "_dependencies", lambda: (torch, transformers))
    engine = TransformersEngine.__new__(TransformersEngine)
    with pytest.raises(ModelPrerequisiteError) as failure:
        engine.__init__("example/model", "a" * 40)
    assert "SYNTHETIC_PRIVATE" not in str(failure.value)
    assert engine.model is None and engine._tokenizer is None


def _require_engine_dependencies():
    if not importlib.util.find_spec("torch") or not importlib.util.find_spec("transformers"):
        pytest.skip("Install the optional model-inference environment to execute real models")
    pytest.importorskip("torch")
    pytest.importorskip("transformers")


@pytest.mark.parametrize("architecture", ["gpt2", "llama"])
def test_two_real_architectures_execute_cached_greedy_inference(architecture):
    _require_engine_dependencies()
    engine = make_smoke_engine(architecture)
    try:
        first = engine.generate("Synthetic benchmark input", max_new_tokens=4)
        second = engine.generate("Synthetic benchmark input", max_new_tokens=4)
        assert first["token_ids"] == second["token_ids"]
        assert len(first["token_ids"]) == first["generated_tokens"] == 4
        assert first["finite_logits"] is True
        assert 0 < first["ttft_ms"] <= first["wall_ms"]
        assert first["tokens_per_second"] > 0
        assert first["dtype"] == "float32"
        assert first["device"] == "cpu"
        assert first["provenance"] == "random_initialized"
        assert all(
            math.isfinite(first[name]) for name in ("wall_ms", "ttft_ms", "tokens_per_second")
        )
        assert engine.identity["model_type"] == architecture
        assert engine.identity["hidden_size"] == 32
        assert engine.provenance == "random_initialized"
        assert engine.hook_count() == 0
        handle = engine.model.register_forward_hook(lambda *_args: None)
        assert engine.hook_count() == 1
        handle.remove()
        assert engine.hook_count() == 0
    finally:
        engine.close()
    assert engine.hook_count() == 0
    with pytest.raises(RuntimeError, match="closed"):
        engine.generate("Synthetic benchmark input", 1)


@pytest.mark.parametrize("architecture", ["gpt2", "llama"])
def test_cached_decode_uses_the_full_prompt_then_one_token_per_forward(architecture):
    _require_engine_dependencies()
    engine = make_smoke_engine(architecture)
    lengths = []

    def observe(_module, _args, kwargs):
        lengths.append(int(kwargs["input_ids"].shape[1]))

    handle = engine.model.register_forward_pre_hook(observe, with_kwargs=True)
    try:
        sample = engine.generate("Synthetic input", 4)
        assert sample["generated_tokens"] == 4
        assert lengths[0] > 1
        assert lengths[1:] == [1, 1, 1]
    finally:
        handle.remove()
        engine.close()


def test_smoke_seed_has_stable_identity_without_changing_global_rng():
    _require_engine_dependencies()
    import torch

    before = torch.random.get_rng_state().clone()
    first = make_smoke_engine("gpt2", seed=7)
    second = make_smoke_engine("gpt2", seed=7)
    third = make_smoke_engine("gpt2", seed=8)
    try:
        assert first.identity == second.identity
        assert first.identity != third.identity
        assert torch.equal(before, torch.random.get_rng_state())
    finally:
        first.close()
        second.close()
        third.close()


@pytest.mark.parametrize("tokens", [0, -1, 257, True, 1.5])
def test_generation_budget_is_bounded(tokens):
    _require_engine_dependencies()
    engine = make_smoke_engine("gpt2")
    try:
        with pytest.raises(ValueError, match="max_new_tokens"):
            engine.generate("Synthetic input", tokens)
    finally:
        engine.close()


def test_nonfinite_logits_do_not_count_as_successful_generation():
    _require_engine_dependencies()
    import torch

    engine = make_smoke_engine("gpt2")

    def poison(_module, _inputs, result):
        result.logits[:] = torch.nan
        return result

    handle = engine.model.register_forward_hook(poison)
    try:
        sample = engine.generate("Synthetic input", 4)
        assert sample["finite_logits"] is False
        assert sample["generated_tokens"] == 0
        assert sample["token_ids"] == ()
    finally:
        handle.remove()
        engine.close()


@pytest.mark.parametrize("scale,changed", [(1.0, 0), (0.9, 3)])
def test_independent_activation_probe_observes_real_changes_and_removes_observers(scale, changed):
    _require_engine_dependencies()
    engine = make_smoke_engine("gpt2")
    handle = engine.model.get_input_embeddings().register_forward_hook(
        lambda _module, _inputs, output: output * scale
    )
    try:
        count = engine.hook_count()
        observed = engine.audit_intervention("Synthetic probe", 3)
        assert observed == {"observed_calls": 3, "changed_calls": changed, "finite": True}
        assert engine.hook_count() == count
    finally:
        handle.remove()
        engine.close()


def test_independent_probe_detects_in_place_activation_mutation():
    _require_engine_dependencies()
    engine = make_smoke_engine("llama")
    handle = engine.model.get_input_embeddings().register_forward_hook(
        lambda _module, _inputs, output: output.mul_(0.9)
    )
    try:
        assert engine.audit_intervention("Synthetic probe", 2) == {
            "observed_calls": 2,
            "changed_calls": 2,
            "finite": True,
        }
        assert engine.hook_count() == 1
    finally:
        handle.remove()
        engine.close()


def test_independent_probe_supports_nested_block_outputs():
    _require_engine_dependencies()
    engine = make_smoke_engine("gpt2")

    def scale_block(_module, _inputs, output):
        return (output[0] * 0.9, *output[1:])

    handle = engine.model.transformer.h[0].register_forward_hook(scale_block)
    try:
        assert engine.audit_intervention("Synthetic probe", 2) == {
            "observed_calls": 2,
            "changed_calls": 2,
            "finite": True,
        }
        assert engine.hook_count() == 1
    finally:
        handle.remove()
        engine.close()


def test_probe_without_instance_forward_hooks_cannot_claim_observation():
    _require_engine_dependencies()
    engine = make_smoke_engine("gpt2")
    try:
        assert engine.audit_intervention("Synthetic probe", 2) == {
            "observed_calls": 0,
            "changed_calls": 0,
            "finite": True,
        }
        assert engine.hook_count() == 0
    finally:
        engine.close()


def test_probe_observers_are_removed_when_the_original_hook_raises():
    _require_engine_dependencies()
    engine = make_smoke_engine("gpt2")

    def broken(_module, _inputs, _output):
        raise RuntimeError("SYNTHETIC_HOOK_ERROR")

    handle = engine.model.get_input_embeddings().register_forward_hook(broken)
    try:
        with pytest.raises(RuntimeError, match="SYNTHETIC_HOOK_ERROR"):
            engine.audit_intervention("Synthetic probe", 2)
        assert engine.hook_count() == 1
    finally:
        handle.remove()
        engine.close()
