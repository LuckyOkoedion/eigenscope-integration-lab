"""Independent retrieval, rank-discount and query-specific exposure controls."""

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from eigenscope_lab.simulators.rag import (
    SEED_PASSAGES,
    SEED_QUERIES,
    BenchmarkQuery,
    CorpusPassage,
    LocalRetrievalReceipts,
    LocalSubspaceRagIndex,
    RetrievalGateRejected,
)


@pytest.fixture
def index():
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    return LocalSubspaceRagIndex()


def test_rankings_and_gate_do_not_read_poison_or_target_labels(index):
    unlabelled = LocalSubspaceRagIndex(
        [replace(p, poison=None, target_query_id=None) for p in SEED_PASSAGES]
    )
    for query in SEED_QUERIES:
        assert index._benchmark_rankings(query.text, 5, 0.35) == unlabelled._benchmark_rankings(
            query.text, 5, 0.35
        )
    assert index.ingested_metadata == unlabelled.ingested_metadata


def test_baselines_execute_different_rankers_and_expose_actual_algorithms(index):
    rankings = index._benchmark_rankings(SEED_QUERIES[0].text, 5, 0.35)
    assert rankings["legacy_bm25"][0] != rankings["legacy_dense_bi_encoder"][0]
    assert rankings["mmr_diversity_rerank"] != rankings["legacy_dense_bi_encoder"]
    assert rankings["lexical_overlap_filter_tau_0_95"] != rankings["legacy_bm25"]
    rows = {row["retriever"]: row for row in index.evaluate_benchmark()["rows"]}
    assert rows["legacy_dense_bi_encoder"]["display_name"] == "Dense LSA (truncated SVD)"
    assert (
        rows["legacy_bm25"]["clean_ndcg_at_k"] != rows["legacy_dense_bi_encoder"]["clean_ndcg_at_k"]
    )


def test_ndcg_keeps_poison_at_its_original_zero_gain_rank(index, monkeypatch):
    ranked = ["doc_47ac", "doc_6d0f"]
    names = index._benchmark_rankings(SEED_QUERIES[0].text, 2, 0.35)
    monkeypatch.setattr(
        index, "_benchmark_rankings", lambda *_args: {name: ranked for name in names}
    )
    query = BenchmarkQuery("q_proration", "Seat proration", {"doc_6d0f": 3})
    expected = round(1.0 / math.log2(3), 4)
    assert all(
        row["clean_ndcg_at_k"] == expected
        for row in index.evaluate_benchmark([query], top_k=2)["rows"]
    )


def test_an_attack_targeting_another_query_does_not_count_as_target_exposure(monkeypatch, index):
    passages = [
        CorpusPassage("clean", "Billing policy", "Proration produces a daily credit and charge."),
        CorpusPassage(
            "review-a",
            "Invoice review",
            "Review invoice credit.",
            poison="full",
            target_query_id="q_a",
        ),
        CorpusPassage(
            "review-b",
            "Refund review",
            "Review refund charge.",
            poison="partial",
            target_query_id="q_b",
        ),
    ]
    local = LocalSubspaceRagIndex(passages)
    names = index._benchmark_rankings(SEED_QUERIES[0].text, 2, 0.35)

    def rankings(query, *_args):
        other_attack = "review-b" if query == "invoice" else "review-a"
        return {name: [other_attack, "clean"] for name in names}

    monkeypatch.setattr(local, "_benchmark_rankings", rankings)
    queries = [
        BenchmarkQuery("q_a", "invoice", {"clean": 3}),
        BenchmarkQuery("q_b", "refund", {"clean": 3}),
    ]
    for row in local.evaluate_benchmark(queries, top_k=2)["rows"]:
        assert row["poison_target_exposure_pct"] == 0.0
        assert row["full_query_exposure_pct"] == row["partial_query_exposure_pct"] == 0.0
        assert row["targeted_queries"] == 2
        assert row["full_query_targets"] == row["partial_query_targets"] == 1


def test_reported_task_norm_is_the_actual_projected_vector_norm(index):
    for doc_id, vector in index.task_vectors.items():
        expected = float(index._np.linalg.norm(vector))
        assert index.ingested_metadata[doc_id]["task_norm"] == pytest.approx(expected, abs=0.000051)


@pytest.mark.parametrize("value", [True, False, "0.35", None, float("nan"), float("inf"), 10**1000])
def test_sir_threshold_rejects_coercion_and_nonfinite_values(index, value):
    with pytest.raises(RetrievalGateRejected):
        LocalSubspaceRagIndex(sir_threshold=value)
    if value is not None:
        with pytest.raises(RetrievalGateRejected):
            index.evaluate_benchmark(sir_threshold=value)


def test_duplicate_and_cross_tenant_documents_are_rejected(index):
    with pytest.raises(RetrievalGateRejected):
        LocalSubspaceRagIndex([SEED_PASSAGES[0], SEED_PASSAGES[0]])
    with pytest.raises(RetrievalGateRejected):
        LocalSubspaceRagIndex(
            [SEED_PASSAGES[0], replace(SEED_PASSAGES[1], tenant_id="another-tenant")]
        )
    with pytest.raises(RetrievalGateRejected):
        index.evaluate_benchmark([])


def test_receipt_rejects_empty_or_wrong_tenant_without_destroying_valid_use():
    receipts = LocalRetrievalReceipts()
    token = receipts.issue("tenant-a", ["doc-a"])
    assert receipts.consume(token, "tenant-a", []) is False
    assert receipts.consume(token, "tenant-b", ["doc-a"]) is False
    assert receipts.consume(token, "tenant-a", ["doc-a"]) is True
    assert receipts.consume(token, "tenant-a", ["doc-a"]) is False


def test_receipt_is_consumed_exactly_once_across_concurrent_requests():
    receipts = LocalRetrievalReceipts()
    token = receipts.issue("tenant-a", ["doc-a"])
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: receipts.consume(token, "tenant-a", ["doc-a"]), range(24))
        )
    assert sum(results) == 1
