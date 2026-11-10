# SDK versions and reproducibility

The lab pins official, unmodified EigenScope SDK **0.2.8** archives in hash-named snapshots. Each snapshot preserves the upstream bytes and includes its origin, license declaration, Apache-2.0 license text and SHA-256 manifest. The [EigenScope registry](https://eigenscope.guardianai.network/registry) remains the upstream package source.

The **52,185-byte [Python wheel](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/download/sdk-python-0.2.8-c313c97a/eigenscope_sdk-0.2.8-py3-none-any.whl)** is preserved in the [Python snapshot release](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-python-0.2.8-c313c97a) with this SHA-256:

```text
c313c97a64e8d1a2e56fb32e71001991bad90861aa3859bf740d66b1c1cc7c0a
```

`pyproject.toml` and `uv.lock` identify the archive and enforce its integrity. SDK 0.2.8 includes `eigenscope_sdk.connect_model`, the official `eigenscope_sdk.lab_connector:connect` adapter used by the [model integration guide](model-integration.md), multimodal decomposition/rehydration helpers (`decompose_audio`, `decompose_image`, `decompose_video`, `rehydrate_media`), and domain presets (`HEALTHCARE_CLINICAL_PHI`, `OIL_GAS_PRODUCTION_OPS`). Model inference and its protected runtime use the separate environment described there.

The **122,437-byte [TypeScript tarball](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/download/sdk-typescript-0.2.8-b00813e9/eigenscope-sdk-0.2.8.tgz)** is preserved in the [TypeScript snapshot release](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-typescript-0.2.8-b00813e9) with this SHA-256:

```text
b00813e9ed5e8717fc2aa2fce228c8e6364517fd44ad8ff8c89fe104807a2c31
```

`typescript/package-lock.json` also enforces its complete SHA-512 integrity value:

```text
sha512-G4gy08KhZdUzbPepBaX42o2AcoY7pspS3+NETxjOWZZaQIE7Fa0D5aT3yF96cngXZf/5tmcNwWzHLv2XgH6Ilg==
```

Earlier snapshots remain documented for reproducing prior runs:

- [Python SDK 0.2.7 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-python-0.2.7-96b307ab)
- [TypeScript SDK 0.2.7 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-typescript-0.2.7-000de10c)

- Python SDK **0.2.6** wheel (`35,735` bytes, SHA-256 `790d9658f9d91b66ed78cbf27413ae8233a52ea7e10e75800b2849c86512e6d7`)
- TypeScript SDK **0.2.6** tarball (`51,089` bytes, SHA-256 `acc08d85a2968f870107967f67154c4a29c99f8609ca0b08d937557327179358`)
- Python SDK **0.2.5** wheel (`33,471` bytes, SHA-256 `ac5485b605244ba513e0e99c9f7b2eb04f5499e7c08ff505803cc8afdf14d061`)
- [Python SDK 0.2.2 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-python-0.2.2-4da938e7)
- [TypeScript SDK 0.2.2 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-typescript-0.2.2-80b9beda)

These independent pins make each run's client version reproducible. The configured service is selected separately through `ORTHOCORE_BASE_URL`.

## Update a dependency

1. Obtain the new official package and record its version, origin, complete SHA-256 and license.
2. Review its source and dependency changes. Retain the original package archive and use a separate hash-named snapshot for a new artifact. Upload the unmodified archive, provenance, license text and checksum manifest before changing a dependency URL.
3. Update the explicit archive reference, regenerate the lockfile and run clean-install CI plus the relevant live cases against an authorized environment.
4. Record the SDK artifact and service deployment alongside the new results. Preserve the version information for earlier runs.

Released package bytes and their checksums should remain stable. Publish changed SDK code under a new package version.
