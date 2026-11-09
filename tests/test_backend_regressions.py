"""Independent failure controls for browser results and live wire oracles."""

from __future__ import annotations

import copy
import json
import threading
from types import SimpleNamespace

import pytest

from eigenscope_lab import live, runner
from eigenscope_lab.live import ServiceResponseContractError
from eigenscope_lab.multimodal_live import (
    _modality_specs,
    _projection_checks,
    run_multimodal_live,
)
from eigenscope_lab.rag_live import (
    _evaluation_row,
    _retrieval_ids,
    _validate_receipt_envelope,
)
from eigenscope_lab.simulators.rag import DEFAULT_SIR_THRESHOLD, SEED_PASSAGES, SEED_QUERIES
from eigenscope_lab.web import LabSession, RunCancelled, _summarize_failed_checks


def test_scenario_runner_forwards_telemetry_without_requiring_it(monkeypatch):
    frames = []

    def observed(seed, on_frame):
        on_frame({"seed": seed})
        return {"checks": [{"name": "observed", "passed": True}]}

    monkeypatch.setattr(
        runner.importlib, "import_module", lambda _: SimpleNamespace(run_telecom=observed)
    )
    assert runner.run_scenario("telecom", 11, on_frame=frames.append)["checks"][0]["passed"]
    assert frames == [{"seed": 11}]
    monkeypatch.setattr(
        runner.importlib,
        "import_module",
        lambda _: SimpleNamespace(run_telecom=lambda seed: {"seed": seed}),
    )
    assert runner.run_scenario("telecom", 11, on_frame=frames.append) == {"seed": 11}


def test_live_dashboard_keeps_sdk_failure_when_running_local_fallback(monkeypatch):
    pytest.importorskip("httpx")
    pytest.importorskip("openai")
    pytest.importorskip("eigenscope_sdk")
    from eigenscope_lab.simulators import telecom

    frames = []

    def external(seed, on_frame=None):
        if on_frame:
            on_frame({"phase": "local-control", "seed": seed})
        return {"checks": [{"name": "local_control", "passed": True}], "metrics": {}}

    monkeypatch.setattr(telecom, "run_telecom", external)
    monkeypatch.setattr(
        live,
        "make_observed_client",
        lambda *_args, **_kwargs: SimpleNamespace(close=lambda: None),
    )
    monkeypatch.setattr(
        live,
        "_sdk_case",
        lambda *_args, **_kwargs: {
            "checks": [{"name": "workflow_completed", "passed": False, "http_status": 401}],
            "metrics": {},
            "scope": "synthetic orchestration control",
        },
    )
    _, runners = live.build_live_web_runners(
        base_url="https://service.example.invalid", key="synthetic-unit-test-key"
    )
    result = runners["telecom"](seed=11, on_frame=frames.append)
    assert frames == [{"phase": "local-control", "seed": 11}]
    assert result["checks"] == [
        {"name": "workflow_completed", "passed": False, "http_status": 401},
        {"name": "local_control", "passed": True},
    ]


def test_stop_checkpoint_prevents_the_next_live_step():
    started = threading.Event()
    release = threading.Event()
    calls = []

    def live_steps(seed, check_cancelled):
        calls.append("first-request")
        started.set()
        assert release.wait(timeout=3)
        check_cancelled()
        calls.append("next-request")
        return {"checks": [{"name": "workflow", "passed": True}]}

    session = LabSession({"rag": live_steps}, live_mode=True)
    try:
        session.start("rag", 7, 0)
        assert started.wait(timeout=3)
        session.cancel()
        release.set()
        session.worker.join(timeout=3)
        assert not session.worker.is_alive()
        assert session.snapshot()["status"] == "cancelled"
        assert calls == ["first-request"]
    finally:
        release.set()
        session.close()


def test_sdk_cancellation_still_cleans_its_owned_mapping():
    case = next(case for case in live.CASES if case.id == "coding")
    owned = set()
    scrubbed = []

    class Client:
        owned_tickets = owned

        def decompose(self, _prompt):
            owned.add("vlt_owned_cancellation_control")
            return {
                "status": "DECOMPOSED_OK",
                "task_core": "Review Python HTTP 200 response handling.",
                "vault_ticket": "vlt_owned_cancellation_control",
            }

        def scrub(self, ticket):
            scrubbed.append(ticket)

    def checkpoint():
        if owned:
            raise RunCancelled

    with pytest.raises(RunCancelled):
        live._sdk_case(
            case,
            client=Client(),
            provider=None,
            provider_server=None,
            seed=7,
            check_cancelled=checkpoint,
        )
    assert scrubbed == ["vlt_owned_cancellation_control"]
    assert not owned


def test_rag_cancellation_stops_before_the_next_http_request(monkeypatch):
    httpx = pytest.importorskip("httpx")
    from eigenscope_lab.rag_live import run_rag_live_http

    calls = []

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, url, *, headers, content):
            calls.append(url)
            request = json.loads(content)
            envelope = receipt_envelope()
            envelope["body"].update(doc_id=request["doc_id"], auxiliary_metadata={})
            return SimpleNamespace(status_code=200, json=lambda: envelope)

    def checkpoint():
        if calls:
            raise RunCancelled

    monkeypatch.setattr(httpx, "Client", Client)
    with pytest.raises(RunCancelled):
        run_rag_live_http(
            base_url="https://service.example.invalid", key="unit-test", check_cancelled=checkpoint
        )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "value",
    [
        {"checks": [{"passed": True}], "metrics": {"x": float("nan")}},
        {"checks": [{"passed": True}], "metrics": {"x": object()}},
        {"checks": ["not-a-check"]},
        ["not-an-object"],
    ],
)
def test_invalid_adapter_result_cannot_break_state_or_receive_success(value):
    session = LabSession({"telecom": lambda seed=7: value})
    try:
        session.start("telecom", 7, 0)
        session.worker.join(timeout=3)
        assert not session.worker.is_alive()
        state = session.snapshot()
        assert state["status"] == "failed"
        assert state["result"] is None
        assert "could not finish" in state["error"]
        json.dumps(state, allow_nan=False)
    finally:
        session.close()


@pytest.mark.parametrize("reported_status", ["BLOCKED", "ERROR"])
def test_incomplete_runner_status_cannot_be_overridden_by_positive_checks(reported_status):
    session = LabSession(
        {
            "telecom": lambda seed=7: {
                "status": reported_status,
                "checks": [{"name": "partial_control", "passed": True}],
            }
        }
    )
    try:
        session.start("telecom", 7, 0)
        session.worker.join(timeout=3)
        state = session.snapshot()
        assert state["status"] == "failed"
        assert state["result"]["status"] == reported_status
        assert "could not finish" in state["error"]
    finally:
        session.close()


@pytest.mark.parametrize(
    "checks",
    [
        [{"name": "rag_receipt", "passed": False}],
        [{"name": "rag_benchmark", "passed": False, "http_status": 422}],
        [{"name": "service", "passed": False, "http_status": 503}],
        [{"name": "service", "passed": False, "reason_code": "SURFACE_DENIED_UNTIL_QUALIFIED"}],
    ],
)
def test_failed_check_guidance_does_not_invent_a_cause_or_pass(checks):
    messages = [
        _summarize_failed_checks(checks, live_mode=True),
        live.describe_suite_guidance(checks),
    ]
    for message in messages:
        assert "tier" not in message.casefold()
        assert "calibration" not in message.casefold()
        assert "verification completed" not in message.casefold()
        if not any(check.get("http_status") == 503 for check in checks):
            assert "503" not in message


def receipt_envelope():
    return {
        "schema_version": 1,
        "body": {"receipt_sha256": "sha256:" + "a" * 64, "signature": "ed25519:" + "b" * 128},
        "attestation": {
            "receipt_sha256": "sha256:" + "a" * 64,
            "signature": "ed25519:" + "b" * 128,
            "single_use_enforced": True,
        },
    }


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("root", "schema_version", True),
        ("root", "unexpected", "extra"),
        ("body", "receipt_sha256", "sha256:not-a-digest"),
        ("body", "signature", "ed25519:not-a-signature"),
        ("attestation", "signature", "ed25519:" + "c" * 128),
        ("attestation", "single_use_enforced", False),
        ("attestation", "single_use_enforced", 1),
    ],
)
def test_receipt_field_validation_rejects_malformed_or_unbound_fields(target, field, value):
    envelope = receipt_envelope()
    destination = envelope if target == "root" else envelope[target]
    destination[field] = value
    with pytest.raises(ServiceResponseContractError):
        _validate_receipt_envelope(envelope)


def test_receipt_field_validation_requires_complete_digest_and_signature():
    envelope = receipt_envelope()
    for target in ("body", "attestation"):
        envelope[target]["receipt_sha256"] = "sha256:"
        envelope[target]["signature"] = "ed25519:"
    with pytest.raises(ServiceResponseContractError):
        _validate_receipt_envelope(envelope)


def benchmark_body():
    return {
        "documents": len(SEED_PASSAGES),
        "queries": len(SEED_QUERIES),
        "rows": [
            {
                "retriever": name,
                "poison_target_exposure_pct": 0.0,
                "full_query_exposure_pct": 0.0,
                "partial_query_exposure_pct": 0.0,
                "clean_ndcg_at_k": 0.95,
            }
            for name in (
                "legacy_bm25",
                "legacy_dense_bi_encoder",
                "legacy_hybrid_rrf",
                "lexical_overlap_filter_tau_0_95",
                "mmr_diversity_rerank",
                "orthocore_subspace_disentangled",
            )
        ],
    }


@pytest.mark.parametrize("value", [True, "1.0", -0.1, 1.1, float("nan"), float("inf")])
def test_benchmark_ndcg_must_be_finite_and_in_range(value):
    body = benchmark_body()
    body["rows"][0]["clean_ndcg_at_k"] = value
    with pytest.raises(ServiceResponseContractError):
        _evaluation_row(body)


def test_benchmark_requires_matching_workload_and_unique_retrievers():
    body = benchmark_body()
    assert _evaluation_row(body)["clean_ndcg_at_k"] == 0.95
    body["rows"].append(copy.deepcopy(body["rows"][0]))
    with pytest.raises(ServiceResponseContractError):
        _evaluation_row(body)
    body = benchmark_body()
    body["documents"] -= 1
    with pytest.raises(ServiceResponseContractError):
        _evaluation_row(body)


@pytest.mark.parametrize("incomplete", [True, False])
def test_benchmark_requires_all_six_known_retrievers(incomplete):
    body = benchmark_body()
    if incomplete:
        body["rows"] = [body["rows"][-1]]
    else:
        body["rows"][0]["retriever"] = "unknown_retriever"
    with pytest.raises(ServiceResponseContractError):
        _evaluation_row(body)


def test_rag_sdk_workflow_uses_all_three_public_entry_points(monkeypatch):
    pytest.importorskip("eigenscope_sdk")
    from eigenscope_lab import rag_live

    calls = []

    def sealed(body):
        envelope = receipt_envelope()
        envelope["body"].update(body)
        return envelope

    class Client:
        owned_tickets = set()

        def close(self):
            calls.append("close")

        def rag_ingest(self, doc_id, text, **kwargs):
            assert "poison" not in doc_id
            assert text and kwargs["title"]
            calls.append("ingest")
            return sealed({"doc_id": doc_id, "auxiliary_metadata": {}})

        def rag_retrieve(self, query, **kwargs):
            assert query and kwargs["candidate_pool_n"] == len(SEED_PASSAGES)
            calls.append("retrieve")
            return sealed(
                {
                    "candidate_pool_n": len(SEED_PASSAGES),
                    "sir_threshold": DEFAULT_SIR_THRESHOLD,
                    "admitted_context": [
                        {"doc_id": "doc_6d0f", "task_subspace_score": 0.9, "sir_score": 0.1}
                    ],
                    "quarantined_candidates": [
                        {"doc_id": "doc_47ac", "sir_score": 2.0, "reason": "threshold"},
                        {"doc_id": "doc_b953", "sir_score": 1.5, "reason": "threshold"},
                    ],
                }
            )

        def rag_evaluate(self, documents, queries, **kwargs):
            assert len(documents) == len(SEED_PASSAGES)
            assert all(isinstance(query["grades"], list) for query in queries)
            assert all(len(query["grades"]) == len(documents) for query in queries)
            calls.append("evaluate")
            return sealed(benchmark_body())

    monkeypatch.setattr(rag_live, "make_observed_client", lambda *_args, **_kwargs: Client())
    result = rag_live.run_rag_live_sdk(base_url="https://service.example.invalid", key="unit-test")
    assert calls == [*["ingest"] * len(SEED_PASSAGES), "retrieve", "evaluate", "close"]
    assert all(check["passed"] for check in result["checks"])


def test_rag_sdk_failure_does_not_fall_back_to_direct_http_or_skip_other_methods(monkeypatch):
    pytest.importorskip("eigenscope_sdk")
    from eigenscope_lab import rag_live

    calls = []

    class Client:
        owned_tickets = set()

        def close(self):
            pass

        def rag_ingest(self, *_args, **_kwargs):
            calls.append("ingest")
            raise ServiceResponseContractError("synthetic SDK contract failure")

        def rag_retrieve(self, *_args, **_kwargs):
            calls.append("retrieve")
            raise ServiceResponseContractError("synthetic SDK contract failure")

        def rag_evaluate(self, *_args, **_kwargs):
            calls.append("evaluate")
            raise ServiceResponseContractError("synthetic SDK contract failure")

    monkeypatch.setattr(rag_live, "make_observed_client", lambda *_args, **_kwargs: Client())
    result = rag_live.run_rag_live_sdk(base_url="https://service.example.invalid", key="unit-test")
    assert calls == ["ingest", "retrieve", "evaluate"]
    assert len(result["checks"]) == 3
    assert all(check["passed"] is False for check in result["checks"])
    assert "synthetic SDK contract failure" not in json.dumps(result)


def test_retrieval_cannot_report_one_passage_as_both_admitted_and_quarantined():
    body = {
        "candidate_pool_n": len(SEED_PASSAGES),
        "sir_threshold": DEFAULT_SIR_THRESHOLD,
        "admitted_context": [{"doc_id": "a", "task_subspace_score": 0.9, "sir_score": 0.1}],
        "quarantined_candidates": [{"doc_id": "a", "sir_score": 2.0, "reason": "threshold"}],
    }
    with pytest.raises(ServiceResponseContractError):
        _retrieval_ids(body)
    body["quarantined_candidates"][0]["doc_id"] = "b"
    assert _retrieval_ids(body) == (["a"], ["b"])


def masked_projection(spec):
    from eigenscope_lab.simulators.multimodal import (
        decode_ocvc_rgba,
        decode_png_rgba,
        decode_wav_pcm16,
        encode_ocvc_rgba,
        encode_png_rgba,
        encode_wav_pcm16,
    )

    if spec["modality"] == "audio_wav":
        samples, rate, channels = decode_wav_pcm16(spec["raw_bytes"])
        for span in spec["input_payload"]["transcript_words"]:
            samples[span["start_sample"] : span["end_sample"]] = 0
        return encode_wav_pcm16(samples, sample_rate_hz=rate, channels=channels)
    image = spec["modality"] == "image_frame"
    pixels = decode_png_rgba(spec["raw_bytes"]) if image else decode_ocvc_rgba(spec["raw_bytes"])
    frames = pixels[None, ...] if image else pixels
    for box in spec["input_payload"]["ocr_regions"]:
        frames[
            box["frame"],
            box["y"] : box["y"] + box["height"],
            box["x"] : box["x"] + box["width"],
        ] = (0, 0, 0, 255)
    return encode_png_rgba(pixels) if image else encode_ocvc_rgba(pixels)


@pytest.mark.parametrize("index", [0, 1, 2])
def test_live_media_oracle_observes_annotated_transformation_and_public_signal(index):
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    spec = _modality_specs(7)[index]
    assert all(check["passed"] for check in _projection_checks(spec, masked_projection(spec)))
    checks = {
        check["name"]: check["passed"] for check in _projection_checks(spec, spec["raw_bytes"])
    }
    assert not checks["annotated_media_spans_transformed"]
    assert checks["public_media_signal_present"]


def test_unrelated_change_is_not_an_annotated_transformation_and_blank_erases_signal():
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    from eigenscope_lab.simulators.multimodal import decode_png_rgba, encode_png_rgba

    spec = _modality_specs(7)[1]
    pixels = decode_png_rgba(spec["raw_bytes"])
    pixels[0, 0, 0] ^= 1
    checks = {
        check["name"]: check["passed"]
        for check in _projection_checks(spec, encode_png_rgba(pixels))
    }
    assert not checks["annotated_media_spans_transformed"]
    assert checks["public_media_signal_present"]
    pixels[:] = (0, 0, 0, 255)
    checks = {
        check["name"]: check["passed"]
        for check in _projection_checks(spec, encode_png_rgba(pixels))
    }
    assert checks["annotated_media_spans_transformed"]
    assert not checks["public_media_signal_present"]


def test_corrupt_projected_png_cannot_pass_the_media_contract():
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    spec = _modality_specs(7)[1]
    corrupt = spec["raw_bytes"][:-1] + bytes([spec["raw_bytes"][-1] ^ 1])
    with pytest.raises(ServiceResponseContractError):
        _projection_checks(spec, corrupt)


def test_media_cancellation_keeps_owned_ticket_cleanup():
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    import base64

    spec = _modality_specs(7)[1]
    calls = []

    class Http:
        def post(self, url, **_kwargs):
            calls.append(url)
            if url.endswith("/v1/decompose/image"):
                payload = {
                    "vault_ticket": "vlt_cancellation_media_control",
                    "redacted_frame_base64": base64.b64encode(masked_projection(spec)).decode(),
                }
                return SimpleNamespace(status_code=200, json=lambda: payload)
            assert url.endswith("/api/workbench/vault-zeroize")
            return SimpleNamespace(status_code=200)

    def checkpoint():
        if calls:
            raise RunCancelled

    with pytest.raises(RunCancelled):
        run_multimodal_live(
            base_url="https://service.example.invalid",
            key="unit-test",
            selection="multimodal-image",
            http_client=Http(),
            check_cancelled=checkpoint,
        )
    assert [url.rsplit("/", 1)[-1] for url in calls] == ["image", "vault-zeroize"]


def test_live_media_frames_report_observed_geometry_and_separate_roundtrip(monkeypatch):
    from eigenscope_lab import multimodal_live
    from eigenscope_lab.simulators import multimodal

    monkeypatch.setattr(
        multimodal,
        "run_multimodal",
        lambda **_kwargs: {
            "engine": "synthetic orchestration control",
            "checks": [{"name": "local_control", "passed": True}],
            "metrics": {"simulation_time_s": 0.25, "rejected_negative_controls": 4},
        },
    )
    monkeypatch.setattr(
        multimodal_live,
        "run_multimodal_live",
        lambda **_kwargs: [
            {
                "modality": "image_frame",
                "http_status": 200,
                "checks": [
                    {"name": "media_bit_exact_sha256_restored", "passed": True},
                    {"name": "annotated_media_spans_transformed", "passed": False},
                ],
                "metrics": {"width": 96, "height": 64, "raw_media_bytes": 1024},
            },
            {
                "modality": "audio_wav",
                "http_status": 503,
                "checks": [{"name": "decomposition", "passed": False, "http_status": 503}],
                "metrics": {},
            },
        ],
    )
    frames = []
    _, runners = live.build_live_web_runners(
        base_url="https://service.example.invalid", key="unit-test"
    )
    runners["multimodal"](on_frame=frames.append)
    image, audio = frames
    assert (image["width"], image["height"]) == (96, 64)
    assert image["exact_roundtrip"] is True
    assert image["live_failed"] is True
    assert image["rejected_controls"] == 4
    assert audio["live_modality_status"]["audio_wav"] == "UNAVAILABLE"
    assert "TIER" not in audio["active_stage"]


@pytest.mark.parametrize("repeat", [0, -1, 11, True, 1.5])
def test_direct_multimodal_runner_cannot_succeed_without_executing_cases(repeat):
    with pytest.raises(ValueError, match="repeat"):
        run_multimodal_live(
            base_url="https://service.example.invalid", key="unit-test", repeat=repeat
        )
