# SDK versions and reproducibility

The Python lab uses the official, unmodified EigenScope SDK **0.2.2** from the [versioned SDK snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-python-0.2.2-4da938e7). The 27,492-byte wheel declares Apache-2.0 and is distributed with its provenance, license text and checksum:

```text
4da938e7c6db1acffd3d3098e2e9c0ae692d0741cd7f1ee413f171e856f662b8
```

`pyproject.toml` and `uv.lock` identify the archive and enforce its integrity. The TypeScript example pins its SDK archive in `typescript/package-lock.json`. These pins make a test run's client version reproducible. The configured service is selected separately through `ORTHOCORE_BASE_URL`.

## Update a dependency

1. Obtain the new official package and record its version, origin, complete SHA-256 and license.
2. Review its source and dependency changes. Retain the original package archive and use a separate snapshot name for a new artifact.
3. Update the explicit archive reference, regenerate the lockfile and run clean-install CI plus the relevant live cases against an authorized environment.
4. Record the SDK artifact and service deployment alongside the new results. Preserve the version information for earlier runs.

Released package bytes and their checksums should remain stable. Publish changed SDK code under a new package version.
