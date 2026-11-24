# SDK versions and reproducibility

The lab pins the official **0.2.18** Python, TypeScript and Wasm SDK packages (`eigenscope-sdk==0.2.18`, `@eigenscope/sdk@0.2.18`, `@eigenscope/orthocore-wasm@0.2.18`) alongside lockfile integrity hashes. The [EigenScope registry](https://eigenscope.guardianai.network/registry) and [EigenScope Integrations](https://eigenscope.guardianai.network/integrations) list all 0.2.18 SDKs, MCP server (`@eigenscope/mcp-server`), fleet initializer (`@eigenscope/init`), Pi firewall (`@eigenscope/pi-firewall`), and Java 17 / .NET 8 client kits.

| Package | Archive size | Registry / Channel | SHA-256 |
| --- | ---: | --- | --- |
| Python `eigenscope-sdk==0.2.18` (wheel) | 70,453 bytes | PyPI (`eigenscope-sdk`) | `388685e7757ffed6b04ff1a8946aca6a1a026b5742a671f03e8b9ae085e90bed` |
| Python `eigenscope-sdk==0.2.18` (sdist) | 66,013 bytes | PyPI (`eigenscope-sdk`) | `89225f012e5b57f6a360769eff985a560fed97340b0e7fb0203e75831fa82c09` |
| TypeScript `@eigenscope/sdk@0.2.18` | 120,747 bytes | npm (`@eigenscope/sdk`) | `13f0fd07afcc5ee79fa60a7443a93d51e261cf1585b133d3380e616f8565322d` |
| Wasm `@eigenscope/orthocore-wasm@0.2.18` | 59,184 bytes | npm (`@eigenscope/orthocore-wasm`) | `839d6967d38af547bb15aa7f30ed3f1a58c2ddabd5f67377ae13eeebf14b883b` |

Companion TypeScript CLI / IDE / MCP **0.2.18** packages on npm:

```text
4f50917dc14dce57df9aca4f29d407cd1839dbef4ecd4f514c3088e4c831be50  eigenscope-mcp-server-0.2.18.tgz
231a0f4cc7e1f3ffa3a3d29ba3b3830b4e702590e3cd7b99b931d1e3d38059f6  eigenscope-init-0.2.18.tgz
b2ad6a490be838759a2fba7d34a14288c3c199d8005bce8320601df2c4962ac1  eigenscope-pi-firewall-0.2.18.tgz
```

`pyproject.toml` and `uv.lock` enforce the complete SHA-256 of `eigenscope-sdk==0.2.18`. `typescript/package.json` and `typescript/package-lock.json` enforce complete SHA-512 integrity values for `@eigenscope/sdk@0.2.18` and `@eigenscope/orthocore-wasm@0.2.18`. SDK **0.2.18** provides `eigenscope_sdk.connect_model`, `eigenscope_sdk.lab_connector:connect`, `sanitize_oil_gas_production_context`, `sanitize_clinical_phi`, `decompose_6g_ran_intent`, `sanitize_robotics_vla_observation`, `sanitize_tool_output`, RAG ingestion/retrieval/evaluation methods, and multimodal decomposition/rehydration helpers. Model inference uses the separate environment in the [model integration guide](model-integration.md).

Earlier hash-named snapshots remain documented for reproducing prior runs:

- [Python 0.2.12](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-python-0.2.12-60693e24) · [TypeScript 0.2.12](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-typescript-0.2.12-260f86e6) · [Wasm 0.2.12](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-wasm-0.2.12-5f88cdec)
- [Python SDK 0.2.8 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-python-0.2.8-c313c97a) · [TypeScript SDK 0.2.8 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-typescript-0.2.8-b00813e9)
- [Python SDK 0.2.7 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-python-0.2.7-96b307ab) · [TypeScript SDK 0.2.7 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-typescript-0.2.7-000de10c)
- Python SDK **0.2.6** (`790d9658...`) · TypeScript SDK **0.2.6** (`acc08d85...`) · Python SDK **0.2.5** (`ac5485b6...`)
- [Python SDK 0.2.2 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-python-0.2.2-4da938e7) · [TypeScript SDK 0.2.2 snapshot](https://github.com/LuckyOkoedion/eigenscope-integration-lab/releases/tag/sdk-typescript-0.2.2-80b9beda)

These independent pins make each run's client version reproducible. The configured service is selected separately through `ORTHOCORE_BASE_URL`.
