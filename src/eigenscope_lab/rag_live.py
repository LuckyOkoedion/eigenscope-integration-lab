"""Live /v1/rag/{ingest,retrieve,evaluate} HTTP contract checks."""

from __future__ import annotations

import json
import math
import re
import secrets
import time
from collections.abc import Callable
from contextlib import closing
from typing import Any

from .live import (
    ServiceResponseContractError,
    _check,
    _checkpoint,
    _record_exception,
    make_observed_client,
    safe_error,
)
from .reporting import distribution_version
from .simulators.rag import DEFAULT_SIR_THRESHOLD, DEFAULT_TOP_K, SEED_PASSAGES, SEED_QUERIES

CONTRACT_VERSION = "2026-10-18.v1"
RETRIEVER_NAMES = (
    "legacy_bm25",
    "legacy_dense_bi_encoder",
    "legacy_hybrid_rrf",
    "lexical_overlap_filter_tau_0_95",
    "mmr_diversity_rerank",
    "orthocore_subspace_disentangled",
)


def _new_uuidv7() -> str:
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    val = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    hx = f"{val:032x}"
    return f"{hx[:8]}-{hx[8:12]}-{hx[12:16]}-{hx[16:20]}-{hx[20:]}"


def _validate_receipt_envelope(data: Any) -> dict[str, Any]:
    """Validate the published envelope and matching receipt fields."""
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "body", "attestation"}
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
    ):
        raise ServiceResponseContractError("Invalid sealed RAG envelope schema_version")
    body = data.get("body")
    attestation = data.get("attestation")
    if not isinstance(body, dict) or not isinstance(attestation, dict):
        raise ServiceResponseContractError("Missing body or attestation in sealed RAG envelope")
    receipt = body.get("receipt_sha256")
    sig = body.get("signature")
    if (
        not isinstance(receipt, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", receipt) is None
        or attestation.get("receipt_sha256") != receipt
        or not isinstance(sig, str)
        or re.fullmatch(r"ed25519:[0-9a-f]{128}", sig) is None
        or attestation.get("signature") != sig
        or attestation.get("single_use_enforced") is not True
    ):
        raise ServiceResponseContractError("Sealed RAG attestation does not bind body receipt")
    return body


def _bounded_number(value: Any, name: str, maximum: float | None) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or (maximum is not None and value > maximum)
    ):
        raise ServiceResponseContractError(f"Invalid RAG {name}")
    return float(value)


def _retrieval_ids(body: dict[str, Any]) -> tuple[list[str], list[str]]:
    if (
        type(body.get("candidate_pool_n")) is not int
        or body["candidate_pool_n"] != len(SEED_PASSAGES)
        or _bounded_number(body.get("sir_threshold"), "sir_threshold", 1.0) != DEFAULT_SIR_THRESHOLD
    ):
        raise ServiceResponseContractError("RAG retrieve response changed the requested boundary")
    groups = []
    seen: set[str] = set()
    for name in ("admitted_context", "quarantined_candidates"):
        entries = body.get(name)
        if not isinstance(entries, list) or (
            name == "admitted_context" and len(entries) > DEFAULT_TOP_K
        ):
            raise ServiceResponseContractError("Invalid RAG retrieve candidate list")
        ids = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ServiceResponseContractError("Invalid RAG retrieve candidate")
            doc_id = entry.get("doc_id")
            if not isinstance(doc_id, str) or not doc_id or doc_id in seen:
                raise ServiceResponseContractError("RAG retrieve document IDs must be unique")
            _bounded_number(entry.get("sir_score"), "candidate sir_score", None)
            if name == "admitted_context":
                score = entry.get("task_subspace_score")
                if (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(score)
                ):
                    raise ServiceResponseContractError("Invalid RAG task-subspace score")
            elif not isinstance(entry.get("reason"), str) or not entry["reason"]:
                raise ServiceResponseContractError("RAG quarantine reason is missing")
            seen.add(doc_id)
            ids.append(doc_id)
        groups.append(ids)
    return groups[0], groups[1]


def _evaluation_row(body: dict[str, Any]) -> dict[str, Any]:
    if (
        type(body.get("documents")) is not int
        or body["documents"] != len(SEED_PASSAGES)
        or type(body.get("queries")) is not int
        or body["queries"] != len(SEED_QUERIES)
    ):
        raise ServiceResponseContractError("RAG evaluate response measured another workload")
    rows = body.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ServiceResponseContractError("RAG benchmark rows are missing")
    by_name = {}
    for entry in rows:
        if not isinstance(entry, dict):
            raise ServiceResponseContractError("Invalid RAG benchmark row")
        name = entry.get("retriever")
        if not isinstance(name, str) or not name or name in by_name:
            raise ServiceResponseContractError("RAG benchmark retrievers must be unique")
        for metric in (
            "poison_target_exposure_pct",
            "full_query_exposure_pct",
            "partial_query_exposure_pct",
        ):
            _bounded_number(entry.get(metric), metric, 100.0)
        _bounded_number(entry.get("clean_ndcg_at_k"), "clean_ndcg_at_k", 1.0)
        by_name[name] = entry
    try:
        if set(by_name) != set(RETRIEVER_NAMES):
            raise ServiceResponseContractError("RAG benchmark retriever inventory is incomplete")
        return by_name["orthocore_subspace_disentangled"]
    except KeyError as exc:
        raise ServiceResponseContractError("OrthoCore RAG benchmark row is missing") from exc


def run_rag_live_http(
    *,
    base_url: str,
    key: str,
    timeout: float = 15.0,
    check_cancelled: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Execute the three-stage /v1/rag/{ingest,retrieve,evaluate} pipeline against the live service."""
    import httpx

    row: dict[str, Any] = {
        "suite": "rag:v1-pipeline",
        "engine": "actual /v1/rag/{ingest,retrieve,evaluate} HTTP API",
        "checks": [],
        "metrics": {},
        "scope": (
            "Actual /v1/rag/ingest, /v1/rag/retrieve and /v1/rag/evaluate calls "
            "with receipt-envelope consistency checks on synthetic corpus."
        ),
    }
    checks = row["checks"]
    started = time.perf_counter()

    def headers(req_id: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "EigenScope-SDK/0.2.7",
            "X-OrthoCore-Contract-Version": CONTRACT_VERSION,
            "X-OrthoCore-Request-Id": req_id,
            "X-OrthoCore-Idempotency-Key": f"rag.{req_id}",
        }

    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as http:
            owned_tickets: list[str] = []

            def _record_envelope_ticket(payload: Any) -> None:
                if isinstance(payload, dict):
                    att = payload.get("attestation")
                    if isinstance(att, dict):
                        vt = att.get("vault_ticket")
                        if (
                            isinstance(vt, str)
                            and vt.startswith("vlt_")
                            and vt not in owned_tickets
                        ):
                            owned_tickets.append(vt)

            try:
                ingest_start = time.perf_counter()
                ingested_docs: list[dict[str, Any]] = []
                ingest_ok = True
                for passage in SEED_PASSAGES:
                    _checkpoint(check_cancelled)
                    req_id = _new_uuidv7()
                    resp = http.post(
                        f"{base_url}/v1/rag/ingest",
                        headers=headers(req_id),
                        content=json.dumps(
                            {
                                "schema_version": 1,
                                "request_id": req_id,
                                "doc_id": passage.doc_id,
                                "title": passage.title,
                                "text": passage.text,
                                "sir_threshold": DEFAULT_SIR_THRESHOLD,
                            }
                        ).encode("utf-8"),
                    )
                    row["http_status"] = resp.status_code
                    if resp.status_code != 200:
                        ingest_ok = False
                        break
                    payload = resp.json()
                    _record_envelope_ticket(payload)
                    body = _validate_receipt_envelope(payload)
                    meta = body.get("auxiliary_metadata")
                    if not isinstance(meta, dict) or body.get("doc_id") != passage.doc_id:
                        raise ServiceResponseContractError("Invalid /v1/rag/ingest body metadata")
                    ingested_docs.append({"doc_id": passage.doc_id, "metadata": meta})
                row["metrics"]["rag_ingest_http_wall_ms"] = round(
                    (time.perf_counter() - ingest_start) * 1000, 3
                )
                checks.append(
                    _check(
                        "rag_ingest_succeeded_and_receipt_fields_consistent",
                        ingest_ok and len(ingested_docs) == len(SEED_PASSAGES),
                        ingested_count=len(ingested_docs),
                        http_status=row.get("http_status", 0),
                    )
                )

                if ingest_ok:
                    _checkpoint(check_cancelled)
                    ret_start = time.perf_counter()
                    ret_id = _new_uuidv7()
                    ret_resp = http.post(
                        f"{base_url}/v1/rag/retrieve",
                        headers=headers(ret_id),
                        content=json.dumps(
                            {
                                "schema_version": 1,
                                "request_id": ret_id,
                                "query": SEED_QUERIES[0].text,
                                "top_k": DEFAULT_TOP_K,
                                "candidate_pool_n": len(SEED_PASSAGES),
                                "sir_threshold": DEFAULT_SIR_THRESHOLD,
                            }
                        ).encode("utf-8"),
                    )
                    row["http_status"] = ret_resp.status_code
                    row["metrics"]["rag_retrieve_http_wall_ms"] = round(
                        (time.perf_counter() - ret_start) * 1000, 3
                    )
                    if ret_resp.status_code == 200:
                        ret_payload = ret_resp.json()
                        _record_envelope_ticket(ret_payload)
                        ret_body = _validate_receipt_envelope(ret_payload)
                        admitted_ids, quarantined_ids = _retrieval_ids(ret_body)
                        checks.append(
                            _check("rag_retrieve_succeeded_and_receipt_fields_consistent", True)
                        )
                        poison_ids = {"doc_47ac", "doc_b953"}
                        poison_quarantined = poison_ids.issubset(set(quarantined_ids)) and not (
                            poison_ids & set(admitted_ids)
                        )
                        checks.append(
                            _check(
                                "rag_sir_gate_quarantines_poisoned_passages",
                                poison_quarantined,
                                admitted_poison_count=len(poison_ids & set(admitted_ids)),
                                quarantined_count=len(quarantined_ids),
                            )
                        )
                        checks.append(
                            _check(
                                "rag_clean_ground_truth_ranked_first",
                                bool(admitted_ids) and admitted_ids[0] == "doc_6d0f",
                                top_admitted_doc_id=admitted_ids[0] if admitted_ids else "none",
                            )
                        )
                    else:
                        checks.append(
                            _check(
                                "rag_retrieve_succeeded_and_receipt_fields_consistent",
                                False,
                                http_status=ret_resp.status_code,
                            )
                        )

                    eval_start = time.perf_counter()
                    _checkpoint(check_cancelled)
                    eval_id = _new_uuidv7()
                    eval_docs = [
                        {
                            "doc_id": p.doc_id,
                            "text": p.text,
                            **(
                                {"poison": p.poison, "target_query_id": p.target_query_id}
                                if p.poison is not None
                                else {}
                            ),
                        }
                        for p in SEED_PASSAGES
                    ]
                    eval_queries = [
                        {"query_id": q.query_id, "text": q.text, "grades": dict(q.grades)}
                        for q in SEED_QUERIES
                    ]
                    eval_resp = http.post(
                        f"{base_url}/v1/rag/evaluate",
                        headers=headers(eval_id),
                        content=json.dumps(
                            {
                                "schema_version": 1,
                                "request_id": eval_id,
                                "documents": eval_docs,
                                "queries": eval_queries,
                                "top_k": DEFAULT_TOP_K,
                                "sir_threshold": DEFAULT_SIR_THRESHOLD,
                            }
                        ).encode("utf-8"),
                    )
                    row["http_status"] = eval_resp.status_code
                    row["metrics"]["rag_evaluate_http_wall_ms"] = round(
                        (time.perf_counter() - eval_start) * 1000, 3
                    )
                    if eval_resp.status_code == 200:
                        eval_payload = eval_resp.json()
                        _record_envelope_ticket(eval_payload)
                        eval_body = _validate_receipt_envelope(eval_payload)
                        ortho_row = _evaluation_row(eval_body)
                        exposure = float(ortho_row.get("poison_target_exposure_pct", 100.0))
                        ndcg = float(ortho_row.get("clean_ndcg_at_k", 0.0))
                        row["metrics"]["orthocore_poison_exposure_pct"] = exposure
                        row["metrics"]["orthocore_clean_ndcg_at_k"] = round(ndcg, 4)
                        checks.append(
                            _check(
                                "rag_evaluate_benchmark_zero_poison_exposure",
                                exposure == 0.0 and ndcg >= 0.85,
                                poison_target_exposure_pct=exposure,
                                clean_ndcg_at_k=round(ndcg, 4),
                            )
                        )
                    else:
                        checks.append(
                            _check(
                                "rag_evaluate_benchmark_zero_poison_exposure",
                                False,
                                http_status=eval_resp.status_code,
                            )
                        )
            finally:
                scrubbed_count = 0
                for ticket in list(owned_tickets):
                    try:
                        scrub_id = _new_uuidv7()
                        scrub_resp = http.post(
                            f"{base_url}/v1/vault/scrub",
                            headers=headers(scrub_id),
                            content=json.dumps(
                                {
                                    "schema_version": 1,
                                    "request_id": scrub_id,
                                    "vault_ticket": ticket,
                                }
                            ).encode("utf-8"),
                        )
                        scrub_body = scrub_resp.json() if scrub_resp.status_code == 200 else {}
                        scrub_ok = (
                            scrub_resp.status_code == 200
                            and isinstance(scrub_body, dict)
                            and scrub_body.get("status") == "VAULT_TICKET_SCRUBBED"
                            and scrub_body.get("vault_ticket") == ticket
                            and scrub_body.get("vault_ticket_scrubbed") is True
                        )
                        if scrub_ok:
                            scrubbed_count += 1
                        checks.append(
                            _check(
                                "owned_rag_http_ticket_cleanup",
                                scrub_ok,
                                http_status=scrub_resp.status_code,
                            )
                        )
                    except Exception as exc:
                        checks.append(
                            _check("owned_rag_http_ticket_cleanup", False, **safe_error(exc))
                        )
                if owned_tickets:
                    row["metrics"]["rag_http_scrubbed_tickets"] = scrubbed_count
                    if scrubbed_count == len(owned_tickets):
                        try:
                            replay_id = _new_uuidv7()
                            replay_resp = http.post(
                                f"{base_url}/v1/vault/scrub",
                                headers=headers(replay_id),
                                content=json.dumps(
                                    {
                                        "schema_version": 1,
                                        "request_id": replay_id,
                                        "vault_ticket": owned_tickets[0],
                                    }
                                ).encode("utf-8"),
                            )
                            replay_body = (
                                replay_resp.json() if replay_resp.status_code == 410 else {}
                            )
                            replay_denied = (
                                replay_resp.status_code == 410
                                and isinstance(replay_body, dict)
                                and replay_body.get("error_code") == "VAULT_TICKET_SCRUBBED"
                            )
                            checks.append(
                                _check(
                                    "owned_rag_http_scrubbed_ticket_replay_denied_410",
                                    replay_denied,
                                    http_status=replay_resp.status_code,
                                )
                            )
                        except Exception as exc:
                            checks.append(
                                _check(
                                    "owned_rag_http_scrubbed_ticket_replay_denied_410",
                                    False,
                                    **safe_error(exc),
                                )
                            )
    except Exception as exc:
        _record_exception(row, exc)
    finally:
        row["metrics"]["complete_rag_wall_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return row


def run_rag_live_sdk(
    *,
    base_url: str,
    key: str,
    timeout: float = 15.0,
    check_cancelled: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Exercise the official SDK's three RAG entry points without wire adaptation."""
    from eigenscope_sdk import EigenScopeClient

    row: dict[str, Any] = {
        "suite": "sdk:rag-pipeline",
        "engine": "official EigenScope Python SDK rag_ingest/rag_retrieve/rag_evaluate",
        "sdk_version": distribution_version("eigenscope-sdk"),
        "checks": [],
        "metrics": {},
        "scope": "Actual public SDK RAG methods against the configured service; published synthetic corpus.",
    }
    started = time.perf_counter()
    classifications: list[str | None] = []

    def operation(name: str, action: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
        _checkpoint(check_cancelled)
        begin = time.perf_counter()
        try:
            response = action()
            body = _validate_receipt_envelope(response)
        except Exception as exc:
            detail: dict[str, Any] = {"checks": []}
            _record_exception(detail, exc)
            classifications.append(
                None if getattr(exc, "http_status", None) == 503 else detail.get("status")
            )
            row["checks"].append(_check(name, False, **safe_error(exc)))
            return None
        else:
            classifications.append(None)
            row["checks"].append(_check(name, True))
            return body
        finally:
            row["metrics"][name + "_wall_ms"] = round((time.perf_counter() - begin) * 1000, 3)

    documents = [
        {
            "doc_id": passage.doc_id,
            "text": passage.text,
            **(
                {"poison": passage.poison, "target_query_id": passage.target_query_id}
                if passage.poison is not None
                else {}
            ),
        }
        for passage in SEED_PASSAGES
    ]
    queries = [
        {
            "query_id": query.query_id,
            "text": query.text,
            "grades": [query.grades.get(passage.doc_id, 0) for passage in SEED_PASSAGES],
        }
        for query in SEED_QUERIES
    ]
    try:
        with closing(
            make_observed_client(
                EigenScopeClient, base_url=base_url, bearer_token=key, timeout_seconds=timeout
            )
        ) as client:
            try:
                ingested = 0
                for passage in SEED_PASSAGES:
                    body = operation(
                        "sdk_rag_ingest_response_validated",
                        lambda passage=passage: client.rag_ingest(
                            passage.doc_id,
                            passage.text,
                            title=passage.title,
                            sir_threshold=DEFAULT_SIR_THRESHOLD,
                        ),
                    )
                    if body is None:
                        break
                    ingested += 1
                row["metrics"]["sdk_ingested_documents"] = ingested
                retrieved = operation(
                    "sdk_rag_retrieve_response_validated",
                    lambda: client.rag_retrieve(
                        SEED_QUERIES[0].text,
                        top_k=DEFAULT_TOP_K,
                        candidate_pool_n=len(SEED_PASSAGES),
                        sir_threshold=DEFAULT_SIR_THRESHOLD,
                    ),
                )
                if retrieved is not None:
                    admitted, quarantined = _retrieval_ids(retrieved)
                    row["checks"].append(
                        _check(
                            "sdk_rag_quarantine_preserves_admitted_boundary",
                            {"doc_47ac", "doc_b953"}.issubset(quarantined)
                            and not ({"doc_47ac", "doc_b953"} & set(admitted)),
                        )
                    )
                evaluated = operation(
                    "sdk_rag_evaluate_response_validated",
                    lambda: client.rag_evaluate(
                        documents,
                        queries,
                        top_k=DEFAULT_TOP_K,
                        sir_threshold=DEFAULT_SIR_THRESHOLD,
                    ),
                )
                if evaluated is not None:
                    ortho = _evaluation_row(evaluated)
                    row["checks"].append(
                        _check(
                            "sdk_rag_benchmark_zero_poison_exposure",
                            ortho["poison_target_exposure_pct"] == 0
                            and ortho["clean_ndcg_at_k"] >= 0.85,
                        )
                    )
            finally:
                for ticket in sorted(getattr(client, "owned_tickets", set())):
                    try:
                        client.scrub(ticket)
                        row["checks"].append(_check("owned_rag_ticket_cleanup", True))
                    except Exception as exc:
                        row["checks"].append(
                            _check("owned_rag_ticket_cleanup", False, **safe_error(exc))
                        )
    except Exception as exc:
        _record_exception(row, exc)
    finally:
        row["metrics"]["complete_sdk_rag_wall_ms"] = round(
            (time.perf_counter() - started) * 1000, 3
        )
        if classifications and all(status == "BLOCKED" for status in classifications):
            row["status"] = "BLOCKED"
    return row
