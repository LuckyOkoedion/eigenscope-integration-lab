# RAG & Knowledge Retrieval: SIR quarantine gate and citation verification

Run an in-memory multi-retriever RAG knowledge base, compute four-subspace norms
and Subspace Incongruity Ratio (SIR) scores during document ingestion, quarantine
full-question and partial-paraphrase poisoned passages before synthesis, compare
clean `nDCG@5` and poison exposure across six retrieval configurations, and
verify single-use citation receipts over a loopback HTTP gate.

This scenario uses a synthetic enterprise billing corpus and contains no tenant
documents or API credentials.

## Run it

```bash
uv sync --locked --extra rag
uv run --frozen eigenscope-lab run rag
uv run --frozen pytest tests/test_rag.py
```

## Subspace Incongruity Ratio (SIR) and retrieval pipeline

Each ingested passage is decomposed into a task-subspace vector ($d_{\text{task}}$)
and auxiliary norms ($\|d_{\text{task}}\|_2$, $\|d_{\text{anchor}}\|_2$,
$\|d_{\text{core}}\|_2$, $\|d_{\text{noise}}\|_2$). The Subspace Incongruity
Ratio measures interrogative-anchor and directive energy relative to task-domain
content:

```text
SIR(d) = ||d_anchor||_2 / ||d_task||_2
```

Passages with $\text{SIR}(d) \ge \theta$ (default $\theta = 0.35$) are marked
`quarantined = true` at ingest and filtered prior to synthesis, preventing
corpus-poisoning chunks that repeat or paraphrase user questions from capturing
top-$k$ retrieval slots.

| Parameter | Synthetic lab value |
| --- | --- |
| Seed corpus size | 7 passages (5 clean, 2 poisoned) |
| Full-query poison fixture | `kb_poison_proration_override` |
| Partial-query poison fixture | `kb_poison_refund_escalation` |
| Ground-truth proration fixture | `kb_invoice_proration` |
| Default SIR threshold ($\theta$) | `0.35` |
| Default retrieval depth ($k$) | `5` |
| Evaluated retriever baselines | 6 (`legacy_bm25`, `legacy_dense_bi_encoder`, `legacy_hybrid_rrf`, `lexical_overlap_filter_tau_0_95`, `mmr_diversity_rerank`, `orthocore_subspace_disentangled`) |

## Four operating phases

The default run emits 10 frames covering ingestion, gated retrieval, the
6-retriever benchmark, and loopback HTTP citation controls:

| Phase | Steps | Expected result |
| --- | --- | --- |
| `corpus-ingest` | 7 | Ingests 5 clean passages ($\text{SIR} < 0.35$) and flags 2 poisoned passages ($\text{SIR} \ge 0.35$) |
| `gated-retrieval` | 1 | Quarantines both poisoned passages and ranks `kb_invoice_proration` #1 in admitted context |
| `retriever-benchmark` | 1 | Evaluates all 6 retrievers across 4 graded queries; `orthocore_subspace_disentangled` achieves `0.0%` poison exposure and clean `nDCG@5 >= 0.95` |
| `citation-gate-controls` | 1 | Loopback HTTP gate accepts verified admitted citations (`200`) and rejects poisoned citations (`422`), cross-tenant citations (`403`), directive-injected synthesis (`422`), and replayed receipts (`409`) |

## Live service verification

When authorized against a live EigenScope deployment, run:

```bash
uv run --frozen eigenscope-lab live --case rag --output results/rag-live.json
```

The live `--case rag` workflow exercises both the SDK sanitization round-trip on
`SB-RAG-150` and the `/v1/rag/ingest`, `/v1/rag/retrieve`, and `/v1/rag/evaluate`
HTTP endpoints with Ed25519 sealed receipt verification.
