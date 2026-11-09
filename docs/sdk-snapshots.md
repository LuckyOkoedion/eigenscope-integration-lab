# SDK versions and reproducibility

The Python lab uses the official, unmodified EigenScope SDK **0.2.7** from the [EigenScope package registry](https://eigenscope.guardianai.network/registry). Its immutable 44,766-byte [Python wheel](https://eigenscope.guardianai.network/pypi/packages/eigenscope_sdk-0.2.7-py3-none-any.whl) declares Apache-2.0 and has this SHA-256:

```text
37036c2959b9f7e052d07b02f8e892f258017ba6572e3af5a4a847ea70ad0258
```

`pyproject.toml` and `uv.lock` identify the archive and enforce its integrity. SDK 0.2.7 includes `eigenscope_sdk.connect_model`, the official `eigenscope_sdk.lab_connector:connect` adapter used by the [model integration guide](model-integration.md), multimodal decomposition/rehydration helpers (`decompose_audio`, `decompose_image`, `decompose_video`, `rehydrate_media`), and domain presets (`HEALTHCARE_CLINICAL_PHI`, `OIL_GAS_PRODUCTION_OPS`). Model inference and its protected runtime use the separate environment described there.

The TypeScript workspace pins the official, unmodified [TypeScript SDK 0.2.7 tarball](https://eigenscope.guardianai.network/npm/eigenscope-sdk-0.2.7.tgz) (102,432 bytes, SHA-256 `3d9597ee6d65d0a396f370a7a02243c3afaa4e0f9a475789f4deb3dac11fc743`), with SHA-512 integrity enforced in `typescript/package-lock.json`.

Earlier snapshots remain documented for reproducing prior runs:

- Python SDK **0.2.6** wheel (`35,735` bytes, SHA-256 `790d9658f9d91b66ed78cbf27413ae8233a52ea7e10e75800b2849c86512e6d7`)
- TypeScript SDK **0.2.6** tarball (`51,089` bytes, SHA-256 `acc08d85a2968f870107967f67154c4a29c99f8609ca0b08d937557327179358`)
- Python SDK **0.2.5** wheel (`33,471` bytes, SHA-256 `ac5485b605244ba513e0e99c9f7b2eb04f5499e7c08ff505803cc8afdf14d061`)
- [Python SDK 0.2.2 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-python-0.2.2-4da938e7)
- [TypeScript SDK 0.2.2 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-typescript-0.2.2-80b9beda)

These independent pins make each run's client version reproducible. The configured service is selected separately through `ORTHOCORE_BASE_URL`.

## Update a dependency

1. Obtain the new official package and record its version, origin, complete SHA-256 and license.
2. Review its source and dependency changes. Retain the original package archive and use a separate snapshot name for a new artifact.
3. Update the explicit archive reference, regenerate the lockfile and run clean-install CI plus the relevant live cases against an authorized environment.
4. Record the SDK artifact and service deployment alongside the new results. Preserve the version information for earlier runs.

Released package bytes and their checksums should remain stable. Publish changed SDK code under a new package version.
