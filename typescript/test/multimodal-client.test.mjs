/** Unit tests for the TypeScript multimodal HTTP reference client against loopback HTTP. */
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import { crc32, deflateSync } from "node:zlib";
import test from "node:test";
import {
  buildSyntheticOcvcClip,
  buildSyntheticPngRgba,
  buildSyntheticWav16k,
  createMultimodalHttpClient,
  decodePngRgba,
  MultimodalContractError,
  sha256Prefixed,
} from "../examples/multimodal-client.ts";

const pngSignature = buildSyntheticPngRgba(2, 2).subarray(0, 8);
const rgbaHeader = buildSyntheticPngRgba(2, 2).subarray(16, 29);
function chunk(type, data) {
  const body = Buffer.concat([Buffer.from(type, "latin1"), Buffer.from(data)]);
  const length = Buffer.alloc(4),
    checksum = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  checksum.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, checksum]);
}
function pngFromChunks(chunks) {
  return Buffer.concat([pngSignature, ...chunks.map(([type, data]) => chunk(type, data))]);
}
const pixels = Buffer.from([10, 20, 30, 255, 40, 50, 60, 255, 15, 25, 35, 255, 45, 55, 65, 255]);
const filteredRows = [
  [0, 10, 20, 30, 255, 40, 50, 60, 255, 0, 15, 25, 35, 255, 45, 55, 65, 255],
  [1, 10, 20, 30, 255, 30, 30, 30, 0, 1, 15, 25, 35, 255, 30, 30, 30, 0],
  [2, 10, 20, 30, 255, 40, 50, 60, 255, 2, 5, 5, 5, 0, 5, 5, 5, 0],
  [3, 10, 20, 30, 255, 35, 40, 45, 128, 3, 10, 15, 20, 128, 18, 18, 18, 0],
  [4, 10, 20, 30, 255, 30, 30, 30, 0, 4, 5, 5, 5, 0, 5, 5, 5, 0],
];
const encodedRows = deflateSync(Buffer.from(filteredRows[0]));
const validPng = pngFromChunks([
  ["IHDR", rgbaHeader],
  ["IDAT", encodedRows],
  ["IEND", []],
]);
for (let filter = 0; filter <= 4; filter++) {
  test(`PNG filter ${filter} decodes to the expected RGBA samples`, () => {
    const png = pngFromChunks([
      ["IHDR", rgbaHeader],
      ["IDAT", deflateSync(Buffer.from(filteredRows[filter]))],
      ["IEND", []],
    ]);
    const decoded = decodePngRgba(png);
    assert.equal(decoded.width, 2);
    assert.equal(decoded.height, 2);
    assert.deepEqual(decoded.rgba, pixels);
  });
}
const corruptCrc = Buffer.from(validPng);
corruptCrc[corruptCrc.length - 1] ^= 1;
const hugeHeader = Buffer.from(rgbaHeader);
hugeHeader.writeUInt32BE(8192, 0);
hugeHeader.writeUInt32BE(8192, 4);
const invalidFilter = Buffer.from(filteredRows[0]);
invalidFilter[0] = 5;
const malformedPngs = [
  ["bad IEND checksum", corruptCrc],
  ["IHDR only", validPng.subarray(0, 33)],
  ["truncated chunk", validPng.subarray(0, -3)],
  [
    "missing IDAT",
    pngFromChunks([
      ["IHDR", rgbaHeader],
      ["IEND", []],
    ]),
  ],
  [
    "duplicate IHDR",
    pngFromChunks([
      ["IHDR", rgbaHeader],
      ["IHDR", rgbaHeader],
      ["IDAT", encodedRows],
      ["IEND", []],
    ]),
  ],
  [
    "unknown critical chunk",
    pngFromChunks([
      ["IHDR", rgbaHeader],
      ["ABCD", []],
      ["IDAT", encodedRows],
      ["IEND", []],
    ]),
  ],
  [
    "non-ASCII chunk type",
    pngFromChunks([
      ["IHDR", rgbaHeader],
      ["t\u00c5Xt", []],
      ["IDAT", encodedRows],
      ["IEND", []],
    ]),
  ],
  [
    "nonconsecutive IDAT",
    pngFromChunks([
      ["IHDR", rgbaHeader],
      ["IDAT", encodedRows.subarray(0, 4)],
      ["tEXt", []],
      ["IDAT", encodedRows.subarray(4)],
      ["IEND", []],
    ]),
  ],
  [
    "nonempty IEND",
    pngFromChunks([
      ["IHDR", rgbaHeader],
      ["IDAT", encodedRows],
      ["IEND", [0]],
    ]),
  ],
  ["trailing bytes", Buffer.concat([validPng, Buffer.from([0])])],
  [
    "truncated zlib stream",
    pngFromChunks([
      ["IHDR", rgbaHeader],
      ["IDAT", encodedRows.subarray(0, -1)],
      ["IEND", []],
    ]),
  ],
  [
    "concatenated zlib stream",
    pngFromChunks([
      ["IHDR", rgbaHeader],
      ["IDAT", Buffer.concat([encodedRows, encodedRows])],
      ["IEND", []],
    ]),
  ],
  [
    "short decoded data",
    pngFromChunks([
      ["IHDR", rgbaHeader],
      ["IDAT", deflateSync(Buffer.alloc(17))],
      ["IEND", []],
    ]),
  ],
  [
    "oversized decoded data",
    pngFromChunks([
      ["IHDR", rgbaHeader],
      ["IDAT", deflateSync(Buffer.alloc(1024 * 1024))],
      ["IEND", []],
    ]),
  ],
  [
    "invalid row filter",
    pngFromChunks([
      ["IHDR", rgbaHeader],
      ["IDAT", deflateSync(invalidFilter)],
      ["IEND", []],
    ]),
  ],
  [
    "excessive geometry",
    pngFromChunks([
      ["IHDR", hugeHeader],
      ["IDAT", encodedRows],
      ["IEND", []],
    ]),
  ],
];
for (const [name, png] of malformedPngs) {
  test(`PNG rejects ${name}`, () => assert.throws(() => decodePngRgba(png)));
}

test("rejected projected PNG data keeps only owned tickets available for cleanup", async () => {
  const responses = [corruptCrc, validPng.subarray(0, 33)];
  let issued = 0,
    cleanups = 0;
  const server = createServer(async (req, res) => {
    for await (const _ of req) {
    }
    res.setHeader("Content-Type", "application/json");
    if (req.url === "/api/workbench/vault-zeroize") {
      cleanups++;
      res.end(JSON.stringify({ vault_ticket_scrubbed: true }));
    } else {
      const projected = responses[issued++];
      res.end(
        JSON.stringify({
          vault_ticket: `vlt_loopback_badpng_${issued}`,
          projected_media_base64: projected.toString("base64"),
        }),
      );
    }
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const client = createMultimodalHttpClient({
    baseUrl: `http://127.0.0.1:${server.address().port}`,
    bearerToken: "synthetic-loopback-token",
  });
  try {
    for (const _ of responses) {
      try {
        await assert.rejects(
          () => client.decomposeMedia("image_frame", { width: 2, height: 2 }),
          (error) =>
            error instanceof MultimodalContractError &&
            error.message === "Projected media format or dimensions are invalid",
        );
        assert.equal(client.ownedTickets.size, 1);
      } finally {
        assert.deepEqual(await client.cleanupOwned(), [
          { name: "owned_media_cleanup", passed: true },
        ]);
        assert.equal(client.ownedTickets.size, 0);
      }
    }
    assert.equal(cleanups, 2);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
});

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
  const maskedPng = buildSyntheticPngRgba(16, 16);
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

for (const modality of ["audio_wav", "image_frame", "video_clip"]) {
  for (const invalid of [false, true]) {
    test(`${modality} ${invalid ? "rejects invalid projected containers and cleans up" : "accepts valid projected geometry"}`, async () => {
      const original =
        modality === "audio_wav"
          ? buildSyntheticWav16k(1600)
          : modality === "image_frame"
            ? buildSyntheticPngRgba(16, 16)
            : buildSyntheticOcvcClip(2, 16, 16);
      const projected = !invalid
        ? original
        : modality === "audio_wav"
          ? original.subarray(0, 44)
          : modality === "image_frame"
            ? buildSyntheticPngRgba(16, 12)
            : Buffer.concat([original.subarray(0, 16), deflateSync(original.subarray(16))]);
      let cleanups = 0;
      const server = createServer(async (req, res) => {
        for await (const _ of req) {
        }
        res.setHeader("Content-Type", "application/json");
        if (req.url === "/api/workbench/vault-zeroize") {
          cleanups++;
          res.end(JSON.stringify({ vault_ticket_scrubbed: true }));
        } else {
          res.end(
            JSON.stringify({
              vault_ticket: "vlt_loopback_container_test",
              projected_media_base64: projected.toString("base64"),
              task_core: "Review the signal.",
            }),
          );
        }
      });
      server.listen(0, "127.0.0.1");
      await once(server, "listening");
      const client = createMultimodalHttpClient({
        baseUrl: `http://127.0.0.1:${server.address().port}`,
        bearerToken: "synthetic-loopback-token",
      });
      try {
        const run = () =>
          client.decomposeMedia(modality, {
            kind: modality,
            sample_rate_hz: 16000,
            channels: 1,
            width: 16,
            height: 16,
            frame_count: 2,
          });
        if (invalid) {
          await assert.rejects(
            run,
            (err) =>
              err instanceof MultimodalContractError &&
              err.message === "Projected media format or dimensions are invalid",
          );
        } else {
          assert.deepEqual((await run()).projectedBytes, original);
        }
        assert.equal(client.ownedTickets.size, 1);
      } finally {
        assert.deepEqual(await client.cleanupOwned(), [
          { name: "owned_media_cleanup", passed: true },
        ]);
        assert.equal(client.ownedTickets.size, 0);
        assert.equal(cleanups, 1);
        server.closeAllConnections();
        await new Promise((resolve) => server.close(resolve));
      }
    });
  }
}
