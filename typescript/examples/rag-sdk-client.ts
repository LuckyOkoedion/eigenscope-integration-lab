/** Execute ingestion, retrieval and evaluation through the official RAG SDK methods. */
import { randomUUID } from "node:crypto";
import type { EigenScopeTypeScriptClient } from "@eigenscope/sdk";
import type { CleanupCheck } from "./owned-client.ts";

type OwnedClient = {
  client: Pick<EigenScopeTypeScriptClient, "ragIngest" | "ragRetrieve" | "ragEvaluate">;
  ownedTickets: ReadonlySet<string>;
  cleanupSince(before: ReadonlySet<string>): Promise<CleanupCheck[]>;
};
type Check = { name: string; passed: boolean; exception?: string; http_status?: number };

export async function runRagSdkWorkflow(owned: OwnedClient) {
  const suffix = randomUUID().replaceAll("-", "");
  const documents = [
    {
      doc_id: `doc_${suffix}_1`,
      title: "Seat changes",
      text: "Seat upgrades are prorated for the unused portion of the billing month.",
    },
    {
      doc_id: `doc_${suffix}_2`,
      title: "Invoice delivery",
      text: "Invoice copies are available from the billing dashboard in PDF format.",
    },
  ];
  const query = "How are seat upgrades prorated during the billing month?";
  const checks: Check[] = [];
  const before = new Set(owned.ownedTickets);
  const started = performance.now();
  const stage = async (name: string, execute: () => Promise<boolean>) => {
    try {
      checks.push({ name, passed: await execute() });
    } catch (error: unknown) {
      const status =
        error && typeof error === "object" && "http_status" in error
          ? error.http_status
          : undefined;
      checks.push({
        name,
        passed: false,
        exception: error instanceof Error ? error.name : "UnknownError",
        ...(typeof status === "number" && Number.isInteger(status) ? { http_status: status } : {}),
      });
    }
  };
  try {
    for (const doc of documents) {
      await stage("rag_sdk_ingest", async () => {
        const result = await owned.client.ragIngest({
          docId: doc.doc_id,
          title: doc.title,
          text: doc.text,
          sirThreshold: 0.35,
        });
        return result.body.doc_id === doc.doc_id;
      });
    }
    await stage("rag_sdk_retrieve", async () => {
      const result = await owned.client.ragRetrieve({
        query,
        topK: 2,
        candidatePoolN: 2,
        sirThreshold: 0.35,
      });
      return result.body.admitted_context.some((doc) => doc.doc_id === documents[0].doc_id);
    });
    await stage("rag_sdk_evaluate", async () => {
      const result = await owned.client.ragEvaluate({
        documents,
        queries: [{ query_id: "query_1", text: query, grades: { [documents[0].doc_id]: 3 } }],
        topK: 2,
        sirThreshold: 0.35,
      });
      return (
        result.body.documents === documents.length &&
        result.body.queries === 1 &&
        [
          "legacy_bm25",
          "legacy_dense_bi_encoder",
          "legacy_hybrid_rrf",
          "lexical_overlap_filter_tau_0_95",
          "mmr_diversity_rerank",
          "orthocore_subspace_disentangled",
        ].every((name) => result.body.rows.filter((row) => row.retriever === name).length === 1) &&
        result.body.rows.length === 6
      );
    });
  } finally {
    checks.push(...(await owned.cleanupSince(before)));
  }
  return {
    case: "rag-sdk",
    status: checks.length >= 4 && checks.every((check) => check.passed === true) ? "PASS" : "FAIL",
    checks,
    sdk_workflow_wall_ms: Math.round((performance.now() - started) * 1000) / 1000,
  };
}
