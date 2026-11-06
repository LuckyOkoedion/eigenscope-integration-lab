"""In-memory multimodal container, signal/spatial masking and round-trip checks.

NumPy and SciPy advance deterministic audio (16 kHz PCM16 RIFF/WAVE), image
(8-bit RGBA PNG with IEEE CRC-32) and multi-frame video (OCVC v1 RGBA) fixtures.
All codecs and single-use restoration handles execute in memory without disk I/O
or hosted service calls; hosted endpoints are exercised separately in live mode.
"""

from __future__ import annotations

import hashlib
import io
import math
import secrets
import struct
import time
import wave
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
OCVC_MAGIC = b"OCVC\x01\x00"
MAX_IMAGE_SIDE = 4096
MAX_VIDEO_WIDTH = 1280
MAX_VIDEO_HEIGHT = 720
MAX_VIDEO_FRAMES = 128


class MediaContractRejected(ValueError):
    """A media container, bounding region or restoration handle violated contract."""


def _engine() -> tuple[Any, Any, Any]:
    try:
        import numpy as np
        import scipy
        from scipy import signal
    except ImportError as exc:
        raise RuntimeError("Install the multimodal extra: pip install -e '.[multimodal]'") from exc
    return np, scipy, signal


def sha256_prefixed(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def encode_wav_pcm16(samples_i16: Any, *, sample_rate_hz: int = 16_000, channels: int = 1) -> bytes:
    np, _, _ = _engine()
    if type(sample_rate_hz) is not int or not 16_000 <= sample_rate_hz <= 48_000:
        raise MediaContractRejected("WAV sample_rate_hz must be in [16000, 48000]")
    if channels not in {1, 2}:
        raise MediaContractRejected("WAV channels must be 1 or 2")
    arr = np.asarray(samples_i16, dtype="<i2")
    if arr.size == 0:
        raise MediaContractRejected("WAV sample buffer must be nonempty")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate_hz)
        wav_file.writeframes(arr.tobytes())
    return buf.getvalue()


def decode_wav_pcm16(raw: bytes) -> tuple[Any, int, int]:
    np, _, _ = _engine()
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 44 or raw[:4] != b"RIFF":
        raise MediaContractRejected("Invalid RIFF/WAVE container")
    try:
        with wave.open(io.BytesIO(raw), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sampwidth = wav_file.getsampwidth()
            sample_rate_hz = wav_file.getframerate()
            nframes = wav_file.getnframes()
            comptype = wav_file.getcomptype()
            pcm = wav_file.readframes(nframes)
    except wave.Error as exc:
        raise MediaContractRejected("Corrupted WAVE header or frame payload") from exc
    if comptype != "NONE" or sampwidth != 2 or channels not in {1, 2} or nframes <= 0:
        raise MediaContractRejected("Unsupported WAVE encoding; 16-bit linear PCM required")
    if not 16_000 <= sample_rate_hz <= 48_000:
        raise MediaContractRejected("WAVE sample rate outside [16000, 48000] Hz")
    samples = np.frombuffer(pcm, dtype="<i2").copy()
    if samples.size != nframes * channels:
        raise MediaContractRejected("Truncated WAVE PCM data chunk")
    return samples, sample_rate_hz, channels


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type)
    crc = zlib.crc32(data, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def encode_png_rgba(rgba: Any) -> bytes:
    np, _, _ = _engine()
    arr = np.asarray(rgba, dtype=np.uint8)
    if arr.ndim != 3 or arr.shape[2] != 4:
        raise MediaContractRejected("PNG input must be an HxWx4 uint8 RGBA array")
    height, width, _ = arr.shape
    if not (1 <= width <= MAX_IMAGE_SIDE and 1 <= height <= MAX_IMAGE_SIDE):
        raise MediaContractRejected("Image dimensions exceed [1, 4096] bounds")
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    # Prepend filter byte 0 (None) to each scanline
    filtered = np.hstack([np.zeros((height, 1), dtype=np.uint8), arr.reshape(height, width * 4)])
    idat = zlib.compress(filtered.tobytes(), level=6)
    return (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def decode_png_rgba(raw: bytes) -> Any:
    np, _, _ = _engine()
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 33 or raw[:8] != PNG_SIGNATURE:
        raise MediaContractRejected("Invalid PNG signature")
    pos = 8
    width = height = 0
    idat_parts: list[bytes] = []
    seen_iend = False
    while pos + 12 <= len(raw):
        length = struct.unpack(">I", raw[pos : pos + 4])[0]
        chunk_type = bytes(raw[pos + 4 : pos + 8])
        end_data = pos + 8 + length
        if end_data + 4 > len(raw):
            raise MediaContractRejected("Truncated PNG chunk")
        chunk_data = bytes(raw[pos + 8 : end_data])
        expected_crc = struct.unpack(">I", raw[end_data : end_data + 4])[0]
        actual_crc = zlib.crc32(chunk_data, zlib.crc32(chunk_type)) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise MediaContractRejected("PNG chunk CRC-32 mismatch")
        pos = end_data + 4
        if chunk_type == b"IHDR":
            if length != 13:
                raise MediaContractRejected("Invalid PNG IHDR length")
            width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(
                ">IIBBBBB", chunk_data
            )
            if (
                not (1 <= width <= MAX_IMAGE_SIDE and 1 <= height <= MAX_IMAGE_SIDE)
                or bit_depth != 8
                or color_type != 6
                or comp != 0
                or filt != 0
                or interlace != 0
            ):
                raise MediaContractRejected("PNG header outside 8-bit non-interlaced RGBA contract")
        elif chunk_type == b"IDAT":
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            seen_iend = True
            break
    if not seen_iend or pos != len(raw) or width == 0 or not idat_parts:
        raise MediaContractRejected("Incomplete PNG stream")
    try:
        decompressed = zlib.decompress(b"".join(idat_parts))
    except zlib.error as exc:
        raise MediaContractRejected("Corrupted PNG zlib payload") from exc
    stride = width * 4 + 1
    if len(decompressed) != height * stride:
        raise MediaContractRejected("PNG uncompressed byte count mismatch")
    rows = np.frombuffer(decompressed, dtype=np.uint8).reshape(height, stride)
    if np.any(rows[:, 0] != 0):
        raise MediaContractRejected("Only filter-0 RGBA scanlines are admitted in local codec")
    return rows[:, 1:].reshape(height, width, 4).copy()


def encode_ocvc_rgba(frames: Any) -> bytes:
    np, _, _ = _engine()
    arr = np.asarray(frames, dtype=np.uint8)
    if arr.ndim != 4 or arr.shape[3] != 4:
        raise MediaContractRejected("OCVC input must be an FxHxWx4 uint8 RGBA tensor")
    frame_count, height, width, _ = arr.shape
    if (
        not 1 <= frame_count <= MAX_VIDEO_FRAMES
        or not 1 <= width <= MAX_VIDEO_WIDTH
        or not 1 <= height <= MAX_VIDEO_HEIGHT
    ):
        raise MediaContractRejected("Video clip exceeds frame count or 1280x720 dimension bounds")
    header = OCVC_MAGIC + struct.pack(">HII", frame_count, width, height)
    return header + arr.tobytes()


def decode_ocvc_rgba(raw: bytes) -> Any:
    np, _, _ = _engine()
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 16 or raw[:6] != OCVC_MAGIC:
        raise MediaContractRejected("Invalid OCVC v1 container header")
    frame_count, width, height = struct.unpack(">HII", raw[6:16])
    if (
        not 1 <= frame_count <= MAX_VIDEO_FRAMES
        or not 1 <= width <= MAX_VIDEO_WIDTH
        or not 1 <= height <= MAX_VIDEO_HEIGHT
    ):
        raise MediaContractRejected("OCVC clip dimensions or frame count outside contract bounds")
    expected_len = 16 + frame_count * width * height * 4
    if len(raw) != expected_len:
        raise MediaContractRejected("OCVC byte payload length does not match header geometry")
    return (
        np.frombuffer(raw, dtype=np.uint8, offset=16).reshape(frame_count, height, width, 4).copy()
    )


@dataclass(frozen=True)
class BoundingBox:
    x: int
    y: int
    width: int
    height: int

    def validate(self, frame_width: int, frame_height: int) -> None:
        for val in (self.x, self.y, self.width, self.height):
            if type(val) is not int:
                raise MediaContractRejected("Bounding box coordinates must be integers")
        if (
            self.x < 0
            or self.y < 0
            or self.width <= 0
            or self.height <= 0
            or self.x + self.width > frame_width
            or self.y + self.height > frame_height
        ):
            raise MediaContractRejected("Bounding box falls outside frame dimensions")


class LocalMediaVaultFixture:
    """In-memory single-use residual store for local harness tests.

    Opaque handles never leave the local run and do not represent hosted keys.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, str, bytes]] = {}

    def stage(self, modality: str, projected_bytes: bytes, original_bytes: bytes) -> str:
        ticket = "vlt_local_" + secrets.token_urlsafe(18)
        self._entries[ticket] = (modality, sha256_prefixed(projected_bytes), bytes(original_bytes))
        return ticket

    def restore_once(self, ticket: str, modality: str, projected_bytes: bytes) -> bytes:
        if not isinstance(ticket, str) or ticket not in self._entries:
            raise MediaContractRejected("Unknown or already consumed local media handle")
        expected_mod, expected_sha, original = self._entries[ticket]
        if expected_mod != modality or sha256_prefixed(projected_bytes) != expected_sha:
            raise MediaContractRejected("Projected media digest or modality mismatch")
        del self._entries[ticket]
        return original

    def close(self) -> None:
        self._entries.clear()


def build_synthetic_media_bundle(seed: int = 7) -> dict[str, Any]:
    """Generate deterministic synthetic WAV, PNG and OCVC bytes in memory."""
    np, _, _ = _engine()
    rng = np.random.default_rng(seed)

    # 1) 1.5 s @ 16 kHz mono WAV: [0.0..0.5s task tone], [0.55..1.05s identifier burst], [1.1..1.5s task tone]
    sr = 16_000
    n_samples = int(1.5 * sr)
    t = np.arange(n_samples, dtype=np.float64) / sr
    waveform = np.zeros(n_samples, dtype=np.float64)
    task_slice_a = slice(0, int(0.50 * sr))
    priv_slice = slice(int(0.55 * sr), int(1.05 * sr))
    task_slice_b = slice(int(1.10 * sr), n_samples)
    waveform[task_slice_a] = 0.35 * np.sin(2.0 * math.pi * 440.0 * t[task_slice_a])
    waveform[priv_slice] = (
        0.55 * np.sin(2.0 * math.pi * 880.0 * t[priv_slice])
        + 0.20 * np.cos(2.0 * math.pi * 1320.0 * t[priv_slice])
        + 0.02 * rng.standard_normal(priv_slice.stop - priv_slice.start)
    )
    waveform[task_slice_b] = 0.30 * np.sin(2.0 * math.pi * 523.25 * t[task_slice_b])
    pcm_i16 = np.clip(np.round(waveform * 32767.0), -32767, 32767).astype("<i2")
    wav_bytes = encode_wav_pcm16(pcm_i16, sample_rate_hz=sr, channels=1)

    # 2) 96x64 RGBA PNG with background grid, clinical task band, and synthetic header region
    img_h, img_w = 64, 96
    image = np.full((img_h, img_w, 4), (242, 246, 250, 255), dtype=np.uint8)
    # Clinical task waveform region (bottom half)
    for col in range(8, img_w - 8):
        row = 44 + int(round(6.0 * math.sin(col * 0.25)))
        image[row - 1 : row + 1, col] = (24, 118, 108, 255)
    # Sensitive badge region (top-left box)
    image_box = BoundingBox(x=10, y=8, width=52, height=16)
    image[
        image_box.y : image_box.y + image_box.height,
        image_box.x : image_box.x + image_box.width,
        :3,
    ] = rng.integers(20, 140, size=(image_box.height, image_box.width, 3), dtype=np.uint8)
    png_bytes = encode_png_rgba(image)

    # 3) 6-frame 96x64 RGBA OCVC clip with translating badge region + stationary status bar
    n_frames = 6
    clip = np.full((n_frames, img_h, img_w, 4), (236, 241, 247, 255), dtype=np.uint8)
    video_boxes: list[BoundingBox] = []
    for f_idx in range(n_frames):
        # Equipment status bar at bottom (must remain untouched)
        clip[f_idx, 50:58, 12:84] = (32, 110, 168, 255)
        bx = 8 + f_idx * 6
        vbox = BoundingBox(x=bx, y=12, width=36, height=14)
        video_boxes.append(vbox)
        clip[
            f_idx,
            vbox.y : vbox.y + vbox.height,
            vbox.x : vbox.x + vbox.width,
            :3,
        ] = rng.integers(30, 180, size=(vbox.height, vbox.width, 3), dtype=np.uint8)
    ocvc_bytes = encode_ocvc_rgba(clip)

    return {
        "wav_bytes": wav_bytes,
        "sample_rate_hz": sr,
        "audio_private_slice": (priv_slice.start, priv_slice.stop),
        "audio_task_slices": (
            (task_slice_a.start, task_slice_a.stop),
            (task_slice_b.start, task_slice_b.stop),
        ),
        "png_bytes": png_bytes,
        "image_width": img_w,
        "image_height": img_h,
        "image_box": image_box,
        "ocvc_bytes": ocvc_bytes,
        "video_width": img_w,
        "video_height": img_h,
        "video_frames": n_frames,
        "video_boxes": tuple(video_boxes),
    }


def run_multimodal(
    seed: int = 7,
    on_frame: Callable[[dict[str, Any]], None] | None = None,
    pacing_ms: float = 0,
) -> dict[str, Any]:
    """Execute six deterministic multimodal container, masking and round-trip phases."""
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise MediaContractRejected("Seed must be an unsigned 32-bit integer")
    if (
        isinstance(pacing_ms, bool)
        or not isinstance(pacing_ms, (int, float))
        or not math.isfinite(pacing_ms)
    ):
        raise MediaContractRejected("Pacing must be a finite number")
    pacing_ms = float(pacing_ms)
    if not 0.0 <= pacing_ms <= 100.0:
        raise MediaContractRejected("Pacing must be in [0, 100] wall-clock ms per frame")

    np, scipy, signal = _engine()
    vault = LocalMediaVaultFixture()
    telemetry: list[dict[str, Any]] = []
    sim_t = 0.0

    def emit(phase: str, modality: str, payload: dict[str, Any]) -> None:
        nonlocal sim_t
        sim_t = round(sim_t + 0.25, 6)
        frame = {
            "frame": len(telemetry),
            "phase": phase,
            "modality": modality,
            "t": sim_t,
            **payload,
        }
        telemetry.append(frame)
        if on_frame is not None:
            on_frame(dict(frame))
        if pacing_ms:
            time.sleep(pacing_ms / 1000.0)

    try:
        bundle = build_synthetic_media_bundle(seed=seed)

        # Phase 1: Audio framing, Parseval DFT identity & SciPy STFT energy analysis
        samples, sr, channels = decode_wav_pcm16(bundle["wav_bytes"])
        norm_x = samples.astype(np.float64) / 32768.0
        time_energy = float(np.sum(norm_x**2))
        fft_vals = np.fft.fft(norm_x)
        freq_energy = float(np.sum(np.abs(fft_vals) ** 2) / norm_x.size)
        parseval_rel_err = abs(time_energy - freq_energy) / max(time_energy, 1e-15)
        _, _, zxx = signal.stft(norm_x, fs=sr, nperseg=256)
        stft_peak_mag = float(np.max(np.abs(zxx)))

        p_start, p_stop = bundle["audio_private_slice"]
        (t1_s, t1_e), (t2_s, t2_e) = bundle["audio_task_slices"]
        raw_priv_rms = float(np.sqrt(np.mean(norm_x[p_start:p_stop] ** 2)))

        masked_samples = samples.copy()
        masked_samples[p_start:p_stop] = 0
        projected_wav = encode_wav_pcm16(masked_samples, sample_rate_hz=sr, channels=channels)
        dec_masked, _, _ = decode_wav_pcm16(projected_wav)
        masked_priv_rms = float(
            np.sqrt(np.mean((dec_masked[p_start:p_stop].astype(np.float64) / 32768.0) ** 2))
        )
        task_islands_exact = bool(
            np.array_equal(dec_masked[t1_s:t1_e], samples[t1_s:t1_e])
            and np.array_equal(dec_masked[t2_s:t2_e], samples[t2_s:t2_e])
        )
        emit(
            "audio-framing",
            "audio_wav",
            {
                "container_bytes": len(bundle["wav_bytes"]),
                "parseval_rel_error": parseval_rel_err,
                "stft_peak_magnitude": stft_peak_mag,
                "raw_span_rms": raw_priv_rms,
                "masked_span_rms": masked_priv_rms,
                "exact_roundtrip": False,
                "active_stage": "MASKED",
            },
        )

        # Phase 2: Audio bit-exact SHA-256 reconstruction
        audio_ticket = vault.stage("audio_wav", projected_wav, bundle["wav_bytes"])
        restored_wav = vault.restore_once(audio_ticket, "audio_wav", projected_wav)
        audio_sha_exact = sha256_prefixed(restored_wav) == sha256_prefixed(bundle["wav_bytes"])
        emit(
            "audio-reconstruction",
            "audio_wav",
            {
                "container_bytes": len(restored_wav),
                "parseval_rel_error": parseval_rel_err,
                "raw_span_rms": raw_priv_rms,
                "masked_span_rms": masked_priv_rms,
                "exact_roundtrip": audio_sha_exact,
                "active_stage": "RESTORED",
            },
        )

        # Phase 3: Image PNG CRC-32 decode & spatial box masking
        img = decode_png_rgba(bundle["png_bytes"])
        ibox: BoundingBox = bundle["image_box"]
        ibox.validate(bundle["image_width"], bundle["image_height"])
        masked_img = img.copy()
        masked_img[ibox.y : ibox.y + ibox.height, ibox.x : ibox.x + ibox.width, :3] = 0
        projected_png = encode_png_rgba(masked_img)
        dec_masked_img = decode_png_rgba(projected_png)
        box_zeroed = bool(
            np.all(
                dec_masked_img[ibox.y : ibox.y + ibox.height, ibox.x : ibox.x + ibox.width, :3] == 0
            )
        )
        outside_mask = np.ones((bundle["image_height"], bundle["image_width"]), dtype=bool)
        outside_mask[ibox.y : ibox.y + ibox.height, ibox.x : ibox.x + ibox.width] = False
        unmasked_pixels_exact = bool(
            np.array_equal(dec_masked_img[outside_mask], img[outside_mask])
        )
        masked_pixel_ratio = (ibox.width * ibox.height) / float(
            bundle["image_width"] * bundle["image_height"]
        )
        emit(
            "image-masking",
            "image_frame",
            {
                "container_bytes": len(projected_png),
                "width": bundle["image_width"],
                "height": bundle["image_height"],
                "masked_pixel_ratio": masked_pixel_ratio,
                "unmasked_pixels_exact": unmasked_pixels_exact,
                "exact_roundtrip": False,
                "active_stage": "MASKED",
            },
        )

        # Phase 4: Image bit-exact SHA-256 reconstruction
        img_ticket = vault.stage("image_frame", projected_png, bundle["png_bytes"])
        restored_png = vault.restore_once(img_ticket, "image_frame", projected_png)
        image_sha_exact = sha256_prefixed(restored_png) == sha256_prefixed(bundle["png_bytes"])
        emit(
            "image-reconstruction",
            "image_frame",
            {
                "container_bytes": len(restored_png),
                "width": bundle["image_width"],
                "height": bundle["image_height"],
                "masked_pixel_ratio": masked_pixel_ratio,
                "unmasked_pixels_exact": unmasked_pixels_exact,
                "exact_roundtrip": image_sha_exact,
                "active_stage": "RESTORED",
            },
        )

        # Phase 5: Video multi-frame spatiotemporal box tracking & bit-exact restoration
        clip = decode_ocvc_rgba(bundle["ocvc_bytes"])
        masked_clip = clip.copy()
        vboxes: tuple[BoundingBox, ...] = bundle["video_boxes"]
        all_tracks_zeroed = True
        status_bar_preserved = True
        for f_idx, vbox in enumerate(vboxes):
            vbox.validate(bundle["video_width"], bundle["video_height"])
            masked_clip[f_idx, vbox.y : vbox.y + vbox.height, vbox.x : vbox.x + vbox.width, :3] = 0
        projected_ocvc = encode_ocvc_rgba(masked_clip)
        dec_masked_clip = decode_ocvc_rgba(projected_ocvc)
        for f_idx, vbox in enumerate(vboxes):
            if not np.all(
                dec_masked_clip[
                    f_idx, vbox.y : vbox.y + vbox.height, vbox.x : vbox.x + vbox.width, :3
                ]
                == 0
            ):
                all_tracks_zeroed = False
            if not np.array_equal(dec_masked_clip[f_idx, 50:58, 12:84], clip[f_idx, 50:58, 12:84]):
                status_bar_preserved = False
            emit(
                "video-tracking",
                "video_clip",
                {
                    "container_bytes": len(projected_ocvc),
                    "video_frame_index": f_idx,
                    "video_frame_count": bundle["video_frames"],
                    "track_box_x": vbox.x,
                    "track_box_y": vbox.y,
                    "exact_roundtrip": False,
                    "active_stage": "MASKED",
                },
            )

        vid_ticket = vault.stage("video_clip", projected_ocvc, bundle["ocvc_bytes"])
        restored_ocvc = vault.restore_once(vid_ticket, "video_clip", projected_ocvc)
        video_sha_exact = sha256_prefixed(restored_ocvc) == sha256_prefixed(bundle["ocvc_bytes"])

        # Phase 6: Adversarial tamper, replay & preflight ceiling controls
        rejected_controls = 0
        # 6a) Replay already consumed video handle
        try:
            vault.restore_once(vid_ticket, "video_clip", projected_ocvc)
        except MediaContractRejected:
            rejected_controls += 1
        # 6b) Tamper 1 byte of projected PNG before restore
        tamper_ticket = vault.stage("image_frame", projected_png, bundle["png_bytes"])
        tampered_png = bytearray(projected_png)
        tampered_png[-6] ^= 0x01
        try:
            vault.restore_once(tamper_ticket, "image_frame", bytes(tampered_png))
        except MediaContractRejected:
            rejected_controls += 1
        # 6c) Corrupted PNG CRC-32 rejected by decoder
        try:
            decode_png_rgba(bytes(tampered_png))
        except MediaContractRejected:
            rejected_controls += 1
        # 6d) Oversized image width > 4096 rejected
        try:
            encode_png_rgba(np.zeros((8, MAX_IMAGE_SIDE + 1, 4), dtype=np.uint8))
        except MediaContractRejected:
            rejected_controls += 1
        # 6e) Oversized video frame count > 128 rejected
        try:
            encode_ocvc_rgba(np.zeros((MAX_VIDEO_FRAMES + 1, 8, 8, 4), dtype=np.uint8))
        except MediaContractRejected:
            rejected_controls += 1
        # 6f) Unsupported WAV sample rate (8 kHz) rejected
        try:
            encode_wav_pcm16(np.zeros(160, dtype="<i2"), sample_rate_hz=8_000)
        except MediaContractRejected:
            rejected_controls += 1

        emit(
            "tamper-and-ceiling-controls",
            "video_clip",
            {
                "container_bytes": len(restored_ocvc),
                "rejected_controls": rejected_controls,
                "expected_rejected_controls": 6,
                "exact_roundtrip": video_sha_exact,
                "active_stage": "VERIFIED",
            },
        )

        checks = [
            {
                "name": "wav_pcm16_container_and_parseval_energy",
                "passed": bool(parseval_rel_err < 1e-12 and stft_peak_mag > 0.0),
            },
            {
                "name": "audio_span_masking_and_task_island_preserved",
                "passed": bool(
                    raw_priv_rms > 0.25 and masked_priv_rms == 0.0 and task_islands_exact
                ),
            },
            {
                "name": "audio_bit_exact_sha256_roundtrip",
                "passed": bool(audio_sha_exact and restored_wav == bundle["wav_bytes"]),
            },
            {
                "name": "png_crc32_container_and_spatial_masking",
                "passed": bool(box_zeroed and unmasked_pixels_exact),
            },
            {
                "name": "image_bit_exact_sha256_roundtrip",
                "passed": bool(image_sha_exact and restored_png == bundle["png_bytes"]),
            },
            {
                "name": "video_spatiotemporal_track_masking",
                "passed": bool(all_tracks_zeroed and status_bar_preserved),
            },
            {
                "name": "video_bit_exact_sha256_roundtrip",
                "passed": bool(video_sha_exact and restored_ocvc == bundle["ocvc_bytes"]),
            },
            {
                "name": "tampered_media_and_replayed_handle_rejected",
                "passed": bool(rejected_controls == 6),
            },
            {
                "name": "callback_clock_strictly_monotonic",
                "passed": bool(
                    all(a["t"] < b["t"] for a, b in zip(telemetry, telemetry[1:], strict=False))
                ),
            },
        ]
        return {
            "suite": "multimodal",
            "engine": "NumPy + SciPy STFT + lossless PCM16 WAV / CRC-32 PNG / OCVC v1 codecs",
            "engine_version": {"NumPy": np.__version__, "SciPy": scipy.__version__},
            "seed": seed,
            "checks": checks,
            "telemetry": telemetry,
            "metrics": {
                "audio_wav_bytes": len(bundle["wav_bytes"]),
                "audio_parseval_rel_error": parseval_rel_err,
                "image_png_bytes": len(bundle["png_bytes"]),
                "video_ocvc_bytes": len(bundle["ocvc_bytes"]),
                "video_frames": bundle["video_frames"],
                "rejected_negative_controls": rejected_controls,
                "frame_count": len(telemetry),
                "simulation_time_s": sim_t,
                "pacing_ms_per_frame": pacing_ms,
            },
            "scope": "In-memory container validation, time/spatial span masking and byte-exact SHA-256 restoration fixtures. Local single-use handles test integration boundaries without hosted API calls.",
        }
    finally:
        vault.close()
