/** Actual published SDK validators against explicitly synthetic wire responses. */
import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "node:http";
import { once } from "node:events";
import {
  EigenScopeTypeScriptClient as EigenScopeClient,
  computeSymbolTableCommitment,
} from "@eigenscope/sdk";

const digest = "sha256:" + "0".repeat(64);

async function fixture(request) {
  return {
    schema_version: 1,
    request_id: request.request_id,
    status: "DECOMPOSED_OK",
    task_core: "Compute 17 * 24.",
    vault_ticket: "vlt_synthetic_lab_only",
    private_symbol_summary: {
      symbol_count: 0,
      symbol_keys: [],
      custody_mode: "LOCAL_MEMORY_VAULT_ONLY",
      symbol_table_commitment: await computeSymbolTableCommitment({}),
    },
    core_proof_digest: digest,
    style_vector: {
      urgency_pressure: 0,
      authority_framing: 0,
      sycophancy_pull: 0,
      verbosity_inflation: 0,
      register_tag: "neutral_institutional_brief",
    },
    noise_residual: { dropped_spans: [], filler_tokens_removed: 0 },
    telemetry: {
      task_sufficiency_eta: 1,
      mi_leakage_bound_bits: 0,
      token_compression_ratio_rho: 0,
      raw_token_count: 4,
      task_core_token_count: 4,
      latency_ms: 1,
    },
    token_saliency: [],
  };
}

function clientWith(change = () => {}) {
  let calls = 0;
  const client = new EigenScopeClient({
    bearerToken: "synthetic-local-only",
    transport: async (_path, _headers, body) => {
      calls++;
      const value = await fixture(JSON.parse(body));
      change(value);
      return { status: 200, bodyText: JSON.stringify(value) };
    },
  });
  return { client, calls: () => calls };
}

test("valid synthetic wire fixture is accepted by actual SDK", async () => {
  const { client } = clientWith();
  const result = await client.decompose({ text: "Compute 17 * 24.", returnTokenSaliency: false });
  assert.equal(result.status, "DECOMPOSED_OK");
});

test("missing bearer is rejected before any network dispatch", async () => {
  let called = false;
  const client = new EigenScopeClient({
    transport: async () => {
      called = true;
      throw new Error("unexpected");
    },
  });
  await assert.rejects(client.decompose({ text: "Synthetic input" }));
  assert.equal(called, false);
});

for (const [name, mutate] of [
  [
    "wrong token compression",
    (r) => {
      r.telemetry.token_compression_ratio_rho = 0.5;
    },
  ],
  [
    "MI above ceiling",
    (r) => {
      r.telemetry.mi_leakage_bound_bits = 1;
    },
  ],
  [
    "task sufficiency below floor",
    (r) => {
      r.telemetry.task_sufficiency_eta = 0.1;
    },
  ],
  [
    "negative latency",
    (r) => {
      r.telemetry.latency_ms = -1;
    },
  ],
  [
    "fractional raw tokens",
    (r) => {
      r.telemetry.raw_token_count = 4.5;
    },
  ],
  [
    "unexpected response field",
    (r) => {
      r.unexpected = true;
    },
  ],
  [
    "invalid ticket",
    (r) => {
      r.vault_ticket = "not-a-ticket";
    },
  ],
  [
    "duplicate symbol keys",
    (r) => {
      r.private_symbol_summary.symbol_count = 2;
      r.private_symbol_summary.symbol_keys = ["[ENTITY_A]", "[ENTITY_A]"];
    },
  ],
  [
    "wrong custody mode",
    (r) => {
      r.private_symbol_summary.custody_mode = "REMOTE_RAW";
    },
  ],
  [
    "malformed commitment",
    (r) => {
      r.private_symbol_summary.symbol_table_commitment = "invalid";
    },
  ],
  [
    "unknown telemetry",
    (r) => {
      r.telemetry.raw_prompt = "synthetic";
    },
  ],
]) {
  test(`rejects ${name}`, async () => {
    const { client } = clientWith(mutate);
    await assert.rejects(
      client.decompose({ text: "Compute 17 * 24.", returnTokenSaliency: false }),
    );
  });
}

for (const text of ["", "x".repeat(131073)]) {
  test(`invalid input length ${text.length} never dispatches`, async () => {
    const fixtureClient = clientWith();
    await assert.rejects(fixtureClient.client.decompose({ text }));
    assert.equal(fixtureClient.calls(), 0);
  });
}

test("real fetch transport reaches a local HTTP fixture with authentication binding", async () => {
  const requests = [];
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString());
    requests.push({ path: request.url, authorization: request.headers.authorization });
    response.writeHead(200, { "Content-Type": "application/json", Connection: "close" });
    response.end(JSON.stringify(await fixture(body)));
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  try {
    const address = server.address();
    const client = new EigenScopeClient({
      baseUrl: `http://127.0.0.1:${address.port}`,
      bearerToken: "synthetic-local-only",
    });
    await client.decompose({ text: "Compute 17 * 24.", returnTokenSaliency: false });
    assert.deepEqual(requests, [
      { path: "/v1/decompose", authorization: "Bearer synthetic-local-only" },
    ]);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
});
