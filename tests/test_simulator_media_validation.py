"""Independent malformed PNG framing and bounded-inflation controls."""

import struct
import tracemalloc
import zlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from eigenscope_lab.simulators.multimodal import (
    PNG_SIGNATURE,
    LocalMediaVaultFixture,
    MediaContractRejected,
    decode_png_rgba,
    decode_wav_pcm16,
    encode_wav_pcm16,
)


def chunk(name, data):
    return (
        struct.pack(">I", len(data))
        + name
        + data
        + struct.pack(">I", zlib.crc32(data, zlib.crc32(name)) & 0xFFFFFFFF)
    )


def header():
    return chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))


def pixels():
    return chunk(b"IDAT", zlib.compress(b"\0\x11\x22\x33\xff"))


@pytest.fixture(autouse=True)
def require_media_extras():
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")


def test_independent_one_pixel_png_is_admitted():
    image = decode_png_rgba(PNG_SIGNATURE + header() + pixels() + chunk(b"IEND", b""))
    assert image.tolist() == [[[17, 34, 51, 255]]]


@pytest.mark.parametrize(
    "parts",
    [
        lambda: [pixels(), header(), chunk(b"IEND", b"")],
        lambda: [header(), header(), pixels(), chunk(b"IEND", b"")],
        lambda: [header(), pixels(), chunk(b"IEND", b"not empty")],
        lambda: [header(), chunk(b"ABCD", b""), pixels(), chunk(b"IEND", b"")],
        lambda: [header(), chunk(b"abca", b""), pixels(), chunk(b"IEND", b"")],
        lambda: [header(), pixels(), chunk(b"PLTE", b"\0\0\0"), chunk(b"IEND", b"")],
        lambda: [
            header(),
            pixels(),
            chunk(b"tEXt", b"note\0gap"),
            chunk(b"IDAT", b""),
            chunk(b"IEND", b""),
        ],
    ],
)
def test_invalid_png_chunk_order_and_critical_chunks_are_rejected(parts):
    with pytest.raises(MediaContractRejected):
        decode_png_rgba(PNG_SIGNATURE + b"".join(parts()))


def test_consecutive_idat_chunks_and_ancillary_metadata_are_supported():
    compressed = zlib.compress(b"\0\x11\x22\x33\xff")
    raw = (
        PNG_SIGNATURE
        + header()
        + chunk(b"tEXt", b"description\0one pixel")
        + chunk(b"IDAT", compressed[:4])
        + chunk(b"IDAT", compressed[4:])
        + chunk(b"IEND", b"")
    )
    assert decode_png_rgba(raw).tolist() == [[[17, 34, 51, 255]]]


@pytest.mark.parametrize(
    "compressed",
    [
        zlib.compress(b"\0" * 1_000_000),
        zlib.compress(b"\0\x11\x22\x33\xff") + zlib.compress(b"extra stream"),
        zlib.compress(b"\0\x11\x22\x33\xff")[:-1],
    ],
)
def test_png_inflation_is_limited_to_geometry_and_one_complete_stream(compressed):
    raw = PNG_SIGNATURE + header() + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")
    with pytest.raises(MediaContractRejected):
        decode_png_rgba(raw)


@pytest.mark.parametrize("filter_type", range(5))
def test_all_standard_png_filters_decode_to_the_same_independent_pixels(filter_type):
    # Independent forward filter: values include wraparound and all Paeth ties.
    rows = [bytes([250, 4, 40, 255, 10, 12, 90, 255]), bytes([8, 20, 5, 255, 12, 1, 200, 255])]
    encoded = bytearray()
    for row_index, original in enumerate(rows):
        encoded.append(filter_type)
        for index, value in enumerate(original):
            left = original[index - 4] if index >= 4 else 0
            up = rows[row_index - 1][index] if row_index else 0
            upper_left = rows[row_index - 1][index - 4] if row_index and index >= 4 else 0
            if filter_type == 0:
                prediction = 0
            elif filter_type == 1:
                prediction = left
            elif filter_type == 2:
                prediction = up
            elif filter_type == 3:
                prediction = (left + up) // 2
            else:
                p = left + up - upper_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upper_left)
                prediction = left if pa <= pb and pa <= pc else up if pb <= pc else upper_left
            encoded.append((value - prediction) & 255)
    raw = (
        PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(encoded)))
        + chunk(b"IEND", b"")
    )
    assert decode_png_rgba(raw).tobytes() == b"".join(rows)


def test_unknown_png_row_filter_is_rejected():
    raw = (
        PNG_SIGNATURE
        + header()
        + chunk(b"IDAT", zlib.compress(b"\5\0\0\0\0"))
        + chunk(b"IEND", b"")
    )
    with pytest.raises(MediaContractRejected):
        decode_png_rgba(raw)


def test_tiny_png_cannot_allocate_its_oversized_decompressed_payload():
    # Warm optional imports before measuring the decoder's own allocation.
    decode_png_rgba(PNG_SIGNATURE + header() + pixels() + chunk(b"IEND", b""))
    raw = (
        PNG_SIGNATURE
        + header()
        + chunk(b"IDAT", zlib.compress(b"\0" * 1_000_000))
        + chunk(b"IEND", b"")
    )
    tracemalloc.start()
    try:
        with pytest.raises(MediaContractRejected):
            decode_png_rgba(raw)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 200_000


@pytest.mark.parametrize(
    "samples,channels",
    [([0, 1, 2], 2), ([0], True), ([32768], 1), ([-32769], 1), ([0.5], 1), ([True], 1)],
)
def test_wav_encoder_rejects_partial_frames_coercion_and_wrapping(samples, channels):
    with pytest.raises(MediaContractRejected):
        encode_wav_pcm16(samples, channels=channels)


def test_stereo_pcm16_extremes_roundtrip_in_complete_frames():
    raw = encode_wav_pcm16([-32768, 32767, 0, 1], channels=2)
    samples, rate, channels = decode_wav_pcm16(raw)
    assert samples.tolist() == [-32768, 32767, 0, 1]
    assert rate == 16000 and channels == 2


def test_local_media_handle_is_single_use_even_with_concurrent_restore():
    vault = LocalMediaVaultFixture()
    token = vault.stage("image_frame", b"projected", b"original")

    def restore(_):
        try:
            return vault.restore_once(token, "image_frame", b"projected") == b"original"
        except MediaContractRejected:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(restore, range(24))) == 1
    vault.close()
