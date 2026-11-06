/** Live multimodal HTTP contract check: synthetic WAV, PNG and OCVC round-trips. */
import {
  buildSyntheticOcvcClip,
  buildSyntheticPngRgba,
  buildSyntheticWav16k,
  createMultimodalHttpClient,
  MultimodalContractError,
  sha256Prefixed,
  type MediaModality,
} from "./multimodal-client.ts";

const rawBase = process.env.ORTHOCORE_BASE_URL ?? "https://eigenscope.guardianai.network";
const key = process.env.ORTHOCORE_API_KEY;
if (!key || key.trim() !== key || /[\r\n]/.test(key)) {
  throw new Error("Configure ORTHOCORE_API_KEY securely");
}

const client = createMultimodalHttpClient({ baseUrl: rawBase, bearerToken: key });
const wav = buildSyntheticWav16k();
const png = buildSyntheticPngRgba();
const ocvc = buildSyntheticOcvcClip();

const fixtures: Array<{
  id: string;
  modality: MediaModality;
  rawBytes: Buffer;
  payload: Record<string, unknown>;
}> = [
  {
    id: "multimodal-audio",
    modality: "audio_wav",
    rawBytes: wav,
    payload: {
      kind: "audio_wav",
      wav_base64: wav.toString("base64"),
      sample_rate_hz: 16000,
      channels: 1,
      transcript: "Cardiology intake for Elena Rostova MRN-7741092: repeat labs in 48 hours.",
      transcript_words: [
        { word: "Elena", start_sample: 1600, end_sample: 3200 },
        { word: "Rostova", start_sample: 3250, end_sample: 4800 },
      ],
    },
  },
  {
    id: "multimodal-image",
    modality: "image_frame",
    rawBytes: png,
    payload: {
      kind: "image_frame",
      frame_base64: png.toString("base64"),
      width: 64,
      height: 48,
      ocr_regions: [
        { text: "Elena Rostova MRN-7741092", x: 8, y: 6, width: 36, height: 14, frame: 0 },
      ],
    },
  },
  {
    id: "multimodal-video",
    modality: "video_clip",
    rawBytes: ocvc,
    payload: {
      kind: "video_clip",
      clip_base64: ocvc.toString("base64"),
      width: 64,
      height: 48,
      frame_count: 4,
      ocr_regions: [{ text: "BADGE-99412", x: 8, y: 8, width: 32, height: 12, frame: 0 }],
    },
  },
];

const results: Array<Record<string, unknown>> = [];
for (const item of fixtures) {
  const checks: Array<Record<string, unknown>> = [];
  const started = performance.now();
  try {
    const dec = await client.decomposeMedia(item.modality, item.payload);
    checks.push({
      name: "projected_media_differs_from_raw",
      passed: sha256Prefixed(dec.projectedBytes) !== sha256Prefixed(item.rawBytes),
    });
    const reh = await client.rehydrateMedia({
      vaultTicket: dec.vaultTicket,
      modality: item.modality,
      projectedBase64: dec.projectedBase64,
      llmTaskOutput: dec.taskCore,
    });
    checks.push({
      name: "bit_exact_sha256_restoration_and_scrub",
      passed: reh.scrubbed && sha256Prefixed(reh.rehydratedBytes) === sha256Prefixed(item.rawBytes),
    });
  } catch (error: unknown) {
    const info =
      error instanceof MultimodalContractError
        ? {
            http_status: error.http_status,
            error_code: error.error_code,
            reason_code: error.reason_code,
          }
        : { exception: error instanceof Error ? error.name : "UnknownError" };
    checks.push({ name: "multimodal_workflow_completed", passed: false, ...info });
  } finally {
    checks.push(...(await client.cleanupOwned()));
  }
  results.push({
    case: item.id,
    modality: item.modality,
    status: checks.every((c) => c.passed === true) ? "PASS" : "FAIL",
    checks,
    wall_ms: Math.round((performance.now() - started) * 1000) / 1000,
  });
}

console.log(
  JSON.stringify(
    {
      schema_version: "1.0",
      generated_at_utc: new Date().toISOString(),
      scope:
        "Public /v1 multimodal HTTP endpoints; deterministic synthetic media; single-use ticket cleanup; no secrets exported.",
      results,
    },
    null,
    2,
  ),
);
process.exitCode = results.every((r) => r.status === "PASS") ? 0 : 1;
