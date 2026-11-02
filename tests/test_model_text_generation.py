"""Bounded text-decoding and EOS mechanics, with no checkpoint downloads.

The optional neural tests use local random models and a recording decoder to
exercise generation mechanics. They do not evaluate a privacy profile or claim
that a model completed a semantic task.
"""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest

from eigenscope_lab.model_engine import TransformersEngine, make_smoke_engine


def decoder_fixture(text='{"status":200}'):
    engine = TransformersEngine.__new__(TransformersEngine)
    generation_calls = []
    decode_calls = []
    observation = {"token_ids": (17, 29, 2), "generated_tokens": 3}

    def generate(prompt, budget, *, stop_at_eos):
        generation_calls.append((prompt, budget, stop_at_eos))
        return observation

    def decode(tokens, **options):
        decode_calls.append((tokens, options))
        return text

    engine._generate = generate
    engine._tokenizer = SimpleNamespace(decode=decode)
    return engine, observation, generation_calls, decode_calls


def test_semantic_probe_decodes_only_new_tokens_with_exact_decoder_options():
    engine, observation, generated, decoded = decoder_fixture()
    result = engine.generate_text("Produce the report.", 24)
    assert generated == [("Produce the report.", 24, True)]
    assert decoded == [
        (
            observation["token_ids"],
            {"skip_special_tokens": True, "clean_up_tokenization_spaces": False},
        )
    ]
    assert result["token_ids"] == (17, 29, 2)
    assert result["generated_text"] == '{"status":200}'
    assert "generated_text" not in observation


def test_semantic_probe_has_a_bounded_default_token_budget():
    engine, _observation, generated, _decoded = decoder_fixture()
    engine.generate_text("Produce the report.")
    assert generated == [("Produce the report.", 192, True)]


def test_latency_generation_retains_its_fixed_length_execution_mode():
    engine, observation, generated, decoded = decoder_fixture()
    assert engine.generate("Produce the report.", 3) is observation
    assert generated == [("Produce the report.", 3, False)]
    assert decoded == []


@pytest.mark.parametrize("decoded", [None, b"result", {}, [], 200, "x" * 65537])
def test_invalid_or_oversized_decoded_observations_are_rejected(decoded):
    engine, _observation, _generated, _decoded = decoder_fixture(decoded)
    with pytest.raises(ValueError, match="invalid generated text"):
        engine.generate_text("Produce the report.", 3)


def test_decoded_text_at_the_bound_is_retained_only_in_the_observation():
    engine, observation, _generated, _decoded = decoder_fixture("x" * 65536)
    assert len(engine.generate_text("Produce the report.", 3)["generated_text"]) == 65536
    assert "generated_text" not in observation


@pytest.mark.parametrize("tokenizer", [None, object(), SimpleNamespace(decode=None)])
def test_missing_decoder_blocks_before_any_generation(tokenizer):
    engine = TransformersEngine.__new__(TransformersEngine)
    engine._tokenizer = tokenizer

    def forbidden(*_args, **_kwargs):
        pytest.fail("A semantic probe requires a decoder before running inference")

    engine._generate = forbidden
    with pytest.raises(NotImplementedError, match="checkpoint tokenizer"):
        engine.generate_text("Produce the report.", 3)


def require_neural_dependencies():
    if not importlib.util.find_spec("torch") or not importlib.util.find_spec("transformers"):
        pytest.skip("Install the optional model-inference environment for neural EOS checks")
    pytest.importorskip("torch")
    pytest.importorskip("transformers")


class RecordingTokenizer:
    def __init__(self, encoder):
        self.encoder = encoder
        self.decoded = []

    def __call__(self, prompt, **options):
        return self.encoder(prompt, **options)

    def decode(self, tokens, **options):
        self.decoded.append((tuple(tokens), options))
        return '{"status":200}'


@pytest.mark.parametrize("architecture", ["gpt2", "llama"])
@pytest.mark.parametrize("eos_form", ["integer", "list"])
def test_real_inference_stops_semantic_probe_at_eos_but_keeps_benchmark_length(
    architecture, eos_form
):
    require_neural_dependencies()
    engine = make_smoke_engine(architecture)
    tokenizer = RecordingTokenizer(engine._tokenizer)
    engine._tokenizer = tokenizer
    eos_token = 2
    engine.model.generation_config.eos_token_id = eos_token if eos_form == "integer" else [2, 3]
    calls = []

    def emit_eos(_module, _inputs, result):
        calls.append(1)
        result.logits.fill_(-1000)
        result.logits[..., eos_token] = 1000
        return result

    handle = engine.model.register_forward_hook(emit_eos)
    try:
        semantic = engine.generate_text("Produce the report.", 3)
        assert semantic["token_ids"] == (eos_token,)
        assert semantic["generated_tokens"] == 1
        assert semantic["finite_logits"] is True
        assert semantic["provenance"] == "random_initialized"
        assert tokenizer.decoded == [
            ((eos_token,), {"skip_special_tokens": True, "clean_up_tokenization_spaces": False})
        ]
        fixed = engine.generate("Produce the report.", 3)
        assert fixed["token_ids"] == (eos_token,) * 3
        assert fixed["generated_tokens"] == 3
        assert len(calls) == 4
        assert len(tokenizer.decoded) == 1
    finally:
        handle.remove()
        engine.close()


def test_real_semantic_probe_uses_checkpoint_eos_when_generation_override_is_absent():
    require_neural_dependencies()
    engine = make_smoke_engine("gpt2")
    engine._tokenizer = RecordingTokenizer(engine._tokenizer)
    engine.model.generation_config.eos_token_id = None
    engine.model.config.eos_token_id = 3

    def emit_eos(_module, _inputs, result):
        result.logits.fill_(-1000)
        result.logits[..., 3] = 1000
        return result

    handle = engine.model.register_forward_hook(emit_eos)
    try:
        assert engine.generate_text("Produce the report.", 3)["token_ids"] == (3,)
    finally:
        handle.remove()
        engine.close()
