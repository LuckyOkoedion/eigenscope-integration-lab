"""Unit controls for checkpoint semantic orchestration; no model/service claims.

The evaluator uses natural entity-bearing prompts. These deterministic outputs
exercise the runner's pass/fail wiring independently from model inference.
"""

from __future__ import annotations

import json

import pytest
from test_model_integration import FixtureConnection, FixtureEngine, model_case

from eigenscope_lab.model_integration import run_model_matrix
from eigenscope_lab.model_privacy import MODEL_PRIVACY_CASES


def completed_output(case, *, protected: bool) -> str:
    if case.format == "json":
        values = {
            field: f"[ENTITY_{index + 1}]" if protected else entity
            for index, (field, entity) in enumerate(
                zip(case.fields, case.sensitive_values, strict=True)
            )
        }
        return json.dumps({**values, **case.public_values}, ensure_ascii=False)
    entities = " ".join(case.sensitive_values) if not protected else "[ENTITY_1] [ENTITY_2]"
    return f"- Python onboarding {entities}\n- HTTP 200 response\n- 45 ms maximum duration"


class SemanticEngine(FixtureEngine):
    provenance = "checkpoint"

    def __init__(self, case, scenario="pass"):
        super().__init__(case)
        self.scenario = scenario
        self.semantic_calls = []
        if scenario == "unsupported":
            self.generate_text = None

    def generate_text(self, prompt, max_new_tokens=192):
        assert self.connection is not None
        assert max_new_tokens == 192
        self.semantic_calls.append((self.connection.mode, prompt, max_new_tokens))
        case = next(case for case in MODEL_PRIVACY_CASES if case.prompt == prompt)
        protected = self.connection.mode == "protected"
        if self.scenario == "exception":
            raise RuntimeError("SYNTHETIC_RAW_SEMANTIC_EXCEPTION")
        if self.scenario == "no-projection":
            self.connection.counters["hook_calls"] += 1
        else:
            self.connection.observe(protected)
        if self.scenario == "late-provision":
            self.connection.counters["provision_count"] += 1
        text = completed_output(case, protected=protected)
        if self.scenario == "baseline-witness" and not protected:
            text = completed_output(case, protected=True)
        if self.scenario == "leak" and protected:
            text = completed_output(case, protected=False)
        if self.scenario == "public-loss" and protected:
            if case.format == "json":
                body = json.loads(text)
                key, value = next(iter(case.public_values.items()))
                body[key] = 0 if type(value) is int else "changed public value"
                text = json.dumps(body)
            else:
                text = text.replace("Python", "changed language")
        if self.scenario == "format-loss" and protected:
            if case.format == "json":
                body = json.loads(text)
                body.pop(case.fields[0])
                text = json.dumps(body)
            else:
                text += "\n- A fourth bullet"
        if self.scenario == "empty":
            text = ""
        observed = {
            "generated_text": text,
            "finite_logits": self.scenario != "nonfinite",
            "generated_tokens": 32,
            "raw_response": "SYNTHETIC_RAW_RESPONSE_MUST_NOT_EXPORT",
        }
        if self.scenario == "missing-text":
            observed.pop("generated_text")
        if self.scenario == "zero-tokens":
            observed["generated_tokens"] = 0
        if self.scenario == "too-many-tokens":
            observed["generated_tokens"] = 193
        if self.scenario == "boolean-tokens":
            observed["generated_tokens"] = True
        return observed


def execute_semantics(*, scenario="pass", connection_options=None, connect=True, repeat=1):
    engines = []
    connections = []

    def factory(case):
        engine = SemanticEngine(case, scenario)
        engines.append(engine)
        return engine

    def connector(model, *, cache_dir, refresh):
        connection = FixtureConnection(model, cache_dir, refresh, **(connection_options or {}))
        connections.append(connection)
        return connection

    result = run_model_matrix(
        [model_case()],
        connector=connector if connect else None,
        engine_factory=factory,
        repeat=repeat,
        warmup=0,
    )
    rows = {row["suite"].rsplit(":", 1)[1]: row for row in result["suites"]}
    return result, rows, engines, connections


def test_checkpoint_requires_natural_generated_output_checks_before_integration_pass():
    result, rows, engines, connections = execute_semantics()
    assert rows["baseline"]["status"] == "PASS"
    assert rows["integration"]["status"] == "PASS"
    assert rows["privacy"]["status"] == "PASS"
    assert rows["privacy"]["semantic_cases"] == len(MODEL_PRIVACY_CASES) == 4
    calls = engines[0].semantic_calls
    assert len(calls) == 8
    assert [mode for mode, _prompt, _tokens in calls] == ["identity", "protected"] * 4
    assert {prompt for _mode, prompt, _tokens in calls} == {
        case.prompt for case in MODEL_PRIVACY_CASES
    }
    assert engines[0].closed and engines[0].hooks == 0
    assert len(connections) == 2 and all(connection.closed for connection in connections)
    assert all(connection.close_calls == 2 for connection in connections)
    assert result["summary"] == {"PASS": 3, "FAIL": 0, "BLOCKED": 0, "ERROR": 0}


def test_protected_entity_leak_is_p0_and_fails_the_integration():
    _result, rows, engines, connections = execute_semantics(scenario="leak")
    assert rows["privacy"]["status"] == "FAIL"
    assert rows["privacy"]["severity"] == "P0"
    assert rows["privacy"]["reason_code"] == "ZERO_TAG_PRIVACY_LEAK"
    assert rows["integration"]["status"] == "FAIL"
    assert engines[0].closed and all(connection.closed for connection in connections)


def test_baseline_must_expose_entities_before_protection_is_adjudicated():
    _result, rows, _engines, _connections = execute_semantics(scenario="baseline-witness")
    assert rows["privacy"]["status"] == "FAIL"
    assert rows["integration"]["status"] == "FAIL"
    assert rows["privacy"].get("severity") != "P0"
    assert rows["privacy"].get("reason_code") != "ZERO_TAG_PRIVACY_LEAK"
    assert any(
        check["name"].endswith(":identity:baseline_sensitive_entities_observable")
        and check["passed"] is False
        for check in rows["privacy"]["checks"]
    )


@pytest.mark.parametrize(
    "scenario",
    [
        "public-loss",
        "format-loss",
        "empty",
        "nonfinite",
        "missing-text",
        "zero-tokens",
        "too-many-tokens",
        "boolean-tokens",
    ],
)
def test_missing_public_values_task_structure_or_valid_generation_cannot_pass(scenario):
    _result, rows, engines, connections = execute_semantics(scenario=scenario)
    assert rows["privacy"]["status"] in {"FAIL", "ERROR"}
    assert rows["integration"]["status"] == "FAIL"
    assert rows["privacy"].get("reason_code") != "ZERO_TAG_PRIVACY_LEAK"
    assert engines[0].closed and all(connection.closed for connection in connections)


@pytest.mark.parametrize("scenario", ["no-projection", "late-provision"])
def test_correct_decoded_text_does_not_replace_actual_projection_or_preparation_contract(scenario):
    _result, rows, engines, connections = execute_semantics(scenario=scenario)
    assert rows["privacy"]["status"] == "FAIL"
    assert rows["integration"]["status"] == "FAIL"
    assert engines[0].closed and all(connection.closed for connection in connections)
    suffix = (
        ":real_projection_executed" if scenario == "no-projection" else ":no_runtime_provisioning"
    )
    assert any(
        check["name"].endswith(suffix) and check["passed"] is False
        for check in rows["privacy"]["checks"]
    )


def test_missing_decoded_generation_is_blocked_without_a_fake_privacy_pass():
    _result, rows, engines, connections = execute_semantics(scenario="unsupported")
    assert rows["privacy"]["status"] == "BLOCKED"
    assert rows["integration"]["status"] == "FAIL"
    assert engines[0].closed and all(connection.closed for connection in connections)


@pytest.mark.parametrize("failure", ["no-connector", "bad-receipt"])
def test_unconnected_checkpoint_privacy_stays_blocked(failure):
    _result, rows, engines, connections = execute_semantics(
        connect=failure != "no-connector",
        connection_options={"receipt": {"signature_verified": False}}
        if failure == "bad-receipt"
        else None,
    )
    assert rows["privacy"]["status"] == "BLOCKED"
    assert rows["integration"]["status"] != "PASS"
    assert not engines[0].semantic_calls
    assert engines[0].closed and all(connection.closed for connection in connections)


@pytest.mark.parametrize("scenario", ["pass", "leak", "exception"])
def test_semantic_results_never_export_generated_text_entities_prompts_or_raw_details(scenario):
    result, _rows, engines, connections = execute_semantics(scenario=scenario)
    serialized = json.dumps(result)
    assert "generated_text" not in serialized
    assert "raw_response" not in serialized
    assert "SYNTHETIC_RAW" not in serialized
    for case in MODEL_PRIVACY_CASES:
        assert case.prompt not in serialized
        assert all(entity not in serialized for entity in case.sensitive_values)
    assert engines[0].closed and all(connection.closed for connection in connections)


def test_semantic_generations_are_excluded_from_latency_sample_populations():
    _result, rows, engines, _connections = execute_semantics(repeat=2)
    assert rows["integration"]["status"] == "PASS"
    assert len(engines[0].semantic_calls) == 8
    assert rows["baseline"]["inference"]["wall_ms"]["n"] == 2
    assert rows["integration"]["protected_inference"]["wall_ms"]["n"] == 2
    assert rows["integration"]["projection_ms"]["n"] == 2
