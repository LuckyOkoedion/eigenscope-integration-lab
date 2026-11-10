/** Reference HTTP client for public /v1 multimodal decomposition and rehydration. */
import { createHash, randomBytes } from "node:crypto";
import { deflateSync, inflateSync } from "node:zlib";
import { decodeOcvcClip, parsePngDimensions, parseWavHeader } from "@eigenscope/sdk";
import { validateOrigin } from "./owned-client.ts";

export type MediaModality = "audio_wav" | "image_frame" | "video_clip";

export interface MultimodalErrorInfo {
  http_status?: number;
  error_code?: string;
  reason_code?: string;
}

export class MultimodalContractError extends Error {
  readonly http_status?: number;
  readonly error_code?: string;
  readonly reason_code?: string;

  constructor(message: string, info: MultimodalErrorInfo = {}) {
    super(message);
    this.name = "MultimodalContractError";
    this.http_status = info.http_status;
    this.error_code = info.error_code;
    this.reason_code = info.reason_code;
  }
}

const CONTRACT_VERSION = "2026-10-18.v1";
const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const OCVC_MAGIC = Buffer.from([0x4f, 0x43, 0x56, 0x43, 0x01, 0x00]);

export function sha256Prefixed(bytes: Uint8Array): string {
  return "sha256:" + createHash("sha256").update(bytes).digest("hex");
}

export function uuidV7(): string {
  const buf = randomBytes(16);
  const now = BigInt(Date.now());
  buf[0] = Number((now >> 40n) & 0xffn);
  buf[1] = Number((now >> 32n) & 0xffn);
  buf[2] = Number((now >> 24n) & 0xffn);
  buf[3] = Number((now >> 16n) & 0xffn);
  buf[4] = Number((now >> 8n) & 0xffn);
  buf[5] = Number(now & 0xffn);
  buf[6] = (buf[6]! & 0x0f) | 0x70;
  buf[8] = (buf[8]! & 0x3f) | 0x80;
  const hex = buf.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function crc32(bytes: Uint8Array): number {
  let c = 0xffffffff;
  for (const byte of bytes) {
    c ^= byte;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  }
  return (c ^ 0xffffffff) >>> 0;
}

function pngChunk(typeAscii: string, data: Uint8Array): Buffer {
  const typeBuf = Buffer.from(typeAscii, "ascii");
  const lenBuf = Buffer.alloc(4);
  lenBuf.writeUInt32BE(data.byteLength, 0);
  const crcInput = Buffer.concat([typeBuf, Buffer.from(data)]);
  const crcBuf = Buffer.alloc(4);
  crcBuf.writeUInt32BE(crc32(crcInput), 0);
  return Buffer.concat([lenBuf, crcInput, crcBuf]);
}

export function decodePngRgba(bytes: Buffer): { width: number; height: number; rgba: Buffer } {
  const { width, height } = parsePngDimensions(bytes);
  if (width * height > 4096 * 4096 || bytes.length > 64 * 1024 * 1024) throw new Error();
  const parts: Buffer[] = [];
  let ended = false,
    idatClosed = false,
    seenPalette = false;
  for (let offset = 8; offset < bytes.length;) {
    if (offset + 12 > bytes.length) throw new Error();
    const length = bytes.readUInt32BE(offset);
    const end = offset + 12 + length;
    if (end > bytes.length) throw new Error();
    const type = bytes.toString("latin1", offset + 4, offset + 8);
    if (!/^[A-Za-z]{2}[A-Z][A-Za-z]$/.test(type)) throw new Error();
    const data = bytes.subarray(offset + 8, end - 4);
    if (crc32(bytes.subarray(offset + 4, end - 4)) !== bytes.readUInt32BE(end - 4))
      throw new Error();
    if (offset === 8 && type !== "IHDR") throw new Error();
    if (parts.length && type !== "IDAT") idatClosed = true;
    if (type === "IHDR") {
      if (
        offset !== 8 ||
        length !== 13 ||
        data[8] !== 8 ||
        data[9] !== 6 ||
        data[10] !== 0 ||
        data[11] !== 0 ||
        data[12] !== 0
      )
        throw new Error();
    } else if (type === "IDAT") {
      if (idatClosed) throw new Error();
      parts.push(data);
    } else if (type === "PLTE") {
      if (seenPalette || parts.length || length < 3 || length > 768 || length % 3)
        throw new Error();
      seenPalette = true;
    } else if (type === "IEND") {
      if (length !== 0 || end !== bytes.length || !parts.length) throw new Error();
      ended = true;
      break;
    } else if (type.charCodeAt(0) < 97) throw new Error();
    offset = end;
  }
  if (!ended) throw new Error();
  const rowBytes = width * 4,
    stride = rowBytes + 1,
    expected = height * stride;
  const compressed = Buffer.concat(parts);
  const inflated = inflateSync(compressed, {
    maxOutputLength: expected + 1,
    info: true,
  }) as unknown as {
    buffer: Buffer;
    engine: { bytesWritten: number };
  };
  const filtered = inflated.buffer;
  const rgba = Buffer.alloc(width * height * 4);
  try {
    if (filtered.length !== expected || inflated.engine.bytesWritten !== compressed.length)
      throw new Error();
    for (let y = 0; y < height; y++) {
      const filter = filtered[y * stride]!;
      if (filter > 4) throw new Error();
      for (let x = 0; x < rowBytes; x++) {
        const pos = y * rowBytes + x;
        const left = x >= 4 ? rgba[pos - 4]! : 0;
        const up = y ? rgba[pos - rowBytes]! : 0;
        const upperLeft = y && x >= 4 ? rgba[pos - rowBytes - 4]! : 0;
        let prediction = 0;
        if (filter === 1) prediction = left;
        else if (filter === 2) prediction = up;
        else if (filter === 3) prediction = Math.floor((left + up) / 2);
        else if (filter === 4) {
          const p = left + up - upperLeft;
          const a = Math.abs(p - left),
            b = Math.abs(p - up),
            c = Math.abs(p - upperLeft);
          prediction = a <= b && a <= c ? left : b <= c ? up : upperLeft;
        }
        rgba[pos] = (filtered[y * stride + x + 1]! + prediction) & 255;
      }
    }
    return { width, height, rgba };
  } catch (error) {
    rgba.fill(0);
    throw error;
  } finally {
    filtered.fill(0);
  }
}

export function buildSyntheticWav16k(durationSamples = 8000): Buffer {
  const sampleRate = 16000;
  const dataBytes = durationSamples * 2;
  const header = Buffer.alloc(44);
  header.write("RIFF", 0, "ascii");
  header.writeUInt32LE(36 + dataBytes, 4);
  header.write("WAVEfmt ", 8, "ascii");
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36, "ascii");
  header.writeUInt32LE(dataBytes, 40);
  const pcm = Buffer.alloc(dataBytes);
  for (let i = 0; i < durationSamples; i++) {
    const sample = Math.round(11000 * Math.sin((2 * Math.PI * 440 * i) / sampleRate));
    pcm.writeInt16LE(sample, i * 2);
  }
  return Buffer.concat([header, pcm]);
}

export function buildSyntheticPngRgba(width = 64, height = 48): Buffer {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  const raw = Buffer.alloc(height * (1 + width * 4));
  for (let y = 0; y < height; y++) {
    const rowOffset = y * (1 + width * 4);
    raw[rowOffset] = 0;
    for (let x = 0; x < width; x++) {
      const px = rowOffset + 1 + x * 4;
      const inBox = x >= 8 && x < 44 && y >= 6 && y < 20;
      raw[px] = inBox ? 42 : 240;
      raw[px + 1] = inBox ? 76 : 244;
      raw[px + 2] = inBox ? 118 : 248;
      raw[px + 3] = 255;
    }
  }
  const idat = deflateSync(raw);
  return Buffer.concat([
    PNG_SIGNATURE,
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", idat),
    pngChunk("IEND", new Uint8Array(0)),
  ]);
}

export function buildSyntheticOcvcClip(frameCount = 4, width = 64, height = 48): Buffer {
  const meta = Buffer.alloc(10);
  meta.writeUInt16BE(frameCount, 0);
  meta.writeUInt32BE(width, 2);
  meta.writeUInt32BE(height, 6);
  const frames = Buffer.alloc(frameCount * width * height * 4, 235);
  for (let i = 3; i < frames.length; i += 4) frames[i] = 255;
  return Buffer.concat([OCVC_MAGIC, meta, frames]);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function safeCode(value: unknown): string | undefined {
  return typeof value === "string" && /^[A-Z][A-Z0-9_]{0,79}$/.test(value) ? value : undefined;
}

export function createMultimodalHttpClient(options: {
  baseUrl: string;
  bearerToken: string;
  timeoutMs?: number;
}) {
  const origin = validateOrigin(options.baseUrl);
  const token = options.bearerToken;
  if (!token || token.trim() !== token || /[\r\n]/.test(token)) {
    throw new Error("Configure a valid bearerToken without whitespace");
  }
  const timeoutMs = options.timeoutMs ?? 15_000;
  const owned = new Set<string>();

  async function postJson(
    path: string,
    prefix: string,
    payload: Record<string, unknown>,
  ): Promise<{ status: number; data: Record<string, unknown> }> {
    const reqId = typeof payload.request_id === "string" ? payload.request_id : uuidV7();
    const response = await fetch(origin + path, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json; charset=utf-8",
        Accept: "application/json",
        "User-Agent": "EigenScope-Integration-Lab/0.2.0",
        "X-OrthoCore-Contract-Version": CONTRACT_VERSION,
        "X-OrthoCore-Request-Id": reqId,
        "X-OrthoCore-Idempotency-Key": `${prefix}-${reqId}`,
      },
      body: JSON.stringify(payload),
      redirect: "error",
      credentials: "omit",
      signal: AbortSignal.timeout(timeoutMs),
    });
    let parsed: unknown;
    try {
      parsed = await response.json();
    } catch {
      throw new MultimodalContractError("Non-JSON multimodal HTTP response", {
        http_status: response.status,
      });
    }
    if (!isRecord(parsed)) {
      throw new MultimodalContractError("Expected JSON object response", {
        http_status: response.status,
      });
    }
    if (
      response.status === 200 &&
      path.startsWith("/v1/decompose/") &&
      typeof parsed.vault_ticket === "string" &&
      parsed.vault_ticket.startsWith("vlt_") &&
      parsed.vault_ticket.length <= 512
    ) {
      owned.add(parsed.vault_ticket);
    }
    if (response.status !== 200) {
      throw new MultimodalContractError("Multimodal HTTP request rejected", {
        http_status: response.status,
        error_code: safeCode(parsed.error_code),
        reason_code: safeCode(parsed.reason_code),
      });
    }
    return { status: response.status, data: parsed };
  }

  function extractProjectedBase64(data: Record<string, unknown>): string {
    for (const key of [
      "task_projected_audio_base64",
      "task_projected_media_base64",
      "deidentified_wav_base64",
      "redacted_frame_base64",
      "redacted_clip_base64",
      "projected_media_base64",
    ]) {
      if (typeof data[key] === "string" && data[key]) return data[key] as string;
    }
    throw new MultimodalContractError("Missing projected media base64 in decompose response");
  }

  function extractRehydratedBase64(data: Record<string, unknown>): string {
    for (const key of ["rehydrated_wav_base64", "rehydrated_media_base64"]) {
      if (typeof data[key] === "string" && data[key]) return data[key] as string;
    }
    throw new MultimodalContractError("Missing rehydrated media base64 in rehydrate response");
  }

  return {
    ownedTickets: owned as ReadonlySet<string>,
    async decomposeMedia(
      modality: MediaModality,
      inputPayload: Record<string, unknown>,
    ): Promise<{
      vaultTicket: string;
      projectedBytes: Buffer;
      projectedBase64: string;
      taskCore: string;
    }> {
      const route =
        modality === "audio_wav"
          ? "/v1/decompose/audio"
          : modality === "image_frame"
            ? "/v1/decompose/image"
            : "/v1/decompose/video";
      const reqId = uuidV7();
      const { data } = await postJson(route, "mm-dec", {
        schema_version: 1,
        request_id: reqId,
        execution_mode: "vpc_sidecar",
        input_payload: inputPayload,
        separation_policy: {
          clamp_private: 1,
          clamp_style: 1,
          drop_noise: true,
          target_task_equivalencefloor: 0.995,
          max_mi_leakage_bits: 0.01,
        },
      });
      const vaultTicket = typeof data.vault_ticket === "string" ? data.vault_ticket : "";
      if (!vaultTicket.startsWith("vlt_")) {
        throw new MultimodalContractError("Missing valid vault_ticket in decompose response");
      }
      const projectedBase64 = extractProjectedBase64(data);
      const projectedBytes = Buffer.from(projectedBase64, "base64");
      try {
        if (modality === "audio_wav") {
          const wav = parseWavHeader(projectedBytes);
          if (
            wav.audio_format !== 1 ||
            wav.bits_per_sample !== 16 ||
            wav.sample_rate_hz !== inputPayload.sample_rate_hz ||
            wav.channels !== (inputPayload.channels ?? 1) ||
            wav.data_bytes < 1
          )
            throw new Error();
        } else if (modality === "image_frame") {
          const png = decodePngRgba(projectedBytes);
          try {
            if (png.width !== inputPayload.width || png.height !== inputPayload.height)
              throw new Error();
          } finally {
            png.rgba.fill(0);
          }
        } else {
          const clip = decodeOcvcClip(projectedBytes);
          try {
            if (
              clip.width !== inputPayload.width ||
              clip.height !== inputPayload.height ||
              clip.frame_count !== inputPayload.frame_count ||
              (clip.version === 1 &&
                projectedBytes.length !== 16 + clip.frame_count * clip.width * clip.height * 4)
            )
              throw new Error();
          } finally {
            clip.zeroize();
          }
        }
      } catch {
        throw new MultimodalContractError("Projected media format or dimensions are invalid");
      }
      const taskCore = typeof data.task_core === "string" ? data.task_core : "[TASK_CORE]";
      return { vaultTicket, projectedBytes, projectedBase64, taskCore };
    },
    async rehydrateMedia(params: {
      vaultTicket: string;
      modality: MediaModality;
      projectedBase64: string;
      llmTaskOutput: string;
    }): Promise<{ rehydratedBytes: Buffer; scrubbed: boolean }> {
      const reqId = uuidV7();
      const { data } = await postJson("/v1/rehydrate/media", "mm-reh", {
        schema_version: 1,
        request_id: reqId,
        vault_ticket: params.vaultTicket,
        modality: params.modality,
        projected_media_base64: params.projectedBase64,
        llm_task_output: params.llmTaskOutput,
        consume_and_scrub_vault_ticket: true,
      });
      const rehydratedBase64 = extractRehydratedBase64(data);
      const rtv = isRecord(data.round_trip_verification) ? data.round_trip_verification : null;
      const scrubbed = rtv
        ? rtv.vault_ticket_scrubbed === true
        : data.vault_ticket_scrubbed === true;
      if (scrubbed) owned.delete(params.vaultTicket);
      return { rehydratedBytes: Buffer.from(rehydratedBase64, "base64"), scrubbed };
    },
    async cleanupOwned(): Promise<Array<{ name: "owned_media_cleanup"; passed: boolean }>> {
      const checks: Array<{ name: "owned_media_cleanup"; passed: boolean }> = [];
      for (const ticket of [...owned]) {
        try {
          const response = await fetch(origin + "/api/workbench/vault-zeroize", {
            method: "POST",
            headers: {
              Authorization: `Bearer ${token}`,
              "Content-Type": "application/json",
              "User-Agent": "EigenScope-Integration-Lab/0.2.0",
            },
            body: JSON.stringify({ vault_ticket: ticket }),
            redirect: "error",
            credentials: "omit",
            signal: AbortSignal.timeout(timeoutMs),
          });
          const ok = response.status === 200 || response.status === 410;
          if (ok) owned.delete(ticket);
          checks.push({ name: "owned_media_cleanup", passed: ok });
        } catch {
          checks.push({ name: "owned_media_cleanup", passed: false });
        }
      }
      return checks;
    },
  };
}
