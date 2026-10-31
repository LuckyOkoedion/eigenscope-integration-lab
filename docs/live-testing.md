# Test an EigenScope integration

Run the published sample cases against an EigenScope service, with local simulators providing the external systems.

## Install and configure

```sh
uv sync --locked --all-extras
export ORTHOCORE_BASE_URL=https://eigenscope.guardianai.network
# Set ORTHOCORE_API_KEY using your shell or secret manager.
uv run --frozen eigenscope-lab live --output results/live.json
```

HTTPS is required except for loopback sidecars. The base URL must be an origin without credentials, a path, query string or fragment. The lab reads the key from the environment. `.env.example` documents the variables; it is not loaded automatically.

Live tests send the fixed synthetic cases in `src/eigenscope_lab/fixtures.py` to your configured service. Hosted usage follows your service plan. Each invocation consumes or scrubs the temporary mappings it creates.

## What the runner checks

1. Decompose each case through the official SDK's general or domain-specific method.
2. Check private-value removal, required task terms and the specified untrusted-content conditions.
3. Send the protected core through the OpenAI HTTP client to the local echo provider, which preserves the response text and symbol slots exactly.
4. Rehydrate through the official SDK/API; check restored identifiers, mapping consumption and replay rejection.
5. Scrub any remaining mapping created by the invocation, including after a provider failure.
6. For robotics, telecom and Oil & Gas, exercise the scenario adapter with the returned core.

The SDK uses its public transport hook for bounded HTTP requests, with redirects and environment proxies disabled. Authentication, policy, canonicalization and response validation remain in the official SDK. The runner tracks only its own returned mapping handles in memory, including handles from responses rejected by SDK validation.

Required-term assertions cover the stated sample contract. The deterministic provider isolates round-trip behavior from model reasoning. See [architecture and fidelity](architecture.md) for the systems exercised by each scenario.

## Workbench controls

The workbench group calls `/api/workbench/decompose` with six arithmetic cases and three malformed policy types. It checks final answers and mapping consumption. A schema test passes only for a schema-specific rejection; authentication, rate-limit and separation-gate responses have separate meanings.

## Select or repeat cases

```sh
uv run --frozen eigenscope-lab live --case robotics --output results/robotics-live.json
uv run --frozen eigenscope-lab live --case workbench --output results/workbench.json
uv run --frozen eigenscope-lab live --repeat 3 --timeout 15 --output results/repeated.json
```

Other case IDs are `coding`, `tool-injection`, `telecom`, `oil-gas`, `email` and `unicode`. Requests run sequentially. Repeat applies to SDK cases, is capped at ten and leaves the workbench controls as a single pass.

Timings identify SDK, local-provider and complete-workflow boundaries. SDK wall time includes serialization, network requests, service execution and response validation. Reports record the sample count; compare runs using the same client, configuration and environment.

## TypeScript

```sh
cd typescript
npm ci --ignore-scripts
npm test
npm run typecheck
npm run live
```

The server-side example reads `ORTHOCORE_API_KEY` and passes it as `bearerToken`. It runs coding, telecom, robotics and Oil & Gas cases with a deterministic provider echo. The Python runner separately exercises the local provider over HTTP. See the [TypeScript guide](../typescript/README.md).

## Read and share results

Results contain checks, timings and structured error codes. Python CLI exit codes are **0** for passing checks, **1** for a failed contract, **2** for missing configuration or prerequisites, and **3** for a test-runner error. A failed check remains visible in the report.

Generated results stay in the ignored `results/` folder. Credentials, mapping handles and raw exception messages are excluded. Review any JSON or screenshots before sharing them, and use the [security reporting process](../SECURITY.md) for vulnerabilities.
