# SDK versions and reproducibility

The lab pins the official **0.2.17** Python, TypeScript and Wasm SDK packages (`eigenscope-sdk==0.2.17`, `@eigenscope/sdk@0.2.17`, `@eigenscope/orthocore-wasm@0.2.17`) alongside lockfile integrity hashes. The [EigenScope registry](https://eigenscope.guardianai.network/registry) and [EigenScope Integrations](https://eigenscope.guardianai.network/integrations) list all 0.2.17 SDKs, MCP server (`@eigenscope/mcp-server`), fleet initializer (`@eigenscope/init`), Pi firewall (`@eigenscope/pi-firewall`), and Java 17 / .NET 8 client kits.

| Package | Archive size | Registry / Channel | SHA-256 |
| --- | ---: | --- | --- |
| Python `eigenscope-sdk==0.2.17` | 69,877 bytes | PyPI (`eigenscope-sdk`) | `60f5d9370cf7fae93eb5b67549b00c426d607a331032ef49fe7df06459f60faa` |
| TypeScript `@eigenscope/sdk@0.2.17` | 119,353 bytes | npm (`@eigenscope/sdk`) | `617351333388ad7b9fe3c06beabc414f7f33cc0560c1552dde68a05e3765a53c` |
| Wasm `@eigenscope/orthocore-wasm@0.2.17` | 59,184 bytes | npm (`@eigenscope/orthocore-wasm`) | `cfe0dce30c07eed31e6edae165b8930b2c2fa7bfef80e697508239eae92d468a` |

Companion TypeScript CLI / IDE / MCP **0.2.17** packages on npm:

```text
7bd5497670d20fbe6104457da9b1f9b6b6848a2760a41006eb880fcab438bce8  eigenscope-mcp-server-0.2.17.tgz
eb7feb4db6c8484867340a4b8807dbd378dff05050c4a2f7d4d6de660a60bd1b  eigenscope-init-0.2.17.tgz
99e259486e132398e5a9594e40f471a3d0e5d9e1930c96167f8416e76b72e7e2  eigenscope-pi-firewall-0.2.17.tgz
```

`pyproject.toml` and `uv.lock` enforce the complete SHA-256 of `eigenscope-sdk==0.2.17`. `typescript/package.json` and `typescript/package-lock.json` enforce complete SHA-512 integrity values for `@eigenscope/sdk@0.2.17` and `@eigenscope/orthocore-wasm@0.2.17`. SDK **0.2.17** provides `eigenscope_sdk.connect_model`, `eigenscope_sdk.lab_connector:connect`, `sanitize_oil_gas_production_context`, `sanitize_clinical_phi`, `decompose_6g_ran_intent`, `sanitize_robotics_vla_observation`, `sanitize_tool_output`, RAG ingestion/retrieval/evaluation methods, and multimodal decomposition/rehydration helpers. Model inference uses the separate environment in the [model integration guide](model-integration.md).

Earlier hash-named snapshots remain documented for reproducing prior runs:

- [Python 0.2.12](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-python-0.2.12-60693e24) · [TypeScript 0.2.12](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-typescript-0.2.12-260f86e6) · [Wasm 0.2.12](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-wasm-0.2.12-5f88cdec)
- [Python SDK 0.2.8 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-python-0.2.8-c313c97a) · [TypeScript SDK 0.2.8 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-typescript-0.2.8-b00813e9)
- [Python SDK 0.2.7 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-python-0.2.7-96b307ab) · [TypeScript SDK 0.2.7 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-typescript-0.2.7-000de10c)
- Python SDK **0.2.6** (`790d9658...`) · TypeScript SDK **0.2.6** (`acc08d85...`) · Python SDK **0.2.5** (`ac5485b6...`)
- [Python SDK 0.2.2 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-python-0.2.2-4da938e7) · [TypeScript SDK 0.2.2 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-typescript-0.2.2-80b9beda)

These independent pins make each run's client version reproducible. The configured service is selected separately through `ORTHOCORE_BASE_URL`.
