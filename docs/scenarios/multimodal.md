# Multimodal: audio, image and video container and round-trip checks

Validate lossless container framing, signal/spatial span masking and byte-exact
SHA-256 restoration contracts across audio (`16 kHz` PCM16 `RIFF/WAVE`), image
(`8-bit` RGBA `PNG` with IEEE CRC-32) and multi-frame video (`OCVC v1` RGBA).
NumPy and SciPy execute all codecs and spectral checks in memory without writing
temporary files, keeping the scenario compatible with read-only container
filesystems.

## Run it

```bash
uv sync --locked --extra multimodal
uv run --frozen eigenscope-lab run multimodal
uv run --frozen pytest tests/test_multimodal.py
```

## Container specifications and bounds

| Modality | Container | Local validation contract |
| --- | --- | --- |
| Audio (`audio_wav`) | `RIFF/WAVE` 16-bit linear PCM | `16,000–48,000 Hz`, 1–2 channels, DFT Parseval energy identity and SciPy STFT windowing |
| Image (`image_frame`) | `PNG` 8-bit non-interlaced RGBA | Signature, `IHDR`/`IDAT`/`IEND` structure, per-chunk IEEE CRC-32, `1..4096` px per side |
| Video (`video_clip`) | `OCVC v1` raw RGBA clip | `OCVC\x01\x00` + big-endian `(frames, width, height)`, `1..128` frames, up to `1280×720` |

## Six verification phases

1. **`audio-framing`** — Decodes a deterministic `1.5 s` `16 kHz` mono PCM16
   recording, checks Discrete Fourier Transform Parseval energy conservation
   (`relative error < 1e-12`) and STFT magnitude, and zeroes the declared speech
   span while preserving surrounding task audio samples bit-for-bit.
2. **`audio-reconstruction`** — Restores the original WAV container through a
   single-use local fixture handle and verifies SHA-256 equality.
3. **`image-masking`** — Decodes a `96×64` RGBA PNG with CRC-32 verification and
   masks a declared bounding box while keeping unmasked task waveform pixels
   unchanged.
4. **`image-reconstruction`** — Restores the original PNG bytes and verifies
   SHA-256 equality.
5. **`video-tracking`** — Masks a translating badge bounding box across 6 video
   frames while preserving stationary equipment status pixels, then verifies
   SHA-256 clip restoration.
6. **`tamper-and-ceiling-controls`** — Verifies that handle replay, 1-byte media
   tampering, corrupted PNG CRC-32 chunks, oversized dimensions (`> 4096` px or
   `> 128` frames) and out-of-range sample rates (`8 kHz`) are rejected.

## Live HTTP endpoints

Local simulation uses in-memory test handles and makes no network requests. To
exercise a configured EigenScope deployment's `/v1/decompose/{audio,image,video}`
and `/v1/rehydrate/media` HTTP endpoints with synthetic containers:

```bash
export ORTHOCORE_BASE_URL=https://eigenscope.guardianai.network
# Configure ORTHOCORE_API_KEY securely in your shell environment.
uv run --frozen eigenscope-lab live --case multimodal --output results/multimodal-live.json
```
