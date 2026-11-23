# TypeScript SDK integration

Requires Node >=22.18. The official SDK is installed from the lab's unmodified, versioned compatibility snapshot; `package-lock.json` pins its integrity.

```sh
npm ci --ignore-scripts
npm test
npm run typecheck
```

The unit suite calls the actual published SDK. Its explicitly synthetic API responses test schema/telemetry/authentication rejection; one local HTTP server exercises the real fetch transport. These fixtures do not establish privacy-engine efficacy.

For actual service calls, configure `ORTHOCORE_BASE_URL` and `ORTHOCORE_API_KEY` securely, then run `npm run live` (text SDK workflows including `coding`, `telecom`, `robotics`, `oil-gas`, `healthcare`, `legal`, `finance`, `rag-sdk`) or `npm run live:multimodal` (public `/v1` audio, image and video HTTP round-trip checks). Results contain checks and timings, never credentials or ticket values. Exit 1 means a contract failed.

The example is server-side. Do not bundle API credentials into public browser code. For real provider streaming, the Python lab separately exercises HTTP/SSE, tool calls, timeouts, malformed events and cancellation.

The live example also exercises the official RAG SDK ingestion, retrieval and evaluation methods as `rag-sdk`. Legal uses the `LEGAL_CONTRACT_MA_ANALYTICS` policy and checks preservation of the cap, survival window and jurisdiction. Finance exercises `sanitizeToolOutput` (`UNTRUSTED_TOOL_IPI_FIREWALL`) while rejecting settlement redirect directives. Video validation accepts both official OCVC versions through the SDK decoder.

For additional runtimes and native distribution packages (0.2.17): `@eigenscope/mcp-server`, `@eigenscope/init`, `@eigenscope/pi-firewall`, plus [Java 17 & C# / .NET 8 SDKs](https://eigenscope.guardianai.network/downloads/kits/enterprise-sdks-java-dotnet-kit.zip) and all 24 production kits on [EigenScope Integrations](https://eigenscope.guardianai.network/integrations).
