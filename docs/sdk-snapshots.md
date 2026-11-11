# SDK versions and reproducibility

The lab pins official, unmodified Python, TypeScript and Wasm SDK **0.2.12** archives in hash-named snapshots. Each snapshot includes its upstream origin, license declaration, Apache-2.0 license text and checksums. The [EigenScope registry](https://eigenscope.guardianai.network/registry) remains the upstream package source.

| Package | Archive size | Snapshot |
| --- | ---: | --- |
| Python `eigenscope-sdk` | 50,327 bytes | [Python 0.2.12](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-python-0.2.12-60693e24) |
| TypeScript `@eigenscope/sdk` | 118,569 bytes | [TypeScript 0.2.12](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-typescript-0.2.12-260f86e6) |
| Wasm `@eigenscope/orthocore-wasm` | 58,284 bytes | [Wasm 0.2.12](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-wasm-0.2.12-5f88cdec) |

SHA-256 identities:

```text
60693e24ec67b59409a372a0de8edd90ebf8c452c927a2f4e0dc9e585f5655b7  eigenscope_sdk-0.2.12-py3-none-any.whl
260f86e62f084c3b497d8c97fca16081184a5d81aa897c3daac911780d854d44  eigenscope-sdk-0.2.12.tgz
5f88cdecd3f2a8c078992b476260143fd987d07d7f13a39f94009f64f2700ae5  eigenscope-orthocore-wasm-0.2.12.tgz
```

`pyproject.toml` and `uv.lock` identify the Python archive and enforce its complete SHA-256. `typescript/package.json` and `typescript/package-lock.json` identify both npm archives and enforce their complete SHA-512 integrity values. SDK 0.2.12 provides `eigenscope_sdk.connect_model`, the `eigenscope_sdk.lab_connector:connect` adapter, multimodal decomposition/rehydration helpers and the domain policy presets. Model inference uses the separate environment in the [model integration guide](model-integration.md).

Earlier snapshots remain documented for reproducing prior runs:

- [Python SDK 0.2.8 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-python-0.2.8-c313c97a)
- [TypeScript SDK 0.2.8 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-typescript-0.2.8-b00813e9)

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
