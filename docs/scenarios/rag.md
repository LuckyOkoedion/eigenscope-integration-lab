# RAG & Knowledge Retrieval: SIR quarantine gate and citation verification

Run an in-memory multi-retriever RAG knowledge base, compute vector norms
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

The local reference gate partitions sentences using directive patterns and
question echoes coupled to those directives. It builds BM25-weighted vectors,
projects the retained content through a truncated-SVD basis, and measures the
actual L2 norms of retained, anchor and projection-residual vectors. Document IDs,
evaluation labels and target-query metadata do not determine rankings or gates.
The Subspace Incongruity Ratio compares anchor magnitude to projected task content:

```text
SIR(d) = ||d_anchor||_2 / ||d_task||_2
```

Passages with $\text{SIR}(d) \ge \theta$ (default $\theta = 0.35$) are marked
`quarantined = true` at ingest and filtered prior to synthesis, preventing
corpus-poisoning chunks that repeat or paraphrase user questions from capturing
top-$k$ retrieval slots.

SIR is a nonnegative ratio and can exceed one. The local sentence/vector model
provides a repeatable retrieval boundary for the lab; the live command below
exercises the configured EigenScope runtime.

| Parameter | Synthetic lab value |
| --- | --- |
| Seed corpus size | 7 passages (5 clean, 2 poisoned) |
| Full-query poison fixture | `doc_47ac` |
| Partial-query poison fixture | `doc_b953` |
| Ground-truth proration fixture | `doc_6d0f` |
| Default SIR threshold ($\theta$) | `0.35` |
| Default retrieval depth ($k$) | `5` |
| Evaluated retriever baselines | 6 (`legacy_bm25`, `legacy_dense_bi_encoder`, `legacy_hybrid_rrf`, `lexical_overlap_filter_tau_0_95`, `mmr_diversity_rerank`, `orthocore_subspace_disentangled`) |

The six rows execute separate algorithms: BM25 lexical scoring, dense LSA cosine
similarity, reciprocal rank fusion (`k=60`), a first-sentence query-overlap filter
(Jaccard threshold `0.95`), maximal marginal relevance (`λ=0.65`), and the task
projection with SIR gate. `legacy_dense_bi_encoder` remains a result identifier
for compatibility; its display name identifies the actual local LSA algorithm.

Clean nDCG uses gain `2^grade − 1` discounted by `log2(rank + 1)`. A poisoned
passage contributes zero gain in its original rank position. Exposure counts
only attacks targeting the evaluated query; full-query and partial-query rates
each include their own target counts in the report.

## Four operating phases

The default run emits 10 frames covering ingestion, gated retrieval, the
6-retriever benchmark, and loopback HTTP citation controls:

| Phase | Steps | Expected result |
| --- | --- | --- |
| `corpus-ingest` | 7 | Ingests 5 clean passages ($\text{SIR} < 0.35$) and flags 2 poisoned passages ($\text{SIR} \ge 0.35$) |
| `gated-retrieval` | 1 | Quarantines both poisoned passages and ranks `doc_6d0f` #1 in admitted context |
| `retriever-benchmark` | 1 | Evaluates all 6 retrievers across 2 graded queries; the seeded task-subspace gate is checked for `0.0%` poison exposure and clean `nDCG@5 >= 0.95` |
| `citation-gate-controls` | 1 | Loopback HTTP gate accepts verified admitted citations (`200`) and rejects poisoned citations (`422`), cross-tenant citations (`403`), directive-injected synthesis (`422`), and replayed receipts (`409`) |

## Live service verification

When authorized against a live EigenScope deployment, run:

```bash
uv run --frozen eigenscope-lab live --case rag --output results/rag-live.json
```

The live `--case rag` workflow exercises the SDK sanitization round-trip on
`SB-RAG-150`, the official SDK's `rag_ingest`, `rag_retrieve` and `rag_evaluate`
methods, and the matching `/v1/rag/ingest`, `/v1/rag/retrieve` and `/v1/rag/evaluate`
HTTP endpoints with receipt-envelope consistency checks.

## References

- [BM25: Robertson and Zaragoza](https://www.staff.city.ac.uk/~sbrp622/papers/foundations_bm25_review.pdf).
- [Reciprocal rank fusion: Cormack, Clarke and Büttcher](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf).
- [Maximal marginal relevance: Carbonell and Goldstein](https://www.cs.cmu.edu/afs/cs/Web/People/jgc/publication/MMR_DiversityBased_Reranking_SIGIR_1998.pdf).
- [Graded cumulative-gain evaluation: Järvelin and Kekäläinen](https://researchportal.tuni.fi/en/publications/cumulated-gain-based-evaluation-of-ir-techniques).
