# TypeScript SDK integration

Requires Node >=22.18. The official SDK is installed from the lab's unmodified, versioned compatibility snapshot; `package-lock.json` pins its integrity.

```sh
npm ci --ignore-scripts
npm test
npm run typecheck
```

The unit suite calls the actual published SDK. Its explicitly synthetic API responses test schema/telemetry/authentication rejection; one local HTTP server exercises the real fetch transport. These fixtures do not establish privacy-engine efficacy.

For actual service calls, configure `ORTHOCORE_BASE_URL` and `ORTHOCORE_API_KEY` securely, then run `npm run live`. This runs the fixed coding/telecom/robotics/Oil & Gas examples, uses deterministic provider echo, restores the mapping and cleans up owned tickets. Results contain checks and timings, never credentials or ticket values. Exit1 means a contract failed.

The example is server-side. Do not bundle API credentials into public browser code. For real provider streaming, the Python lab separately exercises HTTP/SSE, tool calls, timeouts, malformed events and cancellation.
