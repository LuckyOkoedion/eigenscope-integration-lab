/** Unit tests for the TypeScript multimodal HTTP reference client against loopback HTTP. */
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import test from "node:test";
import {
  buildSyntheticOcvcClip,
  buildSyntheticPngRgba,
  buildSyntheticWav16k,
  createMultimodalHttpClient,
  MultimodalContractError,
  sha256Prefixed,
} from "../examples/multimodal-client.ts";

test("synthetic WAV, PNG and OCVC builders emit canonical container headers", () => {
  const wav = buildSyntheticWav16k(1600);
  assert.equal(wav.subarray(0, 4).toString("ascii"), "RIFF");
  assert.equal(wav.subarray(8, 12).toString("ascii"), "WAVE");
  assert.equal(wav.byteLength, 44 + 3200);

  const png = buildSyntheticPngRgba(32, 24);
  assert.deepEqual([...png.subarray(0, 8)], [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

  const ocvc = buildSyntheticOcvcClip(3, 32, 24);
  assert.equal(ocvc.subarray(0, 4).toString("ascii"), "OCVC");
  assert.equal(ocvc[4], 1);
  assert.equal(ocvc.byteLength, 16 + 3 * 32 * 24 * 4);
});

test("multimodal HTTP client verifies SHA-256 restoration and scrubs unconsumed tickets", async () => {
  const rawPng = buildSyntheticPngRgba(16, 16);
  const maskedPng = buildSyntheticPngRgba(16, 12);
  let consumed = false;
  let zeroized = false;

  const server = createServer(async (req, res) => {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Connection", "close");

    if (req.url === "/v1/decompose/image") {
      if (body.input_payload?.frame_base64 === "TRIGGER_503") {
        res.writeHead(503);
        res.end(
          JSON.stringify({
            error_code: "CORE_AUTHORITY_UNAVAILABLE",
            reason_code: "SURFACE_DENIED_UNTIL_QUALIFIED",
          }),
        );
        return;
      }
      res.writeHead(200);
      res.end(
        JSON.stringify({
          schema_version: 1,
          vault_ticket: "vlt_loopback_media_test",
          redacted_frame_base64: maskedPng.toString("base64"),
          task_core: "Verify waveform.",
        }),
      );
      return;
    }
    if (req.url === "/v1/rehydrate/media") {
      if (consumed) {
        res.writeHead(410);
        res.end(
          JSON.stringify({
            error_code: "VAULT_TICKET_SCRUBBED",
            reason_code: "TICKET_ALREADY_CONSUMED",
          }),
        );
        return;
      }
      consumed = true;
      res.writeHead(200);
      res.end(
        JSON.stringify({
          schema_version: 1,
          rehydrated_media_base64: rawPng.toString("base64"),
          round_trip_verification: { vault_ticket_scrubbed: true },
        }),
      );
      return;
    }
    if (req.url === "/api/workbench/vault-zeroize") {
      zeroized = true;
      res.writeHead(200);
      res.end(JSON.stringify({ vault_ticket_scrubbed: true }));
      return;
    }
    res.writeHead(404);
    res.end(JSON.stringify({ error_code: "NOT_FOUND" }));
  });

  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  try {
    const addr = server.address();
    const client = createMultimodalHttpClient({
      baseUrl: `http://127.0.0.1:${addr.port}`,
      bearerToken: "synthetic-loopback-token",
    });

    const dec = await client.decomposeMedia("image_frame", {
      kind: "image_frame",
      frame_base64: rawPng.toString("base64"),
      width: 16,
      height: 16,
    });
    assert.equal(client.ownedTickets.size, 1);
    const reh = await client.rehydrateMedia({
      vaultTicket: dec.vaultTicket,
      modality: "image_frame",
      projectedBase64: dec.projectedBase64,
      llmTaskOutput: dec.taskCore,
    });
    assert.equal(reh.scrubbed, true);
    assert.equal(sha256Prefixed(reh.rehydratedBytes), sha256Prefixed(rawPng));
    assert.equal(client.ownedTickets.size, 0);

    // Replay must reject with 410 VAULT_TICKET_SCRUBBED
    await assert.rejects(
      client.rehydrateMedia({
        vaultTicket: dec.vaultTicket,
        modality: "image_frame",
        projectedBase64: dec.projectedBase64,
        llmTaskOutput: dec.taskCore,
      }),
      (err) => err instanceof MultimodalContractError && err.http_status === 410,
    );

    // 503 structured error attribution without raw message leak
    await assert.rejects(
      client.decomposeMedia("image_frame", {
        kind: "image_frame",
        frame_base64: "TRIGGER_503",
        width: 16,
        height: 16,
      }),
      (err) =>
        err instanceof MultimodalContractError &&
        err.http_status === 503 &&
        err.error_code === "CORE_AUTHORITY_UNAVAILABLE" &&
        err.reason_code === "SURFACE_DENIED_UNTIL_QUALIFIED",
    );

    // Unconsumed ticket cleanup via zeroize
    await client.decomposeMedia("image_frame", {
      kind: "image_frame",
      frame_base64: rawPng.toString("base64"),
      width: 16,
      height: 16,
    });
    const cleanup = await client.cleanupOwned();
    assert.deepEqual(cleanup, [{ name: "owned_media_cleanup", passed: true }]);
    assert.equal(zeroized, true);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
});
