"""Optional real causal-model execution with bounded, synchronized measurements.

Importing this module does not import Torch or Transformers. Checkpoints stay in
an external model cache; neither model files nor generated text enter reports.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from pathlib import Path
from typing import Any

_REVISION = re.compile(r"[a-fA-F0-9]{40}\Z")
_LOCAL_REVISION = re.compile(r"(?:sha256:)?([a-fA-F0-9]{64})\Z")
_MODEL_ID = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?\Z")
_DEVICE = re.compile(r"(?:cpu|mps|cuda(?::[0-9]{1,2})?)\Z")


class ModelPrerequisiteError(ImportError):
    """A checkpoint, optional dependency or selected device is unavailable."""


def _validate_reference(reference: str, revision: str | None) -> Path | None:
    if not isinstance(reference, str) or not reference or len(reference) > 1024:
        raise ValueError("Provide a model ID or an existing local checkpoint directory")
    candidate = Path(reference).expanduser()
    if candidate.is_dir():
        return candidate.resolve()
    if reference.startswith(("/", "~", ".")):
        raise ModelPrerequisiteError("The local checkpoint directory is unavailable")
    if not _MODEL_ID.fullmatch(reference):
        raise ValueError("The local checkpoint directory or model ID is invalid")
    if any(part in {".", ".."} for part in reference.split("/")):
        raise ValueError("The model ID is invalid")
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
        raise ValueError("Remote checkpoints require an immutable 40-character commit revision")
    return None


def _local_revision(directory: Path) -> str:
    """Fingerprint the actual local configuration and safetensors once at startup."""
    config = directory / "config.json"
    weights = sorted(directory.glob("*.safetensors"))
    if not config.is_file() or not weights:
        raise ModelPrerequisiteError(
            "The local checkpoint needs config.json and safetensors weights"
        )
    digest = hashlib.sha256()
    for path in (config, *weights):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return "sha256:" + digest.hexdigest()


def checkpoint_fingerprint(directory: str | Path) -> str:
    """Fingerprint a local checkpoint without returning its path or model data."""
    return _local_revision(Path(directory).expanduser().resolve())


def _dependencies() -> tuple[Any, Any]:
    import torch
    import transformers

    return torch, transformers


def _validate_device(torch: Any, device: str) -> Any:
    if not isinstance(device, str) or not _DEVICE.fullmatch(device):
        raise ValueError("Choose cpu, mps or a CUDA device")
    parsed = torch.device(device)
    if parsed.type == "cuda":
        if not torch.cuda.is_available():
            raise ModelPrerequisiteError("The requested CUDA device is unavailable")
        index = parsed.index if parsed.index is not None else torch.cuda.current_device()
        if index >= torch.cuda.device_count():
            raise ModelPrerequisiteError("The requested CUDA device is unavailable")
    if parsed.type == "mps" and not torch.backends.mps.is_available():
        raise ModelPrerequisiteError("The requested MPS device is unavailable")
    return parsed


class TransformersEngine:
    """Load a local or pinned checkpoint and measure real cached greedy inference."""

    def __init__(
        self,
        reference: str,
        revision: str | None,
        device: str = "cpu",
        allow_downloads: bool = False,
        seed: int = 7,
    ) -> None:
        local = _validate_reference(reference, revision)
        if type(allow_downloads) is not bool:
            raise ValueError("allow_downloads must be a boolean")
        if type(seed) is not int or not 0 <= seed <= 4294967295:
            raise ValueError("seed must be an unsigned 32-bit integer")
        if not isinstance(device, str) or not _DEVICE.fullmatch(device):
            raise ValueError("Choose cpu, mps or a CUDA device")
        if local:
            expected = None
            if revision is not None:
                matched = _LOCAL_REVISION.fullmatch(revision) if isinstance(revision, str) else None
                if matched is None:
                    raise ValueError("Local checkpoint revisions must be SHA-256 digests")
                expected = "sha256:" + matched.group(1).lower()
            checkpoint_revision = _local_revision(local)
            if expected is not None and expected != checkpoint_revision:
                raise ValueError("The local checkpoint does not match the expected revision")
        else:
            checkpoint_revision = str(revision).lower()
        torch, transformers = _dependencies()
        self._torch = torch
        self._device = _validate_device(torch, device)
        self._tokenizer = None
        self.model = None
        self.provenance = "checkpoint"
        # Never permit repository-provided Python code or pickle model weights.
        options: dict[str, Any] = {
            "local_files_only": not allow_downloads,
            "trust_remote_code": False,
        }
        if local is None:
            options["revision"] = checkpoint_revision
        source = str(local) if local else reference
        try:
            self._tokenizer = transformers.AutoTokenizer.from_pretrained(source, **options)
            self.model = transformers.AutoModelForCausalLM.from_pretrained(
                source, use_safetensors=True, **options
            )
            self.model.to(self._device)
            self.model.eval()
            loaded_revision = getattr(self.model.config, "_commit_hash", None)
            if local is None and loaded_revision and loaded_revision.lower() != checkpoint_revision:
                raise ValueError("The loaded model does not match the requested revision")
            self.identity = self._identity(checkpoint_revision)
        except OSError as exc:
            self.close()
            if not allow_downloads:
                raise ModelPrerequisiteError(
                    "The checkpoint is unavailable in the local cache"
                ) from exc
            raise
        except BaseException:
            self.close()
            raise

    def _identity(self, revision: str) -> dict[str, Any]:
        config = self.model.config
        width = getattr(config, "hidden_size", getattr(config, "n_embd", None))
        kind = getattr(config, "model_type", "")
        if (
            type(width) is not int
            or width <= 0
            or not isinstance(kind, str)
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", kind)
        ):
            raise ValueError("The model configuration lacks a valid activation identity")
        return {"model_type": kind, "model_revision": revision, "hidden_size": width}

    def _synchronize(self) -> None:
        if self._device.type == "cuda":
            self._torch.cuda.synchronize(self._device)
        elif self._device.type == "mps":
            self._torch.mps.synchronize()

    def hook_count(self) -> int:
        """Count instance forward hooks without mutating model or global hook state."""
        if self.model is None:
            return 0
        return sum(
            len(module._forward_hooks) + len(module._forward_pre_hooks)
            for module in self.model.modules()
        )

    def audit_intervention(self, prompt: str, max_new_tokens: int) -> dict[str, Any]:
        """Observe actual activation changes independently of connector counters.

        The extra tensor copies and comparisons belong to an untimed validation
        probe, separate from performance samples. Observers are removed on every
        exit and no activation values leave this method.
        """
        if self.model is None:
            raise RuntimeError("The model engine is closed")
        torch = self._torch
        targets = [module for module in self.model.modules() if module._forward_hooks]
        result = {"observed_calls": 0, "changed_calls": 0, "finite": True}
        if not targets:
            return result
        handles = []

        def capture(value: Any) -> tuple[Any, list[Any]]:
            if isinstance(value, torch.Tensor):
                return "tensor", [value.detach().clone()]
            if isinstance(value, (tuple, list)):
                signatures, tensors = [], []
                for item in value:
                    signature, children = capture(item)
                    signatures.append(signature)
                    tensors.extend(children)
                return (type(value).__name__, signatures), tensors
            return ("other", type(value).__name__), []

        def instrument(module: Any) -> None:
            pending: list[tuple[Any, list[Any]]] = []

            def before(_module: Any, _inputs: Any, output: Any) -> None:
                pending.append(capture(output))

            def after(_module: Any, _inputs: Any, output: Any) -> None:
                if not pending:
                    result["finite"] = False
                    return
                signature, originals = pending.pop()
                current_signature, current = capture(output)
                if (
                    not originals
                    or signature != current_signature
                    or len(originals) != len(current)
                ):
                    result["finite"] = False
                    return
                result["observed_calls"] += 1
                changed = False
                for original, observed in zip(originals, current, strict=True):
                    if (
                        original.shape != observed.shape
                        or original.dtype != observed.dtype
                        or original.device != observed.device
                    ):
                        result["finite"] = False
                        changed = True
                        continue
                    if not bool(torch.isfinite(original).all().item()) or not bool(
                        torch.isfinite(observed).all().item()
                    ):
                        result["finite"] = False
                    changed = changed or not torch.equal(original, observed)
                result["changed_calls"] += int(changed)

            handles.append(module.register_forward_hook(before, prepend=True))
            handles.append(module.register_forward_hook(after))

        try:
            for module in targets:
                instrument(module)
            observed = self.generate(prompt, max_new_tokens)
            result["finite"] = result["finite"] and observed["finite_logits"] is True
            return result
        finally:
            for handle in reversed(handles):
                handle.remove()

    def generate(self, prompt: str, max_new_tokens: int) -> dict[str, Any]:
        """Run fixed-length greedy decoding; timings include hook and cache execution.

        Token IDs remain an internal comparison value. The outer runner exports
        only checks and timing aggregates. EOS does not shorten this benchmark.
        Timing starts after tokenization and the initial input-device transfer.
        """
        if self.model is None or self._tokenizer is None:
            raise RuntimeError("The model engine is closed")
        if not isinstance(prompt, str) or not prompt or len(prompt.encode("utf-8")) > 16384:
            raise ValueError("Use a nonempty synthetic prompt of at most 16384 UTF-8 bytes")
        if type(max_new_tokens) is not int or not 1 <= max_new_tokens <= 256:
            raise ValueError("max_new_tokens must be between 1 and 256")
        torch = self._torch
        encoded = self._tokenizer(prompt, return_tensors="pt")
        input_ids = encoded["input_ids"].to(self._device)
        if input_ids.ndim != 2 or input_ids.shape[0] != 1 or not input_ids.shape[1]:
            raise ValueError("The benchmark requires one nonempty tokenized sequence")
        attention_mask = encoded.get("attention_mask", torch.ones_like(input_ids)).to(self._device)
        positions = getattr(self.model.config, "max_position_embeddings", None)
        if type(positions) is int and input_ids.shape[1] + max_new_tokens > positions:
            raise ValueError("The requested generation exceeds the model context window")
        tokens: list[int] = []
        past = None
        finite = True
        ttft_ms = 0.0
        self._synchronize()
        started = time.perf_counter()
        with torch.inference_mode():
            for _index in range(max_new_tokens):
                result = self.model(
                    input_ids=input_ids,
                    attention_mask=attention_mask,
                    past_key_values=past,
                    use_cache=True,
                    return_dict=True,
                )
                logits = result.logits[:, -1, :]
                if not bool(torch.isfinite(logits).all().item()):
                    finite = False
                    break
                next_token = torch.argmax(logits, dim=-1, keepdim=True)
                # .item() ensures each emitted token is actually available on the CPU.
                tokens.append(int(next_token.item()))
                self._synchronize()
                if len(tokens) == 1:
                    ttft_ms = (time.perf_counter() - started) * 1000
                past = result.past_key_values
                if past is None:
                    raise RuntimeError("The model did not return the requested inference cache")
                input_ids = next_token
                attention_mask = torch.cat((attention_mask, torch.ones_like(next_token)), dim=-1)
        self._synchronize()
        wall_ms = (time.perf_counter() - started) * 1000
        if not math.isfinite(wall_ms) or wall_ms <= 0:
            raise RuntimeError("The model benchmark clock produced an invalid duration")
        return {
            "token_ids": tuple(tokens),
            "wall_ms": wall_ms,
            "ttft_ms": ttft_ms,
            "tokens_per_second": len(tokens) * 1000 / wall_ms,
            "finite_logits": finite,
            "generated_tokens": len(tokens),
            "dtype": str(self.model.dtype).removeprefix("torch."),
            "device": str(self.model.device),
            "provenance": self.provenance,
        }

    def close(self) -> None:
        """Drop owned model references without clearing shared device caches."""
        self.model = None
        self._tokenizer = None


class _SmokeTokenizer:
    def __init__(self, torch: Any) -> None:
        self._torch = torch

    def __call__(self, prompt: str, return_tensors: str) -> dict[str, Any]:
        if return_tensors != "pt":
            raise ValueError("The smoke tokenizer supports Torch tensors")
        values = [1, *(2 + value % 62 for value in prompt.encode("utf-8")[:64])]
        ids = self._torch.tensor([values], dtype=self._torch.long)
        return {"input_ids": ids, "attention_mask": self._torch.ones_like(ids)}


def make_smoke_engine(architecture: str, seed: int = 7, device: str = "cpu") -> TransformersEngine:
    """Create a tiny genuine model for engine checks, without downloading weights.

    These random initializations exercise execution mechanics. They are not
    pretrained models or EigenScope projection profiles.
    """
    if architecture not in {"gpt2", "llama"}:
        raise ValueError("The smoke architecture must be gpt2 or llama")
    if type(seed) is not int or not 0 <= seed <= 4294967295:
        raise ValueError("seed must be an unsigned 32-bit integer")
    torch, transformers = _dependencies()
    parsed_device = _validate_device(torch, device)
    # Fork the RNG so constructing test models does not change a caller's RNG stream.
    with torch.random.fork_rng(devices=[]):
        torch.random.default_generator.manual_seed(seed)
        if architecture == "gpt2":
            config = transformers.GPT2Config(
                vocab_size=64,
                n_positions=128,
                n_ctx=128,
                n_embd=32,
                n_layer=2,
                n_head=4,
                resid_pdrop=0.0,
                embd_pdrop=0.0,
                attn_pdrop=0.0,
            )
            model = transformers.GPT2LMHeadModel(config)
        else:
            config = transformers.LlamaConfig(
                vocab_size=64,
                hidden_size=32,
                intermediate_size=64,
                num_hidden_layers=2,
                num_attention_heads=4,
                num_key_value_heads=2,
                max_position_embeddings=128,
                attention_dropout=0.0,
            )
            model = transformers.LlamaForCausalLM(config)
    model.eval()
    digest = hashlib.sha256(json.dumps(config.to_dict(), sort_keys=True).encode())
    for name, tensor in sorted(model.state_dict().items()):
        digest.update(name.encode())
        digest.update(tensor.detach().cpu().contiguous().view(torch.uint8).numpy().tobytes())
    engine = TransformersEngine.__new__(TransformersEngine)
    engine._torch = torch
    engine._device = parsed_device
    engine._tokenizer = _SmokeTokenizer(torch)
    engine.model = model.to(parsed_device)
    engine.identity = engine._identity("sha256:" + digest.hexdigest())
    engine.provenance = "random_initialized"
    return engine
