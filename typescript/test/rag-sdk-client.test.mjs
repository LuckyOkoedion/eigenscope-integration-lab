import assert from "node:assert/strict";
import test from "node:test";
import { runRagSdkWorkflow } from "../examples/rag-sdk-client.ts";

function workflowClient({
  rejectIngest = false,
  wrongRanking = false,
  wrongInventory = false,
  cleanup = true,
} = {}) {
  const calls = [];
  let first;
  const tickets = new Set(["prior-owned-record"]);
  return {
    calls,
    ownedTickets: tickets,
    client: {
      async ragIngest(params) {
        calls.push(["ingest", params]);
        first ??= params.docId;
        tickets.add(params.docId);
        if (rejectIngest)
          throw Object.assign(new Error("UPSTREAM_BODY_MUST_STAY_LOCAL /internal/path"), {
            http_status: 502,
          });
        return { body: { doc_id: params.docId } };
      },
      async ragRetrieve(params) {
        calls.push(["retrieve", params]);
        return { body: { admitted_context: [{ doc_id: wrongRanking ? "other" : first }] } };
      },
      async ragEvaluate(params) {
        calls.push(["evaluate", params]);
        return {
          body: {
            documents: 2,
            queries: 1,
            rows: [
              "legacy_bm25",
              "legacy_dense_bi_encoder",
              "legacy_hybrid_rrf",
              "lexical_overlap_filter_tau_0_95",
              "mmr_diversity_rerank",
              "orthocore_subspace_disentangled",
            ].map((retriever) => ({
              retriever: wrongInventory ? "orthocore_subspace_disentangled" : retriever,
            })),
          },
        };
      },
    },
    async cleanupSince(before) {
      assert.deepEqual([...before], ["prior-owned-record"]);
      calls.push(["cleanup"]);
      for (const ticket of [...tickets]) if (!before.has(ticket) && cleanup) tickets.delete(ticket);
      return [{ name: "owned_cleanup", passed: cleanup }];
    },
  };
}

test("RAG workflow invokes all official methods and scopes cleanup to its own mappings", async () => {
  const owned = workflowClient();
  const result = await runRagSdkWorkflow(owned);
  assert.equal(result.status, "PASS");
  assert.deepEqual(
    owned.calls.map(([name]) => name),
    ["ingest", "ingest", "retrieve", "evaluate", "cleanup"],
  );
  assert.deepEqual([...owned.ownedTickets], ["prior-owned-record"]);
  const documents = owned.calls[3][1].documents;
  assert.equal(new Set(documents.map((doc) => doc.doc_id)).size, 2);
  assert.equal(
    documents.some((doc) => /poison|trust|private/i.test(doc.text)),
    false,
  );
  assert.equal(JSON.stringify(result).includes(documents[0].doc_id), false);
});

test("SDK rejection remains failed while retrieval, evaluation and owned cleanup still execute", async () => {
  const owned = workflowClient({ rejectIngest: true });
  const result = await runRagSdkWorkflow(owned);
  assert.equal(result.status, "FAIL");
  assert.equal(
    result.checks.filter((row) => row.name === "rag_sdk_ingest" && !row.passed).length,
    2,
  );
  assert.equal(result.checks.find((row) => row.name === "rag_sdk_retrieve").passed, true);
  assert.equal(result.checks.find((row) => row.name === "rag_sdk_evaluate").passed, true);
  assert.deepEqual([...owned.ownedTickets], ["prior-owned-record"]);
  assert.equal(JSON.stringify(result).includes("UPSTREAM_BODY"), false);
  assert.equal(JSON.stringify(result).includes("/internal/path"), false);
});

for (const options of [{ wrongRanking: true }, { wrongInventory: true }, { cleanup: false }]) {
  test(`RAG workflow retains failed expectation ${JSON.stringify(options)}`, async () => {
    const result = await runRagSdkWorkflow(workflowClient(options));
    assert.equal(result.status, "FAIL");
  });
}
