# SDK versions and reproducibility

The Python lab uses the official, unmodified EigenScope SDK **0.2.5** from the [EigenScope package registry](https://eigenscope.guardianai.network/registry). Its immutable 33,471-byte [Python wheel](https://eigenscope.guardianai.network/pypi/packages/eigenscope_sdk-0.2.5-py3-none-any.whl) declares Apache-2.0 and has this SHA-256:

```text
ac5485b605244ba513e0e99c9f7b2eb04f5499e7c08ff505803cc8afdf14d061
```

`pyproject.toml` and `uv.lock` identify the archive and enforce its integrity. SDK 0.2.5 includes `eigenscope_sdk.connect_model` and the official `eigenscope_sdk.lab_connector:connect` adapter used by the [model integration guide](model-integration.md). Model inference and its protected runtime use the separate environment described there.

The TypeScript example retains its independent [unmodified SDK 0.2.2 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-typescript-0.2.2-80b9beda), with npm integrity pinned in `typescript/package-lock.json`. The earlier [Python SDK 0.2.2 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-python-0.2.2-4da938e7) remains available with its original provenance, license and checksum for reproducing earlier runs. These independent pins make each run's client version reproducible. The configured service is selected separately through `ORTHOCORE_BASE_URL`.

## Update a dependency

1. Obtain the new official package and record its version, origin, complete SHA-256 and license.
2. Review its source and dependency changes. Retain the original package archive and use a separate snapshot name for a new artifact.
3. Update the explicit archive reference, regenerate the lockfile and run clean-install CI plus the relevant live cases against an authorized environment.
4. Record the SDK artifact and service deployment alongside the new results. Preserve the version information for earlier runs.

Released package bytes and their checksums should remain stable. Publish changed SDK code under a new package version.
