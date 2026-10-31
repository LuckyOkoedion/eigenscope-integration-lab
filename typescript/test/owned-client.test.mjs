/** Cleanup and transport controls exercised through the real SDK and local HTTP. */
import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "node:http";
import { once } from "node:events";
import { createOwnedClient, validateOrigin } from "../examples/owned-client.ts";

async function localServer(handler) {
  const requests = [];
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString());
    requests.push({ path: request.url, body });
    handler(request, response, body, requests.length);
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return {
    origin: `http://127.0.0.1:${server.address().port}`,
    requests,
    async close() {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

function send(response, body, status = 200) {
  response.writeHead(status, { "Content-Type": "application/json", Connection: "close" });
  response.end(JSON.stringify(body));
}

test("SDK-rejected decomposition still cleans its owned ticket and preserves prior ownership", async () => {
  let decompositions = 0;
  const server = await localServer((request, response, body) => {
    if (request.url === "/v1/decompose") {
      decompositions++;
      // This is deliberately incomplete. The actual SDK must reject it before
      // registering the returned handle in its private ticket map.
      send(response, {
        schema_version: 1,
        request_id: body.request_id,
        status: "DECOMPOSED_OK",
        task_core: "synthetic safe task",
        vault_ticket: decompositions === 1 ? "vlt_prior_synthetic" : "vlt_current_synthetic",
      });
    } else {
      assert.equal(request.url, "/v1/vault/scrub");
      send(response, {
        schema_version: 1,
        request_id: body.request_id,
        status: "VAULT_TICKET_SCRUBBED",
        vault_ticket: body.vault_ticket,
        vault_ticket_scrubbed: true,
      });
    }
  });
  try {
    const owned = createOwnedClient({
      baseUrl: server.origin,
      bearerToken: "synthetic-local-only",
    });
    await assert.rejects(owned.client.decompose({ text: "prior synthetic input" }));
    const before = new Set(owned.ownedTickets);
    await assert.rejects(owned.client.decompose({ text: "current synthetic input" }));
    const checks = await owned.cleanupSince(before);
    assert.deepEqual(checks, [{ name: "owned_cleanup", passed: true }]);
    assert.deepEqual([...owned.ownedTickets], ["vlt_prior_synthetic"]);
    assert.deepEqual(
      server.requests.map((row) => row.path),
      ["/v1/decompose", "/v1/decompose", "/v1/vault/scrub"],
    );
    assert.equal(server.requests.at(-1).body.vault_ticket, "vlt_current_synthetic");
    assert.equal(JSON.stringify(checks).includes("vlt_"), false);
    await owned.cleanupSince(new Set());
  } finally {
    await server.close();
  }
});

test("cleanup requires success or a precise consumed-ticket denial", async () => {
  let scrubStatus = 400;
  const server = await localServer((request, response, body) => {
    if (request.url === "/v1/decompose") {
      send(response, {
        schema_version: 1,
        request_id: body.request_id,
        vault_ticket: "vlt_denial_synthetic",
      });
    } else {
      send(
        response,
        {
          error_code: scrubStatus === 410 ? "VAULT_TICKET_SCRUBBED" : "INVALID_REQUEST_SCHEMA",
          reason_code: scrubStatus === 410 ? "TICKET_ALREADY_CONSUMED" : "UNKNOWN_OR_MISSING_FIELD",
        },
        scrubStatus,
      );
    }
  });
  try {
    const owned = createOwnedClient({
      baseUrl: server.origin,
      bearerToken: "synthetic-local-only",
    });
    await assert.rejects(owned.client.decompose({ text: "synthetic input" }));
    assert.deepEqual(await owned.cleanupSince(new Set()), [
      { name: "owned_cleanup", passed: false },
    ]);
    assert.equal(owned.ownedTickets.size, 1);
    scrubStatus = 410;
    assert.deepEqual(await owned.cleanupSince(new Set()), [
      { name: "owned_cleanup", passed: true },
    ]);
    assert.equal(owned.ownedTickets.size, 0);
  } finally {
    await server.close();
  }
});

test("explicitly consumed owned ticket sends no redundant cleanup request", async () => {
  const server = await localServer((_request, response, body) => {
    send(response, {
      schema_version: 1,
      request_id: body.request_id,
      vault_ticket: "vlt_consumed_synthetic",
    });
  });
  try {
    const owned = createOwnedClient({
      baseUrl: server.origin,
      bearerToken: "synthetic-local-only",
    });
    await assert.rejects(owned.client.decompose({ text: "synthetic input" }));
    owned.markConsumed("vlt_consumed_synthetic");
    assert.deepEqual(await owned.cleanupSince(new Set()), []);
    assert.equal(server.requests.length, 1);
  } finally {
    await server.close();
  }
});

test("redirects are rejected before credentials can reach the destination", async () => {
  const destination = await localServer((_request, response) => send(response, {}));
  const server = await localServer((_request, response) => {
    response.writeHead(307, { Location: destination.origin + "/redirected", Connection: "close" });
    response.end();
  });
  try {
    const owned = createOwnedClient({
      baseUrl: server.origin,
      bearerToken: "synthetic-local-only",
    });
    await assert.rejects(owned.client.decompose({ text: "synthetic input" }));
    assert.equal(server.requests.length, 1);
    assert.equal(destination.requests.length, 0);
    assert.equal(owned.ownedTickets.size, 0);
  } finally {
    await server.close();
    await destination.close();
  }
});

test(
  "HTTP request timeout also covers a response body that never completes",
  { timeout: 3000 },
  async () => {
    const server = await localServer((_request, response) => {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.write("{");
    });
    try {
      const owned = createOwnedClient({
        baseUrl: server.origin,
        bearerToken: "synthetic-local-only",
        timeoutMs: 150,
      });
      await assert.rejects(owned.client.decompose({ text: "synthetic input" }));
      assert.equal(server.requests.length, 1);
      assert.equal(owned.ownedTickets.size, 0);
    } finally {
      await server.close();
    }
  },
);

test("origin validation rejects whitespace and non-origin URLs before fetch", () => {
  for (const origin of [
    "https://example.invalid\n",
    " https://example.invalid",
    "https://user:synthetic@example.invalid",
    "https://example.invalid/path",
    "https://example.invalid?key=synthetic",
    "http://example.invalid",
  ]) {
    assert.throws(() => validateOrigin(origin));
  }
  assert.equal(validateOrigin("https://example.invalid/"), "https://example.invalid");
});
