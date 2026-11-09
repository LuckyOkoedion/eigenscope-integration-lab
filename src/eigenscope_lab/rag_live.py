"""Live /v1/rag/{ingest,retrieve,evaluate} HTTP contract checks."""

from __future__ import annotations

import json
import secrets
import time
from typing import Any

from .live import ServiceResponseContractError, _check, _record_exception
from .simulators.rag import DEFAULT_SIR_THRESHOLD, DEFAULT_TOP_K, SEED_PASSAGES, SEED_QUERIES

CONTRACT_VERSION = "2026-10-18.v1"


def _new_uuidv7() -> str:
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    val = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    hx = f"{val:032x}"
    return f"{hx[:8]}-{hx[8:12]}-{hx[12:16]}-{hx[16:20]}-{hx[20:]}"


def _verify_sealed_envelope(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ServiceResponseContractError("Invalid sealed RAG envelope schema_version")
    body = data.get("body")
    attestation = data.get("attestation")
    if not isinstance(body, dict) or not isinstance(attestation, dict):
        raise ServiceResponseContractError("Missing body or attestation in sealed RAG envelope")
    receipt = body.get("receipt_sha256")
    sig = body.get("signature")
    if (
        not isinstance(receipt, str)
        or not receipt.startswith("sha256:")
        or attestation.get("receipt_sha256") != receipt
        or not isinstance(sig, str)
        or not sig.startswith("ed25519:")
        or attestation.get("signature") != sig
    ):
        raise ServiceResponseContractError("Sealed RAG attestation does not bind body receipt")
    return body


def run_rag_live_http(
    *,
    base_url: str,
    key: str,
    timeout: float = 15.0,
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
            "with sealed Ed25519 receipt verification on synthetic corpus."
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
            ingest_start = time.perf_counter()
            ingested_docs: list[dict[str, Any]] = []
            ingest_ok = True
            for passage in SEED_PASSAGES:
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
                body = _verify_sealed_envelope(resp.json())
                meta = body.get("auxiliary_metadata")
                if not isinstance(meta, dict) or body.get("doc_id") != passage.doc_id:
                    raise ServiceResponseContractError("Invalid /v1/rag/ingest body metadata")
                ingested_docs.append({"doc_id": passage.doc_id, "metadata": meta})
            row["metrics"]["rag_ingest_http_wall_ms"] = round(
                (time.perf_counter() - ingest_start) * 1000, 3
            )
            checks.append(
                _check(
                    "rag_ingest_succeeded_and_sealed_receipt_bound",
                    ingest_ok and len(ingested_docs) == len(SEED_PASSAGES),
                    ingested_count=len(ingested_docs),
                    http_status=row.get("http_status", 0),
                )
            )

            if ingest_ok:
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
                    ret_body = _verify_sealed_envelope(ret_resp.json())
                    admitted = ret_body.get("admitted_context", [])
                    quarantined = ret_body.get("quarantined_candidates", [])
                    if not isinstance(admitted, list) or not isinstance(quarantined, list):
                        raise ServiceResponseContractError("Invalid /v1/rag/retrieve lists")
                    admitted_ids = [
                        str(item.get("doc_id")) for item in admitted if isinstance(item, dict)
                    ]
                    quarantined_ids = [
                        str(item.get("doc_id")) for item in quarantined if isinstance(item, dict)
                    ]
                    checks.append(_check("rag_retrieve_succeeded_and_sealed_receipt_bound", True))
                    poison_ids = {"kb_poison_proration_override", "kb_poison_refund_escalation"}
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
                            bool(admitted_ids) and admitted_ids[0] == "kb_invoice_proration",
                            top_admitted_doc_id=admitted_ids[0] if admitted_ids else "none",
                        )
                    )
                else:
                    checks.append(
                        _check(
                            "rag_retrieve_succeeded_and_sealed_receipt_bound",
                            False,
                            http_status=ret_resp.status_code,
                        )
                    )

                eval_start = time.perf_counter()
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
                row["metrics"]["rag_evaluate_http_wall_ms"] = round(
                    (time.perf_counter() - eval_start) * 1000, 3
                )
                if eval_resp.status_code == 200:
                    eval_body = _verify_sealed_envelope(eval_resp.json())
                    rows = eval_body.get("rows", [])
                    ortho_row = next(
                        (
                            r
                            for r in rows
                            if isinstance(r, dict)
                            and r.get("retriever") == "orthocore_subspace_disentangled"
                        ),
                        None,
                    )
                    if not isinstance(ortho_row, dict):
                        raise ServiceResponseContractError(
                            "Missing orthocore_subspace_disentangled row in /v1/rag/evaluate"
                        )
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
    except Exception as exc:
        _record_exception(row, exc)
    finally:
        row["metrics"]["complete_rag_wall_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return row
