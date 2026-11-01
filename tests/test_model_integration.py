"""Model-runner contracts with synthetic adapters, without SDK or service claims.

These fixtures exercise attribution, lifecycle and report validation. Actual
model inference lives in the optional engine tests and the ``models`` command.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from eigenscope_lab.model_integration import load_matrix, run_model_matrix


def model_case(label: str = "model-a", revision: str = "a" * 40) -> dict[str, Any]:
    return {
        "id": label,
        "reference": f"example/{label}",
        "revision": revision,
        "prompt": "Return the next token in this synthetic sequence.",
        "max_new_tokens": 3,
    }


class FixtureEngine:
    def __init__(self, case: dict[str, Any], **options: Any):
        self.model = self
        self.identity = {
            "model_type": "architecture-a" if case["id"] == "model-a" else "architecture-b",
            "model_revision": case["revision"],
            "hidden_size": 32,
        }
        self.options = options
        self.connection: FixtureConnection | None = None
        self.hooks = 0
        self.closed = False
        self.generations = 0
        self.token_buffer = [1, 2, 3]

    def generate(self, prompt: str, max_new_tokens: int) -> dict[str, Any]:
        assert prompt and max_new_tokens == 3
        self.generations += 1
        if self.options.get("generate_error"):
            raise RuntimeError("SYNTHETIC_PRIVATE_GENERATION_DETAIL")
        protected = self.connection is not None and self.connection.mode == "protected"
        if self.connection is not None:
            self.connection.observe(protected)
        changed_identity = (
            self.options.get("changed_identity") and self.connection is not None and not protected
        )
        self.token_buffer[:] = [1, 2, 4 if protected or changed_identity else 3]
        return {
            "token_ids": (
                self.token_buffer
                if self.options.get("shared_token_buffer")
                else list(self.token_buffer)
            ),
            "wall_ms": self.options.get("wall_ms", 12.0 if protected else 10.0),
            "ttft_ms": self.options.get("ttft_ms", 6.0 if protected else 5.0),
            "tokens_per_second": 250.0 if protected else 300.0,
            "finite_logits": self.options.get("finite_logits", True),
            "generated_tokens": 3,
        }

    def hook_count(self) -> int:
        return self.hooks

    def close(self) -> None:
        self.closed = True


class FixtureConnection:
    def __init__(
        self,
        engine: FixtureEngine,
        cache_dir: Path,
        refresh: bool,
        **options: Any,
    ):
        self.engine = engine
        self.options = options
        self.cache_dir = cache_dir
        self.mode = "identity"
        self.closed = False
        self.close_calls = 0
        self.snapshot_calls = 0
        self.receipt = {
            "schema_version": 1,
            **engine.identity,
            "profile_digest": hashlib.sha256(
                engine.identity["model_revision"].encode()
            ).hexdigest(),
            "signature_verified": True,
            "events": ["detected", "cache_miss" if refresh else "cache_hit", "verified", "loaded"],
        }
        self.receipt.update(options.get("receipt", {}))
        if options.get("missing_snapshot"):
            self.snapshot = None
        self.counters = {
            "hook_calls": 0,
            "projected_calls": 0,
            "changed_calls": 0,
            "provision_count": 1 if refresh else 0,
            "projection_ms": [],
        }
        engine.connection = self
        if not options.get("no_hook"):
            engine.hooks += 1

    def set_mode(self, mode: str) -> None:
        assert mode in {"identity", "protected"}
        self.mode = mode

    def observe(self, protected: bool) -> None:
        self.counters["hook_calls"] += 1
        if not self.options.get("no_projection"):
            self.counters["projected_calls"] += 1
        if (protected and not self.options.get("no_change")) or self.options.get("identity_change"):
            self.counters["changed_calls"] += 1
        if self.options.get("late_provision"):
            self.counters["provision_count"] += 1
        self.counters["projection_ms"].append(self.options.get("projection_ms", 0.25))
        if self.options.get("extra_projection_timing"):
            self.counters["projection_ms"].append(0.25)

    def snapshot(self) -> dict[str, Any]:
        self.snapshot_calls += 1
        if self.options.get("counter_regression") and self.snapshot_calls == 1:
            return {
                "hook_calls": 100,
                "projected_calls": 100,
                "changed_calls": 0,
                "provision_count": self.counters["provision_count"],
                "projection_ms": [0.25] * 100,
            }
        return {
            **self.counters,
            "projection_ms": list(self.counters["projection_ms"]),
            **self.options.get("snapshot", {}),
        }

    def close(self) -> None:
        self.close_calls += 1
        if self.closed:
            if self.options.get("non_idempotent_close"):
                raise RuntimeError("SYNTHETIC_PRIVATE_REPEATED_CLOSE_DETAIL")
            return
        self.closed = True
        self.engine.connection = None
        if not self.options.get("no_hook") and not self.options.get("leak_hook"):
            self.engine.hooks -= 1
        if self.options.get("close_error"):
            raise RuntimeError("SYNTHETIC_PRIVATE_CLEANUP_DETAIL")


def execute(
    *,
    cases: list[dict[str, Any]] | None = None,
    engine_options: dict[str, Any] | None = None,
    connection_options: dict[str, Any] | None = None,
    **kwargs: Any,
) -> tuple[dict[str, Any], list[FixtureEngine], list[FixtureConnection]]:
    engines: list[FixtureEngine] = []
    connections: list[FixtureConnection] = []

    def factory(case: dict[str, Any]) -> FixtureEngine:
        engine = FixtureEngine(case, **(engine_options or {}))
        engines.append(engine)
        return engine

    def connect(model: FixtureEngine, *, cache_dir: Path, refresh: bool) -> FixtureConnection:
        connection = FixtureConnection(model, cache_dir, refresh, **(connection_options or {}))
        connections.append(connection)
        return connection

    result = run_model_matrix(
        cases or [model_case()],
        connector=connect,
        engine_factory=factory,
        repeat=3,
        warmup=1,
        **kwargs,
    )
    return result, engines, connections


def integration_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in result["suites"] if row["suite"].endswith(":integration")]


def assert_integration_failed(result: dict[str, Any]) -> None:
    rows = integration_rows(result)
    assert rows
    assert all(row["status"] in {"FAIL", "ERROR"} for row in rows)


def write_matrix(tmp_path: Path, payload: Any) -> Path:
    destination = tmp_path / "matrix.json"
    destination.write_text(json.dumps(payload), encoding="utf-8")
    return destination


def test_matrix_loads_multiple_models_without_profile_or_credentials(tmp_path):
    cases = [model_case(), model_case("model-b", "b" * 40)]
    path = write_matrix(tmp_path, {"schema_version": 1, "models": cases})
    assert load_matrix(path) == cases


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"schema_version": True, "models": [model_case()]},
        {"schema_version": 2, "models": [model_case()]},
        {"schema_version": 1, "models": []},
        {"schema_version": 1, "models": [model_case()] * 2},
        {"schema_version": 1, "models": [model_case()], "api_key": "SYNTHETIC_PRIVATE_KEY"},
        {"schema_version": 1, "models": [{**model_case(), "profile": "private-basis"}]},
        {"schema_version": 1, "models": [{**model_case(), "revision": "main"}]},
        {"schema_version": 1, "models": [{**model_case(), "revision": "a" * 39}]},
        {"schema_version": 1, "models": [{**model_case(), "max_new_tokens": True}]},
        {"schema_version": 1, "models": [{**model_case(), "max_new_tokens": 0}]},
        {"schema_version": 1, "models": [{**model_case(), "max_new_tokens": 65}]},
        {"schema_version": 1, "models": [{**model_case(), "prompt": "x" * 4097}]},
        {"schema_version": 1, "models": [{**model_case(), "reference": "https://u:s@x"}]},
    ],
)
def test_invalid_matrix_rejected_without_echoing_values(tmp_path, payload):
    with pytest.raises(ValueError) as failure:
        load_matrix(write_matrix(tmp_path, payload))
    assert "SYNTHETIC_PRIVATE_KEY" not in str(failure.value)
    assert "private-basis" not in str(failure.value)


def test_duplicate_json_fields_and_oversized_matrix_are_rejected(tmp_path):
    destination = tmp_path / "matrix.json"
    destination.write_text('{"schema_version":1,"schema_version":1,"models":[]}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_matrix(destination)
    destination.write_text(" " * 65537, encoding="utf-8")
    with pytest.raises(ValueError):
        load_matrix(destination)


def test_two_architectures_prepare_twice_and_close_every_resource():
    cases = [model_case(), model_case("model-b", "b" * 40)]
    result, engines, connections = execute(cases=cases)
    assert len(integration_rows(result)) == 2
    assert all(row["status"] == "PASS" for row in integration_rows(result))
    assert len(connections) == 4
    assert all(engine.closed and engine.hooks == 0 for engine in engines)
    assert all(connection.closed for connection in connections)
    assert connections[0].cache_dir == connections[1].cache_dir
    assert connections[2].cache_dir == connections[3].cache_dir
    assert connections[0].cache_dir != connections[2].cache_dir
    assert all(not connection.cache_dir.exists() for connection in connections)
    assert all(not connection.cache_dir.is_relative_to(Path.cwd()) for connection in connections)
    assert all(str(connection.cache_dir) not in json.dumps(result) for connection in connections)


def test_warmup_and_mode_samples_are_kept_out_of_each_others_latency_population():
    result, _engines, _connections = execute()
    baseline = next(row for row in result["suites"] if row["suite"].endswith(":baseline"))
    integration = integration_rows(result)[0]
    assert baseline["inference"]["wall_ms"]["n"] == 3
    assert integration["identity_inference"]["wall_ms"]["n"] == 3
    assert integration["protected_inference"]["wall_ms"]["n"] == 3
    assert integration["protected_inference"]["wall_ms"]["p50"] == 12
    assert integration["protected_inference"]["wall_ms"]["p95"] == 12
    assert integration["protected_inference"]["wall_ms"]["p99"] == 12
    assert integration["projection_ms"]["n"] == 3
    assert integration["overhead"] == {"wall_pct": 20, "ttft_pct": 20}


def test_missing_connector_keeps_baseline_separate_from_blocked_integration():
    engines: list[FixtureEngine] = []

    def factory(case):
        engine = FixtureEngine(case)
        engines.append(engine)
        return engine

    result = run_model_matrix([model_case()], connector=None, engine_factory=factory)
    assert integration_rows(result)[0]["status"] == "BLOCKED"
    assert any(row["status"] == "PASS" for row in result["suites"])
    assert all(engine.closed for engine in engines)


def test_identity_mode_is_compared_with_unmodified_baseline_tokens():
    result, engines, connections = execute(engine_options={"changed_identity": True})
    assert_integration_failed(result)
    assert all(engine.closed for engine in engines)
    assert all(connection.closed for connection in connections)


def test_reused_token_buffers_cannot_rewrite_the_baseline_expectation():
    result, engines, connections = execute(
        engine_options={"changed_identity": True, "shared_token_buffer": True}
    )
    assert_integration_failed(result)
    assert all(engine.closed for engine in engines)
    assert all(connection.closed for connection in connections)


def test_identity_mode_must_preserve_activations_even_when_output_tokens_match():
    result, engines, connections = execute(connection_options={"identity_change": True})
    assert_integration_failed(result)
    assert all(engine.closed for engine in engines)
    assert all(connection.closed for connection in connections)


@pytest.mark.parametrize(
    "receipt",
    [
        {"model_type": "wrong-architecture"},
        {"model_revision": "b" * 40},
        {"hidden_size": 33},
        {"signature_verified": False},
        {"signature_verified": 1},
        {"profile_digest": "not-a-digest"},
        {"events": ["detected", "cache_hit", "verified", "loaded"]},
        {"events": ["detected", "cache_miss", "loaded"]},
    ],
)
def test_invalid_profile_receipt_cannot_pass(receipt):
    result, engines, connections = execute(connection_options={"receipt": receipt})
    assert_integration_failed(result)
    assert all(engine.closed for engine in engines)
    assert all(connection.closed for connection in connections)


@pytest.mark.parametrize(
    "options",
    [
        {"no_hook": True},
        {"no_projection": True},
        {"no_change": True},
        {"late_provision": True},
        {"leak_hook": True},
        {"projection_ms": float("nan")},
        {"projection_ms": -1},
        {"extra_projection_timing": True},
        {"counter_regression": True},
        {"missing_snapshot": True},
    ],
)
def test_noop_projection_late_provision_and_bad_timings_cannot_pass(options):
    result, engines, connections = execute(connection_options=options)
    assert_integration_failed(result)
    assert all(engine.closed for engine in engines)
    assert all(connection.closed for connection in connections)
    assert "NaN" not in json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "snapshot",
    [
        {"hook_calls": -1},
        {"hook_calls": True},
        {"projected_calls": -1},
        {"changed_calls": 1.5},
        {"provision_count": True},
        {"hook_calls": 0, "projected_calls": 1},
        {"projected_calls": 0, "changed_calls": 1},
    ],
)
def test_invalid_counter_types_cannot_be_treated_as_observed_projection(snapshot):
    result, engines, connections = execute(connection_options={"snapshot": snapshot})
    assert_integration_failed(result)
    assert all(engine.closed for engine in engines)
    assert all(connection.closed for connection in connections)


@pytest.mark.parametrize(
    "options",
    [
        {"wall_ms": float("nan")},
        {"ttft_ms": float("inf")},
        {"finite_logits": False},
        {"finite_logits": 1},
    ],
)
def test_invalid_inference_observations_cannot_be_reported_as_success(options):
    result, engines, _connections = execute(engine_options=options)
    assert result["summary"]["FAIL"] + result["summary"]["ERROR"] > 0
    assert all(engine.closed for engine in engines)
    json.dumps(result, allow_nan=False)


def test_error_details_and_unknown_receipt_fields_never_enter_results():
    result, _engines, _connections = execute(
        connection_options={"receipt": {"private_basis": "SYNTHETIC_PRIVATE_PROFILE"}}
    )
    assert "SYNTHETIC_PRIVATE_PROFILE" not in json.dumps(result)
    result, engines, _connections = execute(engine_options={"generate_error": True})
    assert "SYNTHETIC_PRIVATE_GENERATION_DETAIL" not in json.dumps(result)
    assert result["summary"]["ERROR"] > 0
    assert all(engine.closed for engine in engines)


def test_session_cleanup_failure_remains_visible_without_private_exception_text():
    result, engines, connections = execute(connection_options={"close_error": True})
    assert_integration_failed(result)
    assert all(engine.closed for engine in engines)
    assert all(connection.closed for connection in connections)
    assert "SYNTHETIC_PRIVATE_CLEANUP_DETAIL" not in json.dumps(result)


@pytest.mark.parametrize(
    "budgets",
    [
        {"max_projection_p95_ms": 0.1},
        {"max_wall_overhead_pct": 10},
        {"max_ttft_overhead_pct": 10},
    ],
)
def test_exceeded_performance_budgets_fail_the_integration(budgets):
    result, _engines, _connections = execute(budgets=budgets)
    assert_integration_failed(result)


def test_matching_architecture_dimensions_cannot_reuse_unrelated_model_profile():
    result, _engines, _connections = execute(
        cases=[model_case(), model_case("model-b", "b" * 40)],
        connection_options={"receipt": {"profile_digest": "f" * 64}},
    )
    rows = integration_rows(result)
    assert len(rows) == 2
    assert rows[1]["status"] in {"FAIL", "ERROR"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"repeat": 0},
        {"repeat": True},
        {"warmup": -1},
        {"budgets": {"max_projection_p95_ms": float("nan")}},
        {"budgets": {"max_wall_overhead_pct": True}},
        {"budgets": {"unknown": 1}},
        {"budgets": []},
        {"budgets": 0},
        {"budgets": ""},
    ],
)
def test_invalid_controls_prevent_engine_start(kwargs):
    def forbidden_factory(_case):
        pytest.fail("Invalid controls must be rejected before loading a model")

    with pytest.raises(ValueError):
        run_model_matrix([model_case()], engine_factory=forbidden_factory, **kwargs)
