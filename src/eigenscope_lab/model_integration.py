"""Real-model integration orchestration with a small, explicit SDK connector contract.

The runner owns measurements and result validation. The configured connector
owns model detection, signed profile provisioning and the production hook.
Private profile material and generated text never enter the result schema.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import math
import re
import statistics
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .reporting import distribution_version, report

DEFAULT_PROMPT = "Complete this synthetic laboratory sequence: one, two,"
_DIGEST = re.compile(r"[a-f0-9]{64}")
_REVISION = re.compile(r"[a-f0-9]{40}")
_LABEL = re.compile(r"[a-z][a-z0-9-]{0,47}")
_BUDGETS = {"max_projection_p95_ms", "max_wall_overhead_pct", "max_ttft_overhead_pct"}


class ModelContractError(ValueError):
    """An observed integration response violates the public runner contract."""


@contextlib.contextmanager
def _quiet():
    # Third-party loaders/connectors must not spill paths, credentials or raw
    # exception bodies into CLI JSON. The discarded buffers are never exported.
    class Discard(io.TextIOBase):
        def write(self, value):
            return len(value)

    with contextlib.redirect_stdout(Discard()), contextlib.redirect_stderr(Discard()):
        yield


def _integer(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _number(value: Any, *, positive: bool = False) -> bool:
    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and (value > 0 if positive else value >= 0)
    )


def _validate_cases(cases: Any) -> list[dict[str, Any]]:
    if not isinstance(cases, list) or not 1 <= len(cases) <= 8:
        raise ValueError("Provide between one and eight model entries")
    labels: set[str] = set()
    normalized = []
    for case in cases:
        if not isinstance(case, dict) or set(case) - {
            "id",
            "reference",
            "revision",
            "prompt",
            "max_new_tokens",
            "dtype",
            "attn_implementation",
        }:
            raise ValueError("Model entries contain unsupported fields")
        label, reference = case.get("id"), case.get("reference")
        if not isinstance(label, str) or not _LABEL.fullmatch(label) or label in labels:
            raise ValueError("Model IDs must be unique short lowercase labels")
        if (
            not isinstance(reference, str)
            or not reference
            or len(reference) > 1024
            or any(ord(c) < 32 for c in reference)
            or "://" in reference
            or "?" in reference
            or "#" in reference
        ):
            raise ValueError("Use a checkpoint directory or model repository ID")
        local = Path(reference).expanduser().is_dir() or reference.startswith(
            ("/", "./", "../", "~")
        )
        revision = case.get("revision")
        if not local and (not isinstance(revision, str) or not _REVISION.fullmatch(revision)):
            raise ValueError("Remote checkpoints require an immutable 40-character commit revision")
        if (
            local
            and revision is not None
            and (
                not isinstance(revision, str)
                or not re.fullmatch(r"(?:sha256:)?[a-f0-9]{64}", revision)
            )
        ):
            raise ValueError("Local checkpoint revisions must be immutable digests")
        prompt = case.get("prompt", DEFAULT_PROMPT)
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4096:
            raise ValueError("Provide a nonempty synthetic prompt of at most 4096 characters")
        tokens = case.get("max_new_tokens", 8)
        if not _integer(tokens, 1, 64):
            raise ValueError("max_new_tokens must be an integer from one to 64")
        if "dtype" in case and (
            not isinstance(case["dtype"], str)
            or case["dtype"] not in {"auto", "float32", "float16", "bfloat16"}
        ):
            raise ValueError("dtype must be auto, float32, float16 or bfloat16")
        if "attn_implementation" in case and (
            not isinstance(case["attn_implementation"], str)
            or case["attn_implementation"] not in {"eager", "sdpa"}
        ):
            raise ValueError("attn_implementation must be eager or sdpa")
        labels.add(label)
        normalized.append({**case, "prompt": prompt, "max_new_tokens": tokens})
    return normalized


def load_matrix(path: str | Path) -> list[dict[str, Any]]:
    """Load bounded configuration without accepting profile material or secrets."""
    try:
        source = Path(path)
        if source.stat().st_size > 65536:
            raise ValueError("Model matrix is too large")
        value = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Could not read the model matrix JSON") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "models"}
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
    ):
        raise ValueError("Use model matrix schema version 1 with a models array")
    return _validate_cases(value["models"])


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON fields are not supported")
        value[key] = item
    return value


def load_connector(reference: str) -> Callable[..., Any]:
    """Import an explicitly selected local SDK adapter, never an inferred endpoint."""
    if not isinstance(reference, str) or not re.fullmatch(
        r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", reference
    ):
        raise ValueError("Use a connector reference in package.module:function form")
    module, name = reference.split(":")
    with _quiet():
        function = getattr(importlib.import_module(module), name)
    if not callable(function):
        raise ValueError("The selected connector must be callable")
    return function


def _check(row: dict[str, Any], name: str, passed: bool) -> None:
    row["checks"].append({"name": name, "passed": passed is True})


def _stats(values: list[float]) -> dict[str, Any]:
    if not values or any(not _number(x) for x in values):
        raise ModelContractError("Invalid timing samples")
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "p50": round(statistics.median(ordered), 6),
        "p95": round(ordered[math.ceil(0.95 * len(ordered)) - 1], 6),
        "p99": round(ordered[math.ceil(0.99 * len(ordered)) - 1], 6),
        "min": round(ordered[0], 6),
        "max": round(ordered[-1], 6),
    }


def _generation(value: Any, tokens: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelContractError("Missing inference observation")
    ids = value.get("token_ids")
    if (
        not isinstance(ids, (list, tuple))
        or len(ids) != tokens
        or any(not _integer(x, 0, 2**31 - 1) for x in ids)
        or value.get("finite_logits") is not True
        or not _integer(value.get("generated_tokens"), tokens, tokens)
        or not _number(value.get("wall_ms"), positive=True)
        or not _number(value.get("ttft_ms"), positive=True)
        or value["ttft_ms"] > value["wall_ms"]
        or not _number(value.get("tokens_per_second"), positive=True)
        or not math.isclose(
            value["tokens_per_second"], tokens * 1000 / value["wall_ms"], rel_tol=1e-6
        )
    ):
        raise ModelContractError("Invalid inference observation")
    # Store only known values in process memory; token IDs are never serialized.
    observed = {
        name: value[name]
        for name in ("token_ids", "wall_ms", "ttft_ms", "tokens_per_second", "generated_tokens")
    }
    observed["token_ids"] = tuple(value["token_ids"])
    if "dtype" in value:
        if not isinstance(value["dtype"], str) or value["dtype"] not in {
            "float16",
            "bfloat16",
            "float32",
            "float64",
        }:
            raise ModelContractError("Invalid inference precision observation")
        observed["dtype"] = value["dtype"]
    if "attn_implementation" in value:
        if not isinstance(value["attn_implementation"], str) or value[
            "attn_implementation"
        ] not in {"eager", "sdpa", "flash_attention_2", "flash_attention_3", "flex_attention"}:
            raise ModelContractError("Invalid attention implementation observation")
        observed["attn_implementation"] = value["attn_implementation"]
    return observed


def _metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    result = {
        "wall_ms": _stats([x["wall_ms"] for x in samples]),
        "ttft_ms": _stats([x["ttft_ms"] for x in samples]),
        "tokens_per_second": _stats([x["tokens_per_second"] for x in samples]),
        "generated_tokens": sum(x["generated_tokens"] for x in samples),
    }
    precisions = {sample.get("dtype") for sample in samples}
    if len(precisions) != 1:
        raise ModelContractError("Inference precision changed during measurement")
    precision = precisions.pop()
    if precision is not None:
        result["dtype"] = precision
    implementations = {sample.get("attn_implementation") for sample in samples}
    if len(implementations) != 1:
        raise ModelContractError("Inference attention implementation changed during measurement")
    implementation = implementations.pop()
    if implementation is not None:
        result["attn_implementation"] = implementation
    return result


def _snapshot(session: Any) -> dict[str, Any]:
    with _quiet():
        value = session.snapshot()
    names = ("hook_calls", "projected_calls", "changed_calls", "provision_count")
    if not isinstance(value, dict) or any(
        not _integer(value.get(name), 0, 2**53) for name in names
    ):
        raise ModelContractError("Invalid connector counters")
    timings = value.get("projection_ms")
    if (
        not isinstance(timings, list)
        or len(timings) > 100000
        or any(not _number(x) for x in timings)
        or value["changed_calls"] > value["projected_calls"]
        or value["projected_calls"] > value["hook_calls"]
    ):
        raise ModelContractError("Invalid projection observations")
    return {**{key: value[key] for key in names}, "projection_ms": list(timings)}


def _receipt(value: Any, identity: dict[str, Any], *, refresh: bool) -> str:
    expected_events = ["detected", "cache_miss" if refresh else "cache_hit", "verified", "loaded"]
    if (
        not isinstance(value, dict)
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or any(value.get(key) != identity[key] for key in identity)
        or type(value.get("hidden_size")) is not int
        or value.get("signature_verified") is not True
        or not isinstance(value.get("profile_digest"), str)
        or not _DIGEST.fullmatch(value["profile_digest"])
        or value.get("events") != expected_events
    ):
        raise ModelContractError("Profile identity, verification or preparation mismatch")
    return value["profile_digest"]


def _error(row: dict[str, Any], exc: Exception) -> None:
    if isinstance(exc, (ImportError, ModuleNotFoundError, NotImplementedError, FileNotFoundError)):
        if not any(check["passed"] is False for check in row["checks"]):
            row["status"] = "BLOCKED"
        row["reason"] = "Install the model engine and compatible automatic-profile connector"
    elif isinstance(exc, ModelContractError):
        _check(row, "integration_contract", False)
    elif type(exc).__module__.startswith("eigenscope_sdk"):
        _check(row, "sdk_connection_or_inference_completed", False)
    else:
        row["status"] = "ERROR"
        row["reason"] = "Model integration execution failed; inspect local connector diagnostics"


def _identity(engine: Any) -> dict[str, Any]:
    value = engine.identity
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("model_type"), str)
        or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value["model_type"])
        or not isinstance(value.get("model_revision"), str)
        or not re.fullmatch(r"(?:[a-f0-9]{40}|sha256:[a-f0-9]{64})", value["model_revision"])
        or not _integer(value.get("hidden_size"), 1, 1000000)
    ):
        raise ModelContractError("Model identity could not be established")
    return {key: value[key] for key in ("model_type", "model_revision", "hidden_size")}


def _observe(engine: Any, case: dict[str, Any]) -> dict[str, Any]:
    with _quiet():
        result = engine.generate(case["prompt"], case["max_new_tokens"])
    return _generation(result, case["max_new_tokens"])


def _audit_modes(engine: Any, session: Any, case: dict[str, Any], row: dict[str, Any]) -> None:
    """Observe actual hook outputs outside the timed generation population."""
    if not hasattr(engine, "audit_intervention"):
        row["activation_observation"] = "connector_counters"
        return
    row["activation_observation"] = "native_hook_validation_probes"
    before = _snapshot(session)
    for mode in ("identity", "protected"):
        with _quiet():
            session.set_mode(mode)
            observed = engine.audit_intervention(case["prompt"], case["max_new_tokens"])
        if (
            not isinstance(observed, dict)
            or not _integer(observed.get("observed_calls"), 0, 2**53)
            or not _integer(observed.get("changed_calls"), 0, 2**53)
            or observed["changed_calls"] > observed["observed_calls"]
        ):
            raise ModelContractError("Invalid native hook observations")
        _check(row, f"{mode}_native_hook_observed", observed["observed_calls"] > 0)
        _check(row, f"{mode}_native_hook_finite", observed.get("finite") is True)
        _check(
            row,
            f"{mode}_native_hook_effect",
            observed["changed_calls"] == 0 if mode == "identity" else observed["changed_calls"] > 0,
        )
    after = _snapshot(session)
    _check(
        row,
        "no_provisioning_during_validation_probes",
        before["provision_count"] == after["provision_count"],
    )


def _session_samples(
    engine: Any,
    session: Any,
    case: dict[str, Any],
    row: dict[str, Any],
    baseline: list[dict[str, Any]],
    repeat: int,
    warmup: int,
    prepared: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[float]]:
    modes: dict[str, list[dict[str, Any]]] = {"identity": [], "protected": []}
    projection_ms: list[float] = []
    previous = prepared
    for mode in modes:
        with _quiet():
            session.set_mode(mode)
        for sample in range(warmup + repeat):
            before = _snapshot(session)
            _check(
                row, f"{mode}_sample_{sample}_counters_preserved_between_calls", before == previous
            )
            observed = _observe(engine, case)
            _check(
                row,
                f"{mode}_sample_{sample}_precision_preserved",
                observed.get("dtype") == baseline[0].get("dtype"),
            )
            _check(
                row,
                f"{mode}_sample_{sample}_attention_implementation_preserved",
                observed.get("attn_implementation") == baseline[0].get("attn_implementation"),
            )
            after = _snapshot(session)
            monotonic = (
                all(
                    after[key] >= before[key]
                    for key in ("hook_calls", "projected_calls", "changed_calls", "provision_count")
                )
                and after["projection_ms"][: len(before["projection_ms"])]
                == before["projection_ms"]
            )
            _check(row, f"{mode}_sample_{sample}_counters_monotonic", monotonic)
            _check(
                row,
                f"{mode}_sample_{sample}_hook_and_projection_executed",
                after["hook_calls"] > before["hook_calls"]
                and after["projected_calls"] > before["projected_calls"],
            )
            _check(
                row,
                f"{mode}_sample_{sample}_no_provisioning_during_inference",
                after["provision_count"] == before["provision_count"],
            )
            if mode == "protected":
                _check(
                    row,
                    f"protected_sample_{sample}_activations_changed",
                    after["changed_calls"] > before["changed_calls"],
                )
            else:
                _check(
                    row,
                    f"identity_sample_{sample}_baseline_tokens_preserved",
                    tuple(observed["token_ids"]) == tuple(baseline[0]["token_ids"]),
                )
                _check(
                    row,
                    f"identity_sample_{sample}_activations_preserved",
                    after["changed_calls"] == before["changed_calls"],
                )
            fresh = after["projection_ms"][len(before["projection_ms"]) :]
            _check(
                row,
                f"{mode}_sample_{sample}_projection_measurements_present",
                bool(fresh) and len(fresh) == after["projected_calls"] - before["projected_calls"],
            )
            if sample >= warmup:
                modes[mode].append(observed)
                if mode == "protected":
                    projection_ms.extend(fresh)
            previous = after
    row["identity_inference"] = _metrics(modes["identity"])
    return modes["protected"], projection_ms


def run_model_matrix(
    cases: list[dict[str, Any]],
    *,
    connector: Callable[..., Any] | None = None,
    engine_factory: Callable[..., Any] | None = None,
    repeat: int = 3,
    warmup: int = 1,
    seed: int = 7,
    device: str = "cpu",
    allow_downloads: bool = False,
    budgets: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Exercise actual inference; a missing SDK connector remains a separate block."""
    cases = _validate_cases(cases)
    if not _integer(repeat, 1, 100) or not _integer(warmup, 0, 10):
        raise ValueError("repeat must be 1..100 and warmup 0..10")
    if not _integer(seed, 0, 4294967295) or type(allow_downloads) is not bool:
        raise ValueError("Provide an unsigned 32-bit seed and boolean download setting")
    if not isinstance(device, str) or not re.fullmatch(r"cpu|mps|cuda(?::[0-9]{1,2})?", device):
        raise ValueError("Choose cpu, mps or a CUDA device")
    budgets = {} if budgets is None else budgets
    if (
        not isinstance(budgets, dict)
        or set(budgets) - _BUDGETS
        or any(not _number(value, positive=True) for value in budgets.values())
    ):
        raise ValueError("Latency budgets must be finite positive numbers with supported names")
    if engine_factory is None:
        from .model_engine import TransformersEngine

        def engine_factory(case):
            return TransformersEngine(
                case["reference"],
                case.get("revision"),
                device=device,
                allow_downloads=allow_downloads,
                seed=seed,
                **({"dtype": case["dtype"]} if "dtype" in case else {}),
                **(
                    {"attn_implementation": case["attn_implementation"]}
                    if "attn_implementation" in case
                    else {}
                ),
            )

    suites: list[dict[str, Any]] = []
    used_profiles: dict[str, dict[str, Any]] = {}
    for case in cases:
        baseline_row: dict[str, Any] = {"suite": f"model:{case['id']}:baseline", "checks": []}
        row: dict[str, Any] = {"suite": f"model:{case['id']}:integration", "checks": []}
        suites.extend([baseline_row, row])
        engine = None
        try:
            with _quiet():
                engine = engine_factory(case)
            identity = _identity(engine)
            identity_digest = hashlib.sha256(
                json.dumps(identity, sort_keys=True).encode()
            ).hexdigest()
            baseline_row["model_fingerprint"] = identity_digest
            row["model_fingerprint"] = identity_digest
            before_hooks = engine.hook_count()
            baseline = [_observe(engine, case) for _ in range(warmup + repeat)][warmup:]
            _check(baseline_row, "finite_actual_generation", True)
            _check(
                baseline_row,
                "deterministic_greedy_baseline",
                all(tuple(x["token_ids"]) == tuple(baseline[0]["token_ids"]) for x in baseline),
            )
            baseline_row["inference"] = _metrics(baseline)
            if connector is None:
                row["status"] = "BLOCKED"
                row["reason"] = (
                    "Configure an SDK connector that detects and provisions model profiles"
                )
                continue
            setup_times: list[float] = []
            profile_digests: list[str] = []
            protected: list[dict[str, Any]] = []
            projections: list[float] = []
            # Never create protected profiles inside a checkout, even if TMPDIR
            # was configured to point into the project.
            checkouts = {Path(__file__).resolve().parents[2]}
            working = Path.cwd().resolve()
            for candidate in (working, *working.parents):
                if (candidate / ".git").exists() or (candidate / "pyproject.toml").is_file():
                    checkouts.add(candidate)
                    break
            if any(
                Path(tempfile.gettempdir()).resolve().is_relative_to(path) for path in checkouts
            ):
                raise ModelContractError("Choose a private temporary cache outside the checkout")
            with tempfile.TemporaryDirectory(prefix="eigenscope-model-profile-") as cache:
                cache_dir = Path(cache)
                for refresh in (True, False):
                    session = None
                    try:
                        start = time.perf_counter()
                        with _quiet():
                            session = connector(engine.model, cache_dir=cache_dir, refresh=refresh)
                        setup_times.append((time.perf_counter() - start) * 1000)
                        digest = _receipt(session.receipt, identity, refresh=refresh)
                        previous = used_profiles.get(digest)
                        _check(
                            row,
                            "profile_matches_model_identity",
                            previous is None or previous == identity,
                        )
                        used_profiles[digest] = identity
                        profile_digests.append(digest)
                        _check(row, "hook_attached", engine.hook_count() > before_hooks)
                        prepared = _snapshot(session)
                        _check(
                            row,
                            "fresh_or_cached_provision_count",
                            prepared["provision_count"] == (1 if refresh else 0),
                        )
                        _audit_modes(engine, session, case, row)
                        after_audit = _snapshot(session)
                        _check(
                            row,
                            "preparation_counters_preserved",
                            all(
                                after_audit[key] >= prepared[key]
                                for key in (
                                    "hook_calls",
                                    "projected_calls",
                                    "changed_calls",
                                    "provision_count",
                                )
                            )
                            and after_audit["projection_ms"][: len(prepared["projection_ms"])]
                            == prepared["projection_ms"],
                        )
                        prepared = after_audit
                        values, timings = _session_samples(
                            engine,
                            session,
                            case,
                            row,
                            baseline,
                            repeat,
                            warmup,
                            prepared,
                        )
                        if not refresh:
                            protected, projections = values, timings
                    finally:
                        if session is not None:
                            for attempt in range(2):
                                try:
                                    with _quiet():
                                        session.close()
                                    _check(row, f"session_cleanup_{attempt}_completed", True)
                                except Exception:
                                    _check(row, f"session_cleanup_{attempt}_completed", False)
                        _check(
                            row, "hooks_removed_after_session", engine.hook_count() == before_hooks
                        )
                        session = None
            _check(row, "cold_and_cached_profile_identity_stable", len(set(profile_digests)) == 1)
            row["setup_ms"] = {"cold": round(setup_times[0], 6), "cached": round(setup_times[1], 6)}
            row["protected_inference"] = _metrics(protected)
            row["projection_ms"] = _stats(projections)
            wall = statistics.median([x["wall_ms"] for x in protected])
            baseline_wall = statistics.median([x["wall_ms"] for x in baseline])
            ttft = statistics.median([x["ttft_ms"] for x in protected])
            baseline_ttft = statistics.median([x["ttft_ms"] for x in baseline])
            overhead = {
                "wall_pct": round(100 * (wall / baseline_wall - 1), 6),
                "ttft_pct": round(100 * (ttft / baseline_ttft - 1), 6),
            }
            row["overhead"] = overhead
            row["budgets"] = dict(budgets)
            measured = {
                "max_projection_p95_ms": row["projection_ms"]["p95"],
                "max_wall_overhead_pct": overhead["wall_pct"],
                "max_ttft_overhead_pct": overhead["ttft_pct"],
            }
            for name, limit in budgets.items():
                _check(row, f"budget_{name}", measured[name] <= limit)
        except Exception as exc:
            target = baseline_row if not baseline_row["checks"] else row
            _error(target, exc)
            if target is baseline_row:
                row["status"] = "BLOCKED"
                row["reason"] = "Complete baseline inference before testing the integration"
        finally:
            if engine is not None:
                try:
                    with _quiet():
                        engine.close()
                except Exception:
                    _check(row, "engine_cleanup_completed", False)
                    row.pop("status", None)
    result = report(suites, "model-integration", seed)
    result["measurement"] = {
        "repeat": repeat,
        "warmup": warmup,
        "device": device,
        "percentiles": "nearest rank p95/p99; median p50; per model, mode and boundary",
        "profile_evidence": "SDK connector receipts and cumulative projection observations",
        "inference_boundary": "After tokenization and initial device transfer, through completed greedy generation",
        "validation_probes_excluded_from_latency": True,
    }
    result["dependencies"] = {
        name: distribution_version(name) for name in ("torch", "transformers", "eigenscope-sdk")
    }
    return result


def run_engine_smoke(
    *, repeat: int = 3, warmup: int = 1, seed: int = 7, device: str = "cpu"
) -> dict[str, Any]:
    """Run genuine local neural inference without representing it as SDK evidence."""
    from .model_engine import make_smoke_engine

    cases = [{"id": name, "reference": f"/generated-smoke/{name}"} for name in ("gpt2", "llama")]
    result = run_model_matrix(
        cases,
        engine_factory=lambda case: make_smoke_engine(case["id"], seed=seed, device=device),
        repeat=repeat,
        warmup=warmup,
        seed=seed,
        device=device,
    )
    baseline = [row for row in result["suites"] if row["suite"].endswith(":baseline")]
    smoke = report(baseline, "model-engine-smoke", seed)
    smoke["measurement"] = result["measurement"]
    smoke["dependencies"] = result["dependencies"]
    smoke["model_source"] = "locally_initialized_gpt2_and_llama"
    smoke["eigenscope_projection_tested"] = False
    return smoke
