"""Live HTTP contract checks for public multimodal decomposition and rehydration.

Exercises /v1/decompose/{audio,image,video} and /v1/rehydrate/media using
published in-memory synthetic containers (16 kHz PCM16 WAV, 8-bit RGBA PNG and
multi-frame RGBA OCVC v1). No credentials, raw error text, response bodies or
vault tickets are exported.
"""

from __future__ import annotations

import base64
import secrets
import time
from collections.abc import Callable
from typing import Any

from . import __version__
from .live import (
    ServiceResponseContractError,
    _check,
    _checkpoint,
    _record_exception,
    describe_suite_guidance,
    safe_error,
)
from .simulators.multimodal import (
    MediaContractRejected,
    build_synthetic_media_bundle,
    decode_ocvc_rgba,
    decode_png_rgba,
    decode_wav_pcm16,
    sha256_prefixed,
)

CONTRACT_VERSION = "2026-10-18.v1"
MULTIMODAL_SELECTIONS = (
    "multimodal",
    "multimodal-audio",
    "multimodal-image",
    "multimodal-video",
)


def _uuid_v7() -> str:
    unix_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    raw = ((unix_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b).to_bytes(
        16, "big"
    )
    hex_str = raw.hex()
    return f"{hex_str[:8]}-{hex_str[8:12]}-{hex_str[12:16]}-{hex_str[16:20]}-{hex_str[20:]}"


def _headers(key: str, req_id: str, prefix: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "User-Agent": f"EigenScope-Integration-Lab/{__version__}",
        "X-OrthoCore-Contract-Version": CONTRACT_VERSION,
        "X-OrthoCore-Request-Id": req_id,
        "X-OrthoCore-Idempotency-Key": f"{prefix}-{req_id}",
    }


def _extract_error_codes(http_status: int, body: Any) -> dict[str, str | int]:
    fake = Exception()
    fake.http_status = http_status  # type: ignore[attr-defined]
    if isinstance(body, dict):
        if isinstance(body.get("error_code"), str):
            fake.error_code = body["error_code"]  # type: ignore[attr-defined]
        if isinstance(body.get("reason_code"), str):
            fake.reason_code = body["reason_code"]  # type: ignore[attr-defined]
    detail = safe_error(fake)
    detail.pop("exception", None)
    return detail


def _parse_json(response: Any) -> dict[str, Any]:
    try:
        data = response.json()
    except (TypeError, ValueError) as exc:
        raise ServiceResponseContractError("Non-JSON multimodal HTTP response") from exc
    if not isinstance(data, dict):
        raise ServiceResponseContractError("Multimodal HTTP response must be a JSON object")
    return data


def _extract_projected_b64(data: dict[str, Any]) -> str:
    for field in (
        "task_projected_audio_base64",
        "task_projected_media_base64",
        "deidentified_wav_base64",
        "redacted_frame_base64",
        "redacted_clip_base64",
        "projected_media_base64",
    ):
        val = data.get(field)
        if isinstance(val, str) and val:
            return val
    return ""


def _extract_rehydrated_b64(data: dict[str, Any]) -> str:
    for field in ("rehydrated_wav_base64", "rehydrated_media_base64"):
        val = data.get(field)
        if isinstance(val, str) and val:
            return val
    return ""


def _modality_specs(seed: int) -> list[dict[str, Any]]:
    bundle = build_synthetic_media_bundle(seed=seed)
    ibox = bundle["image_box"]
    return [
        {
            "id": "multimodal-audio",
            "modality": "audio_wav",
            "route": "/v1/decompose/audio",
            "raw_bytes": bundle["wav_bytes"],
            "task_prompt": "Summarize the cardiology intake vitals and follow-up interval.",
            "input_payload": {
                "kind": "audio_wav",
                "wav_base64": base64.b64encode(bundle["wav_bytes"]).decode("ascii"),
                "sample_rate_hz": bundle["sample_rate_hz"],
                "channels": 1,
                "transcript": "Intake note for Elena Rostova MRN-7741092: BP 124/78, repeat labs in 48 hours.",
                "transcript_words": [
                    {"word": "Elena", "start_sample": 8800, "end_sample": 11200},
                    {"word": "Rostova", "start_sample": 11200, "end_sample": 14400},
                    {"word": "MRN-7741092", "start_sample": 14400, "end_sample": 16800},
                ],
            },
            "public_audio_slices": bundle["audio_task_slices"],
        },
        {
            "id": "multimodal-image",
            "modality": "image_frame",
            "route": "/v1/decompose/image",
            "raw_bytes": bundle["png_bytes"],
            "task_prompt": "Verify the telemetry waveform band and infusion status from the frame.",
            "input_payload": {
                "kind": "image_frame",
                "frame_base64": base64.b64encode(bundle["png_bytes"]).decode("ascii"),
                "width": bundle["image_width"],
                "height": bundle["image_height"],
                "ocr_regions": [
                    {
                        "text": "Elena Rostova MRN-7741092",
                        "x": ibox.x,
                        "y": ibox.y,
                        "width": ibox.width,
                        "height": ibox.height,
                        "frame": 0,
                    }
                ],
            },
        },
        {
            "id": "multimodal-video",
            "modality": "video_clip",
            "route": "/v1/decompose/video",
            "raw_bytes": bundle["ocvc_bytes"],
            "task_prompt": "Confirm pump status bar continuity across the six inspection frames.",
            "input_payload": {
                "kind": "video_clip",
                "clip_base64": base64.b64encode(bundle["ocvc_bytes"]).decode("ascii"),
                "width": bundle["video_width"],
                "height": bundle["video_height"],
                "frame_count": bundle["video_frames"],
                "ocr_regions": [
                    {
                        "text": "Badge ID BADGE-99412",
                        "x": box.x,
                        "y": box.y,
                        "width": box.width,
                        "height": box.height,
                        "frame": frame,
                    }
                    for frame, box in enumerate(bundle["video_boxes"])
                ],
            },
        },
    ]


def _projection_checks(
    spec: dict[str, Any], projected: bytes, measurements: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Decode actual output and observe annotated transformations and public signal."""
    import numpy as np

    payload = spec["input_payload"]
    measurements = measurements if measurements is not None else {}
    try:
        if spec["modality"] == "audio_wav":
            raw, raw_rate, raw_channels = decode_wav_pcm16(spec["raw_bytes"])
            masked, rate, channels = decode_wav_pcm16(projected)
            geometry_ok = (
                rate == raw_rate and channels == raw_channels and masked.shape == raw.shape
            )
            spans = payload["transcript_words"]
            transformed = geometry_ok and all(
                not bool(
                    np.array_equal(
                        masked[span["start_sample"] : span["end_sample"]],
                        raw[span["start_sample"] : span["end_sample"]],
                    )
                )
                for span in spans
            )
            public_signal = geometry_ok and all(
                end > start and bool(np.any(masked[start:end] != 0))
                for start, end in spec["public_audio_slices"]
            )
            if geometry_ok:
                measurements["annotated_audio_changed_samples"] = sum(
                    int(
                        np.count_nonzero(
                            masked[span["start_sample"] : span["end_sample"]]
                            != raw[span["start_sample"] : span["end_sample"]]
                        )
                    )
                    for span in spans
                )
                energy_ratios, correlations = [], []
                for start, end in spec["public_audio_slices"]:
                    a, b = raw[start:end].astype(float), masked[start:end].astype(float)
                    energy = float(np.sum(a**2))
                    energy_ratios.append(float(np.sum(b**2) / energy) if energy else None)
                    correlations.append(
                        float(np.corrcoef(a, b)[0, 1]) if a.std() and b.std() else None
                    )
                measurements["public_audio_energy_ratios"] = energy_ratios
                measurements["public_audio_correlations"] = correlations
        else:
            decoder = decode_png_rgba if spec["modality"] == "image_frame" else decode_ocvc_rgba
            raw = decoder(spec["raw_bytes"])
            masked = decoder(projected)
            geometry_ok = masked.shape == raw.shape
            transformed = geometry_ok
            public_signal = geometry_ok
            if geometry_ok:
                raw_frames = raw[None, ...] if raw.ndim == 3 else raw
                masked_frames = masked[None, ...] if masked.ndim == 3 else masked
                public = np.ones(raw_frames.shape[:-1], dtype=bool)
                for region in payload["ocr_regions"]:
                    frame, x, y = region["frame"], region["x"], region["y"]
                    width, height = region["width"], region["height"]
                    block = masked_frames[frame, y : y + height, x : x + width]
                    source = raw_frames[frame, y : y + height, x : x + width]
                    transformed = transformed and not bool(np.array_equal(block, source))
                    public[frame, y : y + height, x : x + width] = False
                observed = masked_frames[public, :3]
                public_signal = len(observed) > 0 and len(np.unique(observed, axis=0)) > 1
                measurements["public_pixel_count"] = int(np.count_nonzero(public))
                measurements["public_pixels_changed"] = int(
                    np.count_nonzero(np.any(masked_frames[public] != raw_frames[public], axis=-1))
                )
        return [
            _check("projected_media_container_and_geometry_valid", geometry_ok),
            _check("annotated_media_spans_transformed", transformed),
            _check("public_media_signal_present", public_signal),
        ]
    except (MediaContractRejected, ValueError, IndexError) as exc:
        raise ServiceResponseContractError(
            "Projected media violates the container contract"
        ) from exc


def _run_modality_case(
    spec: dict[str, Any],
    *,
    http: Any,
    base_url: str,
    key: str,
    check_cancelled: Callable[[], None] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "suite": f"api:{spec['id']}",
        "modality": spec["modality"],
        "engine": "public /v1 multimodal HTTP contract + SHA-256 round-trip verifier",
        "checks": [],
        "metrics": {"raw_media_bytes": len(spec["raw_bytes"])},
        "scope": "Actual hosted multimodal endpoint; decoded span transformations, public signal presence and exact owned restoration.",
    }
    checks = row["checks"]
    payload = spec["input_payload"]
    for field in ("width", "height", "frame_count", "sample_rate_hz", "channels"):
        if field in payload:
            row["metrics"][field] = payload[field]
    ticket: str | None = None
    consumed = False
    started = time.perf_counter()
    try:
        _checkpoint(check_cancelled)
        dec_id = _uuid_v7()
        t0 = time.perf_counter()
        dec_res = http.post(
            f"{base_url}{spec['route']}",
            headers=_headers(key, dec_id, "mm-dec"),
            json={
                "schema_version": 1,
                "request_id": dec_id,
                "execution_mode": "vpc_sidecar",
                "input_payload": spec["input_payload"],
                "separation_policy": {
                    "clamp_private": 1,
                    "clamp_style": 1,
                    "drop_noise": True,
                    "target_task_equivalencefloor": 0.995,
                    "max_mi_leakage_bits": 0.01,
                },
            },
        )
        row["metrics"]["decompose_http_wall_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        row["http_status"] = dec_res.status_code
        if dec_res.status_code == 401:
            row["status"] = "BLOCKED"
            checks.append(_check("authorized_multimodal_access", False, http_status=401))
            return row
        dec_data = _parse_json(dec_res)
        if dec_res.status_code != 200:
            codes = _extract_error_codes(dec_res.status_code, dec_data)
            checks.append(_check("actual_multimodal_decomposition_succeeded", False, **codes))
            return row

        raw_ticket = dec_data.get("vault_ticket")
        if isinstance(raw_ticket, str) and raw_ticket.startswith("vlt_") and len(raw_ticket) <= 512:
            ticket = raw_ticket
        projected_b64 = _extract_projected_b64(dec_data)
        if not ticket or not projected_b64:
            raise ServiceResponseContractError("Missing vault_ticket or projected media payload")
        try:
            projected_bytes = base64.b64decode(projected_b64, validate=True)
        except ValueError as exc:
            raise ServiceResponseContractError("Invalid base64 projected media") from exc

        checks.append(_check("actual_multimodal_decomposition_succeeded", True))
        checks.append(
            _check(
                "projected_media_differs_from_raw_input",
                len(projected_bytes) > 0
                and sha256_prefixed(projected_bytes) != sha256_prefixed(spec["raw_bytes"]),
            )
        )
        checks.extend(_projection_checks(spec, projected_bytes, row["metrics"]))

        task_core = str(dec_data.get("task_core") or spec["task_prompt"])
        _checkpoint(check_cancelled)
        reh_id = _uuid_v7()
        t1 = time.perf_counter()
        reh_res = http.post(
            f"{base_url}/v1/rehydrate/media",
            headers=_headers(key, reh_id, "mm-reh"),
            json={
                "schema_version": 1,
                "request_id": reh_id,
                "vault_ticket": ticket,
                "modality": spec["modality"],
                "projected_media_base64": projected_b64,
                "llm_task_output": task_core,
                "consume_and_scrub_vault_ticket": True,
            },
        )
        row["http_status"] = reh_res.status_code
        row["metrics"]["rehydrate_http_wall_ms"] = round((time.perf_counter() - t1) * 1000, 3)
        reh_data = _parse_json(reh_res)
        if reh_res.status_code != 200:
            codes = _extract_error_codes(reh_res.status_code, reh_data)
            checks.append(_check("media_rehydrated_and_ticket_consumed", False, **codes))
            return row

        rehydrated_b64 = _extract_rehydrated_b64(reh_data)
        if not rehydrated_b64:
            raise ServiceResponseContractError("Missing rehydrated media payload")
        try:
            rehydrated_bytes = base64.b64decode(rehydrated_b64, validate=True)
        except ValueError as exc:
            raise ServiceResponseContractError("Invalid base64 rehydrated media") from exc

        verification = reh_data.get("round_trip_verification")
        scrubbed_flag = (
            verification.get("vault_ticket_scrubbed") is True
            if isinstance(verification, dict)
            else reh_data.get("vault_ticket_scrubbed") is True
        )
        exact_sha = sha256_prefixed(rehydrated_bytes) == sha256_prefixed(spec["raw_bytes"])
        consumed = scrubbed_flag
        checks.append(_check("media_bit_exact_sha256_restored", exact_sha))
        checks.append(_check("media_ticket_marked_scrubbed", scrubbed_flag))

        # Replay negative control: second rehydrate with same single-use ticket must return 410
        _checkpoint(check_cancelled)
        replay_id = _uuid_v7()
        replay_res = http.post(
            f"{base_url}/v1/rehydrate/media",
            headers=_headers(key, replay_id, "mm-replay"),
            json={
                "schema_version": 1,
                "request_id": replay_id,
                "vault_ticket": ticket,
                "modality": spec["modality"],
                "projected_media_base64": projected_b64,
                "llm_task_output": task_core,
                "consume_and_scrub_vault_ticket": True,
            },
        )
        row["http_status"] = replay_res.status_code
        replay_body: dict[str, Any] = {}
        try:
            parsed_replay = replay_res.json()
            if isinstance(parsed_replay, dict):
                replay_body = parsed_replay
        except (TypeError, ValueError):
            pass
        replay_codes = _extract_error_codes(replay_res.status_code, replay_body)
        proven_denial = replay_res.status_code == 410 and (
            replay_codes.get("error_code") == "VAULT_TICKET_SCRUBBED"
            or replay_codes.get("reason_code")
            in {"TICKET_ALREADY_CONSUMED", "TICKET_ZEROIZED_OR_TTL_EXPIRED"}
        )
        checks.append(_check("consumed_media_ticket_reuse_denied", proven_denial, **replay_codes))
        consumed = consumed and proven_denial
    except Exception as exc:
        _record_exception(row, exc)
    finally:
        if ticket and not consumed:
            try:
                zero_res = http.post(
                    f"{base_url}/api/workbench/vault-zeroize",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "User-Agent": f"EigenScope-Integration-Lab/{__version__}",
                    },
                    json={"vault_ticket": ticket},
                )
                zero_ok = zero_res.status_code in {200, 410}
                checks.append(_check("owned_media_ticket_cleanup", zero_ok))
            except Exception as exc:
                checks.append(_check("owned_media_ticket_cleanup", False, **safe_error(exc)))
        row["metrics"]["complete_modality_wall_ms"] = round(
            (time.perf_counter() - started) * 1000, 3
        )
        guidance = describe_suite_guidance(checks)
        if guidance:
            row["guidance"] = guidance
    return row


def run_multimodal_live(
    *,
    base_url: str,
    key: str,
    selection: str = "multimodal",
    seed: int = 7,
    repeat: int = 1,
    timeout: float = 15.0,
    http_client: Any = None,
    check_cancelled: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """Run selected live multimodal HTTP cases with deterministic synthetic containers."""
    if selection not in MULTIMODAL_SELECTIONS:
        raise ValueError(f"Unsupported multimodal selection: {selection}")
    if type(repeat) is not int or not 1 <= repeat <= 10:
        raise ValueError("repeat must be an integer from 1 to 10")
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("Install live and multimodal extras: uv sync --all-extras") from exc

    specs = [spec for spec in _modality_specs(seed) if selection in {"multimodal", spec["id"]}]
    rows: list[dict[str, Any]] = []
    if http_client is not None:
        for iteration in range(repeat):
            for spec in specs:
                row = _run_modality_case(
                    spec,
                    http=http_client,
                    base_url=base_url,
                    key=key,
                    check_cancelled=check_cancelled,
                )
                row["iteration"] = iteration + 1
                rows.append(row)
        return rows

    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as http:
        for iteration in range(repeat):
            for spec in specs:
                row = _run_modality_case(
                    spec, http=http, base_url=base_url, key=key, check_cancelled=check_cancelled
                )
                row["iteration"] = iteration + 1
                rows.append(row)
    return rows
