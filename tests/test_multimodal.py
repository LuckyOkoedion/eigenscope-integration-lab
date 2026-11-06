"""Multimodal container codecs, single-use restoration handles and HTTP contract tests."""

import base64
import json
from types import SimpleNamespace

import pytest

from eigenscope_lab.multimodal_live import run_multimodal_live
from eigenscope_lab.reporting import outcome
from eigenscope_lab.simulators.multimodal import (
    BoundingBox,
    LocalMediaVaultFixture,
    MediaContractRejected,
    build_synthetic_media_bundle,
    decode_ocvc_rgba,
    decode_png_rgba,
    decode_wav_pcm16,
    encode_ocvc_rgba,
    encode_png_rgba,
    encode_wav_pcm16,
    run_multimodal,
    sha256_prefixed,
)


@pytest.fixture(autouse=True)
def require_multimodal_extras():
    pytest.importorskip("numpy", reason="Install the multimodal extra")
    pytest.importorskip("scipy", reason="Install the multimodal extra")


def test_wav_png_and_ocvc_codecs_roundtrip_and_reject_corruption():
    bundle = build_synthetic_media_bundle(seed=11)

    samples, sr, channels = decode_wav_pcm16(bundle["wav_bytes"])
    assert sr == 16_000 and channels == 1
    assert encode_wav_pcm16(samples, sample_rate_hz=sr, channels=channels) == bundle["wav_bytes"]
    with pytest.raises(MediaContractRejected):
        decode_wav_pcm16(b"NOT_A_WAV_HEADER")

    rgba = decode_png_rgba(bundle["png_bytes"])
    assert rgba.shape == (bundle["image_height"], bundle["image_width"], 4)
    assert encode_png_rgba(rgba) == bundle["png_bytes"]
    corrupted_png = bytearray(bundle["png_bytes"])
    corrupted_png[20] ^= 0xFF
    with pytest.raises(MediaContractRejected, match="CRC-32"):
        decode_png_rgba(bytes(corrupted_png))

    clip = decode_ocvc_rgba(bundle["ocvc_bytes"])
    assert clip.shape == (
        bundle["video_frames"],
        bundle["video_height"],
        bundle["video_width"],
        4,
    )
    assert encode_ocvc_rgba(clip) == bundle["ocvc_bytes"]
    with pytest.raises(MediaContractRejected):
        decode_ocvc_rgba(bundle["ocvc_bytes"][:-4])


def test_bounding_box_and_single_use_media_vault_controls():
    with pytest.raises(MediaContractRejected):
        BoundingBox(x=-1, y=0, width=10, height=10).validate(64, 64)
    with pytest.raises(MediaContractRejected):
        BoundingBox(x=56, y=0, width=16, height=10).validate(64, 64)

    vault = LocalMediaVaultFixture()
    ticket = vault.stage("image_frame", b"projected-bytes", b"original-bytes")
    with pytest.raises(MediaContractRejected):
        vault.restore_once(ticket, "audio_wav", b"projected-bytes")
    with pytest.raises(MediaContractRejected):
        vault.restore_once(ticket, "image_frame", b"tampered-bytes")

    # Tamper failure leaves ticket intact or rejects; stage fresh ticket to verify single-use
    ticket2 = vault.stage("image_frame", b"projected-bytes", b"original-bytes")
    assert vault.restore_once(ticket2, "image_frame", b"projected-bytes") == b"original-bytes"
    with pytest.raises(MediaContractRejected):
        vault.restore_once(ticket2, "image_frame", b"projected-bytes")
    vault.close()


def test_run_multimodal_passes_all_nine_checks():
    frames = []
    result = run_multimodal(seed=7, on_frame=frames.append)
    assert outcome(result) == "PASS"
    assert len(result["checks"]) == 9
    assert len(frames) == result["metrics"]["frame_count"] == 11
    video_frames = [f for f in frames if f["modality"] == "video_clip"]
    assert video_frames
    assert all(
        f.get("video_frame_count") == 6 and f.get("width") == 96 and f.get("height") == 64
        for f in video_frames
    )


class _FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return dict(self._body)


def test_multimodal_live_contract_pass_replay_and_secret_safe_failure():
    bundle = build_synthetic_media_bundle(seed=7)
    masked_png = bundle["png_bytes"][:-1] + bytes([bundle["png_bytes"][-1] ^ 1])
    consumed = {"done": False}
    zeroized = []

    class FakeHttp:
        def post(self, url, *, headers=None, json=None):
            assert "Bearer SYNTHETIC_SECRET_DO_NOT_EXPORT" in headers["Authorization"]
            if url.endswith("/v1/decompose/image"):
                return _FakeResponse(
                    200,
                    {
                        "schema_version": 1,
                        "vault_ticket": "vlt_synthetic_mm_ticket",
                        "redacted_frame_base64": base64.b64encode(masked_png).decode("ascii"),
                        "task_core": "Verify telemetry band.",
                    },
                )
            if url.endswith("/v1/rehydrate/media"):
                if not consumed["done"]:
                    consumed["done"] = True
                    return _FakeResponse(
                        200,
                        {
                            "schema_version": 1,
                            "rehydrated_media_base64": base64.b64encode(bundle["png_bytes"]).decode(
                                "ascii"
                            ),
                            "round_trip_verification": {"vault_ticket_scrubbed": True},
                        },
                    )
                return _FakeResponse(
                    410,
                    {
                        "error_code": "VAULT_TICKET_SCRUBBED",
                        "reason_code": "TICKET_ALREADY_CONSUMED",
                    },
                )
            if url.endswith("/api/workbench/vault-zeroize"):
                zeroized.append(json["vault_ticket"])
                return SimpleNamespace(status_code=200)
            raise AssertionError(f"Unexpected URL {url}")

    rows = run_multimodal_live(
        base_url="https://service.example.invalid",
        key="SYNTHETIC_SECRET_DO_NOT_EXPORT",
        selection="multimodal-image",
        seed=7,
        http_client=FakeHttp(),
    )
    assert len(rows) == 1
    assert outcome(rows[0]) == "PASS"
    serialized = json.dumps(rows)
    assert "SYNTHETIC_SECRET_DO_NOT_EXPORT" not in serialized
    assert "vlt_synthetic_mm_ticket" not in serialized
    assert not zeroized
    assert sha256_prefixed(bundle["png_bytes"]).startswith("sha256:")


def test_multimodal_live_503_and_401_attribution():
    class Unavailable503Http:
        def post(self, _url, **_kwargs):
            return _FakeResponse(
                503,
                {
                    "detail": "INTERNAL_DIAGNOSTIC_TEXT_NEVER_EXPORTED",
                    "error_code": "CORE_AUTHORITY_UNAVAILABLE",
                    "reason_code": "SURFACE_DENIED_UNTIL_QUALIFIED",
                },
            )

    rows_503 = run_multimodal_live(
        base_url="https://service.example.invalid",
        key="SYNTHETIC_KEY",
        selection="multimodal-audio",
        http_client=Unavailable503Http(),
    )
    assert outcome(rows_503[0]) == "FAIL"
    assert rows_503[0]["checks"][0]["error_code"] == "CORE_AUTHORITY_UNAVAILABLE"
    assert rows_503[0]["checks"][0]["reason_code"] == "SURFACE_DENIED_UNTIL_QUALIFIED"
    assert "INTERNAL_DIAGNOSTIC" not in json.dumps(rows_503)

    class Unauthorized401Http:
        def post(self, _url, **_kwargs):
            return _FakeResponse(401, {"error_code": "UNAUTHORIZED"})

    rows_401 = run_multimodal_live(
        base_url="https://service.example.invalid",
        key="SYNTHETIC_KEY",
        selection="multimodal-video",
        http_client=Unauthorized401Http(),
    )
    assert outcome(rows_401[0]) == "BLOCKED"
